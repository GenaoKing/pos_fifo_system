from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from reportlab.platypus import SimpleDocTemplate

from apps.auditoria.models import Auditoria
from apps.clientes.models import Cliente
from apps.configuracion.models import ConfiguracionNegocio
from apps.configuracion.utils import config_para_documento
from apps.permisos import testing as permisos_testing
from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal
from apps.ventas.models import DetalleVenta, Pago, Venta
from apps.ventas.pdf_comprobante import generar_comprobante_venta


class ComprobanteVentaPdfTests(TestCase):
    def setUp(self):
        User = get_user_model()

        self.negocio = permisos_testing.crear_negocio('Negocio Comprobante')
        self.suc_a = Sucursal.objects.create(
            codigo='CMP-A', nombre='Sucursal A', activa=True, negocio=self.negocio,
        )
        self.suc_b = Sucursal.objects.create(
            codigo='CMP-B', nombre='Sucursal B', activa=True, negocio=self.negocio,
        )
        ConfiguracionNegocio.objects.create(
            sucursal=self.suc_a, nombre_negocio='Negocio A',
        )
        ConfiguracionNegocio.objects.create(
            sucursal=self.suc_b, nombre_negocio='Negocio B',
        )

        self.cajero = User.objects.create_user(
            username='cajero_comprobante',
            email='cajero_comprobante@test.local',
            password='pass',
            rol='CAJERA',
            activo=True,
        )
        permisos_testing.habilitar_cajero(
            self.cajero,
            negocio=self.negocio,
            permisos=['ventas.crear', 'ventas.reimprimir'],
        )

        self.cajero_sin_permiso = User.objects.create_user(
            username='cajero_sin_comprobante',
            email='cajero_sin_comprobante@test.local',
            password='pass',
            rol='CAJERA',
            activo=True,
        )
        permisos_testing.habilitar_cajero(
            self.cajero_sin_permiso,
            negocio=self.negocio,
            permisos=['ventas.crear'],
        )

        self.cliente = Cliente.objects.create(
            tipo='PERSONAL',
            nombre='Cliente Comprobante',
            cedula_rnc='00300000003',
            telefono='809-555-1111',
            direccion='Santo Domingo',
            activo=True,
        )
        categoria = Categoria.objects.create(nombre='Comprobante')
        self.producto = Producto.objects.create(
            sku='CMP-001',
            codigo_barras='CMP-001',
            nombre='Producto comprobante',
            descripcion='',
            categoria=categoria,
            precio_venta=Decimal('150.00'),
            stock_minimo=1,
            activo=True,
            estado='nuevo',
            marca='',
            atributos={},
        )
        self.venta = self._crear_venta(sucursal=self.suc_a, cliente=self.cliente)

    def _crear_venta(self, *, sucursal, cliente):
        venta = Venta.objects.create(
            usuario=self.cajero,
            cliente=cliente,
            sucursal=sucursal,
            subtotal=Decimal('150.00'),
            descuento_total=Decimal('0.00'),
            total=Decimal('150.00'),
        )
        DetalleVenta.objects.create(
            venta=venta,
            producto=self.producto,
            cantidad=1,
            precio_unitario=Decimal('150.00'),
            descuento_monto=Decimal('0.00'),
            subtotal=Decimal('0.00'),
            total_linea=Decimal('0.00'),
        )
        Pago.objects.create(venta=venta, metodo='EFECTIVO', monto=Decimal('150.00'))
        return venta

    def _url(self, venta):
        return reverse('pos:comprobante_pdf', args=[venta.id])

    def _generar_y_capturar_elementos(self, venta):
        capturado = {}
        original_build = SimpleDocTemplate.build

        def _build_espia(doc_self, elements, **kwargs):
            # copia: reportlab consume (vacia) la lista de flowables durante
            # el build, asi que guardar la referencia desnuda deja
            # `capturado['elements']` vacio para cuando el `with` termina.
            capturado['elements'] = list(elements)
            return original_build(doc_self, elements, **kwargs)

        with patch('reportlab.platypus.SimpleDocTemplate.build', _build_espia):
            generar_comprobante_venta(venta)

        return capturado['elements']

    def test_genera_pdf_inline(self):
        self.client.force_login(self.cajero)
        with self.settings(SUCURSAL_CODIGO='CMP-A'):
            res = self.client.get(self._url(self.venta))

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertIn('inline', res['Content-Disposition'])
        self.assertTrue(res.content.startswith(b'%PDF'))

    def test_venta_sin_cliente_no_revienta(self):
        venta_contado = self._crear_venta(sucursal=self.suc_a, cliente=None)
        self.client.force_login(self.cajero)
        with self.settings(SUCURSAL_CODIGO='CMP-A'):
            res = self.client.get(self._url(venta_contado))

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.content.startswith(b'%PDF'))

    def test_venta_anulada_incluye_banner_de_anulacion(self):
        self.venta.estado = 'ANULADA'
        self.venta.fecha_anulacion = timezone.now()
        self.venta.save(update_fields=['estado', 'fecha_anulacion'])

        elementos = self._generar_y_capturar_elementos(self.venta)
        textos = [getattr(el, 'text', '') for el in elementos]
        self.assertTrue(any('ANULADA' in texto for texto in textos))

    def test_encabezado_usa_la_configuracion_de_la_sucursal_de_la_venta(self):
        # COM-001: el encabezado se resuelve con la sucursal de la VENTA
        # (venta_b.sucursal = suc_b), nunca con el contexto/settings del
        # proceso -- `wraps` deja que la llamada real siga resolviendo el
        # config (el PDF se genera igual), solo espia con que sucursal se
        # invoco.
        venta_b = self._crear_venta(sucursal=self.suc_b, cliente=self.cliente)

        with patch(
            'apps.ventas.pdf_comprobante.config_para_documento',
            wraps=config_para_documento,
        ) as spy_config:
            generar_comprobante_venta(venta_b)

        spy_config.assert_called_once_with(self.suc_b)

    def test_sin_permiso_ventas_reimprimir_redirige(self):
        self.client.force_login(self.cajero_sin_permiso)
        with self.settings(SUCURSAL_CODIGO='CMP-A'):
            res = self.client.get(self._url(self.venta))

        self.assertEqual(res.status_code, 302)

    def test_no_se_abre_el_comprobante_de_otra_sucursal(self):
        venta_b = self._crear_venta(sucursal=self.suc_b, cliente=self.cliente)
        self.client.force_login(self.cajero)
        with self.settings(SUCURSAL_CODIGO='CMP-A'):
            res = self.client.get(self._url(venta_b))

        self.assertEqual(res.status_code, 404)

    def test_registra_auditoria_de_emision(self):
        self.client.force_login(self.cajero)
        with self.settings(SUCURSAL_CODIGO='CMP-A'):
            res = self.client.get(self._url(self.venta))

        self.assertEqual(res.status_code, 200)
        existe = Auditoria.objects.filter(
            accion=Auditoria.TipoAccion.COMPROBANTE_EMITIDO,
            metadata__numero_venta=self.venta.numero_venta,
        ).exists()
        self.assertTrue(existe)
