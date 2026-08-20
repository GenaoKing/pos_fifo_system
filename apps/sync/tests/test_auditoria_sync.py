"""
apps/sync/tests/test_auditoria_sync.py

Regresion de los hallazgos de `docs/exploracion/AUDITORIA_CODIGO_APPS_SYNC.md`.

Cada clase referencia el hallazgo que la motiva. Lo que se prueba no es "el
codigo hace X", sino la garantia: que un hecho no se pierda, que no se aplique
dos veces, que el cursor no adelante lo que no aplico, y que el estado
operativo diga la verdad.
"""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Count
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.clientes.models import Cliente
from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal
from apps.sync import events as sync_events
from apps.sync import registry
from apps.sync.constants import TIPOS_EVENTO_CODIGOS
from apps.sync.engine import SyncEngine, clasificar_ciclo
from apps.sync.models import EventoSync, VersionMaestro, reactivar_eventos


class _Resp:
    text = ''

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _pagina(items, next_url=None):
    return {'count': len(items), 'next': next_url, 'previous': None, 'results': items}


# =============================================================================
# SYNC-002 - cobertura del registry
# =============================================================================

class RegistryCoberturaTests(TestCase):
    """Todo tipo con objeto local debe poder recuperarse de SIN_PAYLOAD."""

    def test_todos_los_tipos_son_reserializables_salvo_los_declarados(self):
        """
        La asercion que pedia la auditoria: tipos del catalogo vs registry.

        Un tipo fuera del registry NO se puede re-serializar; su evento
        SIN_PAYLOAD agota reintentos y termina en DESCARTADO aunque el hecho
        siga vivo en la BD local.
        """
        sin_registrar = {
            tipo for tipo in TIPOS_EVENTO_CODIGOS
            if registry.por_tipo(tipo) is None
        }

        self.assertEqual(
            sin_registrar,
            set(registry.TIPOS_NO_RESERIALIZABLES),
            'Hay tipos de evento sin forma de re-serializarse. Registralos en '
            'apps/sync/registry.py o declaralos en TIPOS_NO_RESERIALIZABLES.',
        )

    def test_los_hechos_derivados_no_entran_al_backfill(self):
        """
        `verificar_sync --backfill` infiere "objeto sin evento => falta el
        evento". Para un derivado esa inferencia es falsa.
        """
        backfilleables = set(registry.hechos_backfilleables())

        self.assertIn('ventas', backfilleables)
        self.assertNotIn('ventas_anuladas', backfilleables)
        self.assertNotIn('cxc_pagos', backfilleables)

    def test_cada_hecho_declara_serializador_y_emisor_existentes(self):
        from apps.sync import events, serializers

        for clave, hecho in registry.HECHOS.items():
            with self.subTest(hecho=clave):
                self.assertTrue(
                    hasattr(serializers, hecho.serializador),
                    f'{clave}: serializador {hecho.serializador} no existe',
                )
                self.assertTrue(
                    hasattr(events, hecho.emisor),
                    f'{clave}: emisor {hecho.emisor} no existe',
                )


# =============================================================================
# SYNC-001 - durabilidad del outbox
# =============================================================================

@override_settings(SUCURSAL_CODIGO='SD-001')
class OutboxDurabilidadTests(TestCase):
    def setUp(self):
        cache.clear()
        self.sucursal = Sucursal.objects.create(
            codigo='SD-001', nombre='SD', activa=True,
        )

    def tearDown(self):
        cache.clear()

    def test_fallo_de_serializacion_degrada_a_sin_payload(self):
        """Un serializador roto no puede tumbar la operacion de negocio."""
        def serializar_roto():
            raise ValueError('serializador roto')

        evento = sync_events._crear_evento(
            tipo='VENTA_CREADA',
            serializar=serializar_roto,
            referencia='V-TEST-0001',
            objeto_id_local=1,
        )

        self.assertIsNotNone(evento)
        self.assertEqual(evento.estado, 'SIN_PAYLOAD')
        self.assertIsNone(evento.payload)

    def test_fallo_de_persistencia_se_propaga(self):
        """
        Lo contrario del anterior: si la COLA no se puede escribir, la
        excepcion sale y la transaccion de negocio revierte. Tragarsela dejaba
        la venta confirmada sin nada que reintentar (perdida silenciosa).
        """
        with mock.patch.object(
            EventoSync.objects, 'create', side_effect=RuntimeError('tabla ausente')
        ):
            with self.assertRaises(RuntimeError):
                sync_events._crear_evento(
                    tipo='VENTA_CREADA',
                    serializar=lambda: {'numero_venta': 'V-TEST-0002'},
                    referencia='V-TEST-0002',
                    objeto_id_local=2,
                )

        self.assertFalse(EventoSync.objects.exists())

    def test_payload_impersistible_reintenta_como_sin_payload(self):
        """
        Escalon intermedio: si el INSERT falla por el payload, se degrada en vez
        de tumbar la venta. Solo el segundo fallo se propaga.
        """
        original = EventoSync.objects.create
        llamadas = []

        def falla_solo_con_payload(*args, **kwargs):
            llamadas.append(kwargs.get('payload'))
            if kwargs.get('payload') is not None:
                raise TypeError('no serializable a JSON')
            return original(*args, **kwargs)

        with mock.patch.object(
            EventoSync.objects, 'create', side_effect=falla_solo_con_payload
        ):
            evento = sync_events._crear_evento(
                tipo='VENTA_CREADA',
                serializar=lambda: {'numero_venta': 'V-TEST-0003'},
                referencia='V-TEST-0003',
                objeto_id_local=3,
            )

        self.assertEqual(len(llamadas), 2)
        self.assertEqual(evento.estado, 'SIN_PAYLOAD')


# =============================================================================
# SYNC-003 - idempotencia con respaldo de BD
# =============================================================================

@override_settings(SUCURSAL_CODIGO='SD-001')
class HashUnicoTests(TestCase):
    def setUp(self):
        cache.clear()
        self.sucursal = Sucursal.objects.create(
            codigo='SD-001', nombre='SD', activa=True,
        )

    def tearDown(self):
        cache.clear()

    def _crear(self, hash_payload, estado='PENDIENTE', payload=None):
        return EventoSync.objects.create(
            sucursal=self.sucursal,
            tipo_evento='VENTA_CREADA',
            payload=payload if payload is not None else {'x': 1},
            hash_payload=hash_payload,
            estado=estado,
        )

    def test_no_se_puede_insertar_dos_veces_el_mismo_hash(self):
        from django.db import IntegrityError, transaction

        self._crear('abc123')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._crear('abc123')

    def test_varios_eventos_sin_payload_conviven(self):
        """El hash vacio queda fuera de la constraint: SIN_PAYLOAD es plural."""
        self._crear('', estado='SIN_PAYLOAD', payload=None)
        self._crear('', estado='SIN_PAYLOAD', payload=None)

        self.assertEqual(
            EventoSync.objects.filter(estado='SIN_PAYLOAD').count(), 2
        )

    def test_encolar_el_mismo_hecho_dos_veces_devuelve_el_existente(self):
        """El productor local no duplica ni revienta: reconoce el hecho."""
        payload = {'numero_venta': 'V-DUP-0001'}

        primero = sync_events._crear_evento(
            tipo='VENTA_CREADA', serializar=lambda: payload,
            referencia='V-DUP-0001', objeto_id_local=10,
        )
        segundo = sync_events._crear_evento(
            tipo='VENTA_CREADA', serializar=lambda: payload,
            referencia='V-DUP-0001', objeto_id_local=10,
        )

        self.assertEqual(primero.id, segundo.id)
        self.assertEqual(EventoSync.objects.count(), 1)

    def test_marcar_error_no_degrada_un_evento_confirmado(self):
        """
        Dos workers empujan el mismo evento; la respuesta lenta de uno llega
        despues de que el otro lo confirmo. Antes reabria el evento.
        """
        evento = self._crear('hash_confirmado')
        obsoleto = EventoSync.objects.get(pk=evento.pk)

        evento.marcar_confirmado()
        aplicado = obsoleto.marcar_error('respuesta tardia')

        self.assertFalse(aplicado)
        evento.refresh_from_db()
        self.assertEqual(evento.estado, 'CONFIRMADO')
        self.assertEqual(evento.intentos, 0)


class MigracionDedupTests(TestCase):
    """
    La parte de `sync.0008` que toca datos productivos.

    Una BD de test arranca vacia, asi que el `AddConstraint` se prueba solo a si
    mismo. Lo que corre contra datos reales al promover es el colapso previo de
    duplicados, y eso es lo que se ejercita aca: se baja la constraint, se
    fabrica el estado que dejo el bug, y se verifica que la migracion lo deja
    en condiciones de aceptarla.
    """

    CONSTRAINT = 'uniq_eventosync_hash_no_vacio'

    def setUp(self):
        cache.clear()
        self.sucursal = Sucursal.objects.create(
            codigo='SD-001', nombre='SD', activa=True,
        )

    def tearDown(self):
        cache.clear()

    def _sql(self, sentencia):
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute(sentencia)

    def _crear(self, hash_payload):
        return EventoSync.objects.create(
            sucursal=self.sucursal,
            tipo_evento='VENTA_CREADA',
            payload={'x': 1},
            hash_payload=hash_payload,
            estado='CONFIRMADO',
        )

    def test_colapsa_duplicados_y_deja_pasar_la_constraint(self):
        import importlib

        from django.apps import apps as django_apps

        # El modulo empieza con digito, asi que no se puede importar con `from`.
        migracion = importlib.import_module(
            'apps.sync.migrations.0008_eventosync_hash_unico'
        )

        # Django materializa una UniqueConstraint CON condicion como indice
        # parcial, no como constraint de tabla: se baja con DROP INDEX.
        # El DDL en Postgres es transaccional: el rollback del TestCase repone
        # el indice al terminar, no hace falta recrearlo a mano (y no se podria:
        # CREATE INDEX no corre con triggers de FK pendientes).
        self._sql(f'DROP INDEX "{self.CONSTRAINT}"')

        # Estado que dejaba el bug: el mismo hecho aplicado dos veces.
        primero = self._crear('hash_repetido')
        segundo = self._crear('hash_repetido')
        tercero = self._crear('hash_repetido')
        self._crear('hash_unico')
        # Varios SIN_PAYLOAD conviven y NO deben tocarse.
        for _ in range(2):
            EventoSync.objects.create(
                sucursal=self.sucursal, tipo_evento='VENTA_CREADA',
                payload=None, hash_payload='', estado='SIN_PAYLOAD',
            )

        # `schema_editor` real: la migracion fija el alias con
        # `schema_editor.connection.alias` porque el cloud es multi-tenant.
        from django.db import connection

        with connection.schema_editor() as schema_editor:
            migracion.colapsar_hashes_duplicados(django_apps, schema_editor)

        # Se conserva el mas antiguo de cada grupo.
        self.assertTrue(EventoSync.objects.filter(pk=primero.pk).exists())
        self.assertFalse(EventoSync.objects.filter(pk=segundo.pk).exists())
        self.assertFalse(EventoSync.objects.filter(pk=tercero.pk).exists())
        self.assertEqual(
            EventoSync.objects.filter(hash_payload='hash_unico').count(), 1
        )
        self.assertEqual(EventoSync.objects.filter(hash_payload='').count(), 2)

        # Invariante que la constraint exige: ningun hash no vacio repetido.
        repetidos = (
            EventoSync.objects
            .exclude(hash_payload='')
            .values('hash_payload')
            .annotate(total=Count('id'))
            .filter(total__gt=1)
        )
        self.assertEqual(list(repetidos), [])


# =============================================================================
# SYNC-011 - reintento efectivo
# =============================================================================

@override_settings(SUCURSAL_CODIGO='SD-001', SYNC_MAX_RETRIES=10)
class ReintentoTests(TestCase):
    def setUp(self):
        cache.clear()
        self.sucursal = Sucursal.objects.create(
            codigo='SD-001', nombre='SD', activa=True,
        )

    def tearDown(self):
        cache.clear()

    def test_reactivar_reinicia_el_contador_de_intentos(self):
        """
        Poner PENDIENTE sin tocar `intentos` dejaba el evento invisible: el
        push excluye por `intentos >= SYNC_MAX_RETRIES`.
        """
        evento = EventoSync.objects.create(
            sucursal=self.sucursal,
            tipo_evento='VENTA_CREADA',
            payload={'x': 1},
            hash_payload='hash_descartado',
            estado='DESCARTADO',
            intentos=10,
        )

        reactivar_eventos(EventoSync.objects.filter(pk=evento.pk))

        evento.refresh_from_db()
        self.assertEqual(evento.estado, 'PENDIENTE')
        self.assertEqual(evento.intentos, 0)

        # Y ahora el push si lo ve.
        enviables = (
            EventoSync.objects
            .filter(estado__in=EventoSync.ESTADOS_ENVIABLES)
            .exclude(intentos__gte=10)
        )
        self.assertIn(evento, list(enviables))

    def test_un_evento_sin_payload_vuelve_como_sin_payload(self):
        evento = EventoSync.objects.create(
            sucursal=self.sucursal,
            tipo_evento='VENTA_CREADA',
            payload=None,
            hash_payload='',
            estado='DESCARTADO',
            intentos=10,
        )

        reactivar_eventos(EventoSync.objects.filter(pk=evento.pk))

        evento.refresh_from_db()
        self.assertEqual(evento.estado, 'SIN_PAYLOAD')


# =============================================================================
# SYNC-009 - contrato del ACK
# =============================================================================

@override_settings(
    SUCURSAL_CODIGO='SD-001', SYNC_ENABLED=True,
    CLOUD_API_URL='https://cloud.test', CLOUD_API_TOKEN='t',
    SYNC_MAX_RETRIES=3,
)
class AckTests(TestCase):
    def setUp(self):
        cache.clear()
        self.sucursal = Sucursal.objects.create(
            codigo='SD-001', nombre='SD', activa=True,
        )
        self.evento = EventoSync.objects.create(
            sucursal=self.sucursal,
            tipo_evento='VENTA_CREADA',
            payload={'numero_venta': 'V-ACK-0001'},
            hash_payload='hash_ack',
            estado='PENDIENTE',
        )
        self.engine = SyncEngine()

    def tearDown(self):
        cache.clear()

    @mock.patch('apps.sync.engine.requests.post')
    def test_ack_sin_el_hash_consume_un_intento(self, mock_post):
        """
        Antes esto solo sumaba a `fallidos`: el evento volvia en cada batch
        para siempre, sin causa registrada y sin llegar nunca a DESCARTADO.
        """
        mock_post.return_value = _Resp({'recibidos': 0, 'detalle': []})

        metricas = self.engine.push_eventos()

        self.assertEqual(metricas['fallidos'], 1)
        self.evento.refresh_from_db()
        self.assertEqual(self.evento.intentos, 1)
        self.assertEqual(self.evento.estado, 'ERROR')
        self.assertIn('ACK', self.evento.ultimo_error)

    @mock.patch('apps.sync.engine.requests.post')
    def test_ack_que_no_es_objeto_marca_error(self, mock_post):
        mock_post.return_value = _Resp([{'hash': 'hash_ack', 'estado': 'CONFIRMADO'}])

        metricas = self.engine.push_eventos()

        self.assertEqual(metricas['fallidos'], 1)
        self.evento.refresh_from_db()
        self.assertEqual(self.evento.estado, 'ERROR')
        self.assertIn('formato invalido', self.evento.ultimo_error)

    @mock.patch('apps.sync.engine.requests.post')
    def test_ack_repetido_no_rompe_el_mapa(self, mock_post):
        mock_post.return_value = _Resp({'detalle': [
            {'hash': 'hash_ack', 'estado': 'CONFIRMADO'},
            {'hash': 'hash_ack', 'estado': 'CONFIRMADO'},
            {'estado': 'CONFIRMADO'},  # sin hash: se ignora
        ]})

        metricas = self.engine.push_eventos()

        self.assertEqual(metricas['confirmados'], 1)
        self.evento.refresh_from_db()
        self.assertEqual(self.evento.estado, 'CONFIRMADO')

    @mock.patch('apps.sync.engine.requests.post')
    def test_duplicado_cuenta_como_entregado(self, mock_post):
        mock_post.return_value = _Resp({'detalle': [
            {'hash': 'hash_ack', 'estado': 'DUPLICADO'},
        ]})

        metricas = self.engine.push_eventos()

        self.assertEqual(metricas['confirmados'], 1)
        self.evento.refresh_from_db()
        self.assertEqual(self.evento.estado, 'CONFIRMADO')


# =============================================================================
# SYNC-010 - progreso del recorrido keyset
# =============================================================================

@override_settings(
    SUCURSAL_CODIGO='SD-001', SYNC_ENABLED=True,
    CLOUD_API_URL='https://cloud.test', CLOUD_API_TOKEN='t',
)
class KeysetProgresoTests(TestCase):
    def setUp(self):
        cache.clear()
        Sucursal.objects.create(codigo='SD-001', nombre='SD', activa=True)
        self.engine = SyncEngine()

    def tearDown(self):
        cache.clear()

    @mock.patch('apps.sync.engine.requests.get')
    def test_pagina_sin_clave_con_next_no_cicla(self, mock_get):
        """
        Una pagina cuyos items no traen (fecha_modificacion, id) no mueve la
        frontera; con `next` presente el cliente volvia a pedir exactamente lo
        mismo para siempre y colgaba el ciclo del daemon.
        """
        item_sin_clave = {
            'nombre': 'Cat sin clave',
            'descripcion': '',
            'tipo_negocio': '',
            'atributos_configurados': {},
            'activa': True,
        }
        # Si el guardarrail no existiera, este side_effect se agotaria y el
        # test fallaria con StopIteration en vez de terminar.
        mock_get.side_effect = [
            _Resp(_pagina([item_sin_clave], next_url='https://cloud.test/next')),
        ] * 3

        resultado = self.engine._pull_categorias()

        self.assertEqual(mock_get.call_count, 1)
        self.assertIsNotNone(resultado['bloqueo'])
        self.assertIn('avanzar el cursor', resultado['bloqueo'])

    @mock.patch('apps.sync.engine.requests.get')
    def test_limite_de_paginas_corta_el_recorrido(self, mock_get):
        """Freno defensivo: el resto continua en el proximo ciclo."""
        self.engine.max_paginas_pull = 2

        def pagina(indice):
            return _pagina(
                [{
                    'nombre': f'Cat {indice}',
                    'descripcion': '', 'tipo_negocio': '',
                    'atributos_configurados': {}, 'activa': True,
                    'fecha_modificacion': (
                        timezone.now() + timedelta(seconds=indice)
                    ).isoformat(),
                    'id': indice,
                }],
                next_url='https://cloud.test/next',
            )

        mock_get.side_effect = [_Resp(pagina(i)) for i in range(1, 10)]

        resultado = self.engine._pull_categorias()

        self.assertEqual(mock_get.call_count, 2)
        self.assertIn('limite de 2 paginas', resultado['bloqueo'])


# =============================================================================
# SYNC-005 - veredicto honesto del ciclo
# =============================================================================

class ClasificacionDeCicloTests(TestCase):
    def _pull(self, **kwargs):
        base = {'total': 0, 'ok': True, 'errores': [], 'bloqueos': []}
        base.update(kwargs)
        return base

    def _push(self, **kwargs):
        base = {'procesados': 0, 'confirmados': 0, 'fallidos': 0}
        base.update(kwargs)
        return base

    def test_todo_bien_es_exitoso(self):
        estado, motivos = clasificar_ciclo(
            heartbeat=True, push=self._push(), pull=self._pull(),
        )
        self.assertEqual(estado, 'EXITOSO')
        self.assertEqual(motivos, [])

    def test_pull_con_401_no_es_exitoso(self):
        """
        El escenario de la auditoria: /health/ responde 200 pero todos los
        endpoints autenticados fallan. Antes se registraba EXITOSO.
        """
        estado, motivos = clasificar_ciclo(
            heartbeat=True,
            push=self._push(),
            pull=self._pull(ok=False, errores=['roles: HTTP 401']),
        )
        self.assertEqual(estado, 'PARCIAL')
        self.assertIn('pull roles: HTTP 401', motivos)

    def test_heartbeat_caido_sin_avance_es_fallo(self):
        estado, _ = clasificar_ciclo(
            heartbeat=False, push=self._push(), pull=self._pull(),
        )
        self.assertEqual(estado, 'FALLO')

    def test_heartbeat_caido_con_avance_es_parcial(self):
        estado, _ = clasificar_ciclo(
            heartbeat=False,
            push=self._push(procesados=1, confirmados=1),
            pull=self._pull(),
        )
        self.assertEqual(estado, 'PARCIAL')

    def test_cursor_bloqueado_se_reporta(self):
        estado, motivos = clasificar_ciclo(
            heartbeat=True,
            push=self._push(),
            pull=self._pull(total=5, bloqueos=['roles: item x diferido']),
        )
        self.assertEqual(estado, 'PARCIAL')
        self.assertIn('cursor bloqueado -> roles: item x diferido', motivos)


# =============================================================================
# SYNC-007 - identidad cloud estable
# =============================================================================

@override_settings(
    SUCURSAL_CODIGO='SD-001', SYNC_ENABLED=True,
    CLOUD_API_URL='https://cloud.test', CLOUD_API_TOKEN='t',
)
class IdentidadCloudTests(TestCase):
    def setUp(self):
        cache.clear()
        Sucursal.objects.create(codigo='SD-001', nombre='SD', activa=True)
        self.engine = SyncEngine()

    def tearDown(self):
        cache.clear()

    def _item_categoria(self, cloud_id, nombre, segundos=1):
        return {
            'id': cloud_id,
            'nombre': nombre,
            'descripcion': '',
            'tipo_negocio': '',
            'atributos_configurados': {},
            'activa': True,
            'fecha_modificacion': (
                timezone.now() + timedelta(seconds=segundos)
            ).isoformat(),
        }

    @mock.patch('apps.sync.engine.requests.get')
    def test_renombrar_una_categoria_no_la_duplica(self, mock_get):
        """
        El bug: identificar por `nombre` hacia que un rename creara otra
        categoria y los productos historicos quedaran en la vieja.
        """
        mock_get.side_effect = [
            _Resp(_pagina([self._item_categoria(7, 'Vasos')])),
            _Resp(_pagina([])),
        ]
        self.engine._pull_categorias()

        categoria = Categoria.objects.get()
        self.assertEqual(categoria.origen_cloud_id, 7)

        producto = Producto.objects.create(
            sku='IDENT-001', codigo_barras='IDENT-001', nombre='Vaso',
            descripcion='', categoria=categoria, precio_venta=Decimal('10.00'),
            stock_minimo=1, activo=True, estado='nuevo', marca='', atributos={},
        )

        # El portal la renombra. Mismo id cloud, nombre distinto.
        mock_get.side_effect = [
            _Resp(_pagina([self._item_categoria(7, 'Vasos plasticos', segundos=60)])),
            _Resp(_pagina([])),
        ]
        self.engine._pull_categorias()

        self.assertEqual(Categoria.objects.count(), 1)
        categoria.refresh_from_db()
        self.assertEqual(categoria.nombre, 'Vasos plasticos')
        producto.refresh_from_db()
        self.assertEqual(producto.categoria_id, categoria.id)

    @mock.patch('apps.sync.engine.requests.get')
    def test_la_clave_natural_adopta_la_fila_local_la_primera_vez(self, mock_get):
        """Bootstrap: una categoria que ya existia local se sella, no se duplica."""
        local = Categoria.objects.create(nombre='Vasos')

        mock_get.side_effect = [
            _Resp(_pagina([self._item_categoria(7, 'Vasos')])),
            _Resp(_pagina([])),
        ]
        self.engine._pull_categorias()

        self.assertEqual(Categoria.objects.count(), 1)
        local.refresh_from_db()
        self.assertEqual(local.origen_cloud_id, 7)

    @mock.patch('apps.sync.engine.requests.get')
    def test_colision_de_clave_natural_se_difiere_sin_pisar_nada(self, mock_get):
        """
        Dos registros cloud distintos reclaman la misma clave natural local.
        No se le roba la identidad a la fila sellada ni se intenta crear una
        segunda con el mismo nombre (`nombre` es unique): se difiere y queda
        visible como cursor bloqueado para que lo resuelva el operador.
        """
        Categoria.objects.create(nombre='Vasos', origen_cloud_id=99)

        mock_get.side_effect = [
            _Resp(_pagina([self._item_categoria(7, 'Vasos')])),
            _Resp(_pagina([])),
        ]
        resultado = self.engine._pull_categorias()

        self.assertEqual(resultado['count'], 0)
        self.assertIsNotNone(resultado['bloqueo'])

        self.assertEqual(Categoria.objects.count(), 1)
        vieja = Categoria.objects.get(origen_cloud_id=99)
        self.assertEqual(vieja.nombre, 'Vasos')

        cursor = VersionMaestro.objects.get(tabla='categorias')
        self.assertIsNotNone(cursor.bloqueado_desde)

    @mock.patch('apps.sync.engine.requests.get')
    def test_corregir_la_cedula_de_un_cliente_no_lo_duplica(self, mock_get):
        def item(cloud_id, nombre, cedula, segundos=1):
            return {
                'id': cloud_id,
                'nombre': nombre,
                'tipo': 'PERSONAL',
                'cedula_rnc': cedula,
                'telefono': None,
                'direccion': None,
                'limite_credito': '0.00',
                'plazo_credito_dias': 30,
                'condiciones_pago': None,
                'notas': None,
                'activo': True,
                'fecha_modificacion': (
                    timezone.now() + timedelta(seconds=segundos)
                ).isoformat(),
            }

        mock_get.side_effect = [
            _Resp(_pagina([item(21, 'Juan Perez', '00112345678')])),
            _Resp(_pagina([])),
        ]
        self.engine._pull_clientes()

        cliente = Cliente.objects.get(origen_cloud_id=21)

        # El portal corrige la cedula (estaba mal tipeada).
        mock_get.side_effect = [
            _Resp(_pagina([item(21, 'Juan Perez', '00187654321', segundos=60)])),
            _Resp(_pagina([])),
        ]
        self.engine._pull_clientes()

        self.assertEqual(Cliente.objects.filter(origen_cloud_id=21).count(), 1)
        cliente.refresh_from_db()
        self.assertEqual(cliente.cedula_rnc, '00187654321')


# =============================================================================
# SYNC-006 - dependencias diferidas
# =============================================================================

@override_settings(
    SUCURSAL_CODIGO='SD-001', SYNC_ENABLED=True,
    CLOUD_API_URL='https://cloud.test', CLOUD_API_TOKEN='t',
)
class DependenciaDiferidaTests(TestCase):
    def setUp(self):
        cache.clear()
        Sucursal.objects.create(codigo='SD-001', nombre='SD', activa=True)
        self.engine = SyncEngine()

    def tearDown(self):
        cache.clear()

    def _item_producto(self, sku, categoria_nombre, segundos=1):
        return {
            'id': 1,
            'sku': sku,
            'nombre': 'Producto X',
            'descripcion': '',
            'precio_venta': '10.00',
            'codigo_barras': '',
            'categoria_nombre': categoria_nombre,
            'activo': True,
            'estado': 'nuevo',
            'marca': '',
            'stock_minimo': 5,
            'atributos': {},
            'fecha_modificacion': (
                timezone.now() + timedelta(seconds=segundos)
            ).isoformat(),
        }

    @mock.patch('apps.sync.engine.requests.get')
    def test_producto_con_categoria_ausente_se_difiere(self, mock_get):
        """
        Antes se guardaba el producto con su categoria vieja y el cursor
        avanzaba; cuando la categoria llegaba, el producto ya no volvia a bajar
        y quedaba mal clasificado para siempre.
        """
        mock_get.side_effect = [
            _Resp(_pagina([self._item_producto('DIF-001', 'Categoria Nueva')])),
            _Resp(_pagina([])),
        ]

        resultado = self.engine._pull_productos()

        self.assertEqual(resultado['count'], 0)
        self.assertFalse(Producto.objects.filter(sku='DIF-001').exists())

        cursor = VersionMaestro.objects.get(tabla='productos')
        self.assertIsNone(cursor.ultima_version)
        self.assertIsNotNone(cursor.bloqueado_desde)

    @mock.patch('apps.sync.engine.requests.get')
    def test_el_producto_diferido_se_aplica_cuando_llega_la_categoria(self, mock_get):
        item = self._item_producto('DIF-002', 'Categoria Nueva')

        mock_get.side_effect = [_Resp(_pagina([item])), _Resp(_pagina([]))]
        self.assertEqual(self.engine._pull_productos()['count'], 0)

        Categoria.objects.create(nombre='Categoria Nueva')

        # Mismo payload, sin cambiar en cloud: vuelve a bajar porque el cursor
        # nunca lo dio por aplicado.
        mock_get.side_effect = [_Resp(_pagina([item])), _Resp(_pagina([]))]
        self.assertEqual(self.engine._pull_productos()['count'], 1)

        producto = Producto.objects.get(sku='DIF-002')
        self.assertEqual(producto.categoria.nombre, 'Categoria Nueva')
        self.assertIsNone(
            VersionMaestro.objects.get(tabla='productos').bloqueado_desde
        )

    @mock.patch('apps.sync.engine.requests.get')
    def test_rol_con_permiso_desconocido_se_difiere(self, mock_get):
        """
        Un codigo que el catalogo local no conoce = desfase de version. Antes
        `filter(codigo__in=...)` lo omitia y se guardaba un rol PARCIAL.
        """
        from apps.permisos import testing as permisos_testing
        from apps.permisos.models import Rol

        negocio = permisos_testing.crear_negocio('Royal Plast')
        sucursal = Sucursal.objects.get(codigo='SD-001')
        sucursal.negocio = negocio
        sucursal.save()
        cache.clear()

        mock_get.side_effect = [
            _Resp(_pagina([{
                'slug': 'supervisor',
                'nombre': 'Supervisor',
                'activo': True,
                'permisos': ['ventas.crear', 'modulo.inventado.que.no.existe'],
                'fecha_modificacion': timezone.now().isoformat(),
                'id': 1,
            }])),
            _Resp(_pagina([])),
        ]

        resultado = self.engine._pull_roles()

        self.assertEqual(resultado['count'], 0)
        self.assertFalse(Rol.objects.filter(slug='supervisor').exists())
        self.assertIsNotNone(resultado['bloqueo'])
