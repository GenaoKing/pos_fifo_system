"""
Endurecimiento de cotizaciones (parte 2 del cierre C05):

- COT-008: el servicio rechaza cantidades/descuentos imposibles con 400, no 500.
- COT-009: no se cotiza a un cliente inactivo (revalidacion server-side).
- COT-010: numeracion por maximo sufijo; un hueco en la secuencia no colisiona.
- COT-012: crear y convertir una cotizacion deja auditoria de negocio (CT-01).
- COT-014: un fallo al generar el PDF no filtra el texto de la excepcion.
- COT-017: el listado esta paginado.
"""
import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.auditoria.models import Auditoria
from apps.clientes.models import Cliente
from apps.cotizaciones.models import Cotizacion
from apps.permisos import testing as permisos_testing
from apps.productos.models import Categoria, Producto

User = get_user_model()

COTIZAR = ['ventas.crear', 'cotizaciones.ver', 'cotizaciones.crear']


class CotizacionHardeningBase(TestCase):
    def setUp(self):
        cache.clear()
        self.categoria = Categoria.objects.create(nombre='Cotizables', activa=True)
        self.producto = Producto.objects.create(
            sku='COTH-001', codigo_barras='COTH-001', nombre='Tuberia',
            descripcion='', categoria=self.categoria,
            precio_venta=Decimal('100.00'), stock_minimo=1, activo=True,
            estado='nuevo', marca='', atributos={},
        )
        self.cliente = Cliente.objects.create(nombre='Constructora', tipo='CORPORATIVO')
        self.user = self._usuario('coth_user')

    def tearDown(self):
        cache.clear()

    def _usuario(self, username, permisos=COTIZAR):
        user = User.objects.create_user(
            username=username, email=f'{username}@test.local',
            password='Prueba123', rol='CAJERA', activo=True,
        )
        permisos_testing.habilitar_cajero(user, permisos=list(permisos))
        return user

    def _cotizar(self, *, cantidad=1, descuento=0, precio='100.00', cliente_id=None):
        self.client.force_login(self.user)
        return self.client.post(
            reverse('cotizaciones:api_guardar'),
            data=json.dumps({
                'cliente_id': cliente_id if cliente_id is not None else self.cliente.id,
                'productos': [{
                    'producto_id': self.producto.id,
                    'cantidad': cantidad,
                    'precio_unitario': precio,
                    'descuento': descuento,
                }],
            }),
            content_type='application/json',
        )


class COT008ImportesTests(CotizacionHardeningBase):
    def test_cantidad_cero_es_400(self):
        resp = self._cotizar(cantidad=0)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Cotizacion.objects.count(), 0)

    def test_cantidad_negativa_es_400(self):
        self.assertEqual(self._cotizar(cantidad=-3).status_code, 400)

    def test_descuento_negativo_es_400(self):
        self.assertEqual(self._cotizar(descuento=-5).status_code, 400)

    def test_descuento_supera_subtotal_es_400(self):
        # subtotal = 1 * 100 = 100; descuento 500 lo supera.
        self.assertEqual(self._cotizar(descuento=500).status_code, 400)

    def test_descuento_no_finito_es_400(self):
        self.client.force_login(self.user)
        # json.dumps con allow_nan (default) emite `Infinity`, que json.loads acepta.
        payload = json.dumps({
            'cliente_id': self.cliente.id,
            'productos': [{
                'producto_id': self.producto.id,
                'cantidad': 1, 'precio_unitario': '100.00',
                'descuento': float('inf'),
            }],
        })
        resp = self.client.post(
            reverse('cotizaciones:api_guardar'),
            data=payload, content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Cotizacion.objects.count(), 0)

    def test_cotizacion_valida_es_200(self):
        resp = self._cotizar(cantidad=2, descuento=10)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['success'])


class COT009ClienteActivoTests(CotizacionHardeningBase):
    def test_cliente_inactivo_es_400(self):
        self.cliente.activo = False
        self.cliente.save(update_fields=['activo'])
        resp = self._cotizar()
        self.assertEqual(resp.status_code, 400)
        self.assertIn('inactivo', resp.json()['error'].lower())
        self.assertEqual(Cotizacion.objects.count(), 0)


class COT010NumeracionTests(CotizacionHardeningBase):
    def test_hueco_en_la_secuencia_no_colisiona(self):
        n1 = self._cotizar().json()['numero_cotizacion']
        n2 = self._cotizar().json()['numero_cotizacion']

        # Borrar la primera abre un hueco: `count()+1` reintentaria n2 y chocaria.
        Cotizacion.objects.get(numero_cotizacion=n1).delete()

        resp = self._cotizar()
        self.assertEqual(resp.status_code, 200, resp.content)
        n3 = resp.json()['numero_cotizacion']
        self.assertNotEqual(n3, n2)
        self.assertTrue(n3.endswith('00003'), n3)


class COT012AuditoriaTests(CotizacionHardeningBase):
    def test_crear_cotizacion_deja_auditoria(self):
        numero = self._cotizar().json()['numero_cotizacion']
        self.assertTrue(
            Auditoria.objects.filter(
                accion=Auditoria.TipoAccion.CREAR,
                descripcion__contains=numero,
            ).exists()
        )

    def test_convertir_por_venta_deja_auditoria(self):
        from apps.inventario.models import Compra, DetalleCompra
        from apps.ventas.services import procesar_venta_service

        compra = Compra.objects.create(
            usuario=self.user, proveedor='Prov', numero_factura='F-COTH',
            total=Decimal('500.00'),
        )
        DetalleCompra.objects.create(
            compra=compra, producto=self.producto, cantidad=50,
            costo_unitario=Decimal('10.00'), subtotal=Decimal('500.00'),
        )
        cot_id = self._cotizar().json()['cotizacion_id']

        procesar_venta_service(
            usuario=self.user,
            datos={
                'carrito': [{
                    'id': self.producto.id, 'cantidad': 1,
                    'precio_venta': '100.00', 'descuento': '0.00',
                }],
                'metodo_pago': 'efectivo', 'total': '100.00',
                'cliente_id': self.cliente.id, 'cotizacion_id': cot_id,
            },
        )

        cot = Cotizacion.objects.get(id=cot_id)
        self.assertEqual(cot.estado, 'CONVERTIDA')
        self.assertTrue(
            Auditoria.objects.filter(
                accion=Auditoria.TipoAccion.EDITAR,
                descripcion__contains=cot.numero_cotizacion,
            ).exists()
        )


class COT014PDFErrorTests(CotizacionHardeningBase):
    def test_error_de_pdf_no_filtra_la_excepcion(self):
        cot_id = self._cotizar().json()['cotizacion_id']

        with patch(
            'apps.cotizaciones.views.generar_pdf_cotizacion',
            side_effect=RuntimeError('RUTA/INTERNA/SECRETA'),
        ):
            resp = self.client.get(
                reverse('cotizaciones:descargar_pdf', args=[cot_id]),
                follow=True,
            )

        cuerpo = resp.content.decode('utf-8', 'ignore')
        self.assertNotIn('RUTA/INTERNA/SECRETA', cuerpo)
        self.assertIn('No se pudo generar', cuerpo)


class COT017PaginacionTests(CotizacionHardeningBase):
    def test_listado_paginado(self):
        for i in range(60):
            Cotizacion.objects.create(
                cliente=self.cliente, usuario=self.user,
                fecha_creacion=timezone.now(), total=Decimal('10.00'),
            )
        self.client.force_login(self.user)

        p1 = self.client.get(reverse('cotizaciones:lista'))
        self.assertEqual(p1.status_code, 200)
        self.assertEqual(len(p1.context['cotizaciones']), 50)
        self.assertEqual(p1.context['page_obj'].paginator.count, 60)

        p2 = self.client.get(reverse('cotizaciones:lista') + '?page=2')
        self.assertEqual(len(p2.context['cotizaciones']), 10)
