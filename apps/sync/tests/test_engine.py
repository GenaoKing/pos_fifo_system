from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from apps.clientes.models import Cliente
from apps.productos.models import Categoria, Producto
from apps.sync.engine import SyncEngine


class DummyResponse:
    status_code = 200
    text = ''

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def make_engine():
    return SyncEngine(cloud_url='https://cloud.example', token='token')


def paginated(results):
    return {'count': len(results), 'next': None, 'previous': None, 'results': results}


# ---------------------------------------------------------------------------
# Productos
# ---------------------------------------------------------------------------

class SyncEnginePullProductosTests(TestCase):
    def test_pull_productos_crea_y_actualiza_campos_editables_del_portal(self):
        categoria = Categoria.objects.create(nombre='Vasos', descripcion='Linea vasos')
        Producto.objects.create(
            sku='TEST-001',
            codigo_barras='OLD-001',
            nombre='Producto viejo',
            descripcion='Antes del portal',
            categoria=categoria,
            precio_venta='100.00',
            stock_minimo=5,
            activo=True,
            estado='nuevo',
            marca='',
            atributos={},
        )

        payload = paginated([
            {
                'id': 99,
                'sku': 'TEST-001',
                'codigo_barras': 'CB-001',
                'nombre': 'Producto de prueba',
                'descripcion': 'Editado desde portal',
                'categoria': 1,
                'categoria_nombre': 'Vasos',
                'estado': 'usado',
                'marca': 'Royal',
                'precio_venta': '125.00',
                'stock_minimo': 12,
                'activo': False,
                'imagen_url': 'https://cloud.example/media/productos/test.jpg',
                'atributos': {'color': 'rojo', 'tamano': '16oz'},
                'fecha_modificacion': '2026-05-24T10:00:00-04:00',
            },
            {
                'id': 100,
                'sku': 'TEST-002',
                'codigo_barras': 'CB-002',
                'nombre': 'Producto nuevo',
                'descripcion': '',
                'categoria': 1,
                'categoria_nombre': 'Vasos',
                'estado': 'nuevo',
                'marca': 'Marca nueva',
                'precio_venta': '75.00',
                'stock_minimo': 3,
                'activo': True,
                'atributos': {'material': 'plastico'},
                'fecha_modificacion': '2026-05-24T10:01:00-04:00',
            },
        ])

        with patch('apps.sync.engine.requests.get', return_value=DummyResponse(payload)):
            count = make_engine()._pull_productos()['count']

        self.assertEqual(count, 2)

        p = Producto.objects.get(sku='TEST-001')
        self.assertEqual(p.nombre, 'Producto de prueba')
        self.assertEqual(p.descripcion, 'Editado desde portal')
        self.assertEqual(str(p.precio_venta), '125.00')
        self.assertEqual(p.codigo_barras, 'CB-001')
        self.assertFalse(p.activo)
        self.assertEqual(p.estado, 'usado')
        self.assertEqual(p.marca, 'Royal')
        self.assertEqual(p.stock_minimo, 12)
        self.assertEqual(p.atributos, {'color': 'rojo', 'tamano': '16oz'})
        self.assertEqual(p.categoria.nombre, 'Vasos')
        self.assertFalse(p.imagen)

        p2 = Producto.objects.get(sku='TEST-002')
        self.assertEqual(p2.nombre, 'Producto nuevo')
        self.assertEqual(str(p2.precio_venta), '75.00')
        self.assertEqual(p2.stock_minimo, 3)
        self.assertEqual(p2.marca, 'Marca nueva')
        self.assertEqual(p2.atributos, {'material': 'plastico'})


# ---------------------------------------------------------------------------
# Categorías
# ---------------------------------------------------------------------------

class SyncEnginePullCategoriasTests(TestCase):
    def test_pull_categorias_actualiza_campos_editables_del_portal(self):
        Categoria.objects.create(
            nombre='Envases',
            descripcion='Descripcion vieja',
            activa=True,
            tipo_negocio='general',
            atributos_configurados={},
        )

        payload = paginated([
            {
                'id': 10,
                'nombre': 'Envases',
                'descripcion': 'Envases plásticos y metálicos',
                'activa': True,
                'tipo_negocio': 'plasticos',
                'atributos_configurados': {'material': '', 'capacidad_ml': ''},
                'total_productos': 5,
                'fecha_modificacion': '2026-05-29T10:00:00-04:00',
            },
        ])

        with patch('apps.sync.engine.requests.get', return_value=DummyResponse(payload)):
            count = make_engine()._pull_categorias()['count']

        self.assertEqual(count, 1)
        cat = Categoria.objects.get(nombre='Envases')
        self.assertEqual(cat.descripcion, 'Envases plásticos y metálicos')
        self.assertEqual(cat.tipo_negocio, 'plasticos')
        self.assertEqual(cat.atributos_configurados, {'material': '', 'capacidad_ml': ''})
        self.assertTrue(cat.activa)

    def test_pull_categorias_crea_categoria_nueva(self):
        payload = paginated([
            {
                'id': 20,
                'nombre': 'Autopartes',
                'descripcion': 'Repuestos de vehículos',
                'activa': True,
                'tipo_negocio': 'autopartes',
                'atributos_configurados': {'marca': '', 'modelo': ''},
                'total_productos': 0,
                'fecha_modificacion': '2026-05-29T10:01:00-04:00',
            },
        ])

        with patch('apps.sync.engine.requests.get', return_value=DummyResponse(payload)):
            count = make_engine()._pull_categorias()['count']

        self.assertEqual(count, 1)
        self.assertTrue(Categoria.objects.filter(nombre='Autopartes').exists())
        cat = Categoria.objects.get(nombre='Autopartes')
        self.assertEqual(cat.tipo_negocio, 'autopartes')

    def test_pull_categorias_respeta_activa_false(self):
        Categoria.objects.create(nombre='Obsoleta', activa=True)

        payload = paginated([
            {
                'id': 30,
                'nombre': 'Obsoleta',
                'descripcion': '',
                'activa': False,
                'tipo_negocio': 'general',
                'atributos_configurados': {},
                'total_productos': 0,
                'fecha_modificacion': '2026-05-29T10:02:00-04:00',
            },
        ])

        with patch('apps.sync.engine.requests.get', return_value=DummyResponse(payload)):
            make_engine()._pull_categorias()

        self.assertFalse(Categoria.objects.get(nombre='Obsoleta').activa)


# ---------------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------------

class SyncEnginePullClientesTests(TestCase):
    def test_pull_clientes_actualiza_campos_editables_del_portal(self):
        Cliente.objects.create(
            tipo='CORPORATIVO',
            nombre='Royal Plast SRL',
            cedula_rnc='130123456',
            telefono='809-555-0000',
            activo=True,
        )

        payload = paginated([
            {
                'id': 5,
                'tipo': 'CORPORATIVO',
                'nombre': 'Royal Plast SRL',
                'cedula_rnc': '130123456',
                'telefono': '809-555-9999',
                'direccion': 'Av. Principal #10, Santo Domingo',
                'limite_credito': '50000.00',
                'plazo_credito_dias': 60,
                'condiciones_pago': '30 días',
                'notas': 'Cliente preferencial',
                'activo': True,
                'es_contado': False,
                'fecha_modificacion': '2026-05-29T10:00:00-04:00',
            },
        ])

        with patch('apps.sync.engine.requests.get', return_value=DummyResponse(payload)):
            count = make_engine()._pull_clientes()['count']

        self.assertEqual(count, 1)
        c = Cliente.objects.get(cedula_rnc='130123456')
        self.assertEqual(c.telefono, '809-555-9999')
        self.assertEqual(c.direccion, 'Av. Principal #10, Santo Domingo')
        self.assertEqual(c.limite_credito, Decimal('50000.00'))
        self.assertEqual(c.plazo_credito_dias, 60)
        self.assertEqual(c.condiciones_pago, '30 días')
        self.assertEqual(c.notas, 'Cliente preferencial')

    def test_pull_clientes_reprograma_cxc_si_cambia_plazo_credito(self):
        cliente = Cliente.objects.create(
            tipo='CORPORATIVO',
            nombre='Plazo Sync SRL',
            cedula_rnc='130654321',
            plazo_credito_dias=30,
            activo=True,
        )

        payload = paginated([
            {
                'id': 15,
                'tipo': 'CORPORATIVO',
                'nombre': 'Plazo Sync SRL',
                'cedula_rnc': '130654321',
                'telefono': None,
                'direccion': None,
                'limite_credito': '0.00',
                'plazo_credito_dias': 90,
                'condiciones_pago': None,
                'notas': None,
                'activo': True,
                'es_contado': False,
                'fecha_modificacion': '2026-05-29T10:00:00-04:00',
            },
        ])

        with patch('apps.sync.engine.requests.get', return_value=DummyResponse(payload)):
            with patch('apps.cuentas_por_cobrar.services.reprogramar_cxc_por_plazo_cliente') as reprogramar:
                count = make_engine()._pull_clientes()['count']

        cliente.refresh_from_db()
        self.assertEqual(count, 1)
        self.assertEqual(cliente.plazo_credito_dias, 90)
        reprogramar.assert_called_once()
        self.assertEqual(reprogramar.call_args.kwargs['origen'], 'pull_clientes')
        self.assertEqual(reprogramar.call_args.kwargs['plazo_anterior'], 30)

    def test_pull_clientes_crea_cliente_nuevo_por_cedula(self):
        payload = paginated([
            {
                'id': 6,
                'tipo': 'PERSONAL',
                'nombre': 'Juan Pérez',
                'cedula_rnc': '40212345678',
                'telefono': '809-111-2222',
                'direccion': None,
                'limite_credito': '0.00',
                'condiciones_pago': None,
                'notas': None,
                'activo': True,
                'es_contado': False,
                'fecha_modificacion': '2026-05-29T10:01:00-04:00',
            },
        ])

        with patch('apps.sync.engine.requests.get', return_value=DummyResponse(payload)):
            count = make_engine()._pull_clientes()['count']

        self.assertEqual(count, 1)
        self.assertTrue(Cliente.objects.filter(cedula_rnc='40212345678').exists())
        c = Cliente.objects.get(cedula_rnc='40212345678')
        self.assertEqual(c.nombre, 'Juan Pérez')
        self.assertEqual(c.tipo, 'PERSONAL')

    def test_pull_clientes_crea_cliente_sin_cedula_por_nombre_tipo(self):
        payload = paginated([
            {
                'id': 7,
                'tipo': 'PERSONAL',
                'nombre': 'Sin Cédula',
                'cedula_rnc': None,
                'telefono': None,
                'direccion': None,
                'limite_credito': '0.00',
                'condiciones_pago': None,
                'notas': None,
                'activo': True,
                'es_contado': False,
                'fecha_modificacion': '2026-05-29T10:02:00-04:00',
            },
        ])

        with patch('apps.sync.engine.requests.get', return_value=DummyResponse(payload)):
            count = make_engine()._pull_clientes()['count']

        self.assertEqual(count, 1)
        self.assertTrue(Cliente.objects.filter(nombre='Sin Cédula', tipo='PERSONAL').exists())

    def test_pull_clientes_no_falla_con_payload_sin_email(self):
        # Regresión: _pull_clientes tenía 'email' en defaults pero Cliente no tiene ese campo.
        # Este test verifica que el pull no lanza FieldError.
        payload = paginated([
            {
                'id': 8,
                'tipo': 'CORPORATIVO',
                'nombre': 'Empresa Sin Email',
                'cedula_rnc': '101234567',
                'telefono': None,
                'direccion': None,
                'limite_credito': '0.00',
                'condiciones_pago': None,
                'notas': None,
                'activo': True,
                'es_contado': False,
                'fecha_modificacion': '2026-05-29T10:03:00-04:00',
            },
        ])

        with patch('apps.sync.engine.requests.get', return_value=DummyResponse(payload)):
            count = make_engine()._pull_clientes()['count']

        self.assertEqual(count, 1)

    def test_pull_clientes_respeta_activo_false(self):
        Cliente.objects.create(
            tipo='PERSONAL', nombre='Inactivo Test',
            cedula_rnc='999888777', activo=True,
        )

        payload = paginated([
            {
                'id': 9,
                'tipo': 'PERSONAL',
                'nombre': 'Inactivo Test',
                'cedula_rnc': '999888777',
                'telefono': None,
                'direccion': None,
                'limite_credito': '0.00',
                'condiciones_pago': None,
                'notas': None,
                'activo': False,
                'es_contado': False,
                'fecha_modificacion': '2026-05-29T10:04:00-04:00',
            },
        ])

        with patch('apps.sync.engine.requests.get', return_value=DummyResponse(payload)):
            make_engine()._pull_clientes()

        self.assertFalse(Cliente.objects.get(cedula_rnc='999888777').activo)
