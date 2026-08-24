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
from datetime import datetime, timezone as dt_timezone

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger('sync')

# Punto de partida para el primer pull de una tabla (cursor vacio). Ver
# `_pull_generic`: el parametro `desde` debe viajar siempre para que el servidor
# use el orden del cursor y no el alfabetico.
_EPOCH = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)

# Tope de paginas por entidad y por ciclo. No es una regla de negocio: es un
# freno para que un endpoint que pagina mal no consuma el ciclo entero. Lo que
# queda pendiente se baja en el ciclo siguiente, desde el cursor commiteado.
MAX_PAGINAS_PULL = 200


class _Diferido:
    """Sentinela que un `apply` devuelve cuando NO pudo aplicar el item.

    Se usa para dependencias ausentes (el rol todavia no bajo, la categoria no
    existe local): no es un error -- no hay nada roto -- pero tampoco es un
    exito, y sobre todo NO debe avanzar la marca de agua. Si el cursor avanza,
    la fila no vuelve a bajar cuando la dependencia aparece, porque en el cloud
    esa fila no cambio y el `?desde=` ya la dejo atras.
    """

    def __repr__(self):
        return '<DIFERIDO>'


DIFERIDO = _Diferido()


def _resultado_pull(count=0, ok=True, error=None, bloqueo=None, paginas=0):
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
    }


def clasificar_ciclo(*, heartbeat, push, pull):
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
    for error in pull.get('errores', []):
        motivos.append(f'pull {error}')
    for bloqueo in pull.get('bloqueos', []):
        motivos.append(f'cursor bloqueado -> {bloqueo}')

    if not motivos:
        return 'EXITOSO', motivos

    hubo_avance = bool(push.get('confirmados')) or bool(pull.get('total'))
    if not heartbeat and not hubo_avance:
        return 'FALLO', motivos

    return 'PARCIAL', motivos


class SyncConfigError(Exception):
    """Levantado si faltan settings criticos (URL o token)."""
    pass


class SyncEngine:
    """Engine de sync para la sucursal actual."""

    def __init__(self, cloud_url=None, token=None, timeout=None, batch_size=None,
                 max_retries=None):
        self.cloud_url = (cloud_url or getattr(settings, 'CLOUD_API_URL', '')).rstrip('/')
        self.token = token or getattr(settings, 'CLOUD_API_TOKEN', '')
        self.timeout = timeout or getattr(settings, 'SYNC_HTTP_TIMEOUT', 10)
        self.batch_size = batch_size or getattr(settings, 'SYNC_BATCH_SIZE', 50)
        self.max_retries = max_retries or getattr(settings, 'SYNC_MAX_RETRIES', 10)
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
        try:
            r = requests.get(self._url('/api/v1/health/'), timeout=3)
            return r.status_code == 200
        except requests.RequestException:
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

    def push_eventos(self):
        """
        Empuja hasta batch_size eventos al cloud.
        Retorna dict con metricas: {procesados, confirmados, fallidos}.
        """
        self._require_config()

        # Importacion diferida para no cargar Django al importar el modulo
        from .models import EventoSync

        metricas = {'procesados': 0, 'confirmados': 0, 'fallidos': 0}

        # Claim: toma eventos pendientes con lock para que otro worker no los tome
        with transaction.atomic():
            eventos_qs = (
                EventoSync.objects
                .select_for_update(skip_locked=True)
                .filter(estado__in=EventoSync.ESTADOS_ENVIABLES)
                .exclude(intentos__gte=self.max_retries)
                .order_by('created_at')[:self.batch_size]
            )
            eventos = list(eventos_qs)

            if not eventos:
                return metricas

            # Los eventos SIN_PAYLOAD se encolaron porque el hecho ocurrio, pero
            # su serializacion fallo en su momento. Se reintenta aqui, contra el
            # estado actual de la BD. Los que sigan fallando no entran al batch:
            # `EventoSyncSerializer` rechaza payloads vacios.
            eventos = self._completar_payloads(eventos)

            if not eventos:
                return metricas

            # Marca todos como "sent_at" ANTES de enviar: si el cloud los recibe
            # pero la respuesta se pierde, el hash garantiza idempotencia en la
            # proxima corrida. Peor caso: reintento con el mismo hash => cloud
            # responde "ya confirmado".
            now = timezone.now()
            for e in eventos:
                e.sent_at = now
                e.save(update_fields=['sent_at'])

        metricas['procesados'] = len(eventos)

        # Construye payload del batch segun el formato que espera
        # EventoBatchSerializer de Fase 3:
        #   { "eventos": [{ "tipo_evento", "payload", "hash_payload", "timestamp" }] }
        payload = {
            'eventos': [
                {
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
                e.marcar_error(f'Conexion: {exc}', max_retries=self.max_retries)
            metricas['fallidos'] = len(eventos)
            return metricas

        # Respuesta no-2xx: fallo global del batch
        if resp.status_code >= 400:
            mensaje = f'HTTP {resp.status_code}: {resp.text[:500]}'
            logger.error('push_eventos: %s', mensaje)
            for e in eventos:
                e.marcar_error(mensaje, max_retries=self.max_retries)
            metricas['fallidos'] = len(eventos)
            return metricas

        # Respuesta OK: aplica resultado por evento
        try:
            data = resp.json()
        except ValueError:
            logger.error('push_eventos: respuesta no es JSON valido')
            for e in eventos:
                e.marcar_error('Respuesta cloud invalida (no JSON)', max_retries=self.max_retries)
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
                e.marcar_error(mensaje, max_retries=self.max_retries)
            metricas['fallidos'] = len(eventos)
            return metricas

        estado_por_hash = {}
        for item in data['detalle']:
            if isinstance(item, dict) and item.get('hash'):
                estado_por_hash[item['hash']] = item

        for e in eventos:
            item = estado_por_hash.get(e.hash_payload)
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
                )
                metricas['fallidos'] += 1
                continue

            estado_cloud = item.get('estado')
            if estado_cloud in ('CONFIRMADO', 'DUPLICADO'):
                e.marcar_confirmado()
                metricas['confirmados'] += 1
            else:
                # ERROR u otro: reintentar
                error_msg = item.get('error') or f'Estado cloud: {estado_cloud}'
                e.marcar_error(error_msg, max_retries=self.max_retries)
                metricas['fallidos'] += 1

        logger.info(
            'push_eventos: procesados=%d confirmados=%d fallidos=%d',
            metricas['procesados'], metricas['confirmados'], metricas['fallidos']
        )
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
        )

        metricas = {'total': 0, 'ok': True, 'entidades': {}, 'errores': [], 'bloqueos': []}

        for nombre, funcion in entidades:
            try:
                resultado = funcion()
            except Exception as exc:
                logger.exception('pull_maestros: error en %s: %s', nombre, exc)
                resultado = _resultado_pull(ok=False, error=f'{type(exc).__name__}: {exc}')

            metricas['entidades'][nombre] = resultado
            metricas[nombre] = resultado['count']
            metricas['total'] += resultado['count']

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

    def _pull_generic(self, tabla, endpoint, apply_func):
        """
        Pull incremental con cursor KEYSET y marca de agua contigua.

        Dos cursores, y esa es la idea central (ver BUG-B en docs/BUGS.md):

          req    -> clave del ultimo item RECIBIDO. Sirve para pedir la pagina
                    siguiente. Avanza siempre.
          commit -> clave del ultimo item aplicado con exito EN SECUENCIA
                    CONTIGUA. Es lo unico que se persiste.

        Antes habia un solo cursor que saltaba al maximo visto aunque un item
        hubiera fallado, y ese registro no volvia a entrar en ningun pull: se
        perdia para siempre.

            items:  [ok, ok, FALLA, ok, ok]
            antes:  cursor = clave del ultimo  -> el fallido se pierde
            ahora:  commit = clave del 2o      -> el proximo ciclo lo reintenta

        Los items posteriores al fallo SI se aplican (son idempotentes,
        `update_or_create`), asi que la sucursal no se queda con datos viejos
        por culpa de un registro problematico. Lo unico que se congela es la
        marca de agua persistida, y el bloqueo queda visible en el propio
        cursor (`bloqueado_desde` / `bloqueado_detalle`).

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

        count = 0
        contiguo = True          # mientras nadie falle, commit sigue a req
        bloqueo = None           # primer fallo de esta corrida
        error = None             # fallo de transporte/HTTP de esta corrida
        paginas = 0
        url = self._url(endpoint)

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

            try:
                resp = requests.get(
                    url, params=params, headers=self.headers, timeout=self.timeout,
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
            # Soporta respuesta paginada de DRF o lista directa.
            items = data['results'] if isinstance(data, dict) and 'results' in data else data
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
                return _resultado_pull(
                    count=self._pull_legacy(tabla, endpoint, apply_func, cursor),
                )

            frontera_antes = (req_fecha, req_id)

            for item in items:
                clave = self._clave_cursor(item)

                try:
                    # Savepoint por item: si `apply` falla con un error de BD
                    # (constraint, tipo), en Postgres la transaccion queda
                    # abortada y ni siquiera se podria guardar el cursor. Con
                    # el savepoint el fallo se aisla y el recorrido sigue.
                    with transaction.atomic():
                        resultado = apply_func(item)
                    aplicado = resultado is not DIFERIDO
                    if aplicado:
                        count += 1
                    elif bloqueo is None:
                        # Dependencia ausente: no es un error, pero TAMPOCO es
                        # "aplicado". Antes cualquier retorno sin excepcion
                        # contaba como exito y el cursor avanzaba, asi que la
                        # fila no volvia a bajar cuando la dependencia llegaba
                        # (el registro cloud no cambio, el `?desde=` ya paso).
                        bloqueo = (
                            f'{tabla}: item {self._ref_item(item)} diferido '
                            f'(dependencia ausente)'
                        )
                except Exception as exc:
                    logger.exception('pull %s: error aplicando item %s: %s',
                                     tabla, item.get('id') or item.get('cursor_id'), exc)
                    aplicado = False
                    if bloqueo is None:
                        bloqueo = f"{tabla}: item {self._ref_item(item)} falla al aplicarse: {exc}"

                if clave is not None:
                    req_fecha, req_id = clave
                    # La marca de agua solo avanza mientras la racha sea limpia.
                    if aplicado and contiguo:
                        commit_fecha, commit_id = clave

                if not aplicado:
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

        self._guardar_cursor(cursor, commit_fecha, commit_id, count, bloqueo)
        return _resultado_pull(
            count=count,
            ok=error is None,
            error=error,
            bloqueo=bloqueo,
            paginas=paginas,
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

    def _pull_legacy(self, tabla, endpoint, apply_func, cursor):
        """
        Recorrido antiguo: seguir el `next` de DRF y avanzar el cursor al maximo
        `fecha_modificacion` visto.

        Solo se usa contra un cloud que no soporta el cursor keyset. Conserva el
        comportamiento historico -- incluido su punto debil de saltarse un item
        que falla -- pero al menos recorre el catalogo completo sin perder
        registros por solapamiento de paginas.
        """
        count = 0
        max_fecha = cursor.ultima_version
        url = self._url(endpoint)
        params = {'desde': cursor.ultima_version.isoformat()} if cursor.ultima_version else {}

        while url:
            try:
                resp = requests.get(url, params=params or None, headers=self.headers,
                                    timeout=self.timeout)
            except requests.RequestException as exc:
                logger.warning('pull %s (legacy): error de red: %s', tabla, exc)
                break
            if resp.status_code >= 400:
                logger.error('pull %s (legacy): HTTP %s', tabla, resp.status_code)
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
                try:
                    apply_func(item)
                    count += 1
                except Exception as exc:
                    logger.exception('pull %s (legacy): error aplicando item: %s', tabla, exc)
                    continue
                clave = self._clave_cursor(item)
                if clave and (max_fecha is None or clave[0] > max_fecha):
                    max_fecha = clave[0]

        if count:
            cursor.ultima_version = max_fecha
            cursor.ultima_sync_exitosa = timezone.now()
            cursor.registros_ultima_sync = count
            cursor.save()
        return count

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

        Retorna (instancia_o_None, hay_que_sellar), o DIFERIDO si hay colision.
        """
        if cloud_id:
            existente = modelo.objects.filter(origen_cloud_id=cloud_id).first()
            if existente is not None:
                return existente, False

        candidato = modelo.objects.filter(**lookup_natural).first()
        if candidato is None:
            return None, bool(cloud_id)

        if candidato.origen_cloud_id and candidato.origen_cloud_id != cloud_id:
            # La fila local ya pertenece a OTRO registro cloud y su clave
            # natural suele ser unica, asi que crear una segunda con el mismo
            # nombre/cedula fallaria igual. Es una colision real: dos registros
            # cloud distintos reclaman la misma clave natural local. Quien la
            # resuelve es el operador (renombrar, fusionar), no el sync. Se
            # difiere para que quede visible en el cursor bloqueado.
            logger.warning(
                '%s: la fila local %s ya esta sellada con origen_cloud_id=%s; '
                'el registro cloud %s no puede adoptarla. Resolver manualmente.',
                modelo.__name__, lookup_natural, candidato.origen_cloud_id, cloud_id,
            )
            return DIFERIDO, False

        return candidato, bool(cloud_id)

    def _pull_categorias(self):
        from apps.productos.models import Categoria

        def apply(item):
            cloud_id = item.get('id')
            existente, sellar = self._adoptar_por_identidad_cloud(
                Categoria, cloud_id, {'nombre': item['nombre']},
            )
            if existente is DIFERIDO:
                return DIFERIDO

            campos = {
                'nombre': item['nombre'],
                'descripcion': item.get('descripcion', '') or '',
                'tipo_negocio': item.get('tipo_negocio', '') or '',
                'atributos_configurados': item.get('atributos_configurados') or {},
                'activa': item.get('activa', True),
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
            # Resuelve categoria por nombre (identificador natural)
            categoria = None
            cat_nombre = item.get('categoria_nombre')
            if cat_nombre:
                categoria = Categoria.objects.filter(nombre=cat_nombre).first()
                if categoria is None:
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

            defaults = {
                'nombre': item.get('nombre', ''),
                'descripcion': item.get('descripcion', '') or '',
                'precio_venta': item.get('precio_venta', '0'),
                'codigo_barras': item.get('codigo_barras') or '',
                'activo': item.get('activo', True),
                'estado': item.get('estado') or 'nuevo',
                'marca': item.get('marca') or '',
                'stock_minimo': item.get('stock_minimo', 5),
                'atributos': item.get('atributos') or {},
            }
            if categoria:
                defaults['categoria'] = categoria

            producto, _ = Producto.objects.update_or_create(
                sku=item['sku'],
                defaults=defaults,
            )
            self._descargar_imagen_producto(producto, item.get('imagen_url'))

        return self._pull_generic('productos', '/api/v1/maestros/productos/', apply)

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
            if existente is DIFERIDO:
                return DIFERIDO

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
        from apps.permisos.catalogo import sembrar_catalogo
        from apps.permisos.models import Permiso, Rol
        from apps.sucursales.models import get_sucursal_actual

        sucursal = get_sucursal_actual()
        negocio = getattr(sucursal, 'negocio', None) if sucursal else None
        if negocio is None:
            return _resultado_pull()

        # Asegura el catalogo local para poder resolver los codigos de permiso.
        sembrar_catalogo(Permiso)

        def apply(item):
            codigos = list(item.get('permisos', []))
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

            rol, _ = Rol.objects.update_or_create(
                negocio=negocio,
                slug=item['slug'],
                defaults={
                    'nombre': item.get('nombre') or item['slug'],
                    'activo': item.get('activo', True),
                },
            )
            rol.permisos.set(permisos)

        return self._pull_generic('roles', '/api/v1/sync/roles/', apply)

    def _pull_asignaciones(self):
        """
        Sincroniza asignaciones usuario->rol desde el cloud para la sucursal
        actual. La identidad cross-DB v1 es natural: username, rol.slug y
        sucursal.codigo. No crea usuarios: si el usuario no existe localmente,
        se omite para evitar provisionar credenciales desde sync.
        """
        from django.contrib.auth import get_user_model

        from apps.permisos.models import AsignacionRol, Rol
        from apps.sucursales.models import get_sucursal_actual

        sucursal_actual = get_sucursal_actual()
        negocio = getattr(sucursal_actual, 'negocio', None) if sucursal_actual else None
        if negocio is None:
            return _resultado_pull()

        User = get_user_model()

        def apply(item):
            username = item.get('usuario_username')
            rol_slug = item.get('rol_slug')
            if not username or not rol_slug:
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
            sucursal_codigo = item.get('sucursal_codigo')
            if sucursal_codigo:
                if not sucursal_actual or sucursal_codigo != sucursal_actual.codigo:
                    logger.warning(
                        'pull asignaciones: sucursal %s no es esta instalacion; omitida',
                        sucursal_codigo,
                    )
                    return
                sucursal = sucursal_actual

            AsignacionRol.objects.update_or_create(
                usuario=usuario,
                rol=rol,
                sucursal=sucursal,
                defaults={'activo': item.get('activo', True)},
            )

        return self._pull_generic(
            'asignaciones',
            '/api/v1/sync/asignaciones/',
            apply,
        )

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
        config = ConfiguracionNegocio.load(sucursal=sucursal)
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
            resultado['push'] = self.push_eventos()
            resultado['pull'] = self.pull_maestros()

            estado, motivos = clasificar_ciclo(
                heartbeat=resultado['heartbeat'],
                push=resultado['push'],
                pull=resultado['pull'],
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
