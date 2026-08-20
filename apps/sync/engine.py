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
        estado_por_hash = {
            item.get('hash'): item
            for item in data.get('detalle', [])
        }

        for e in eventos:
            item = estado_por_hash.get(e.hash_payload)
            if item is None:
                # El cloud no respondio por este evento: queda como PENDIENTE
                # para reintentar en la proxima corrida
                metricas['fallidos'] += 1
                continue

            estado_cloud = item.get('estado')
            if estado_cloud in ('CONFIRMADO', 'DUPLICADO'):
                e.marcar_confirmado()
                metricas['confirmados'] += 1
            else:
                # ERROR u otro: reintentar
                error_msg = item.get('error', f'Estado cloud: {estado_cloud}')
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
        Retorna dict con metricas: {categorias, productos, clientes, total}.
        """
        self._require_config()

        metricas = {
            'categorias': 0,
            'productos': 0,
            'clientes': 0,
            'roles': 0,
            'asignaciones': 0,
            'metodos_credito': 0,
            'configuracion': 0,
            'total': 0,
        }

        try:
            metricas['categorias'] = self._pull_categorias()
        except Exception as exc:
            logger.exception('pull_maestros: error en categorias: %s', exc)

        try:
            metricas['productos'] = self._pull_productos()
        except Exception as exc:
            logger.exception('pull_maestros: error en productos: %s', exc)

        try:
            metricas['clientes'] = self._pull_clientes()
        except Exception as exc:
            logger.exception('pull_maestros: error en clientes: %s', exc)

        try:
            metricas['roles'] = self._pull_roles()
        except Exception as exc:
            logger.exception('pull_maestros: error en roles: %s', exc)

        try:
            metricas['asignaciones'] = self._pull_asignaciones()
        except Exception as exc:
            logger.exception('pull_maestros: error en asignaciones: %s', exc)

        try:
            metricas['metodos_credito'] = self._pull_metodos_credito()
        except Exception as exc:
            logger.exception('pull_maestros: error en metodos_credito: %s', exc)

        try:
            metricas['configuracion'] = self._pull_configuracion()
        except Exception as exc:
            logger.exception('pull_maestros: error en configuracion: %s', exc)

        metricas['total'] = (
            metricas['categorias'] + metricas['productos']
            + metricas['clientes'] + metricas['roles'] + metricas['asignaciones']
            + metricas['metodos_credito'] + metricas['configuracion']
        )
        logger.info('pull_maestros: %s', metricas)
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
                break

            if resp.status_code >= 400:
                logger.error('pull %s: HTTP %s: %s', tabla, resp.status_code, resp.text[:500])
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
                return self._pull_legacy(tabla, endpoint, apply_func, cursor)

            for item in items:
                clave = self._clave_cursor(item)

                try:
                    apply_func(item)
                    count += 1
                    aplicado = True
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

        self._guardar_cursor(cursor, commit_fecha, commit_id, count, bloqueo)
        return count

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

    def _pull_categorias(self):
        from apps.productos.models import Categoria

        def apply(item):
            Categoria.objects.update_or_create(
                nombre=item['nombre'],
                defaults={
                    'descripcion': item.get('descripcion', '') or '',
                    'tipo_negocio': item.get('tipo_negocio', '') or '',
                    'atributos_configurados': item.get('atributos_configurados') or {},
                    'activa': item.get('activa', True),
                }
            )

        return self._pull_generic('categorias', '/api/v1/maestros/categorias/', apply)

    def _pull_productos(self):
        from apps.productos.models import Producto, Categoria

        def apply(item):
            # Resuelve categoria por nombre (identificador natural)
            categoria = None
            cat_nombre = item.get('categoria_nombre')
            if cat_nombre:
                try:
                    categoria = Categoria.objects.get(nombre=cat_nombre)
                except Categoria.DoesNotExist:
                    logger.warning('Producto %s: categoria %s no existe local',
                                   item.get('sku'), cat_nombre)

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

            Producto.objects.update_or_create(
                sku=item['sku'],
                defaults=defaults,
            )

        return self._pull_generic('productos', '/api/v1/maestros/productos/', apply)

    def _pull_clientes(self):
        from apps.clientes.models import Cliente
        from apps.cuentas_por_cobrar.services import reprogramar_cxc_por_plazo_cliente

        def apply(item):
            # Identificador natural: cedula_rnc cuando existe, sino nombre+tipo
            cedula = item.get('cedula_rnc')
            if cedula:
                lookup = {'cedula_rnc': cedula}
            else:
                lookup = {'nombre': item['nombre'], 'tipo': item.get('tipo', 'PERSONAL')}

            existente = Cliente.objects.filter(**lookup).first()
            plazo_anterior = existente.plazo_credito_dias if existente else None
            try:
                plazo_credito_dias = int(item.get('plazo_credito_dias') or 30)
            except (TypeError, ValueError):
                plazo_credito_dias = 30
            if plazo_credito_dias < 1 or plazo_credito_dias > 365:
                plazo_credito_dias = 30

            cliente, created = Cliente.objects.update_or_create(
                **lookup,
                defaults={
                    'nombre': item.get('nombre', ''),
                    'tipo': item.get('tipo', 'PERSONAL'),
                    'telefono': item.get('telefono'),
                    'direccion': item.get('direccion'),
                    'limite_credito': item.get('limite_credito', '0.00') or '0.00',
                    'plazo_credito_dias': plazo_credito_dias,
                    'condiciones_pago': item.get('condiciones_pago'),
                    'notas': item.get('notas'),
                    'activo': item.get('activo', True),
                },
            )
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
            return 0

        # Asegura el catalogo local para poder resolver los codigos de permiso.
        sembrar_catalogo(Permiso)

        def apply(item):
            rol, _ = Rol.objects.update_or_create(
                negocio=negocio,
                slug=item['slug'],
                defaults={
                    'nombre': item.get('nombre') or item['slug'],
                    'activo': item.get('activo', True),
                },
            )
            rol.permisos.set(
                Permiso.objects.filter(codigo__in=item.get('permisos', []))
            )

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
            return 0

        User = get_user_model()

        def apply(item):
            username = item.get('usuario_username')
            rol_slug = item.get('rol_slug')
            if not username or not rol_slug:
                return

            usuario = User.objects.filter(username=username).first()
            if usuario is None:
                logger.warning(
                    'pull asignaciones: usuario %s no existe localmente; omitido',
                    username,
                )
                return
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
                logger.warning(
                    'pull asignaciones: rol %s no existe localmente; omitido',
                    rol_slug,
                )
                return

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
        downloaded = self._pull_generic(
            cursor_tabla,
            '/api/v1/sync/configuracion/',
            apply,
        )
        return downloaded or count

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

            # Determina resultado final
            hubo_fallos = resultado['push']['fallidos'] > 0
            if log:
                log.eventos_procesados = resultado['push']['procesados']
                log.eventos_exitosos = resultado['push']['confirmados']
                log.eventos_fallidos = resultado['push']['fallidos']
                log.registros_descargados = resultado['pull']['total']
                log.finalizar(
                    'PARCIAL' if hubo_fallos else 'EXITOSO',
                    mensaje=f"push={resultado['push']} pull={resultado['pull']}",
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
