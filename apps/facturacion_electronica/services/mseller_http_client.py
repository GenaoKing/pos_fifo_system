"""
apps/facturacion_electronica/services/mseller_http_client.py

Cliente HTTP puro contra la API de MSeller ECF. NO conoce nada de
ventas, ECF como modelo, ni del flujo de negocio. Solo habla HTTP.

Endpoints implementados (según docs.ecf.mseller.app, abril 2026):

  POST  /{entorno}/customer/authentication
        body: {email, password}
        → {accessToken, idToken, refreshToken}

  POST  /{entorno}/documentos-ecf[?validate=true]
        headers: Authorization: Bearer {idToken}, X-API-KEY: {api_key}
        body: {ECF: {...}}
        → {rnc, ecf, internalTrackId, securityCode, qr_url, signedDate}

  GET   /{entorno}/documentos-ecf?ecf={eNCF}
        headers: Authorization: Bearer {idToken}, X-API-KEY: {api_key}
        → {fileName, status, ncf, securityCode, qr_url, internalTrackId, ...}

Diseño:
- Auth lazy: solo se autentica cuando hace falta y cachea el idToken
  por proceso. Si una request retorna 401, reintenta UNA vez con auth
  fresca.
- Backoff exponencial sobre 5xx y errores de red. Errores 4xx se
  propagan inmediatamente (no son transitorios).
- Logging detallado a `logs/ecf_mseller.log` (config en settings).
- Sin estado fiscal: este módulo no decide qué hacer con las respuestas,
  solo las parsea a dicts y las retorna o levanta excepciones.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger('ecf.mseller')


# =============================================================================
# Excepciones específicas
# =============================================================================

class MSellerError(Exception):
    """Error genérico al hablar con MSeller."""

    def __init__(self, message: str, status_code: int | None = None,
                 response_body: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body or {}


class MSellerAuthError(MSellerError):
    """Credenciales o API key inválidas/expiradas."""


class MSellerValidationError(MSellerError):
    """El documento fue rechazado por validación (ECF_VALIDATION_FAILED)."""

    def __init__(self, message: str, validation_errors: list[dict],
                 response_body: dict | None = None):
        super().__init__(message, status_code=400, response_body=response_body)
        self.validation_errors = validation_errors


class MSellerRateLimitError(MSellerError):
    """HTTP 429 — agotamos cuota."""


class MSellerServerError(MSellerError):
    """HTTP 5xx — transitorio, candidato a reintento."""


# =============================================================================
# Configuración del cliente
# =============================================================================

@dataclass(frozen=True)
class MSellerConfig:
    """
    Configuración inmutable de una conexión a MSeller para un emisor.

    Se construye desde `Emisor.config_proveedor` resolviendo las env
    vars indicadas. Inmutable para que dos hilos no se pisen los
    valores en runtime.
    """
    email: str
    password: str
    api_key: str
    entorno: str  # 'TesteCF' | 'CerteCF' | 'eCF'
    validar_antes_enviar: bool = False

    BASE_URL = 'https://ecf.api.mseller.app'

    @property
    def base(self) -> str:
        return f'{self.BASE_URL}/{self.entorno}'

    @classmethod
    def from_emisor_config(cls, config_proveedor: dict) -> 'MSellerConfig':
        """
        Construye desde el JSON guardado en Emisor.config_proveedor.
        Resuelve env vars indicadas por nombre, no guarda valores.
        """
        def _resolve(key_name: str) -> str:
            env_var = config_proveedor.get(key_name)
            if not env_var:
                raise MSellerError(
                    f'Falta clave "{key_name}" en config_proveedor del Emisor.'
                )
            value = os.environ.get(env_var)
            if not value:
                raise MSellerError(
                    f'Variable de entorno "{env_var}" no está definida. '
                    f'Configurala en el servicio NSSM antes de reiniciar.'
                )
            return value

        return cls(
            email=_resolve('email_env'),
            password=_resolve('password_env'),
            api_key=_resolve('api_key_env'),
            entorno=config_proveedor.get('entorno', 'TesteCF'),
            validar_antes_enviar=config_proveedor.get('validar_antes_enviar', False),
        )


# =============================================================================
# Cliente HTTP
# =============================================================================

class MSellerHTTPClient:
    """
    Cliente HTTP con auth lazy y reintentos sobre transitorios.

    Una instancia por emisor. Mantiene el idToken cacheado en memoria;
    al recibir 401 reautentica una vez. No persiste el token entre
    procesos a propósito — re-autenticar al arrancar es barato y evita
    cachés rancios.
    """
    DEFAULT_TIMEOUT = 30  # segundos
    MAX_RETRIES = 3
    BACKOFF_BASE = 1.5  # segundos para el primer reintento

    def __init__(self, config: MSellerConfig):
        self.config = config
        self._id_token: str | None = None
        self._session = requests.Session()

    # ------------------------------------------------------------------ auth

    def _authenticate(self) -> str:
        """
        POST /{entorno}/customer/authentication → idToken

        Retorna el idToken (que es el que va en Authorization, NO el
        accessToken — la doc es clara en este punto).
        """
        url = f'{self.config.base}/customer/authentication'
        payload = {
            'email': self.config.email,
            'password': self.config.password,
        }
        logger.info(f'MSeller auth: POST {url}')

        try:
            response = self._session.post(
                url, json=payload, timeout=self.DEFAULT_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise MSellerError(f'Error de red en autenticación: {exc}') from exc

        if response.status_code == 401:
            raise MSellerAuthError(
                'Credenciales MSeller inválidas (401). '
                'Verificá email/password en las variables de entorno.',
                status_code=401,
            )
        if response.status_code != 200:
            raise MSellerError(
                f'Autenticación falló con status {response.status_code}: '
                f'{response.text[:300]}',
                status_code=response.status_code,
            )

        data = response.json()
        id_token = data.get('idToken')
        if not id_token:
            raise MSellerAuthError(
                'Respuesta de auth no contiene idToken.',
                response_body=data,
            )
        logger.info('MSeller auth: OK, idToken cacheado.')
        return id_token

    def _ensure_token(self) -> str:
        if self._id_token is None:
            self._id_token = self._authenticate()
        return self._id_token

    def _invalidate_token(self) -> None:
        self._id_token = None

    def _auth_headers(self) -> dict[str, str]:
        return {
            'Authorization': f'Bearer {self._ensure_token()}',
            'X-API-KEY': self.config.api_key,
        }

    # ----------------------------------------------------------- core request

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """
        Wrapper único para todas las requests autenticadas. Maneja:
        - Reauth automático en 401 (una sola vez)
        - Backoff exponencial en 5xx y errores de red
        - Mapeo de errores a excepciones tipadas
        """
        url = f'{self.config.base}{path}'
        attempt = 0
        last_exc: Exception | None = None

        while attempt < self.MAX_RETRIES:
            attempt += 1
            try:
                headers = self._auth_headers()
                if json_body is not None:
                    headers['Content-Type'] = 'application/json'

                logger.debug(
                    f'MSeller {method} {url} '
                    f'(attempt {attempt}/{self.MAX_RETRIES}) params={params}'
                )

                response = self._session.request(
                    method=method,
                    url=url,
                    json=json_body,
                    params=params,
                    headers=headers,
                    timeout=self.DEFAULT_TIMEOUT,
                )
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning(f'MSeller request error: {exc}')
                self._sleep_backoff(attempt)
                continue

            # 401 → reauth y reintento (una vez)
            if response.status_code == 401 and attempt == 1:
                logger.info('MSeller 401: invalidando token y reintentando.')
                self._invalidate_token()
                continue

            # 429 / 5xx → reintentar con backoff
            if response.status_code == 429:
                logger.warning('MSeller 429 rate limit, backoff...')
                self._sleep_backoff(attempt)
                if attempt >= self.MAX_RETRIES:
                    raise MSellerRateLimitError(
                        'Rate limit excedido tras reintentos.',
                        status_code=429,
                        response_body=self._safe_json(response),
                    )
                continue

            if 500 <= response.status_code < 600:
                logger.warning(f'MSeller {response.status_code} server error.')
                self._sleep_backoff(attempt)
                if attempt >= self.MAX_RETRIES:
                    raise MSellerServerError(
                        f'Server error {response.status_code} tras reintentos.',
                        status_code=response.status_code,
                        response_body=self._safe_json(response),
                    )
                continue

            # OK o 4xx terminal
            return self._handle_response(response)

        # Si salimos del while sin retornar, fue por errores de red repetidos
        raise MSellerError(
            f'Imposible contactar MSeller tras {self.MAX_RETRIES} intentos: '
            f'{last_exc}'
        )

    def _handle_response(self, response: requests.Response) -> dict:
        """
        Caso final: response 2xx o 4xx terminal. Parsea, mapea a
        excepciones tipadas, o retorna el dict.
        """
        body = self._safe_json(response)

        if 200 <= response.status_code < 300:
            return body

        # 400 — puede ser validación estructurada o JSON mal formado
        if response.status_code == 400:
            if body.get('code') == 'ECF_VALIDATION_FAILED':
                raise MSellerValidationError(
                    body.get('message', 'Validación fallida'),
                    validation_errors=body.get('details', {}).get('validationErrors', []),
                    response_body=body,
                )
            raise MSellerError(
                f'Bad request: {body.get("message", response.text[:200])}',
                status_code=400,
                response_body=body,
            )

        if response.status_code == 401:
            raise MSellerAuthError(
                'Token rechazado tras reintento de reauth.',
                status_code=401,
                response_body=body,
            )

        if response.status_code == 403:
            raise MSellerAuthError(
                'API Key inválida o sin permisos (403). Verificá X-API-KEY.',
                status_code=403,
                response_body=body,
            )

        # 4xx genérico
        raise MSellerError(
            f'HTTP {response.status_code}: {body.get("message", response.text[:200])}',
            status_code=response.status_code,
            response_body=body,
        )

    @staticmethod
    def _safe_json(response: requests.Response) -> dict:
        try:
            return response.json()
        except ValueError:
            return {'_raw': response.text}

    def _sleep_backoff(self, attempt: int) -> None:
        delay = self.BACKOFF_BASE * (2 ** (attempt - 1))
        time.sleep(delay)

    # ------------------------------------------------------------- API pública

    def enviar_documento(
        self,
        ecf_payload: dict,
        *,
        validar: bool | None = None,
    ) -> dict:
        """
        POST /{entorno}/documentos-ecf

        Retorna el dict de respuesta tal cual:
            {rnc, ecf, internalTrackId, securityCode, qr_url, signedDate}
        Si validar=True, MSeller valida sin consumir secuencia y retorna:
            {valid: true, message: "..."}

        Levanta MSellerValidationError con detalles si MSeller rechaza
        por estructura/cálculos.
        """
        if validar is None:
            validar = self.config.validar_antes_enviar
        params = {'validate': 'true'} if validar else None

        return self._request(
            'POST',
            '/documentos-ecf',
            json_body=ecf_payload,
            params=params,
        )

    def consultar_documento(self, encf: str) -> dict:
        """
        GET /{entorno}/documentos-ecf?ecf={eNCF}

        Retorna el dict completo del documento. El campo `status` indica
        el estado actual ("Aceptado", "Rechazado", etc.).
        """
        return self._request(
            'GET',
            '/documentos-ecf',
            params={'ecf': encf},
        )

    def consultar_documentos_batch(self, encfs: list[str]) -> dict:
        """
        POST /{entorno}/documentos-ecf/status/batch

        Hasta 100 e-CF por solicitud. MSeller recomienda 50 para mejor
        rendimiento. El management command de reintentos puede usar
        esto para reducir round-trips.
        """
        if len(encfs) > 100:
            raise MSellerError(
                f'Batch de consulta limitado a 100 documentos, '
                f'recibidos {len(encfs)}.'
            )
        return self._request(
            'POST',
            '/documentos-ecf/status/batch',
            json_body={'ecfs': encfs},
        )