"""
apps/sync/engine.py

Motor de sincronizacion.

Responsabilidades:
    1. push_eventos()       -> empuja la cola EventoSync al cloud
    2. pull_maestros()      -> baja productos/categorias/clientes desde cloud
    3. check_connection()   -> ping al cloud (usado por el decorador y por UI)
    4. ciclo_completo()     -> combina los anteriores en un run

El engine depende de estas settings:
    CLOUD_API_URL       (str)    ej: 'https://pos-cloud.azurewebsites.net'
    CLOUD_API_TOKEN     (str)    token de la sucursal
    SYNC_ENABLED        (bool)   default False (modo standalone)
    SYNC_BATCH_SIZE     (int)    default 50
    SYNC_MAX_RETRIES    (int)    default 10
    SYNC_HTTP_TIMEOUT   (int)    default 10 (segundos)

Robustez:
- Cada ciclo se envuelve en try/except y loguea; nunca revienta el daemon.
- Los eventos se toman con select_for_update(skip_locked=True) para que dos
  corridas simultaneas NO procesen los mismos eventos.
- El push es por BATCH: un solo POST con hasta N eventos. El cloud responde
  con {confirmados: [hash...], errores: [{hash, error}...]}. Solo los que
  aparecen en 'confirmados' pasan a estado CONFIRMADO.
"""
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

import requests
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.tenancy.context import get_current_tenant_key

logger = logging.getLogger('sync')

# Punto de partida para el primer pull de una tabla (cursor vacio). Ver
# `_pull_generic`: el parametro `desde` debe viajar siempre para que el servidor
# use el orden del cursor y no el alfabetico.
_EPOCH = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)

# Tope de paginas por entidad y por ciclo. No es una regla de negocio: es un
# freno para que un endpoint que pagina mal no consuma el ciclo entero. Lo que
# queda pendiente se baja en el ciclo siguiente, desde el cursor commiteado.
MAX_PAGINAS_PULL = 200
SCHEMA_RESOLUCION_CONFLICTO_SYNC = 'master.conflict-resolution-sync.v1'


def _revision_cloud_desde_payload(item):
    """Normaliza la revisión remota que acompaña cada maestro.

    DRF suele representar UTC con ``Z`` y ``datetime.isoformat()`` usa
    ``+00:00``. El CAS compara la forma normalizada para que no confunda esas
    dos grafías del mismo instante con una edición concurrente.
    """
    raw = (item or {}).get('fecha_modificacion')
    if not raw:
        return ''
    try:
        return datetime.fromisoformat(str(raw).replace('Z', '+00:00')).isoformat()
    except (TypeError, ValueError):
        return str(raw)


def _fecha_desde_payload(item, campo):
    """Convierte timestamps opcionales del cloud sin inventar una fecha local."""
    raw = (item or {}).get(campo)
    if not raw:
        return None
    return parse_datetime(str(raw).replace('Z', '+00:00'))


class _Diferido:
    """Sentinela que un `apply` devuelve cuando NO pudo aplicar el item.

    Se usa para dependencias ausentes (el rol todavia no bajo, la categoria no
    existe local): no es un error -- no hay nada roto -- pero tampoco es un
    exito. A04 permite avanzar la marca de agua solo despues de guardar una
    copia durable en ``DiferidoSync`` para reintentar aunque el cloud ya no la
    vuelva a enviar.
    """

    def __repr__(self):
        return '<DIFERIDO>'


DIFERIDO = _Diferido()


class ConflictoAdopcionMaestro(RuntimeError):
    """Colision determinista que requiere decision humana, no un upsert."""

    def __init__(self, codigo, detalle):
        self.codigo = codigo
        super().__init__(f'{codigo}: {detalle}')


def _resultado_pull(
    count=0, ok=True, error=None, bloqueo=None, paginas=0,
    diferidos_pendientes=0, diferidos_resueltos=0,
):
    """Resultado estructurado de un pull por entidad.

    `pull_maestros` devolvia solo conteos, asi que un 401 en todos los
    endpoints era indistinguible de "no habia nada que bajar": el ciclo
    imprimia ceros y se registraba EXITOSO.
    """
    return {
        'count': count,
        'ok': ok,
        'error': error,
        'bloqueo': bloqueo,
        'paginas': paginas,
        'diferidos_pendientes': diferidos_pendientes,
        'diferidos_resueltos': diferidos_resueltos,
    }


def _tenant_key_rbac_local(negocio):
    """Devuelve la identidad tecnica esperada por un envelope RBAC v2.

    En DB-per-tenant el contexto conserva el ``tenant_key`` del control plane;
    en instalaciones row-level/standalone se usa el slug estable del negocio.
    """
    return get_current_tenant_key() or negocio.slug


def clasificar_ciclo(*, heartbeat, push, pull, maestros=None):
    """
    Veredicto de un ciclo de sync: ('EXITOSO'|'PARCIAL'|'FALLO', motivos).

    Unica politica, compartida por `SyncEngine.ciclo_completo` y por el comando
    `sincronizar`. Antes cada uno decidia por su cuenta: el comando escribia
    siempre `LogSync(resultado='EXITOSO')` y `ciclo_completo` solo miraba
    `push['fallidos']`. Ninguno de los dos miraba el heartbeat ni los errores
    de pull, asi que un cloud que rechazaba todos los endpoints autenticados
    quedaba registrado como una corrida exitosa y `sync_status` lo mostraba
    como salud verde.

    - FALLO   -> nada de lo que se intento funciono (heartbeat caido y ningun
                 avance): el cloud no esta respondiendo de forma util.
    - PARCIAL -> algo funciono y algo no.
    - EXITOSO -> heartbeat ok, sin eventos fallidos y sin errores de pull.
    """
    motivos = []

    if not heartbeat:
        motivos.append('heartbeat fallido')
    if push.get('fallidos'):
        motivos.append(f"{push['fallidos']} evento(s) no confirmados")
    maestros = maestros or {}
    if maestros.get('fallidas'):
        motivos.append(
            f"{maestros['fallidas']} propuesta(s) de maestro sin ACK válido"
        )
    if maestros.get('conflictos'):
        motivos.append(
            f"{maestros['conflictos']} propuesta(s) de maestro en conflicto"
        )
    if maestros.get('rechazadas'):
        motivos.append(
            f"{maestros['rechazadas']} propuesta(s) de maestro rechazada(s)"
        )
    for error in pull.get('errores', []):
        motivos.append(f'pull {error}')
    for bloqueo in pull.get('bloqueos', []):
        motivos.append(f'cursor bloqueado -> {bloqueo}')
    if pull.get('diferidos_pendientes'):
        motivos.append(
            f"{pull['diferidos_pendientes']} item(s) diferido(s) pendiente(s)"
        )

    if not motivos:
        return 'EXITOSO', motivos

    hubo_avance = (
        bool(push.get('confirmados'))
        or bool(maestros.get('confirmadas'))
        or bool(maestros.get('duplicadas'))
        or bool(pull.get('total'))
    )
    if not heartbeat and not hubo_avance:
        return 'FALLO', motivos

    return 'PARCIAL', motivos


class SyncConfigError(Exception):
    """Levantado si faltan settings criticos (URL o token)."""
    pass


class SyncEngine:
    """Engine de sync para la sucursal actual."""

    def __init__(
        self, cloud_url=None, token=None, timeout=None, batch_size=None,
        max_retries=None, lease_seconds=None, health_timeout=None,
        health_retries=None, health_backoff=None,
    ):
        self.cloud_url = (cloud_url or getattr(settings, 'CLOUD_API_URL', '')).rstrip('/')
        self.token = token or getattr(settings, 'CLOUD_API_TOKEN', '')
        self.timeout = timeout or getattr(settings, 'SYNC_HTTP_TIMEOUT', 10)
        self.batch_size = batch_size or getattr(settings, 'SYNC_BATCH_SIZE', 50)
        self.max_retries = max_retries or getattr(settings, 'SYNC_MAX_RETRIES', 10)
        self.lease_seconds = lease_seconds or getattr(settings, 'SYNC_LEASE_SECONDS', 300)
        self.health_timeout = health_timeout or getattr(
            settings, 'SYNC_HEALTH_TIMEOUT', self.timeout,
        )
        self.health_retries = (
            health_retries
            if health_retries is not None
            else getattr(settings, 'SYNC_HEALTH_RETRIES', 3)
        )
        self.health_backoff = (
            health_backoff
            if health_backoff is not None
            else getattr(settings, 'SYNC_HEALTH_RETRY_BACKOFF', 1)
        )
        self.max_paginas_pull = getattr(settings, 'SYNC_MAX_PAGINAS_PULL', MAX_PAGINAS_PULL)

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------

    def _require_config(self):
        if not self.cloud_url:
            raise SyncConfigError('CLOUD_API_URL no esta configurada en settings.')
        if not self.token:
            raise SyncConfigError('CLOUD_API_TOKEN no esta configurado en settings.')

    @property
    def headers(self):
        return {
            'Authorization': f'Token {self.token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def _url(self, path):
        return f"{self.cloud_url}/{path.lstrip('/')}"

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------

    def check_connection(self):
        """
        Ping al endpoint de health del cloud. No requiere auth (es publico).
        Retorna True/False. Nunca lanza excepcion.
        """
        if not self.cloud_url:
            return False
        intentos = max(1, int(self.health_retries))
        for intento in range(intentos):
            try:
                r = requests.get(
                    self._url('/api/v1/health/'),
                    timeout=self.health_timeout,
                )
                if r.status_code == 200:
                    return True
            except requests.RequestException:
                pass
            if intento + 1 < intentos and self.health_backoff > 0:
                time.sleep(self.health_backoff)
        return False

    def heartbeat(self):
        """
        Senal explicita de liveness hacia el cloud. A diferencia de
        push_eventos(), actualiza ultima_sync aunque no haya eventos pendientes.
        """
        self._require_config()
        try:
            resp = requests.post(
                self._url('/api/v1/sync/heartbeat/'),
                json={'timestamp': timezone.now().isoformat()},
                headers=self.headers,
                timeout=self.timeout,
            )
            return resp.status_code < 400
        except requests.RequestException as exc:
            logger.warning('heartbeat: fallo de red: %s', exc)
            return False

    def consultar_reconciliacion_eventos(self, eventos):
        """Sonda read-only de BUG-K; no depende de un ping previo."""
        self._require_config()
        try:
            resp = requests.post(
                self._url('/api/v1/sync/reconciliacion-eventos/'),
                json={
                    'schema_version': 'sync.reconciliation.v1',
                    'eventos': eventos,
                },
                headers=self.headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise SyncConfigError(f'No se pudo reconciliar eventos: {exc}') from exc

        if resp.status_code >= 400:
            raise SyncConfigError(
                f'Reconciliacion HTTP {resp.status_code}: {resp.text[:300]}'
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise SyncConfigError('Reconciliacion devolvio una respuesta no JSON.') from exc
        if (
            not isinstance(data, dict)
            or data.get('schema_version') != 'sync.reconciliation.v1'
            or not isinstance(data.get('resultados'), list)
        ):
            raise SyncConfigError('Reconciliacion devolvio un contrato invalido.')
        return data

    # ------------------------------------------------------------------
    # RESUMEN: agregados para conciliacion (Fase 3)
    # ------------------------------------------------------------------

    def obtener_resumen(self, desde, hasta, tz):
        """
        Pide al cloud el resumen agregado de `apps/sync/resumen.py` para el
        rango [desde, hasta] (date, inclusive) en la zona `tz`.

        Devuelve (resumen_dict, None) en exito, o (None, motivo) en fallo.
        `motivo == 'no_soportado'` es el caso esperable contra un cloud
        anterior a la Fase 3 (la ruta no existe -> 404/405): quien llama debe
        degradar con un mensaje claro, no tratarlo como un error de verdad.
        """
        self._require_config()
        try:
            resp = requests.get(
                self._url('/api/v1/sync/resumen/'),
                params={'desde': desde.isoformat(), 'hasta': hasta.isoformat(), 'tz': tz},
                headers=self.headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            return None, f'red: {exc}'

        if resp.status_code in (404, 405):
            return None, 'no_soportado'
        if resp.status_code >= 400:
            return None, f'HTTP {resp.status_code}: {resp.text[:300]}'

        try:
            return resp.json(), None
        except ValueError:
            return None, 'respuesta invalida (no JSON)'

    # ------------------------------------------------------------------
    # PUSH: eventos locales -> cloud
    # ------------------------------------------------------------------

    def _completar_payloads(self, eventos):
        """
        Re-serializa los eventos que quedaron sin payload y devuelve solo los
        enviables.

        Un evento sin payload existe porque preferimos registrar que el hecho
        ocurrio antes que perderlo cuando el serializador falla (ver
        `apps/sync/events.py`). Aqui se le da la segunda oportunidad, leyendo el
        objeto de la BD via `apps/sync/registry.py`.
        """
        from .events import _calcular_hash
        from . import registry

        enviables = []
        for evento in eventos:
            if evento.payload:
                enviables.append(evento)
                continue

            hecho = registry.por_tipo(evento.tipo_evento)
            modelo = hecho.modelo() if hecho else None

            if hecho is None or modelo is None or not evento.objeto_id_local:
                evento.marcar_error(
                    f'Evento sin payload y sin forma de re-serializarlo '
                    f'(tipo={evento.tipo_evento}, objeto_id={evento.objeto_id_local})',
                    max_retries=self.max_retries,
                )
                logger.error('Evento %s sin payload no es re-serializable', evento.pk)
                continue

            obj = modelo.objects.filter(pk=evento.objeto_id_local).first()
            if obj is None:
                evento.marcar_error(
                    f'El objeto local {evento.objeto_id_local} ya no existe',
                    max_retries=self.max_retries,
                )
                continue

            try:
                payload = hecho.serializar(obj)
            except Exception as exc:
                evento.marcar_error(f'Re-serializacion fallida: {exc}',
                                    max_retries=self.max_retries)
                logger.exception('Re-serializacion fallida del evento %s', evento.pk)
                continue

            evento.payload = payload
            evento.hash_payload = _calcular_hash(payload)
            evento.estado = 'PENDIENTE'
            evento.save(update_fields=['payload', 'hash_payload', 'estado'])
            logger.info('Evento %s re-serializado en el push', evento.pk)
            enviables.append(evento)

        return enviables

    def _reclamar_eventos(self):
        """Reserva un batch con lease durable y devuelve ``(eventos, lease)``.

        El lock de PostgreSQL solo protege la seleccion. ``EN_VUELO`` y el
        lease sobreviven al commit que necesariamente ocurre antes del HTTP;
        otro proceso no puede tomar esas filas hasta que el lease venza.
        """
        from .models import EventoSync

        ahora = timezone.now()
        vencimiento = ahora + timedelta(seconds=self.lease_seconds)
        lease_id = uuid.uuid4()
        base_qs = EventoSync.objects.all()
        using = base_qs.db

        with transaction.atomic(using=using):
            eventos = list(
                base_qs
                .select_for_update(skip_locked=True)
                .filter(
                    models.Q(
                        estado__in=EventoSync.ESTADOS_ENVIABLES,
                        intentos__lt=self.max_retries,
                    )
                    | models.Q(
                        estado='EN_VUELO',
                        lease_expires_at__lte=ahora,
                    )
                )
                .order_by('created_at')[:self.batch_size]
            )
            if not eventos:
                return [], None

            # Un lease vencido vuelve a ser propiedad del proceso que lo toma.
            # Todavia no consume un intento: pudo haber commit remoto con ACK
            # perdido, y el replay por event_id/hash es precisamente la salida.
            for evento in eventos:
                if evento.estado == 'EN_VUELO':
                    evento.estado = 'PENDIENTE' if evento.payload else 'SIN_PAYLOAD'
                    evento.lease_id = None
                    evento.lease_expires_at = None

            eventos = self._completar_payloads(eventos)
            if not eventos:
                return [], None

            for evento in eventos:
                evento.estado = 'EN_VUELO'
                evento.lease_id = lease_id
                evento.lease_expires_at = vencimiento
                evento.sent_at = ahora
            EventoSync.objects.using(using).bulk_update(
                eventos,
                ['estado', 'lease_id', 'lease_expires_at', 'sent_at'],
            )

        return eventos, lease_id

    def push_eventos(self):
        """
        Empuja hasta batch_size eventos al cloud.
        Retorna dict con metricas: {procesados, confirmados, fallidos}.
        """
        self._require_config()

        # Importacion diferida para no cargar Django al importar el modulo
        from .models import EventoSync

        metricas = {'procesados': 0, 'confirmados': 0, 'fallidos': 0}

        eventos, lease_id = self._reclamar_eventos()
        if not eventos:
            return metricas

        metricas['procesados'] = len(eventos)

        # Construye payload del batch segun el formato que espera
        # EventoBatchSerializer de Fase 3:
        #   { "eventos": [{ "tipo_evento", "payload", "hash_payload", "timestamp" }] }
        payload = {
            'eventos': [
                {
                    'event_id': str(e.event_id),
                    'tipo_evento': e.tipo_evento,
                    'payload': e.payload,
                    'hash_payload': e.hash_payload,
                    'timestamp': e.created_at.isoformat(),
                }
                for e in eventos
            ],
        }

        # POST al cloud
        try:
            resp = requests.post(
                self._url('/api/v1/sync/eventos/'),
                json=payload,
                headers=self.headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            # Fallo de red: marca error a todos (pero no los pierde)
            logger.warning('push_eventos: fallo de red: %s', exc)
            for e in eventos:
                e.marcar_error(
                    f'Conexion: {exc}',
                    max_retries=self.max_retries,
                    lease_id=lease_id,
                )
            metricas['fallidos'] = len(eventos)
            return metricas

        # Respuesta no-2xx: fallo global del batch
        if resp.status_code >= 400:
            mensaje = f'HTTP {resp.status_code}: {resp.text[:500]}'
            logger.error('push_eventos: %s', mensaje)
            for e in eventos:
                e.marcar_error(
                    mensaje, max_retries=self.max_retries, lease_id=lease_id,
                )
            metricas['fallidos'] = len(eventos)
            return metricas

        # Respuesta OK: aplica resultado por evento
        try:
            data = resp.json()
        except ValueError:
            logger.error('push_eventos: respuesta no es JSON valido')
            for e in eventos:
                e.marcar_error(
                    'Respuesta cloud invalida (no JSON)',
                    max_retries=self.max_retries,
                    lease_id=lease_id,
                )
            metricas['fallidos'] = len(eventos)
            return metricas

        # Formato de respuesta del cloud (Fase 3):
        # {
        #   "recibidos": N, "duplicados": N, "errores": N,
        #   "detalle": [{"hash": "...", "estado": "CONFIRMADO|DUPLICADO|ERROR", "error": "..."}]
        # }
        # CONFIRMADO y DUPLICADO cuentan como exito (el cloud tiene el evento);
        # solo ERROR se reintenta.
        # El ACK tiene que ser un objeto con `detalle` como lista. Un proxy, una
        # version incompatible o un bug cloud pueden responder 200 con otra
        # cosa; sin validar, `.get()` sobre una lista revienta y `detalle=[]`
        # dejaba todos los eventos sin veredicto.
        if not isinstance(data, dict) or not isinstance(data.get('detalle'), list):
            mensaje = (
                f'ACK con formato invalido (se esperaba objeto con "detalle" '
                f'como lista, llego {type(data).__name__})'
            )
            logger.error('push_eventos: %s', mensaje)
            for e in eventos:
                e.marcar_error(
                    mensaje, max_retries=self.max_retries, lease_id=lease_id,
                )
            metricas['fallidos'] = len(eventos)
            return metricas

        estado_por_hash = {}
        estado_por_event_id = {}
        for item in data['detalle']:
            if not isinstance(item, dict):
                continue
            if item.get('hash'):
                estado_por_hash[item['hash']] = item
            if item.get('event_id'):
                estado_por_event_id[str(item['event_id'])] = item

        for e in eventos:
            item = (
                estado_por_event_id.get(str(e.event_id))
                or estado_por_hash.get(e.hash_payload)
            )
            if item is None:
                # Se envio y el cloud no dijo nada de este evento. ANTES esto
                # solo sumaba a `fallidos`: el evento quedaba enviable con el
                # contador intacto, asi que volvia en cada batch para siempre,
                # sin causa registrada y sin llegar nunca a DESCARTADO. Ahora
                # consume un intento como cualquier otro fallo.
                e.marcar_error(
                    'El cloud no incluyo este evento en el ACK '
                    '(hash ausente en "detalle")',
                    max_retries=self.max_retries,
                    lease_id=lease_id,
                )
                metricas['fallidos'] += 1
                continue

            estado_cloud = item.get('estado')
            if estado_cloud in ('CONFIRMADO', 'DUPLICADO'):
                if e.marcar_confirmado(lease_id=lease_id):
                    metricas['confirmados'] += 1
                else:
                    metricas['fallidos'] += 1
                    logger.warning(
                        'push_eventos: ACK tardio ignorado para event_id=%s; '
                        'el lease ya no pertenece a este proceso', e.event_id,
                    )
            else:
                # ERROR u otro: reintentar
                error_msg = item.get('error') or f'Estado cloud: {estado_cloud}'
                e.marcar_error(
                    error_msg,
                    max_retries=self.max_retries,
                    lease_id=lease_id,
                )
                metricas['fallidos'] += 1

        logger.info(
            'push_eventos: procesados=%d confirmados=%d fallidos=%d',
            metricas['procesados'], metricas['confirmados'], metricas['fallidos']
        )
        return metricas

    # ------------------------------------------------------------------
    # PUSH: propuestas de maestros locales -> receptor A05.3
    # ------------------------------------------------------------------

    @staticmethod
    def _modelo_mutacion(mutacion):
        from apps.productos.models import Categoria, Producto

        return (
            Producto
            if mutacion.entidad == 'PRODUCTO'
            else Categoria
        )

    def _marcar_conflicto_local(self, mutacion, *, codigo, detalle,
                                cloud_entidad_id=None, cloud_revision=''):
        """Resultado terminal visible; nunca convertirlo en retry silencioso."""
        mutacion.marcar_conflicto(
            detalle,
            codigo=codigo,
            cloud_entidad_id=cloud_entidad_id,
            cloud_revision=cloud_revision,
        )

    def _categoria_resuelta_para_producto(self, producto):
        categoria = getattr(producto, 'categoria', None)
        return categoria is not None and bool(categoria.origen_cloud_id)

    def _reclamar_mutacion_maestro(self):
        """Reserva una única propuesta y conserva el orden por entidad.

        A diferencia del lote financiero, una propuesta posterior del mismo
        maestro necesita la revisión que devuelve la anterior. Procesar una
        por vez es intencional: evita enviar una edición contra la revisión
        previa mientras el ACK de una creación/edición todavía es incierto.
        """
        from apps.productos.models import Producto
        from apps.sync.models import MutacionMaestro

        ahora = timezone.now()
        vencimiento = ahora + timedelta(seconds=self.lease_seconds)
        envio_id = uuid.uuid4()
        using = MutacionMaestro.objects.db
        orden_entidad = models.Case(
            models.When(
                entidad=MutacionMaestro.Entidad.CATEGORIA,
                then=models.Value(0),
            ),
            default=models.Value(1),
            output_field=models.IntegerField(),
        )

        with transaction.atomic(using=using):
            candidatas = list(
                MutacionMaestro.objects.using(using)
                .select_for_update(skip_locked=True)
                .filter(
                    models.Q(estado=MutacionMaestro.Estado.PENDIENTE)
                    | models.Q(
                        estado=MutacionMaestro.Estado.ENVIANDO,
                        envio_expira_at__lte=ahora,
                    )
                )
                .order_by(orden_entidad, 'creado_at', 'id')[:self.batch_size]
            )
            for mutacion in candidatas:
                # No adelantar la segunda propuesta de un mismo maestro si la
                # primera sigue en vuelo en otro worker.
                en_vuelo = (
                    MutacionMaestro.objects.using(using)
                    .filter(
                        entidad=mutacion.entidad,
                        entidad_id=mutacion.entidad_id,
                        estado=MutacionMaestro.Estado.ENVIANDO,
                        envio_expira_at__gt=ahora,
                    )
                    .exclude(pk=mutacion.pk)
                    .exists()
                )
                if en_vuelo:
                    continue

                # Una decisión negativa anterior deja la copia local divergente.
                # Enviar una propuesta posterior sobre esa base aplicaría solo
                # una fracción de la intención del operador; se conserva como
                # conflicto hasta que A06 la resuelva explícitamente.
                anterior_terminal = (
                    MutacionMaestro.objects.using(using)
                    .filter(
                        entidad=mutacion.entidad,
                        entidad_id=mutacion.entidad_id,
                        estado__in=(
                            MutacionMaestro.Estado.CONFLICTO,
                            MutacionMaestro.Estado.RECHAZADA,
                        ),
                        resolucion_conflicto__isnull=True,
                    )
                    .filter(
                        models.Q(creado_at__lt=mutacion.creado_at)
                        | models.Q(creado_at=mutacion.creado_at, id__lt=mutacion.id)
                    )
                    .exists()
                )
                if anterior_terminal:
                    self._marcar_conflicto_local(
                        mutacion,
                        codigo='MASTER_PREVIOUS_PROPOSAL_UNRESOLVED',
                        detalle=(
                            'Existe una propuesta anterior no aceptada para '
                            'este maestro; requiere resolución explícita.'
                        ),
                    )
                    continue

                modelo = self._modelo_mutacion(mutacion)
                entidad = (
                    modelo.objects.using(using)
                    .select_for_update()
                    .filter(pk=mutacion.entidad_id)
                    .first()
                )
                if entidad is None:
                    self._marcar_conflicto_local(
                        mutacion,
                        codigo='MASTER_LOCAL_ENTITY_MISSING',
                        detalle='El maestro local de la propuesta ya no existe.',
                    )
                    continue

                es_creacion = mutacion.operacion == MutacionMaestro.Operacion.CREAR
                if es_creacion:
                    if mutacion.revision_base or entidad.origen_cloud_id:
                        self._marcar_conflicto_local(
                            mutacion,
                            codigo='MASTER_CREATE_IDENTITY_CONFLICT',
                            detalle='La creación local ya tiene identidad o revisión cloud.',
                        )
                        continue
                else:
                    if not entidad.origen_cloud_id:
                        self._marcar_conflicto_local(
                            mutacion,
                            codigo='MASTER_IDENTITY_UNRESOLVED',
                            detalle='No se puede editar un maestro sin identidad cloud resuelta.',
                        )
                        continue
                    if not entidad.revision_cloud or (
                        mutacion.revision_base != entidad.revision_cloud
                    ):
                        self._marcar_conflicto_local(
                            mutacion,
                            codigo='MASTER_REVISION_UNRESOLVED',
                            detalle='La propuesta no conserva una revisión cloud verificable.',
                            cloud_entidad_id=entidad.origen_cloud_id,
                            cloud_revision=entidad.revision_cloud,
                        )
                        continue

                if mutacion.entidad == MutacionMaestro.Entidad.PRODUCTO:
                    entidad = Producto.objects.using(using).select_related('categoria').get(pk=entidad.pk)
                    requiere_categoria = (
                        es_creacion or 'categoria_id' in (mutacion.delta or {})
                    )
                    if requiere_categoria and not self._categoria_resuelta_para_producto(entidad):
                        categoria_pendiente = MutacionMaestro.objects.using(using).filter(
                            entidad=MutacionMaestro.Entidad.CATEGORIA,
                            entidad_id=entidad.categoria_id,
                            estado__in=(
                                MutacionMaestro.Estado.PENDIENTE,
                                MutacionMaestro.Estado.ENVIANDO,
                            ),
                        ).exists()
                        if not categoria_pendiente:
                            self._marcar_conflicto_local(
                                mutacion,
                                codigo='MASTER_CATEGORY_IDENTITY_UNRESOLVED',
                                detalle='La categoría del producto no tiene identidad cloud.',
                            )
                        continue

                mutacion.estado = MutacionMaestro.Estado.ENVIANDO
                mutacion.envio_id = envio_id
                mutacion.envio_expira_at = vencimiento
                mutacion.save(
                    using=using,
                    update_fields=[
                        'estado', 'envio_id', 'envio_expira_at', 'actualizado_at',
                    ],
                )
                return mutacion, envio_id
        return None, None

    def _propuesta_mutacion_maestro(self, mutacion):
        """Serializa identidad remota en el último momento, después del claim."""
        from apps.sync.models import MutacionMaestro

        modelo = self._modelo_mutacion(mutacion)
        using = mutacion._state.db or 'default'
        entidades = modelo.objects.using(using)
        if mutacion.entidad == MutacionMaestro.Entidad.PRODUCTO:
            entidades = entidades.select_related('categoria')
        entidad = entidades.get(pk=mutacion.entidad_id)
        propuesta = {
            'schema_version': 'master.mutation.v1',
            'mutacion_id': str(mutacion.mutacion_id),
            'entidad': mutacion.entidad,
            'entidad_local_id': mutacion.entidad_id,
            'operacion': mutacion.operacion,
            'revision_base': mutacion.revision_base,
            'delta': mutacion.delta,
            'actor_username': mutacion.actor_username,
            'cloud_entidad_id': entidad.origen_cloud_id,
        }
        if mutacion.entidad == MutacionMaestro.Entidad.PRODUCTO:
            propuesta['categoria_cloud_id'] = entidad.categoria.origen_cloud_id
        return propuesta

    def _adoptar_ack_maestro(self, mutacion, *, cloud_entidad_id, cloud_revision, using):
        """Sella la identidad local y rebasa la siguiente intención de la entidad."""
        from apps.sync.models import MutacionMaestro

        modelo = self._modelo_mutacion(mutacion)
        entidad = (
            modelo.objects.using(using).select_for_update()
            .filter(pk=mutacion.entidad_id).first()
        )
        if entidad is None:
            raise ConflictoAdopcionMaestro(
                'MASTER_LOCAL_ENTITY_MISSING',
                'El maestro local desapareció antes del ACK cloud.',
            )
        if entidad.origen_cloud_id not in (None, cloud_entidad_id):
            raise ConflictoAdopcionMaestro(
                'MASTER_LOCAL_CLOUD_ID_CONFLICT',
                'El ACK no coincide con la identidad cloud ya sellada localmente.',
            )
        entidad.origen_cloud_id = cloud_entidad_id
        entidad.revision_cloud = cloud_revision
        entidad.save(
            using=using,
            update_fields=['origen_cloud_id', 'revision_cloud'],
        )
        siguiente = (
            MutacionMaestro.objects.using(using).select_for_update()
            .filter(
                entidad=mutacion.entidad,
                entidad_id=mutacion.entidad_id,
                estado=MutacionMaestro.Estado.PENDIENTE,
            )
            .exclude(pk=mutacion.pk)
            .order_by('creado_at', 'id').first()
        )
        if siguiente is not None:
            siguiente.revision_base = cloud_revision
            siguiente.save(using=using, update_fields=['revision_base', 'actualizado_at'])

    def push_mutaciones_maestro(self):
        """Envía una propuesta A05.3 y trata cada ACK incierto como reintento."""
        from apps.sync.models import MutacionMaestro

        self._require_config()
        metricas = {
            'procesadas': 0,
            'confirmadas': 0,
            'duplicadas': 0,
            'conflictos': 0,
            'rechazadas': 0,
            'fallidas': 0,
        }
        mutacion, envio_id = self._reclamar_mutacion_maestro()
        if mutacion is None:
            return metricas
        metricas['procesadas'] = 1

        try:
            propuesta = self._propuesta_mutacion_maestro(mutacion)
            respuesta = requests.post(
                self._url('/api/v1/sync/mutaciones-maestro/'),
                json={'schema_version': 'master.mutation.v1', 'mutaciones': [propuesta]},
                headers=self.headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            mutacion.marcar_reintento(f'Conexión: {exc}', envio_id=envio_id)
            metricas['fallidas'] = 1
            return metricas

        if respuesta.status_code >= 400:
            mutacion.marcar_reintento(
                f'HTTP {respuesta.status_code}: {respuesta.text[:500]}',
                envio_id=envio_id,
            )
            metricas['fallidas'] = 1
            return metricas
        ack_error = ''
        try:
            data = respuesta.json()
            if not isinstance(data, dict) or (
                data.get('schema_version') != 'master.mutation.v1'
            ):
                ack_error = (
                    'ACK de maestro incompatible: se esperaba '
                    'schema_version master.mutation.v1.'
                )
                item = None
            else:
                detalle = data.get('detalle')
                if not isinstance(detalle, list):
                    ack_error = 'ACK de maestro inválido: detalle debe ser una lista.'
                    item = None
                else:
                    item = next(
                        (
                            fila for fila in detalle
                            if isinstance(fila, dict)
                            and str(fila.get('mutacion_id')) == str(mutacion.mutacion_id)
                        ),
                        None,
                    )
        except (TypeError, ValueError, AttributeError):
            ack_error = 'ACK de maestro inválido: no se pudo leer JSON.'
            item = None
        if item is None:
            mutacion.marcar_reintento(
                ack_error or 'El cloud no incluyó la mutación en el ACK.',
                envio_id=envio_id,
            )
            metricas['fallidas'] = 1
            return metricas

        estado = item.get('estado')
        if estado in ('CONFIRMADA', 'DUPLICADA'):
            cloud_entidad_id = item.get('cloud_entidad_id')
            cloud_revision = item.get('cloud_revision')
            if not cloud_entidad_id or not cloud_revision:
                mutacion.marcar_reintento(
                    'ACK de maestro incompleto: falta identidad o revisión cloud.',
                    envio_id=envio_id,
                )
                metricas['fallidas'] = 1
                return metricas
            using = mutacion._state.db or 'default'
            try:
                with transaction.atomic(using=using):
                    confirmado = mutacion.marcar_confirmada(
                        cloud_entidad_id=cloud_entidad_id,
                        cloud_revision=cloud_revision,
                        envio_id=envio_id,
                    )
                    if confirmado:
                        self._adoptar_ack_maestro(
                            mutacion,
                            cloud_entidad_id=cloud_entidad_id,
                            cloud_revision=cloud_revision,
                            using=using,
                        )
            except ConflictoAdopcionMaestro as exc:
                mutacion.marcar_conflicto(
                    str(exc),
                    codigo=exc.codigo,
                    cloud_entidad_id=cloud_entidad_id,
                    cloud_revision=cloud_revision,
                    envio_id=envio_id,
                )
                metricas['conflictos'] = 1
                return metricas
            if confirmado:
                metricas['duplicadas' if estado == 'DUPLICADA' else 'confirmadas'] = 1
            else:
                metricas['fallidas'] = 1
            return metricas

        if estado == 'CONFLICTO':
            if mutacion.marcar_conflicto(
                item.get('error') or 'El cloud reportó un conflicto.',
                codigo=item.get('codigo') or 'MASTER_CONFLICT',
                cloud_entidad_id=item.get('cloud_entidad_id'),
                cloud_revision=item.get('cloud_revision') or '',
                envio_id=envio_id,
            ):
                metricas['conflictos'] = 1
            else:
                metricas['fallidas'] = 1
            return metricas

        if estado == 'RECHAZADA':
            if mutacion.marcar_rechazada(
                item.get('error') or 'El cloud rechazó la propuesta.',
                codigo=item.get('codigo') or 'MASTER_REJECTED',
                envio_id=envio_id,
            ):
                metricas['rechazadas'] = 1
            else:
                metricas['fallidas'] = 1
            return metricas

        mutacion.marcar_reintento(
            item.get('error') or f'Estado cloud no reconocido: {estado}',
            envio_id=envio_id,
        )
        metricas['fallidas'] = 1
        return metricas

    # ------------------------------------------------------------------
    # PULL: maestros cloud -> local
    # ------------------------------------------------------------------

    def pull_maestros(self):
        """
        Descarga cambios en datos maestros desde el cloud.

        Orden: categorias -> productos -> clientes (respeta FK).

        Retorna un dict con los conteos por entidad (compatibilidad) MAS el
        veredicto real del pull:

            {
              'categorias': 3, ..., 'total': 12,
              'ok': False,                       # alguna entidad fallo
              'entidades': {'roles': {'count':0,'ok':False,'error':'HTTP 401'}},
              'errores': ['roles: HTTP 401: ...'],
              'bloqueos': ['asignaciones: item ... diferido'],
            }

        Antes solo devolvia conteos. Con todos los endpoints respondiendo 401
        el resultado era `{...: 0, 'total': 0}` -- exactamente igual que "no
        habia nada que bajar" -- y el ciclo se registraba EXITOSO.
        """
        self._require_config()

        entidades = (
            ('categorias', self._pull_categorias),
            ('productos', self._pull_productos),
            ('clientes', self._pull_clientes),
            ('roles', self._pull_roles),
            ('asignaciones', self._pull_asignaciones),
            ('metodos_credito', self._pull_metodos_credito),
            ('configuracion', self._pull_configuracion),
            ('resoluciones_conflicto', self._pull_resoluciones_conflicto),
        )

        metricas = {
            'total': 0,
            'ok': True,
            'entidades': {},
            'errores': [],
            'bloqueos': [],
            'diferidos_pendientes': 0,
            'diferidos_resueltos': 0,
        }

        for nombre, funcion in entidades:
            try:
                resultado = funcion()
            except Exception as exc:
                logger.exception('pull_maestros: error en %s: %s', nombre, exc)
                resultado = _resultado_pull(ok=False, error=f'{type(exc).__name__}: {exc}')

            metricas['entidades'][nombre] = resultado
            metricas[nombre] = resultado['count']
            metricas['total'] += resultado['count']
            metricas['diferidos_pendientes'] += resultado.get(
                'diferidos_pendientes', 0,
            )
            metricas['diferidos_resueltos'] += resultado.get(
                'diferidos_resueltos', 0,
            )

            if not resultado['ok']:
                metricas['ok'] = False
                metricas['errores'].append(f"{nombre}: {resultado['error']}")
            if resultado['bloqueo']:
                metricas['bloqueos'].append(resultado['bloqueo'])

        logger.info(
            'pull_maestros: total=%s ok=%s errores=%s',
            metricas['total'], metricas['ok'], metricas['errores'],
        )
        return metricas

    @staticmethod
    def _ambito_diferidos():
        """Scope tecnico estable de la cola local de diferidos."""
        tenant_key = get_current_tenant_key() or ''
        if not tenant_key:
            try:
                from apps.negocios.models import Negocio

                claves = list(
                    Negocio.objects.filter(activo=True)
                    .values_list('slug', flat=True)[:2]
                )
                if len(claves) == 1:
                    tenant_key = claves[0]
            except Exception:
                tenant_key = ''
        return tenant_key, getattr(settings, 'SUCURSAL_CODIGO', '') or ''

    def _diferidos_qs(self, tabla):
        from .models import DiferidoSync

        tenant_key, sucursal_codigo = self._ambito_diferidos()
        return DiferidoSync.objects.filter(
            tenant_key=tenant_key,
            sucursal_codigo=sucursal_codigo,
            tabla=tabla,
        )

    def _guardar_diferido(self, tabla, item, clave, motivo):
        """Persiste un item no aplicado; solo entonces el cursor puede pasarlo."""
        from .events import _calcular_hash
        from .models import DiferidoSync

        tenant_key, sucursal_codigo = self._ambito_diferidos()
        payload_hash = _calcular_hash(item)
        fecha, cursor_id = clave if clave is not None else (None, 0)
        base_qs = self._diferidos_qs(tabla)
        using = base_qs.db

        with transaction.atomic(using=using):
            diferido, creado = (
                DiferidoSync.objects.using(using)
                .select_for_update()
                .get_or_create(
                    tenant_key=tenant_key,
                    sucursal_codigo=sucursal_codigo,
                    tabla=tabla,
                    payload_hash=payload_hash,
                    defaults={
                        'identidad': self._ref_item(item),
                        'cursor_fecha': fecha,
                        'cursor_id': cursor_id,
                        'payload': item,
                        'ultimo_error': motivo[:2000],
                    },
                )
            )
            if not creado:
                diferido.identidad = self._ref_item(item)
                diferido.cursor_fecha = fecha
                diferido.cursor_id = cursor_id
                diferido.payload = item
                diferido.estado = 'PENDIENTE'
                diferido.intentos += 1
                diferido.ultimo_error = motivo[:2000]
                diferido.resuelto_at = None
                diferido.save()
        return diferido

    def _procesar_diferidos(self, tabla, apply_func):
        """Reintenta diferidos de forma transaccional y recuperable ante crash."""
        base_qs = self._diferidos_qs(tabla)
        using = base_qs.db
        ids = list(
            base_qs
            .filter(estado='PENDIENTE')
            .order_by('creado_at', 'id')
            .values_list('id', flat=True)
        )
        resueltos = 0
        for diferido_id in ids:
            with transaction.atomic(using=using):
                diferido = (
                    base_qs.select_for_update(skip_locked=True)
                    .filter(pk=diferido_id, estado='PENDIENTE')
                    .first()
                )
                if diferido is None:
                    continue
                try:
                    resultado = apply_func(diferido.payload)
                except ConflictoAdopcionMaestro as exc:
                    diferido.intentos += 1
                    diferido.ultimo_error = str(exc)[:2000]
                    diferido.save(update_fields=[
                        'intentos', 'ultimo_error', 'actualizado_at',
                    ])
                    continue
                except Exception as exc:
                    diferido.intentos += 1
                    diferido.ultimo_error = (
                        f'{type(exc).__name__}: {exc}'
                    )[:2000]
                    diferido.save(update_fields=[
                        'intentos', 'ultimo_error', 'actualizado_at',
                    ])
                    continue

                if resultado is DIFERIDO:
                    diferido.intentos += 1
                    diferido.ultimo_error = 'Dependencia ausente al reintentar.'
                    diferido.save(update_fields=[
                        'intentos', 'ultimo_error', 'actualizado_at',
                    ])
                    continue

                diferido.estado = 'RESUELTO'
                diferido.resuelto_at = timezone.now()
                diferido.ultimo_error = ''
                diferido.save(update_fields=[
                    'estado', 'resuelto_at', 'ultimo_error', 'actualizado_at',
                ])
                resueltos += 1
        return resueltos

    def _aplicar_o_diferir(self, tabla, item, apply_func, clave):
        """Retorna ``(aplicado, almacenado, motivo)`` para un item de pull."""
        try:
            using = self._diferidos_qs(tabla).db
            with transaction.atomic(using=using):
                resultado = apply_func(item)
            if resultado is not DIFERIDO:
                return True, False, ''
            motivo = 'Dependencia ausente.'
        except ConflictoAdopcionMaestro as exc:
            logger.warning(
                'pull %s: conflicto aplicando item %s: %s',
                tabla, item.get('id') or item.get('cursor_id'), exc,
            )
            motivo = str(exc)
        except Exception as exc:
            logger.exception(
                'pull %s: error aplicando item %s: %s',
                tabla, item.get('id') or item.get('cursor_id'), exc,
            )
            motivo = f'{type(exc).__name__}: {exc}'

        try:
            self._guardar_diferido(tabla, item, clave, motivo)
            return False, True, motivo
        except Exception as exc:
            logger.exception(
                'pull %s: no se pudo guardar el diferido %s: %s',
                tabla, self._ref_item(item), exc,
            )
            return False, False, f'no se pudo persistir diferido: {type(exc).__name__}'

    def _pull_generic(
        self, tabla, endpoint, apply_func, *, headers=None,
        extra_params=None, response_key=None, on_snapshot_complete=None,
        envelope_validator=None, require_envelope=False,
    ):
        """
        Pull incremental con cursor KEYSET y cola durable de diferidos.

        Dos cursores, y esa es la idea central (ver BUG-B en docs/BUGS.md):

          req    -> clave del ultimo item RECIBIDO. Sirve para pedir la pagina
                    siguiente. Avanza siempre.
          commit -> clave del ultimo item aplicado o capturado durablemente EN
                    SECUENCIA CONTIGUA. Es lo unico que se persiste.

        Antes habia un solo cursor que saltaba al maximo visto aunque un item
        hubiera fallado, y ese registro no volvia a entrar en ningun pull: se
        perdia para siempre.

            items:  [ok, ok, FALLA, ok, ok]
            antes:  cursor = clave del ultimo  -> el fallido se pierde
            ahora:  commit = ultimo si FALLA quedo en DiferidoSync; si tampoco
                    pudo persistirse, queda antes del fallo.

        Los items posteriores al fallo SI se aplican (son idempotentes,
        `update_or_create`). Un diferido durable queda visible y vuelve a
        intentarse al inicio del proximo ciclo. El cursor solo se congela si ni
        aplicar ni persistir el item fue posible.

        Pedir cada pagina por su clave -- en vez de seguir el `next` de DRF, que
        es por offset -- hace ademas que un corte de red a media paginacion
        retome donde iba en el proximo ciclo en vez de empezar de cero.
        """
        from .models import VersionMaestro

        cursor = VersionMaestro.get_o_crear(tabla)

        # (fecha, id) de la ultima posicion confirmada.
        commit_fecha = cursor.ultima_version
        commit_id = cursor.ultimo_id or 0
        req_fecha, req_id = commit_fecha, commit_id

        diferidos_resueltos = self._procesar_diferidos(tabla, apply_func)
        count = diferidos_resueltos
        contiguo = True          # mientras nadie falle, commit sigue a req
        bloqueo = None           # primer fallo de esta corrida
        error = None             # fallo de transporte/HTTP de esta corrida
        paginas = 0
        url = self._url(endpoint)
        snapshot_complete = False
        request_headers = {**self.headers, **(headers or {})}

        while True:
            # `desde` va SIEMPRE, incluso en el primer pull. El servidor solo
            # ordena por (fecha_modificacion, id) cuando recibe `?desde=`; sin
            # el parametro ordena por `nombre` y la clave del ultimo item de la
            # pagina no sirve como frontera: la pagina siguiente se solapa.
            # (Paso de verdad: un pull inicial aplico 416 items sobre un
            # catalogo de 273.)
            params = {
                'desde': (req_fecha or _EPOCH).isoformat(),
                'desde_id': req_id,
            }
            params.update(extra_params or {})

            try:
                resp = requests.get(
                    url, params=params, headers=request_headers, timeout=self.timeout,
                )
            except requests.RequestException as exc:
                logger.warning('pull %s: error de red: %s', tabla, exc)
                error = f'red: {exc}'
                break

            if resp.status_code >= 400:
                logger.error('pull %s: HTTP %s: %s', tabla, resp.status_code, resp.text[:500])
                error = f'HTTP {resp.status_code}: {resp.text[:200]}'
                break

            data = resp.json()
            if require_envelope and envelope_validator is not None:
                error_envelope = envelope_validator(data)
                if error_envelope:
                    error = f'envelope inválido: {error_envelope}'
                    logger.error('pull %s: %s', tabla, error)
                    break
            # Soporta respuesta paginada de DRF o lista directa.
            if (
                response_key
                and isinstance(data, dict)
                and data.get('schema_version') == 'rbac.sync.v2'
            ):
                if envelope_validator is not None:
                    error_envelope = envelope_validator(data)
                    if error_envelope:
                        bloqueo = f'{tabla}: envelope invalido: {error_envelope}'
                        logger.error('pull %s: %s', tabla, bloqueo)
                        break
                items = data.get(response_key, [])
                snapshot_complete = bool(data.get('snapshot_complete'))
            else:
                items = (
                    data['results']
                    if isinstance(data, dict) and 'results' in data else data
                )
            if not items:
                break

            # Guardarrail de compatibilidad: si el cloud no ordena por el
            # cursor, el paseo keyset es invalido -- la clave del ultimo item
            # de la pagina no es frontera de nada y las paginas se solapan.
            # Pasa contra un cloud anterior a la Fase 2, que ordena por
            # `nombre` e ignora `desde_id`.
            #
            # Medido: contra un cloud viejo, un pull inicial aplicaba 432 veces
            # y solo llegaban 245 de 273 productos. 28 se perdian.
            #
            # Ante la duda, se degrada al recorrido legacy (seguir `next`), que
            # es correcto aunque no tenga las garantias nuevas.
            if not self._pagina_ordenada(items):
                logger.warning(
                    'pull %s: el cloud no respeta el orden del cursor (version '
                    'anterior a Fase 2). Degradando a paginacion legacy.', tabla,
                )
                return self._pull_legacy(
                    tabla,
                    endpoint,
                    apply_func,
                    cursor,
                    count_inicial=count,
                    diferidos_resueltos=diferidos_resueltos,
                )

            frontera_antes = (req_fecha, req_id)

            for item in items:
                clave = self._clave_cursor(item)

                aplicado, almacenado, motivo = self._aplicar_o_diferir(
                    tabla, item, apply_func, clave,
                )
                if aplicado:
                    count += 1
                elif almacenado:
                    logger.warning(
                        'pull %s: item %s guardado en cola durable: %s',
                        tabla, self._ref_item(item), motivo,
                    )
                elif bloqueo is None:
                    bloqueo = (
                        f'{tabla}: item {self._ref_item(item)} no aplicado ni '
                        f'guardado: {motivo}'
                    )

                if clave is not None:
                    req_fecha, req_id = clave
                    # Aplicado o capturado durablemente son posiciones seguras.
                    if (aplicado or almacenado) and contiguo:
                        commit_fecha, commit_id = clave

                if not (aplicado or almacenado):
                    contiguo = False

            # Sin paginacion (lista plana) se termina en una sola vuelta.
            if not (isinstance(data, dict) and 'results' in data):
                break
            if not data.get('next'):
                break

            # Guardarrail de progreso. El paseo keyset pide la pagina siguiente
            # por la clave del ultimo item; si ninguno de esta pagina trajo una
            # clave valida, la frontera no se movio y el proximo request seria
            # identico. Con `next` presente, eso es un bucle infinito que cuelga
            # el ciclo entero del daemon.
            if (req_fecha, req_id) == frontera_antes:
                bloqueo = bloqueo or (
                    f'{tabla}: la pagina no hizo avanzar el cursor '
                    f'(items sin fecha_modificacion/id) y el cloud dice que hay '
                    f'mas. Recorrido abortado para no ciclar.'
                )
                logger.error('pull %s: %s', tabla, bloqueo)
                break

            paginas += 1
            if paginas >= self.max_paginas_pull:
                bloqueo = bloqueo or (
                    f'{tabla}: se alcanzo el limite de {self.max_paginas_pull} '
                    f'paginas por ciclo. El resto continua en el proximo.'
                )
                logger.warning('pull %s: %s', tabla, bloqueo)
                break

        diferidos_pendientes = self._diferidos_qs(tabla).filter(
            estado='PENDIENTE',
        ).count()

        if (
            error is None
            and bloqueo is None
            and diferidos_pendientes == 0
            and snapshot_complete
            and on_snapshot_complete is not None
        ):
            try:
                with transaction.atomic():
                    count += int(on_snapshot_complete() or 0)
            except Exception as exc:
                logger.exception(
                    'pull %s: fallo reconciliando snapshot completo: %s', tabla, exc,
                )
                bloqueo = f'{tabla}: fallo reconciliando snapshot completo: {exc}'

        self._guardar_cursor(cursor, commit_fecha, commit_id, count, bloqueo)
        return _resultado_pull(
            count=count,
            ok=error is None,
            error=error,
            bloqueo=bloqueo,
            paginas=paginas,
            diferidos_pendientes=diferidos_pendientes,
            diferidos_resueltos=diferidos_resueltos,
        )

    @classmethod
    def _pagina_ordenada(cls, items):
        """True si la pagina viene ordenada por (fecha_modificacion, id).

        Es la firma de que el servidor entiende el contrato del cursor. Un
        cloud anterior a la Fase 2 ordena por `nombre`, asi que las claves
        llegan desordenadas y se nota de inmediato.
        """
        anterior = None
        for item in items:
            clave = cls._clave_cursor(item)
            if clave is None:
                continue
            if anterior is not None and clave < anterior:
                return False
            anterior = clave
        return True

    def _pull_legacy(
        self, tabla, endpoint, apply_func, cursor, *, count_inicial=0,
        diferidos_resueltos=0,
    ):
        """
        Recorrido antiguo: seguir el `next` de DRF y avanzar el cursor al maximo
        `fecha_modificacion` visto.

        Solo se usa contra un cloud que no soporta el cursor keyset. Conserva el
        Los items que no aplican tambien se guardan en la cola durable. Eso
        conserva compatibilidad de transporte sin conservar la perdida
        silenciosa del cliente viejo.
        """
        count = count_inicial
        max_fecha = cursor.ultima_version
        url = self._url(endpoint)
        params = {'desde': cursor.ultima_version.isoformat()} if cursor.ultima_version else {}
        error = None
        bloqueo = None
        paginas = 0

        while url:
            try:
                resp = requests.get(url, params=params or None, headers=self.headers,
                                    timeout=self.timeout)
            except requests.RequestException as exc:
                logger.warning('pull %s (legacy): error de red: %s', tabla, exc)
                error = f'red: {exc}'
                break
            if resp.status_code >= 400:
                logger.error('pull %s (legacy): HTTP %s', tabla, resp.status_code)
                error = f'HTTP {resp.status_code}: {resp.text[:200]}'
                break

            data = resp.json()
            if isinstance(data, dict) and 'results' in data:
                items = data['results']
                url = data.get('next')
                params = None
            else:
                items = data
                url = None

            for item in items:
                clave = self._clave_cursor(item)
                aplicado, almacenado, motivo = self._aplicar_o_diferir(
                    tabla, item, apply_func, clave,
                )
                if aplicado:
                    count += 1
                elif not almacenado and bloqueo is None:
                    bloqueo = (
                        f'{tabla}: item {self._ref_item(item)} no aplicado ni '
                        f'guardado: {motivo}'
                    )
                if (
                    (aplicado or almacenado)
                    and clave
                    and (max_fecha is None or clave[0] > max_fecha)
                ):
                    max_fecha = clave[0]
            paginas += 1

        if (count or max_fecha != cursor.ultima_version) and bloqueo is None:
            cursor.ultima_version = max_fecha
            cursor.ultimo_id = 0
            cursor.ultima_sync_exitosa = timezone.now()
            cursor.registros_ultima_sync = count
            cursor.save()
        if bloqueo:
            cursor.marcar_bloqueado(bloqueo)
        else:
            cursor.limpiar_bloqueo()

        pendientes = self._diferidos_qs(tabla).filter(estado='PENDIENTE').count()
        return _resultado_pull(
            count=count,
            ok=error is None,
            error=error,
            bloqueo=bloqueo,
            paginas=paginas,
            diferidos_pendientes=pendientes,
            diferidos_resueltos=diferidos_resueltos,
        )

    @staticmethod
    def _clave_cursor(item):
        """(fecha_modificacion, id) de un item, o None si no la trae.

        `cursor_id` es el token de paginacion de los endpoints de sync; `id` el
        de los maestros. La configuracion es un singleton sin id: cae a 0 y el
        cursor queda gobernado solo por la fecha.
        """
        fecha_raw = item.get('fecha_modificacion') or item.get('updated_at')
        if not fecha_raw:
            return None
        try:
            fecha = datetime.fromisoformat(str(fecha_raw).replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            return None
        return fecha, int(item.get('cursor_id') or item.get('id') or 0)

    @staticmethod
    def _ref_item(item):
        """Referencia legible de un item para los mensajes de bloqueo."""
        for campo in ('sku', 'nombre', 'slug', 'cedula_rnc', 'cursor_id', 'id'):
            valor = item.get(campo)
            if valor:
                return f'{campo}={valor}'
        return '(sin referencia)'

    def _guardar_cursor(self, cursor, fecha, id_, count, bloqueo):
        """Persiste la marca de agua y el estado de bloqueo."""
        avanzo = fecha is not None and (
            fecha != cursor.ultima_version or (id_ or 0) != (cursor.ultimo_id or 0)
        )

        if avanzo or count:
            cursor.ultima_version = fecha
            cursor.ultimo_id = id_ or 0
            cursor.ultima_sync_exitosa = timezone.now()
            cursor.registros_ultima_sync = count
            cursor.save()

        if bloqueo:
            cursor.marcar_bloqueado(bloqueo)
            logger.warning('pull %s: cursor congelado -> %s', cursor.tabla, bloqueo)
        else:
            cursor.limpiar_bloqueo()

    @staticmethod
    def _adoptar_por_identidad_cloud(modelo, cloud_id, lookup_natural):
        """
        Localiza la fila local que corresponde a un registro cloud.

        Prioridad:
          1. `origen_cloud_id` -> identidad estable. Sobrevive a renombres.
          2. Clave natural, PERO solo si esa fila todavia no esta sellada con
             otro `origen_cloud_id`. Esto es el bootstrap/reconciliacion: la
             primera vez que baja un registro, adopta la fila local que ya
             existia y le graba la identidad.

        Retorna (instancia_o_None, hay_que_sellar). Toda ambiguedad o colision
        levanta ``ConflictoAdopcionMaestro`` para que A04 la capture en la cola
        durable con un motivo estable y visible.
        """
        if cloud_id is not None:
            por_identidad = list(
                modelo.objects.select_for_update()
                .filter(origen_cloud_id=cloud_id)
                .order_by('pk')[:2]
            )
            if len(por_identidad) > 1:
                raise ConflictoAdopcionMaestro(
                    'MASTER_CLOUD_ID_AMBIGUOUS',
                    f'{modelo.__name__} cloud_id={cloud_id} coincide con mas de una fila.',
                )
            if por_identidad:
                return por_identidad[0], False

        por_natural = list(
            modelo.objects.select_for_update()
            .filter(**lookup_natural)
            .order_by('pk')[:2]
        )
        if len(por_natural) > 1:
            raise ConflictoAdopcionMaestro(
                'MASTER_NATURAL_AMBIGUOUS',
                f'{modelo.__name__} {lookup_natural} coincide con mas de una fila local.',
            )
        if not por_natural:
            return None, cloud_id is not None

        candidato = por_natural[0]
        if (
            cloud_id is not None
            and candidato.origen_cloud_id is not None
            and candidato.origen_cloud_id != cloud_id
        ):
            raise ConflictoAdopcionMaestro(
                'MASTER_NATURAL_ID_CONFLICT',
                f'{modelo.__name__} {lookup_natural} pertenece a cloud_id='
                f'{candidato.origen_cloud_id}, no a cloud_id={cloud_id}.',
            )

        return candidato, cloud_id is not None

    def _pull_categorias(self):
        from apps.productos.models import Categoria

        def apply(item):
            cloud_id = item.get('id')
            existente, sellar = self._adoptar_por_identidad_cloud(
                Categoria, cloud_id, {'nombre': item['nombre']},
            )

            campos = {
                'nombre': item['nombre'],
                'descripcion': item.get('descripcion', '') or '',
                'tipo_negocio': item.get('tipo_negocio', '') or '',
                'atributos_configurados': item.get('atributos_configurados') or {},
                'activa': item.get('activa', True),
                'motivo_inactivacion': item.get('motivo_inactivacion', '') or '',
                'inactivado_at': _fecha_desde_payload(item, 'inactivado_at'),
                'revision_cloud': _revision_cloud_desde_payload(item),
            }
            if sellar:
                campos['origen_cloud_id'] = cloud_id

            if existente is None:
                Categoria.objects.create(**campos)
                return

            for campo, valor in campos.items():
                setattr(existente, campo, valor)
            existente.save()

        return self._pull_generic('categorias', '/api/v1/maestros/categorias/', apply)

    def _pull_productos(self):
        from apps.productos.models import Producto, Categoria

        def apply(item):
            # La relacion tambien usa primero la identidad cloud de Categoria;
            # el nombre queda solo como compatibilidad/adopcion inicial.
            categoria = None
            categoria_cloud_id = item.get('categoria')
            if categoria_cloud_id is not None:
                categorias_identidad = list(
                    Categoria.objects.select_for_update()
                    .filter(origen_cloud_id=categoria_cloud_id)
                    .order_by('pk')[:2]
                )
                if len(categorias_identidad) > 1:
                    raise ConflictoAdopcionMaestro(
                        'MASTER_CLOUD_ID_AMBIGUOUS',
                        f'Categoria cloud_id={categoria_cloud_id} coincide con '
                        'mas de una fila local.',
                    )
                if categorias_identidad:
                    categoria = categorias_identidad[0]

            cat_nombre = item.get('categoria_nombre')
            if categoria is None and cat_nombre:
                categorias_naturales = list(
                    Categoria.objects.select_for_update()
                    .filter(nombre=cat_nombre)
                    .order_by('pk')[:2]
                )
                if len(categorias_naturales) > 1:
                    raise ConflictoAdopcionMaestro(
                        'MASTER_NATURAL_AMBIGUOUS',
                        f'Categoria nombre={cat_nombre!r} coincide con mas de '
                        'una fila local.',
                    )
                if categorias_naturales:
                    categoria = categorias_naturales[0]
                    if (
                        categoria_cloud_id is not None
                        and categoria.origen_cloud_id is not None
                        and categoria.origen_cloud_id != categoria_cloud_id
                    ):
                        raise ConflictoAdopcionMaestro(
                            'MASTER_NATURAL_ID_CONFLICT',
                            f'Categoria nombre={cat_nombre!r} pertenece a '
                            f'cloud_id={categoria.origen_cloud_id}, no a '
                            f'cloud_id={categoria_cloud_id}.',
                        )
                    if (
                        categoria_cloud_id is not None
                        and categoria.origen_cloud_id is None
                    ):
                        # El producto trae la PK cloud de su categoria. Si la
                        # coincidencia natural es exacta e inequivoca, sellar
                        # ahora evita que un replay vuelva a depender del nombre.
                        categoria.origen_cloud_id = categoria_cloud_id
                        categoria.save(update_fields=['origen_cloud_id'])
                else:
                    # Antes esto solo avisaba y guardaba el producto con su
                    # categoria vieja, avanzando el cursor: cuando la categoria
                    # llegaba, el producto ya no volvia a bajar y quedaba mal
                    # clasificado para siempre.
                    logger.warning('Producto %s: categoria %s no existe local; diferido',
                                   item.get('sku'), cat_nombre)
                    return DIFERIDO

            # Cinturon extra (BUG-G, docs/BUGS.md): un stub pendiente de
            # revision no deberia llegar aca -- el cloud ya lo excluye del
            # pull para tokens de sucursal (ProductoViewSet.get_base_queryset)
            # -- pero si llegara igual (version cloud distinta, bug futuro),
            # no se aplica: aplicarlo pisaria el producto real de esta
            # sucursal con nombre/precio/categoria de stub.
            if item.get('pendiente_revision'):
                logger.warning(
                    'Producto %s: llego pendiente_revision=True al pull; se '
                    'omite para no pisar el producto local con un stub.',
                    item.get('sku'),
                )
                return

            sku = item.get('sku')
            if not isinstance(sku, str) or not sku:
                raise ConflictoAdopcionMaestro(
                    'MASTER_SKU_INVALID',
                    'Producto sin SKU exacto no se puede adoptar ni crear.',
                )

            cloud_id = item.get('id')
            producto, sellar = self._adoptar_por_identidad_cloud(
                Producto, cloud_id, {'sku': sku},
            )
            if (
                producto is not None
                and cloud_id is not None
                and producto.origen_cloud_id == cloud_id
                and producto.sku != sku
            ):
                raise ConflictoAdopcionMaestro(
                    'MASTER_SKU_IMMUTABLE',
                    f'Producto cloud_id={cloud_id} conserva SKU={producto.sku!r}; '
                    f'el payload intento usar SKU={sku!r}.',
                )

            campos = {
                'nombre': item.get('nombre', ''),
                'descripcion': item.get('descripcion', '') or '',
                'precio_venta': item.get('precio_venta', '0'),
                # PRO-008: NULL se conservaba como cadena vacia, y `''` SI
                # colisiona contra la unicidad (a diferencia de NULL). Al bajar
                # dos productos cloud sin codigo, el primero se guardaba como
                # `''` y el segundo violaba `productos_codigo_barras_key`: el
                # cursor `VersionMaestro` no avanzaba y la sucursal dejaba de
                # recibir TODO el catalogo posterior. Reintentar no curaba nada.
                'codigo_barras': (item.get('codigo_barras') or '').strip() or None,
                'activo': item.get('activo', True),
                'motivo_inactivacion': item.get('motivo_inactivacion', '') or '',
                'inactivado_at': _fecha_desde_payload(item, 'inactivado_at'),
                'estado': item.get('estado') or 'nuevo',
                'marca': item.get('marca') or '',
                'stock_minimo': item.get('stock_minimo', 5),
                'atributos': item.get('atributos') or {},
                'revision_cloud': _revision_cloud_desde_payload(item),
            }
            if categoria:
                campos['categoria'] = categoria
            if sellar:
                campos['origen_cloud_id'] = cloud_id

            if producto is None:
                producto = Producto.objects.create(sku=sku, **campos)
            else:
                for campo, valor in campos.items():
                    setattr(producto, campo, valor)
                producto.save()
            self._descargar_imagen_producto(producto, item.get('imagen_url'))

        return self._pull_generic('productos', '/api/v1/maestros/productos/', apply)

    def _pull_resoluciones_conflicto(self):
        """Replica decisiones A06 y libera solo el bloqueo local correspondiente."""
        from apps.sync.models import MutacionMaestro, ResolucionConflictoMaestro

        def validar_envelope(data):
            if not isinstance(data, dict):
                return 'la respuesta debe ser un objeto'
            if data.get('schema_version') != SCHEMA_RESOLUCION_CONFLICTO_SYNC:
                return 'schema_version desconocido'
            filas = data.get('results')
            if not isinstance(filas, list):
                return 'results debe ser una lista'
            for indice, fila in enumerate(filas):
                if not isinstance(fila, dict):
                    return f'results[{indice}] debe ser un objeto'
                if fila.get('schema_version') != SCHEMA_RESOLUCION_CONFLICTO_SYNC:
                    return f'results[{indice}] tiene schema_version desconocido'
                try:
                    uuid.UUID(str(fila['mutacion_id']))
                    if int(fila['id']) < 1:
                        return f'results[{indice}].id inválido'
                except (KeyError, TypeError, ValueError):
                    return f'results[{indice}] no identifica la resolución'
                if _fecha_desde_payload(fila, 'fecha_modificacion') is None:
                    return f'results[{indice}].fecha_modificacion inválida'
                if fila.get('accion') not in {
                    ResolucionConflictoMaestro.Accion.CONSERVAR_CLOUD,
                    ResolucionConflictoMaestro.Accion.APLICAR_LOCAL,
                }:
                    return f'results[{indice}].accion inválida'
                motivo = fila.get('motivo')
                if not isinstance(motivo, str) or not 1 <= len(motivo.strip()) <= 500:
                    return f'results[{indice}].motivo inválido'
            return ''

        def aplicar(fila):
            mutacion = (
                MutacionMaestro.objects.select_for_update()
                .filter(mutacion_id=fila['mutacion_id'])
                .first()
            )
            # El POS puede haber purgado una cola antigua: no hay bloqueo local
            # que liberar y retener esta decisión no aporta seguridad.
            if mutacion is None:
                return

            existente = ResolucionConflictoMaestro.objects.filter(
                mutacion=mutacion,
            ).first()
            valores = {
                'accion': fila['accion'],
                'motivo': fila['motivo'].strip(),
                'actor_username': str(fila.get('actor_username') or ''),
                'cloud_revision_observada': str(fila.get('cloud_revision_observada') or ''),
                'cloud_revision_resultante': str(fila.get('cloud_revision_resultante') or ''),
                'cloud_entidad_id': fila.get('cloud_entidad_id'),
            }
            if existente is not None:
                if any(getattr(existente, campo) != valor for campo, valor in valores.items()):
                    raise ConflictoAdopcionMaestro(
                        'MASTER_RESOLUTION_REPLAY_CONFLICT',
                        'La decisión cloud no coincide con el ledger local ya recibido.',
                    )
                return

            if mutacion.estado not in {
                MutacionMaestro.Estado.CONFLICTO,
                MutacionMaestro.Estado.RECHAZADA,
            }:
                return
            ResolucionConflictoMaestro.objects.create(mutacion=mutacion, **valores)

        return self._pull_generic(
            'resoluciones_conflicto',
            '/api/v1/sync/mutaciones-maestro/resoluciones/',
            aplicar,
            extra_params={'page_size': 100},
            envelope_validator=validar_envelope,
            require_envelope=True,
        )

    def _descargar_imagen_producto(self, producto, imagen_url):
        """
        Baja la foto del producto si cambio en el cloud (subida desde el
        portal, ver apps/api/views/maestros.py::ProductoViewSet.imagen).

        Best-effort a proposito: NUNCA difiere el item ni frena el cursor --
        el producto (texto) ya se aplico arriba, que es lo que de verdad
        importa para no perder una venta. Si la descarga falla,
        `imagen_origen_url` no se sella, asi que se reintenta solo en el
        proximo ciclo en que el registro vuelva a cambiar, o a mano con
        `manage.py descargar_imagenes_productos`.

        Comparar contra `imagen_origen_url` (la URL ya descargada con exito),
        no contra el nombre de archivo: el storage puede desambiguar nombres
        y dos fotos distintas podrian coincidir en el nombre por casualidad.
        """
        from django.core.files.base import ContentFile
        from apps.productos.models import Producto

        if not imagen_url or imagen_url == producto.imagen_origen_url:
            return

        contenido = None
        ultimo_error = None
        for _intento in range(2):  # una descarga + un reintento
            # Excepcion generica a proposito, mas amplia que en el resto del
            # engine: esto pide un archivo a un storage de terceros (Blob),
            # no al cloud propio, y es best-effort por diseno -- ninguna
            # forma de fallar aca (red, respuesta rara, contenido vacio)
            # puede tumbar el pull de productos, que es lo que de verdad
            # importa.
            try:
                resp = requests.get(imagen_url, timeout=self.timeout)
                if resp.status_code == 200 and resp.content:
                    contenido = resp.content
                    break
                ultimo_error = f'HTTP {resp.status_code}'
            except Exception as exc:
                ultimo_error = str(exc)

        if contenido is None:
            logger.warning('Producto %s: no se pudo descargar la imagen (%s): %s',
                            producto.sku, imagen_url, ultimo_error)
            return

        nombre = imagen_url.rsplit('/', 1)[-1].split('?')[0] or f'{producto.sku}.jpg'
        try:
            # save=True -> Producto.save() completo -> la miniatura local se
            # regenera sola (Producto.sincronizar_miniatura).
            producto.imagen.save(nombre, ContentFile(contenido), save=True)
        except Exception as exc:
            logger.warning('Producto %s: la imagen descargada no se pudo guardar: %s',
                            producto.sku, exc)
            return

        # .update(), no .save(): ya se guardo arriba, esto solo sella la URL
        # sin disparar otro ciclo de guardado/miniatura.
        Producto.objects.filter(pk=producto.pk).update(imagen_origen_url=imagen_url)

    def _pull_clientes(self):
        from apps.clientes.models import Cliente
        from apps.cuentas_por_cobrar.services import reprogramar_cxc_por_plazo_cliente

        def apply(item):
            # Identidad: `origen_cloud_id` primero; la clave natural (cedula, o
            # nombre+tipo cuando no hay) solo sirve para adoptar la fila la
            # primera vez. Antes la clave natural ERA la identidad, asi que
            # corregir una cedula o renombrar un cliente en el portal creaba un
            # cliente nuevo y partia su cartera en dos.
            cloud_id = item.get('id')
            cedula = item.get('cedula_rnc')
            if cedula:
                lookup = {'cedula_rnc': cedula}
            else:
                lookup = {'nombre': item['nombre'], 'tipo': item.get('tipo', 'PERSONAL')}

            existente, sellar = self._adoptar_por_identidad_cloud(
                Cliente, cloud_id, lookup,
            )

            plazo_anterior = existente.plazo_credito_dias if existente else None
            try:
                plazo_credito_dias = int(item.get('plazo_credito_dias') or 30)
            except (TypeError, ValueError):
                plazo_credito_dias = 30
            if plazo_credito_dias < 1 or plazo_credito_dias > 365:
                plazo_credito_dias = 30

            campos = {
                'nombre': item.get('nombre', ''),
                'tipo': item.get('tipo', 'PERSONAL'),
                'cedula_rnc': cedula or None,
                'telefono': item.get('telefono'),
                'direccion': item.get('direccion'),
                'limite_credito': item.get('limite_credito', '0.00') or '0.00',
                'plazo_credito_dias': plazo_credito_dias,
                'condiciones_pago': item.get('condiciones_pago'),
                'notas': item.get('notas'),
                'activo': item.get('activo', True),
            }
            if sellar:
                campos['origen_cloud_id'] = cloud_id

            created = existente is None
            if created:
                cliente = Cliente.objects.create(**campos)
            else:
                cliente = existente
                for campo, valor in campos.items():
                    setattr(cliente, campo, valor)
                cliente.save()

            if (
                not created
                and plazo_anterior is not None
                and int(plazo_anterior) != int(cliente.plazo_credito_dias)
            ):
                reprogramar_cxc_por_plazo_cliente(
                    cliente,
                    origen='pull_clientes',
                    plazo_anterior=int(plazo_anterior),
                )

        return self._pull_generic('clientes', '/api/v1/maestros/clientes/', apply)

    def _pull_roles(self):
        """
        Sincroniza las DEFINICIONES de rol (rol -> permisos) del negocio desde el
        cloud.
        Las signals del motor de permisos invalidan el cache automaticamente.
        """
        import uuid

        from apps.permisos.catalogo import sembrar_catalogo
        from apps.permisos.models import Permiso, Rol
        from apps.sucursales.models import get_sucursal_actual

        sucursal = get_sucursal_actual()
        negocio = getattr(sucursal, 'negocio', None) if sucursal else None
        if negocio is None:
            return _resultado_pull()

        # Asegura el catalogo local para poder resolver los codigos de permiso.
        sembrar_catalogo(Permiso)

        vistos = set()

        def apply(item):
            cloud_id_raw = item.get('cloud_id')
            cloud_id = uuid.UUID(str(cloud_id_raw)) if cloud_id_raw else None
            if cloud_id:
                vistos.add(cloud_id)
            revision_remota = int(item.get('revision') or 1)
            activo = item.get('active', item.get('activo', True))
            codigos = list(item.get('permission_codes', item.get('permisos', [])))
            rol = (
                Rol.objects.filter(cloud_id=cloud_id).first()
                if cloud_id else None
            )
            if rol is not None and revision_remota < rol.revision:
                logger.warning(
                    'pull roles: revision vieja %s<%s ignorada para %s',
                    revision_remota, rol.revision, cloud_id,
                )
                return

            # La revocacion se aplica antes de validar capacidades nuevas. Un
            # POS viejo puede no conocer un permiso, pero nunca por eso conserva
            # activo un rol que el cloud dio de baja.
            if not activo:
                if rol is None and item.get('slug'):
                    candidato = Rol.objects.filter(
                        negocio=negocio, slug=item['slug'],
                    ).first()
                    if candidato is not None and (
                        not candidato.origen_cloud or not cloud_id
                        or candidato.cloud_id == cloud_id
                    ):
                        rol = candidato
                if rol is None:
                    return
                rol.activo = False
                rol.deleted_at = self._fecha_rbac(item.get('deleted_at')) or timezone.now()
                if cloud_id:
                    rol.cloud_id = cloud_id
                    rol.origen_cloud = True
                    rol.revision = revision_remota
                rol.save(
                    update_fields=[
                        'activo', 'deleted_at', 'cloud_id', 'origen_cloud',
                        'revision', 'fecha_modificacion',
                    ],
                    _preserve_rbac_revision=bool(cloud_id),
                    _adopt_cloud_identity=bool(cloud_id),
                )
                return

            permisos = list(Permiso.objects.filter(codigo__in=codigos))

            # Un codigo que el catalogo local no conoce = desfase de version
            # entre cloud y sucursal. Antes `filter(codigo__in=...)` lo omitia
            # en silencio y `set()` guardaba un rol PARCIAL: usuarios sin los
            # permisos que el portal dice que tienen, sin error en ningun lado.
            desconocidos = sorted(set(codigos) - {p.codigo for p in permisos})
            if desconocidos:
                logger.warning(
                    'pull roles: el rol %s referencia permisos desconocidos %s; '
                    'diferido para no guardar un rol incompleto. Actualiza la '
                    'sucursal o corre sync_permisos.',
                    item.get('slug'), desconocidos,
                )
                return DIFERIDO

            if rol is None:
                candidato = Rol.objects.filter(
                    negocio=negocio, slug=item['slug'],
                ).first()
                if candidato is not None and (
                    not candidato.origen_cloud or not cloud_id
                    or candidato.cloud_id == cloud_id
                ):
                    rol = candidato
                elif candidato is not None:
                    logger.error(
                        'pull roles: slug %s ya pertenece al cloud_id %s; diferido',
                        item['slug'], candidato.cloud_id,
                    )
                    return DIFERIDO

            if rol is None:
                rol = Rol(negocio=negocio, slug=item['slug'])
            rol.nombre = item.get('nombre') or item['slug']
            rol.activo = True
            rol.deleted_at = None
            if cloud_id:
                rol.cloud_id = cloud_id
                rol.origen_cloud = True
                rol.revision = revision_remota
            rol.save(
                _preserve_rbac_revision=bool(cloud_id),
                _adopt_cloud_identity=bool(cloud_id),
            )
            actuales = set(rol.permisos.values_list('pk', flat=True))
            nuevos = {permiso.pk for permiso in permisos}
            if actuales != nuevos:
                if cloud_id:
                    rol._preserve_rbac_revision = True
                try:
                    rol.permisos.set(permisos)
                finally:
                    if hasattr(rol, '_preserve_rbac_revision'):
                        del rol._preserve_rbac_revision

        def reconciliar():
            instante = timezone.now()
            return (
                Rol.objects.filter(
                    negocio=negocio, origen_cloud=True, activo=True,
                )
                .exclude(cloud_id__in=vistos)
                .update(
                    activo=False,
                    deleted_at=instante,
                    fecha_modificacion=instante,
                )
            )

        def validar_envelope(data):
            if data.get('tenant_key') != _tenant_key_rbac_local(negocio):
                return 'tenant_key no coincide con el negocio local'
            return None

        return self._pull_generic(
            'roles', '/api/v1/sync/roles/', apply,
            headers={'X-RBAC-Schema': 'rbac.sync.v2'},
            extra_params={'snapshot': 'full'},
            response_key='roles',
            on_snapshot_complete=reconciliar,
            envelope_validator=validar_envelope,
        )

    def _pull_asignaciones(self):
        """
        Sincroniza asignaciones usuario->rol desde el cloud para la sucursal
        actual. V2 usa cloud_id inmutable y conserva username, rol.slug y
        sucursal.codigo para compatibilidad legacy. No crea usuarios: si el
        usuario no existe localmente, se omite para evitar provisionar
        credenciales desde sync.
        """
        import uuid

        from django.contrib.auth import get_user_model
        from django.db.models import Q

        from apps.permisos.models import AsignacionRol, Rol
        from apps.sucursales.models import get_sucursal_actual

        sucursal_actual = get_sucursal_actual()
        negocio = getattr(sucursal_actual, 'negocio', None) if sucursal_actual else None
        if negocio is None:
            return _resultado_pull()

        User = get_user_model()

        vistos = set()

        def apply(item):
            username = item.get('usuario_username')
            rol_slug = item.get('rol_slug')
            if not username or not rol_slug:
                return

            cloud_id_raw = item.get('cloud_id')
            cloud_id = uuid.UUID(str(cloud_id_raw)) if cloud_id_raw else None
            if cloud_id:
                vistos.add(cloud_id)
            revision_remota = int(item.get('revision') or 1)
            activo = item.get('active', item.get('activo', True))
            sucursal_codigo = item.get('sucursal_codigo')

            existente = (
                AsignacionRol.objects.filter(cloud_id=cloud_id)
                .select_related('usuario', 'rol', 'sucursal').first()
                if cloud_id else None
            )
            if existente is not None and revision_remota < existente.revision:
                logger.warning(
                    'pull asignaciones: revision vieja %s<%s ignorada para %s',
                    revision_remota, existente.revision, cloud_id,
                )
                return

            # Una baja no necesita que usuario/rol/permisos nuevos existan. Se
            # buscan tambien duplicados por la terna legacy y la revocacion gana.
            if not activo:
                objetivos = AsignacionRol.objects.filter(rol__negocio=negocio)
                filtro_natural = Q(
                    usuario__username=username,
                    rol__slug=rol_slug,
                    sucursal__codigo=sucursal_codigo,
                ) if sucursal_codigo else Q(
                    usuario__username=username,
                    rol__slug=rol_slug,
                    sucursal__isnull=True,
                )
                if cloud_id:
                    objetivos = objetivos.filter(
                        Q(cloud_id=cloud_id) | filtro_natural
                    )
                else:
                    objetivos = objetivos.filter(filtro_natural)
                instante = self._fecha_rbac(item.get('deleted_at')) or timezone.now()
                cantidad = 0
                for objetivo in objetivos:
                    objetivo.activo = False
                    objetivo.deleted_at = instante
                    if cloud_id and objetivo.cloud_id == cloud_id:
                        objetivo.origen_cloud = True
                        objetivo.revision = revision_remota
                    objetivo.save(
                        update_fields=[
                            'activo', 'deleted_at', 'origen_cloud', 'revision',
                            'fecha_modificacion',
                        ],
                        _preserve_rbac_revision=bool(
                            cloud_id and objetivo.cloud_id == cloud_id
                        ),
                    )
                    cantidad += 1
                return

            usuario = User.objects.filter(username=username).first()
            if usuario is None:
                # Diferido, no omitido: el usuario puede aparecer en un ciclo
                # posterior y la asignacion tiene que seguir pendiente hasta
                # entonces.
                logger.warning(
                    'pull asignaciones: usuario %s no existe localmente; diferido',
                    username,
                )
                return DIFERIDO
            if getattr(usuario, 'negocio_id', None) not in (None, negocio.id):
                logger.warning(
                    'pull asignaciones: usuario %s pertenece a otro negocio; omitido',
                    username,
                )
                return
            if getattr(usuario, 'negocio_id', None) is None:
                usuario.negocio = negocio
                usuario.save(update_fields=['negocio'])

            role_cloud_id = item.get('role_cloud_id')
            rol = (
                Rol.objects.filter(
                    negocio=negocio, cloud_id=role_cloud_id,
                ).first()
                if role_cloud_id else None
            )
            if rol is None:
                rol = Rol.objects.filter(negocio=negocio, slug=rol_slug).first()
            if rol is None:
                # Caso tipico: el pull de roles fallo o difirio este rol. Si la
                # asignacion se diera por aplicada, su cursor avanzaria y ya no
                # volveria a bajar cuando el rol llegue.
                logger.warning(
                    'pull asignaciones: rol %s no existe localmente; diferido',
                    rol_slug,
                )
                return DIFERIDO

            sucursal = None
            if sucursal_codigo:
                if not sucursal_actual or sucursal_codigo != sucursal_actual.codigo:
                    logger.warning(
                        'pull asignaciones: sucursal %s no es esta instalacion; omitida',
                        sucursal_codigo,
                    )
                    return
                sucursal = sucursal_actual

            if existente is not None and (
                existente.usuario_id != usuario.pk
                or existente.rol_id != rol.pk
                or existente.sucursal_id != getattr(sucursal, 'pk', None)
            ):
                logger.error(
                    'pull asignaciones: cloud_id %s intento cambiar su terna; diferido',
                    cloud_id,
                )
                return DIFERIDO

            if existente is None:
                candidato = AsignacionRol.objects.filter(
                    usuario=usuario, rol=rol, sucursal=sucursal,
                ).first()
                if candidato is not None and (
                    not candidato.origen_cloud or not cloud_id
                    or candidato.cloud_id == cloud_id
                ):
                    existente = candidato
                elif candidato is not None:
                    logger.error(
                        'pull asignaciones: la terna ya pertenece al cloud_id %s; diferido',
                        candidato.cloud_id,
                    )
                    return DIFERIDO

            if existente is None:
                existente = AsignacionRol(
                    usuario=usuario, rol=rol, sucursal=sucursal,
                )
            existente.activo = True
            existente.deleted_at = None
            if cloud_id:
                existente.cloud_id = cloud_id
                existente.origen_cloud = True
                existente.revision = revision_remota
            existente.save(
                _preserve_rbac_revision=bool(cloud_id),
                _adopt_cloud_identity=bool(cloud_id),
            )

        def reconciliar():
            instante = timezone.now()
            return (
                AsignacionRol.objects.filter(
                    rol__negocio=negocio,
                    origen_cloud=True,
                    activo=True,
                )
                .filter(Q(sucursal__isnull=True) | Q(sucursal=sucursal_actual))
                .exclude(cloud_id__in=vistos)
                .update(
                    activo=False,
                    deleted_at=instante,
                    fecha_modificacion=instante,
                )
            )

        def validar_envelope(data):
            if data.get('tenant_key') != _tenant_key_rbac_local(negocio):
                return 'tenant_key no coincide con el negocio local'
            scope = data.get('scope') or {}
            if scope.get('branch_code') != sucursal_actual.codigo:
                return 'scope.branch_code no coincide con esta sucursal'
            return None

        return self._pull_generic(
            'asignaciones',
            '/api/v1/sync/asignaciones/',
            apply,
            headers={'X-RBAC-Schema': 'rbac.sync.v2'},
            extra_params={'snapshot': 'full'},
            response_key='assignments',
            on_snapshot_complete=reconciliar,
            envelope_validator=validar_envelope,
        )

    @staticmethod
    def _fecha_rbac(valor):
        if not valor:
            return None
        try:
            return datetime.fromisoformat(str(valor).replace('Z', '+00:00'))
        except (TypeError, ValueError):
            return None

    def _pull_metodos_credito(self):
        """Sincroniza reglas de credito administradas desde cloud."""
        from apps.cuentas_por_cobrar.models import MetodoPlazoCredito
        from apps.sucursales.models import get_sucursal_actual

        sucursal_actual = get_sucursal_actual()

        def apply(item):
            nombre = item.get('nombre')
            if not nombre:
                return
            tipo = item.get('tipo') or MetodoPlazoCredito.TIPO_VENCIMIENTO_UNICO
            if tipo not in dict(MetodoPlazoCredito.TIPO_CHOICES):
                tipo = MetodoPlazoCredito.TIPO_VENCIMIENTO_UNICO
            frecuencia = item.get('frecuencia') or MetodoPlazoCredito.FRECUENCIA_MENSUAL
            if frecuencia not in dict(MetodoPlazoCredito.FRECUENCIA_CHOICES):
                frecuencia = MetodoPlazoCredito.FRECUENCIA_MENSUAL

            sucursal = None
            sucursal_codigo = item.get('sucursal_codigo')
            if sucursal_codigo:
                if not sucursal_actual or sucursal_codigo != sucursal_actual.codigo:
                    logger.warning(
                        'pull metodos_credito: sucursal %s no es esta instalacion; omitido',
                        sucursal_codigo,
                    )
                    return
                sucursal = sucursal_actual

            MetodoPlazoCredito.objects.update_or_create(
                nombre=nombre,
                defaults={
                    'tipo': tipo,
                    'dias_vencimiento': max(int(item.get('dias_vencimiento') or 30), 1),
                    'cantidad_cuotas': max(int(item.get('cantidad_cuotas') or 1), 1),
                    'frecuencia': frecuencia,
                    'inicial_minima_porcentaje': item.get('inicial_minima_porcentaje') or '0.00',
                    'interes_porcentaje': item.get('interes_porcentaje') or '0.00',
                    'activo': item.get('activo', True),
                    'sucursal': sucursal,
                },
            )

        return self._pull_generic(
            'metodos_credito',
            '/api/v1/sync/metodos-credito/',
            apply,
        )

    def _pull_configuracion(self):
        """Sincroniza solo configuracion cloud-safe; excluye hardware/local."""
        from apps.configuracion.models import ConfiguracionNegocio
        from apps.sucursales.models import get_sucursal_actual

        sucursal = get_sucursal_actual()
        # El primer pull materializa de forma explícita la configuración local.
        # `load()` es lectura pura desde CFG-012 y debe seguir siéndolo para
        # todos los consumidores que no están inicializando una sucursal.
        config = ConfiguracionNegocio.bootstrap(sucursal=sucursal)
        count = 0
        cursor_tabla = 'configuracion'

        def apply(item):
            nonlocal count
            allowed = [
                'nombre_negocio',
                'rnc',
                'direccion',
                'telefono',
                'email_negocio',
                'permitir_inventario_negativo',
                'modulo_etiquetas_zebra',
                'modulo_financiacion_coop',
                'modulo_cotizaciones',
                'modulo_impresion_termica',
                'modulo_barcode_scanner',
                'modulo_reportes_ondemand',
                'modulo_ecf',
                'modulo_dashboard',
                'pago_efectivo',
                'pago_transferencia',
                'pago_tarjeta',
                'formato_codigo_barras',
                'dias_anulacion',
                'cantidad_copias_ticket',
                'ecf_proveedor',
                'itbis_incluido_en_precio',
                'itbis_porcentaje_global',
                'modo_contingencia',
            ]
            update_fields = []
            for field in allowed:
                if field in item and hasattr(config, field):
                    setattr(config, field, item[field])
                    update_fields.append(field)
            if update_fields:
                config.save(update_fields=update_fields + ['fecha_modificacion'])
                count += 1

        # Configuracion devuelve una lista pequena, pero usamos el cursor comun.
        # _pull_generic gestiona el cursor (VersionMaestro) con la
        # fecha_modificacion que devuelve el endpoint.
        resultado = self._pull_generic(
            cursor_tabla,
            '/api/v1/sync/configuracion/',
            apply,
        )
        # `apply` cuenta los campos efectivamente escritos; si _pull_generic no
        # conto items (respuesta singleton) se usa ese conteo.
        resultado['count'] = resultado['count'] or count
        return resultado

    # ------------------------------------------------------------------
    # Ciclo completo (lo que usa el command)
    # ------------------------------------------------------------------

    def ciclo_completo(self, registrar_log=True):
        """
        Un ciclo: ping -> push -> pull. Registra LogSync si registrar_log=True.
        Retorna dict con todas las metricas.
        """
        from .models import LogSync
        from apps.sucursales.models import get_sucursal_actual

        log = None
        if registrar_log:
            log = LogSync.objects.create(
                tipo='FULL',
                resultado='FALLO',  # default pesimista, se sobreescribe al final
                sucursal=get_sucursal_actual(),
            )

        resultado = {
            'online': False,
            'push': {'procesados': 0, 'confirmados': 0, 'fallidos': 0},
            'maestros': {
                'procesadas': 0, 'confirmadas': 0, 'duplicadas': 0,
                'conflictos': 0, 'rechazadas': 0, 'fallidas': 0,
            },
            'pull': {'categorias': 0, 'productos': 0, 'clientes': 0, 'total': 0},
            'mensaje': '',
        }

        try:
            if not self.check_connection():
                resultado['mensaje'] = 'Sin conexion al cloud'
                if log:
                    log.finalizar('FALLO', resultado['mensaje'])
                return resultado

            resultado['online'] = True
            resultado['heartbeat'] = self.heartbeat()
            resultado['maestros'] = self.push_mutaciones_maestro()
            resultado['push'] = self.push_eventos()
            resultado['pull'] = self.pull_maestros()

            estado, motivos = clasificar_ciclo(
                heartbeat=resultado['heartbeat'],
                push=resultado['push'],
                pull=resultado['pull'],
                maestros=resultado['maestros'],
            )
            resultado['estado'] = estado
            resultado['motivos'] = motivos

            if log:
                log.eventos_procesados = resultado['push']['procesados']
                log.eventos_exitosos = resultado['push']['confirmados']
                log.eventos_fallidos = resultado['push']['fallidos']
                log.registros_descargados = resultado['pull']['total']
                log.finalizar(
                    estado,
                    mensaje=(
                        '; '.join(motivos) if motivos
                        else f"push={resultado['push']} pull={resultado['pull']}"
                    ),
                )
            return resultado

        except SyncConfigError as exc:
            resultado['mensaje'] = str(exc)
            if log:
                log.finalizar('FALLO', str(exc))
            raise
        except Exception as exc:
            logger.exception('Ciclo completo fallo: %s', exc)
            resultado['mensaje'] = f'Error: {exc}'
            if log:
                log.finalizar('FALLO', str(exc))
            return resultado
