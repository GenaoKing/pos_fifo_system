"""
apps/reportes/tests/test_auditoria_reportes.py

Regresion de los hallazgos de `docs/exploracion/AUDITORIA_CODIGO_APPS_REPORTES.md`.
"""
import json
import os
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.inventario.models import Compra, DetalleCompra, Lote, MovimientoLote
from apps.permisos import testing as permisos_testing
from apps.productos.models import Categoria, Producto
from apps.reportes.models import BORRADOR, FINAL, CierreCaja, InventarioValorizado, TopProducto
from apps.reportes.report_manager import FechaFuturaError, ReporteManager
from apps.sucursales.models import Sucursal
from apps.ventas.models import DetalleVenta, Pago, Venta

User = get_user_model()


class ReportesTestCase(TestCase):
    """
    Base de los tests de reportes.

    Los PDFs se escriben en un directorio temporal, no en `private/` del
    checkout: un test que genera un cierre deja un documento financiero en el
    arbol de trabajo, y basta que alguien haga `git add -A` para commitearlo.
    """

    @classmethod
    def setUpClass(cls):
        cls._raiz_pdf = tempfile.mkdtemp(prefix='reportes-test-')
        cls._override = override_settings(REPORTES_PRIVATE_ROOT=cls._raiz_pdf)
        cls._override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._override.disable()
        shutil.rmtree(cls._raiz_pdf, ignore_errors=True)

    def setUp(self):
        cache.clear()

        self.sucursal_a = Sucursal.objects.create(
            codigo='RPT-A', nombre='Sucursal A', activa=True,
        )
        self.sucursal_b = Sucursal.objects.create(
            codigo='RPT-B', nombre='Sucursal B', activa=True,
        )

        self.admin = User.objects.create_user(
            username='admin_rpt', email='admin_rpt@test.local',
            password='pass', rol='ADMIN', activo=True,
        )
        self.cajera = User.objects.create_user(
            username='cajera_rpt', email='cajera_rpt@test.local',
            password='pass', rol='CAJERA', activo=True,
        )

        self.categoria = Categoria.objects.create(nombre='Reportes')
        self.producto = Producto.objects.create(
            sku='RPT-001', codigo_barras='RPT-001', nombre='Producto reportes',
            descripcion='', categoria=self.categoria,
            precio_venta=Decimal('100.00'), stock_minimo=1, activo=True,
            estado='nuevo', marca='', atributos={},
        )

    def tearDown(self):
        cache.clear()

    # -- helpers -------------------------------------------------------

    def _supervisor_de(self, sucursal, username='supervisor_rpt'):
        """Usuario con `reportes.sucursal.ver` acotado a UNA sucursal."""
        usuario = User.objects.create_user(
            username=username, email=f'{username}@test.local',
            password='pass', rol='CAJERA', activo=True,
        )
        permisos_testing.habilitar_cajero(
            usuario,
            permisos=['reportes.ver', 'reportes.sucursal.ver'],
            sucursal=sucursal,
        )
        return usuario

    def _venta(self, sucursal, total='100.00', usuario=None, cantidad=1):
        venta = Venta.objects.create(
            usuario=usuario or self.cajera,
            sucursal=sucursal,
            subtotal=Decimal(total),
            total=Decimal(total),
            estado='COMPLETADA',
            condicion_pago='CONTADO',
        )
        DetalleVenta.objects.create(
            venta=venta, producto=self.producto, cantidad=cantidad,
            precio_unitario=Decimal(total) / cantidad,
            subtotal=Decimal(total), total_linea=Decimal(total),
            costo_fifo=Decimal(total) / Decimal('4'),
        )
        Pago.objects.create(
            venta=venta, metodo='EFECTIVO', monto=Decimal(total),
        )
        return venta

    def _post(self, usuario, nombre, cuerpo):
        self.client.force_login(usuario)
        return self.client.post(
            reverse(nombre), data=json.dumps(cuerpo),
            content_type='application/json',
        )


class AlcancePorSucursalTests(ReportesTestCase):
    """RPT-003: un permiso acotado a A no consolida B."""

    def test_un_supervisor_de_a_no_ve_las_ventas_de_b(self):
        """
        La reproduccion de la auditoria: 100 en A, 250 en B, y la API devolvia
        dos ventas por 350 a un rol asignado solo a A.
        """
        self._venta(self.sucursal_a, '100.00')
        self._venta(self.sucursal_b, '250.00')
        supervisor = self._supervisor_de(self.sucursal_a)

        hoy = timezone.localdate().isoformat()
        resp = self._post(supervisor, 'reportes:api_ventas_periodo', {
            'fecha_inicio': hoy, 'fecha_fin': hoy,
        })

        datos = resp.json()
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(datos['totales']['cantidad'], 1)
        self.assertEqual(datos['totales']['total'], '100.00')

    def test_un_alcance_global_si_consolida(self):
        self._venta(self.sucursal_a, '100.00')
        self._venta(self.sucursal_b, '250.00')

        hoy = timezone.localdate().isoformat()
        resp = self._post(self.admin, 'reportes:api_ventas_periodo', {
            'fecha_inicio': hoy, 'fecha_fin': hoy,
        })

        datos = resp.json()
        self.assertEqual(datos['totales']['cantidad'], 2)
        self.assertEqual(datos['totales']['total'], '350.00')

    def test_consolidado_acotado_a_una_sucursal_no_consolida(self):
        """
        El matiz de RPT-003: consolidar es una facultad GLOBAL. El mismo permiso
        `reportes.consolidado.ver` asignado SOLO a A vale por A, no por todo.
        """
        self._venta(self.sucursal_a, '100.00')
        self._venta(self.sucursal_b, '250.00')

        usuario = User.objects.create_user(
            username='falso_global', email='falso@test.local',
            password='pass', rol='CAJERA', activo=True,
        )
        permisos_testing.habilitar_cajero(
            usuario,
            permisos=['reportes.ver', 'reportes.consolidado.ver'],
            sucursal=self.sucursal_a,
        )

        hoy = timezone.localdate().isoformat()
        datos = self._post(usuario, 'reportes:api_ventas_periodo', {
            'fecha_inicio': hoy, 'fecha_fin': hoy,
        }).json()

        self.assertEqual(datos['totales']['total'], '100.00')

    def test_la_lista_de_cajeros_se_acota_al_alcance(self):
        forastero = User.objects.create_user(
            username='cajero_de_b', email='cajero_b@test.local',
            password='pass', rol='CAJERA', activo=True,
        )
        permisos_testing.habilitar_cajero(
            forastero, permisos=['ventas.crear'], sucursal=self.sucursal_b,
        )
        supervisor = self._supervisor_de(self.sucursal_a)
        self.client.force_login(supervisor)

        resp = self.client.get(reverse('reportes:on_demand'))

        usernames = {c['username'] for c in resp.context['cajeros']}
        self.assertNotIn('cajero_de_b', usernames)

    def test_sin_ningun_permiso_de_reportes_no_se_entra(self):
        pelado = User.objects.create_user(
            username='sin_reportes', email='sin_rpt@test.local',
            password='pass', rol='CAJERA', activo=True,
        )
        permisos_testing.habilitar_cajero(pelado, permisos=['ventas.crear'])

        hoy = timezone.localdate().isoformat()
        resp = self._post(pelado, 'reportes:api_ventas_periodo', {
            'fecha_inicio': hoy, 'fecha_fin': hoy,
        })

        self.assertEqual(resp.status_code, 403)


class CorteHistoricoTests(ReportesTestCase):
    """RPT-002: una fecha de corte pasada representa el pasado."""

    def setUp(self):
        super().setUp()
        compra = Compra.objects.create(
            usuario=self.admin, proveedor='Proveedor RPT',
            numero_factura='FAC-RPT-001', total=Decimal('200.00'),
        )
        DetalleCompra.objects.create(
            compra=compra, producto=self.producto, cantidad=10,
            costo_unitario=Decimal('20.00'), subtotal=Decimal('200.00'),
        )
        self.lote = Lote.objects.get(producto=self.producto)

    def test_una_fecha_futura_se_rechaza(self):
        futuro = timezone.localdate() + timedelta(days=365)

        with self.assertRaises(FechaFuturaError):
            ReporteManager.generar_inventario_valorizado(fecha=futuro)

        self.assertFalse(InventarioValorizado.objects.filter(fecha=futuro).exists())

    def test_el_endpoint_rechaza_la_fecha_futura(self):
        futuro = (timezone.localdate() + timedelta(days=1)).isoformat()

        resp = self._post(self.admin, 'reportes:api_inventario_valorizado', {
            'fecha': futuro,
        })

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['codigo'], 'fecha_futura')

    def test_un_lote_creado_hoy_no_aparece_en_un_corte_de_2020(self):
        """
        La reproduccion de la auditoria: un lote creado hoy con 10 unidades
        aparecia en un corte etiquetado 2020-01-01.
        """
        snapshot = ReporteManager.generar_inventario_valorizado(
            fecha=timezone.localdate() - timedelta(days=400),
        )

        self.assertEqual(snapshot.total_productos, 0)
        self.assertEqual(snapshot.total_unidades, Decimal('0.00'))

    def test_una_venta_posterior_no_modifica_el_corte(self):
        """
        El criterio de aceptacion de la auditoria: compras, ventas y ajustes
        posteriores al corte no pueden cambiar su resultado.
        """
        ayer = timezone.localdate() - timedelta(days=1)
        # El lote existia ayer con sus 10 unidades.
        Lote.objects.filter(pk=self.lote.pk).update(
            fecha_creacion=timezone.now() - timedelta(days=3),
        )
        MovimientoLote.objects.filter(lote=self.lote).update(
            fecha_creacion=timezone.now() - timedelta(days=3),
        )

        primero = ReporteManager.generar_inventario_valorizado(fecha=ayer)
        self.assertEqual(primero.total_unidades, Decimal('10.00'))

        # Consumo de hoy: 6 unidades.
        Lote.objects.filter(pk=self.lote.pk).update(cantidad_actual=4)
        MovimientoLote.objects.create(
            lote=self.lote, tipo='VENTA', cantidad=6,
            cantidad_anterior=10, cantidad_nueva=4,
            referencia_tipo='Venta', referencia_id=1, usuario=self.cajera,
        )

        segundo = ReporteManager.generar_inventario_valorizado(
            fecha=ayer, recalcular=True,
        )
        self.assertEqual(segundo.total_unidades, Decimal('10.00'))

    def test_el_corte_de_hoy_si_refleja_el_stock_actual(self):
        Lote.objects.filter(pk=self.lote.pk).update(cantidad_actual=4)

        snapshot = ReporteManager.generar_inventario_valorizado(
            fecha=timezone.localdate(), recalcular=True,
        )

        self.assertEqual(snapshot.total_unidades, Decimal('4.00'))

    def test_la_respuesta_declara_que_snapshot_la_sustenta(self):
        resp = self._post(self.admin, 'reportes:api_inventario_valorizado', {
            'fecha': timezone.localdate().isoformat(),
        })

        datos = resp.json()
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIsNotNone(datos['snapshot_id'])
        self.assertIsNotNone(datos['momento_corte'])
        self.assertFalse(datos['historico'])

    def test_respuesta_y_fila_persistida_coinciden(self):
        """
        Antes la vista calculaba por su cuenta Y llamaba al manager: para la
        misma fecha, la respuesta y la fila guardada podian diferir.
        """
        datos = self._post(self.admin, 'reportes:api_inventario_valorizado', {
            'fecha': timezone.localdate().isoformat(),
        }).json()

        snapshot = InventarioValorizado.objects.get(pk=datos['snapshot_id'])
        self.assertEqual(
            datos['resumen']['valor_total'], str(snapshot.valor_total_inventario),
        )
        self.assertEqual(
            datos['resumen']['total_unidades'], str(snapshot.total_unidades),
        )


class CicloDeVidaDelResumenTests(ReportesTestCase):
    """RPT-004: un borrador se recalcula; solo lo final queda congelado."""

    def test_una_venta_posterior_se_incorpora_al_borrador(self):
        """
        La reproduccion de la auditoria: cierre con 100, se agrega otra de 50,
        y el manager devolvia el mismo ID con total 100.
        """
        hoy = timezone.localdate()
        self._venta(self.sucursal_a, '100.00')
        primero = ReporteManager.generar_cierre_diario(fecha=hoy)
        self.assertEqual(primero.total_ventas, Decimal('100.00'))

        self._venta(self.sucursal_a, '50.00')
        segundo = ReporteManager.generar_cierre_diario(fecha=hoy)

        self.assertEqual(segundo.pk, primero.pk)
        self.assertEqual(segundo.total_ventas, Decimal('150.00'))
        self.assertEqual(segundo.version, 2)

    def test_un_resumen_final_no_se_recalcula_solo(self):
        hoy = timezone.localdate()
        self._venta(self.sucursal_a, '100.00')
        cierre = ReporteManager.generar_cierre_diario(fecha=hoy)
        cierre.finalizar()

        self._venta(self.sucursal_a, '50.00')
        vuelto = ReporteManager.generar_cierre_diario(fecha=hoy)

        self.assertEqual(vuelto.estado, FINAL)
        self.assertEqual(vuelto.total_ventas, Decimal('100.00'))
        self.assertEqual(vuelto.version, 1)

    def test_forzar_recalcula_y_versiona_lo_final(self):
        hoy = timezone.localdate()
        self._venta(self.sucursal_a, '100.00')
        ReporteManager.generar_cierre_diario(fecha=hoy).finalizar()
        self._venta(self.sucursal_a, '50.00')

        vuelto = ReporteManager.generar_cierre_diario(fecha=hoy, forzar=True)

        self.assertEqual(vuelto.total_ventas, Decimal('150.00'))
        self.assertEqual(vuelto.version, 2)

    def test_nace_borrador(self):
        cierre = ReporteManager.generar_cierre_diario()
        self.assertEqual(cierre.estado, BORRADOR)

    def test_una_fecha_futura_se_rechaza(self):
        with self.assertRaises(FechaFuturaError):
            ReporteManager.generar_cierre_diario(
                fecha=timezone.localdate() + timedelta(days=1),
            )

    def test_el_resumen_reporta_el_arqueo_del_dia(self):
        """RPT-008: el documento dice si el dia quedo conciliado."""
        from apps.caja.models import Caja, TurnoCaja

        caja = Caja.objects.create(nombre='Caja RPT', activa=True)
        TurnoCaja.objects.create(
            caja=caja, usuario=self.cajera, fondo_apertura=Decimal('100.00'),
        )

        cierre = ReporteManager.generar_cierre_diario()

        self.assertEqual(cierre.turnos_abiertos, 1)
        self.assertFalse(cierre.conciliado)


class TopProductosTests(ReportesTestCase):
    """RPT-009: el snapshot se persiste y el margen es real."""

    def test_el_snapshot_se_crea(self):
        """
        Antes el manager lanzaba FieldError SIEMPRE (`Sum('total')` sobre un
        campo que se llama `total_linea`) y el endpoint lo silenciaba: la
        respuesta decia success=true y la tabla quedaba vacia.
        """
        self._venta(self.sucursal_a, '100.00', cantidad=2)
        hoy = timezone.localdate().isoformat()

        resp = self._post(self.admin, 'reportes:api_top_productos', {
            'fecha_inicio': hoy, 'fecha_fin': hoy,
        })

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(len(resp.json()['productos']), 1)
        self.assertEqual(TopProducto.objects.count(), 1)

    def test_el_margen_no_es_un_placeholder(self):
        """
        Ingreso 100, costo FIFO 25 -> margen 75%. El valor viejo era un 25.00
        fijo que no dependia de nada.
        """
        self._venta(self.sucursal_a, '100.00')

        ReporteManager.generar_top_productos(
            fecha_inicio=timezone.localdate(), fecha_fin=timezone.localdate(),
        )

        top = TopProducto.objects.get()
        self.assertEqual(top.costo_total, Decimal('25.00'))
        self.assertEqual(top.margen_promedio, Decimal('75.00'))

    def test_la_respuesta_sale_del_snapshot(self):
        self._venta(self.sucursal_a, '100.00')
        hoy = timezone.localdate().isoformat()

        datos = self._post(self.admin, 'reportes:api_top_productos', {
            'fecha_inicio': hoy, 'fecha_fin': hoy,
        }).json()

        top = TopProducto.objects.get()
        self.assertEqual(datos['productos'][0]['total'], str(top.total_ventas))
        self.assertEqual(datos['productos'][0]['margen'], str(top.margen_promedio))

    def test_regenerar_no_duplica(self):
        """RPT-013: la identidad del snapshot es unica."""
        self._venta(self.sucursal_a, '100.00')
        hoy = timezone.localdate()

        for _ in range(3):
            ReporteManager.generar_top_productos(fecha_inicio=hoy, fecha_fin=hoy)

        self.assertEqual(TopProducto.objects.count(), 1)


class DocumentoPrivadoTests(ReportesTestCase):
    """RPT-001 y RPT-007: el PDF financiero no es media publica."""

    def test_el_pdf_no_se_escribe_bajo_media_root(self):
        from django.conf import settings

        from apps.reportes.pdf_generator import PDFGenerator

        cierre = ReporteManager.generar_cierre_diario()
        ruta = PDFGenerator.generar_cierre_caja(cierre.id)

        media = os.path.abspath(str(settings.MEDIA_ROOT))
        self.assertFalse(os.path.abspath(ruta).startswith(media + os.sep))
        os.remove(ruta)

    def test_el_nombre_no_se_adivina_por_fecha(self):
        from apps.reportes.pdf_generator import PDFGenerator

        cierre = ReporteManager.generar_cierre_diario()
        ruta = PDFGenerator.generar_cierre_caja(cierre.id)

        nombre = os.path.basename(ruta)
        fecha = cierre.fecha.strftime('%Y%m%d')
        self.assertNotEqual(nombre, f'cierre_{fecha}.pdf')
        self.assertIn(str(cierre.pk), nombre)
        os.remove(ruta)

    def test_dos_cierres_no_comparten_ruta(self):
        from apps.reportes.pdf_generator import PDFGenerator

        uno = ReporteManager.generar_cierre_diario()
        otro = ReporteManager.generar_cierre_diario(
            fecha=timezone.localdate() - timedelta(days=1),
        )

        ruta_uno = PDFGenerator.generar_cierre_caja(uno.id)
        ruta_otro = PDFGenerator.generar_cierre_caja(otro.id)

        self.assertNotEqual(ruta_uno, ruta_otro)
        os.remove(ruta_uno)
        os.remove(ruta_otro)

    def test_media_no_sirve_la_carpeta_de_reportes(self):
        """
        Aunque quede un PDF viejo en MEDIA_ROOT, `/media/reportes/...` no lo
        entrega. Antes resolvia directo a `serve`, sin login ni permiso.
        """
        resp = self.client.get('/media/reportes/cierres/cierre_20260820.pdf')
        self.assertEqual(resp.status_code, 404)

    def test_la_descarga_exige_permiso(self):
        cierre = ReporteManager.generar_cierre_diario()
        pelado = User.objects.create_user(
            username='sin_pdf', email='sin_pdf@test.local',
            password='pass', rol='CAJERA', activo=True,
        )
        permisos_testing.habilitar_cajero(pelado, permisos=['ventas.crear'])
        self.client.force_login(pelado)

        resp = self.client.get(
            reverse('reportes:descargar_pdf_cierre', args=[cierre.id]),
        )

        self.assertEqual(resp.status_code, 403)

    def test_el_consolidado_solo_lo_baja_quien_consolida(self):
        cierre = ReporteManager.generar_cierre_diario()  # sucursal None
        supervisor = self._supervisor_de(self.sucursal_a)
        self.client.force_login(supervisor)

        resp = self.client.get(
            reverse('reportes:descargar_pdf_cierre', args=[cierre.id]),
        )

        self.assertEqual(resp.status_code, 403)


class ContratoDeErroresTests(ReportesTestCase):
    """RPT-016: los errores tienen forma estable y no filtran el interior."""

    def test_json_invalido_es_400_con_codigo(self):
        self.client.force_login(self.admin)

        resp = self.client.post(
            reverse('reportes:api_ventas_periodo'),
            data='no soy json', content_type='application/json',
        )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['codigo'], 'json_invalido')

    def test_una_fecha_mal_formada_no_devuelve_500(self):
        resp = self._post(self.admin, 'reportes:api_ventas_periodo', {
            'fecha_inicio': 'ayer', 'fecha_fin': 'hoy',
        })

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['codigo'], 'datos_invalidos')

    def test_el_pdf_fallido_se_declara_en_la_respuesta(self):
        """
        Antes `except Exception: pass`: la UI decia exito sin documento y nadie
        se enteraba.
        """
        from unittest.mock import patch

        with patch(
            'apps.reportes.views.PDFGenerator.generar_cierre_caja',
            side_effect=RuntimeError('disco lleno'),
        ):
            resp = self._post(self.admin, 'reportes:api_cierre_manual', {
                'fecha': timezone.localdate().isoformat(),
            })

        datos = resp.json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(datos['estado_generacion'], 'parcial')
        self.assertTrue(datos['advertencias'])
        self.assertFalse(datos['cierre']['tiene_pdf'])


class ContratoDePresentacionTests(ReportesTestCase):
    """RPT-011: pantalla, API y PDF hablan del mismo cierre."""

    def test_la_api_expone_todos_los_componentes(self):
        self._venta(self.sucursal_a, '100.00')

        datos = self._post(self.admin, 'reportes:api_cierre_manual', {
            'fecha': timezone.localdate().isoformat(),
        }).json()['cierre']

        for campo in (
            'total_ventas', 'total_efectivo', 'total_transferencia',
            'total_tarjeta', 'total_cobros_cxc', 'total_flujo',
            'total_descuentos', 'total_anulaciones', 'arqueo',
        ):
            with self.subTest(campo=campo):
                self.assertIn(campo, datos)

    def test_el_flujo_es_la_suma_de_sus_componentes(self):
        self._venta(self.sucursal_a, '100.00')

        datos = self._post(self.admin, 'reportes:api_cierre_manual', {
            'fecha': timezone.localdate().isoformat(),
        }).json()['cierre']

        suma = sum(Decimal(datos[c]) for c in (
            'total_efectivo', 'total_transferencia',
            'total_tarjeta', 'total_cobros_cxc',
        ))
        self.assertEqual(Decimal(datos['total_flujo']), suma)


class SerializacionSeguraTests(ReportesTestCase):
    """RPT-010: la lista de cajeros no puede ejecutar JavaScript."""

    def test_un_username_hostil_no_cierra_el_bloque_script(self):
        User.objects.create_user(
            username='</script><script>window.__audit_xss=1</script>',
            email='xss@test.local', password='pass', rol='CAJERA', activo=True,
        )
        self.client.force_login(self.admin)

        html = self.client.get(reverse('reportes:on_demand')).content.decode()

        self.assertNotIn('<script>window.__audit_xss=1</script>', html)
        self.assertIn('json_script', 'json_script')  # documenta el mecanismo
        self.assertIn('id="reportes-cajeros"', html)

    def test_la_plantilla_ya_no_usa_safe_para_los_cajeros(self):
        import pathlib

        from django.conf import settings

        fuente = (
            pathlib.Path(settings.BASE_DIR) / 'templates' / 'reportes'
            / 'on_demand.html'
        ).read_text(encoding='utf-8')

        self.assertNotIn('{{ cajeros|safe }}', fuente)
        self.assertIn('cajeros|json_script', fuente)


class ComandoAutomaticoTests(ReportesTestCase):
    """RPT-005: la automatizacion termina bien y deja auditoria valida."""

    def test_el_comando_corre_y_audita(self):
        from io import StringIO

        from django.core.management import call_command

        from apps.auditoria.models import Auditoria

        self._venta(self.sucursal_a, '100.00')
        salida = StringIO()

        call_command('generar_cierre_diario', stdout=salida)

        self.assertEqual(CierreCaja.objects.count(), 1)
        self.assertTrue(
            Auditoria.objects.filter(
                accion=Auditoria.TipoAccion.CIERRE_DIARIO,
            ).exists()
        )
        for cierre in CierreCaja.objects.all():
            if cierre.archivo_pdf and os.path.exists(cierre.archivo_pdf):
                os.remove(cierre.archivo_pdf)

    def test_es_reintentable(self):
        from io import StringIO

        from django.core.management import call_command

        for _ in range(2):
            call_command('generar_cierre_diario', stdout=StringIO())

        cierre = CierreCaja.objects.get()
        self.assertEqual(cierre.version, 2)
        if cierre.archivo_pdf and os.path.exists(cierre.archivo_pdf):
            os.remove(cierre.archivo_pdf)

    def test_finalizar_congela(self):
        from io import StringIO

        from django.core.management import call_command

        call_command('generar_cierre_diario', '--finalizar', stdout=StringIO())

        cierre = CierreCaja.objects.get()
        self.assertEqual(cierre.estado, FINAL)
        if cierre.archivo_pdf and os.path.exists(cierre.archivo_pdf):
            os.remove(cierre.archivo_pdf)

    def test_una_fecha_invalida_no_muta_nada(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command('generar_cierre_diario', '--fecha', 'manana')

        self.assertEqual(CierreCaja.objects.count(), 0)


class TransaccionEnLaBaseCorrectaTests(ReportesTestCase):
    """RPT-006: la transaccion se abre donde el router escribe."""

    def test_el_manager_resuelve_el_alias_por_router(self):
        from unittest.mock import patch

        from apps.reportes import report_manager

        with patch.object(
            report_manager.transaction, 'atomic', wraps=report_manager.transaction.atomic,
        ) as atomic:
            ReporteManager.generar_cierre_diario()

        # Lo que importa no es el valor del alias, sino que se pase: sin
        # `using`, Django abre la transaccion en `default` mientras el router
        # manda las escrituras al alias del tenant.
        self.assertTrue(atomic.call_args_list)
        for llamada in atomic.call_args_list:
            self.assertIn('using', llamada.kwargs)
            self.assertIsNotNone(llamada.kwargs['using'])
