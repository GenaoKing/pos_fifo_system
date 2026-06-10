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
from datetime import datetime

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger('sync')


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

    # ------------------------------------------------------------------
    # PUSH: eventos locales -> cloud
    # ------------------------------------------------------------------

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
                .filter(estado__in=['PENDIENTE', 'ERROR'])
                .exclude(intentos__gte=self.max_retries)
                .order_by('created_at')[:self.batch_size]
            )
            eventos = list(eventos_qs)

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

        metricas['total'] = (
            metricas['categorias'] + metricas['productos']
            + metricas['clientes'] + metricas['roles'] + metricas['asignaciones']
        )
        logger.info('pull_maestros: %s', metricas)
        return metricas

    def _pull_generic(self, tabla, endpoint, apply_func):
        """
        Pull generico: GET endpoint?desde=<cursor>, paginando, aplicando
        apply_func(item) por cada registro y actualizando el cursor.
        """
        from .models import VersionMaestro

        cursor = VersionMaestro.get_o_crear(tabla)
        params = {}
        if cursor.ultima_version:
            params['desde'] = cursor.ultima_version.isoformat()

        count = 0
        max_fecha = cursor.ultima_version
        url = self._url(endpoint)

        # Paginacion: DRF devuelve {count, next, previous, results}
        while url:
            try:
                resp = requests.get(
                    url,
                    params=params if url == self._url(endpoint) else None,
                    headers=self.headers,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                logger.warning('pull %s: error de red: %s', tabla, exc)
                return count

            if resp.status_code >= 400:
                logger.error('pull %s: HTTP %s: %s', tabla, resp.status_code, resp.text[:500])
                return count

            data = resp.json()
            # Soporta respuesta paginada de DRF o lista directa
            if isinstance(data, dict) and 'results' in data:
                items = data['results']
                url = data.get('next')
                params = None  # ya van en el next
            else:
                items = data
                url = None

            for item in items:
                try:
                    apply_func(item)
                    count += 1
                    # Actualiza cursor con la fecha_modificacion del item
                    fecha_mod = item.get('fecha_modificacion') or item.get('updated_at')
                    if fecha_mod:
                        try:
                            parsed = datetime.fromisoformat(fecha_mod.replace('Z', '+00:00'))
                            if max_fecha is None or parsed > max_fecha:
                                max_fecha = parsed
                        except (ValueError, AttributeError):
                            pass
                except Exception as exc:
                    logger.exception('pull %s: error aplicando item %s: %s',
                                     tabla, item.get('id'), exc)

        # Actualiza cursor si procesamos algo
        if count > 0:
            cursor.ultima_version = max_fecha
            cursor.ultima_sync_exitosa = timezone.now()
            cursor.registros_ultima_sync = count
            cursor.save()

        return count

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

        def apply(item):
            # Identificador natural: cedula_rnc cuando existe, sino nombre+tipo
            cedula = item.get('cedula_rnc')
            if cedula:
                lookup = {'cedula_rnc': cedula}
            else:
                lookup = {'nombre': item['nombre'], 'tipo': item.get('tipo', 'PERSONAL')}

            Cliente.objects.update_or_create(
                **lookup,
                defaults={
                    'nombre': item.get('nombre', ''),
                    'tipo': item.get('tipo', 'PERSONAL'),
                    'telefono': item.get('telefono'),
                    'direccion': item.get('direccion'),
                    'limite_credito': item.get('limite_credito', '0.00') or '0.00',
                    'condiciones_pago': item.get('condiciones_pago'),
                    'notas': item.get('notas'),
                    'activo': item.get('activo', True),
                },
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
