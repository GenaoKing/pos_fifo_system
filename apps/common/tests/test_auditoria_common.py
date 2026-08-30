"""
apps/common/tests/test_auditoria_common.py

Regresion de los hallazgos de `docs/exploracion/AUDITORIA_CODIGO_APPS_COMMON.md`.
"""
from decimal import Decimal
from io import BytesIO

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.common.pdf.standard import (
    MAX_TEXTO,
    SIMBOLO_MONEDA,
    ImporteInvalido,
    clean,
    document,
    footer_canvas,
    money,
)
from apps.configuracion.models import ConfiguracionNegocio
from apps.configuracion.utils import config_de_sucursal, config_para_documento
from apps.permisos import testing as permisos_testing
from apps.sucursales.models import Sucursal


class ImportesTests(TestCase):
    """COM-002 y COM-003."""

    def test_un_importe_corrupto_no_se_imprime_como_cero(self):
        """
        La reproduccion: `money('importe-corrupto')` devolvia exactamente
        `$0.00`. Un dato derivado corrupto se presentaba como ausencia REAL de
        deuda, descuento o pago: el PDF quedaba bien formado y materialmente
        falso.
        """
        with self.assertRaises(ImporteInvalido):
            money('importe-corrupto')

    def test_un_objeto_incompatible_tampoco(self):
        with self.assertRaises(ImporteInvalido):
            money(object())

    def test_los_no_finitos_se_rechazan(self):
        """Salian `$NaN`, `$Infinity` y `$-Infinity` como campos monetarios."""
        for valor in (Decimal('NaN'), Decimal('Infinity'), Decimal('-Infinity')):
            with self.subTest(valor=str(valor)):
                with self.assertRaises(ImporteInvalido):
                    money(valor)

    def test_el_cero_real_sigue_siendo_cero(self):
        self.assertEqual(money(0), f'{SIMBOLO_MONEDA}0.00')
        self.assertEqual(money(Decimal('0.00')), f'{SIMBOLO_MONEDA}0.00')
        self.assertEqual(money(None), f'{SIMBOLO_MONEDA}0.00')

    def test_la_moneda_es_inequivoca(self):
        """
        `$1,234.50` con separadores estadounidenses no distingue DOP de USD en
        un documento que alguien usa para cobrar.
        """
        self.assertEqual(money(Decimal('1234.5')), 'RD$1,234.50')
        self.assertTrue(money(Decimal('1')).startswith('RD$'))

    def test_un_negativo_se_formatea_igual(self):
        self.assertEqual(money(Decimal('-50')), 'RD$-50.00')


class TextoAcotadoTests(TestCase):
    """COM-004: un texto sin limite no impide generar el documento."""

    def test_un_texto_enorme_se_trunca(self):
        """
        Los datos que llegan aca incluyen notas y direcciones en `TextField`,
        sin limite. Un texto suficientemente largo hacia que ReportLab lanzara
        `LayoutError` y el documento entero no se generaba.
        """
        resultado = clean('x' * (MAX_TEXTO * 3))

        self.assertLessEqual(len(resultado), MAX_TEXTO)
        self.assertIn('[...]', resultado)

    def test_un_texto_normal_no_se_toca(self):
        self.assertEqual(clean('Nota corta'), 'Nota corta')

    def test_se_sigue_escapando(self):
        self.assertNotIn('<b>', clean('<b>negrita</b>'))


class PiePaginaTests(TestCase):
    """COM-010 y COM-011."""

    class _Canvas:
        def __init__(self):
            self.textos = []
            self.lineas = []

        def saveState(self):
            pass

        def restoreState(self):
            pass

        def setStrokeColor(self, *a):
            pass

        def setLineWidth(self, *a):
            pass

        def setFont(self, *a):
            pass

        def setFillColor(self, *a):
            pass

        def line(self, x1, y1, x2, y2):
            self.lineas.append((x1, y1, x2, y2))

        def drawString(self, x, y, texto):
            self.textos.append((x, texto))

        def drawCentredString(self, x, y, texto):
            self.textos.append((x, texto))

        def drawRightString(self, x, y, texto):
            self.textos.append((x, texto))

        def getPageNumber(self):
            return 1

    class _Doc:
        def __init__(self, pagesize):
            self.pagesize = pagesize

    @override_settings(TIME_ZONE='America/Santo_Domingo', USE_TZ=True)
    def test_el_sello_usa_la_hora_local(self):
        """
        Decia `datetime.now()`, la hora del HOST. En un contenedor en UTC, un
        cierre generado a las 8 PM en Santo Domingo se sellaba a medianoche del
        dia siguiente y se contradecia con la fecha del propio reporte.
        """
        canvas = self._Canvas()
        footer_canvas(canvas, self._Doc((612.0, 792.0)))

        sello = next(t for _, t in canvas.textos if t.startswith('Generado:'))
        esperado = timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M')
        self.assertIn(esperado[:10], sello)

    def test_el_pie_no_usa_la_hora_del_host(self):
        """
        Aserción estructural: comparar el texto no alcanza cuando el host ya
        esta en la zona del negocio —coinciden por accidente y el test pasaria
        con el bug puesto—. Lo que no puede volver es `datetime.now()`.
        """
        import inspect

        from apps.common.pdf import standard

        fuente = inspect.getsource(standard.footer_canvas)
        self.assertNotIn('datetime.now()', fuente)
        self.assertIn('timezone.localtime', fuente)

    def test_el_pie_usa_el_ancho_real_del_documento(self):
        """
        Las coordenadas salian de la constante Carta del modulo. En apaisado, la
        linea y los textos quedaban a dos tercios del ancho real.
        """
        apaisado = self._Canvas()
        footer_canvas(apaisado, self._Doc((792.0, 612.0)))

        _, _, x_final, _ = apaisado.lineas[0]
        self.assertGreater(x_final, 700)

    def test_sin_pagesize_cae_a_carta(self):
        canvas = self._Canvas()
        footer_canvas(canvas, self._Doc(None))

        self.assertTrue(canvas.lineas)


class EncabezadoPorSucursalTests(TestCase):
    """COM-001: el documento se encabeza con la identidad de SU sucursal."""

    def setUp(self):
        cache.clear()
        self.negocio = permisos_testing.crear_negocio('Negocio COM')
        self.suc_a = Sucursal.objects.create(
            codigo='PDF-A', nombre='Tienda A', activa=True, negocio=self.negocio,
        )
        self.suc_b = Sucursal.objects.create(
            codigo='PDF-B', nombre='Tienda B', activa=True, negocio=self.negocio,
        )
        self.config_a = ConfiguracionNegocio.objects.create(
            sucursal=self.suc_a, nombre_negocio='Identidad A', rnc='101',
        )
        self.config_b = ConfiguracionNegocio.objects.create(
            sucursal=self.suc_b, nombre_negocio='Identidad B', rnc='202',
        )

    def tearDown(self):
        cache.clear()

    def test_cada_sucursal_resuelve_la_suya(self):
        self.assertEqual(config_de_sucursal(self.suc_a).rnc, '101')
        self.assertEqual(config_de_sucursal(self.suc_b).rnc, '202')

    def test_el_documento_no_depende_de_settings(self):
        """
        La reproduccion: con `SUCURSAL_CODIGO=PDF-A`, un documento cuya sucursal
        era B se encabezaba con la configuracion de A — nombre, RNC, direccion,
        telefono y logo de otra tienda.
        """
        with self.settings(SUCURSAL_CODIGO='PDF-A'):
            cache.clear()
            config = config_para_documento(self.suc_b)

        self.assertEqual(config.nombre_negocio, 'Identidad B')
        self.assertEqual(config.rnc, '202')

    def test_alternar_a_y_b_en_el_mismo_proceso(self):
        """El criterio de aceptacion del informe, textual."""
        with self.settings(SUCURSAL_CODIGO='PDF-A'):
            cache.clear()
            for sucursal, rnc in (
                (self.suc_a, '101'), (self.suc_b, '202'),
                (self.suc_a, '101'), (self.suc_b, '202'),
            ):
                with self.subTest(sucursal=sucursal.codigo):
                    self.assertEqual(config_para_documento(sucursal).rnc, rnc)

    def test_un_documento_consolidado_cae_al_contexto(self):
        """Un cierre sin sucursal no documenta una tienda sino todas."""
        with self.settings(SUCURSAL_CODIGO='PDF-A'):
            cache.clear()
            self.assertEqual(config_para_documento(None).rnc, '101')

    def test_una_sucursal_sin_configuracion_cae_al_contexto(self):
        huerfana = Sucursal.objects.create(
            codigo='PDF-C', nombre='Sin config', activa=True, negocio=self.negocio,
        )

        with self.settings(SUCURSAL_CODIGO='PDF-A'):
            cache.clear()
            self.assertEqual(config_para_documento(huerfana).rnc, '101')

    def test_los_generadores_ya_no_resuelven_por_settings(self):
        """
        El invariante: ninguno de los cuatro generadores puede volver a llamar
        `get_config()` para armar su encabezado.
        """
        import inspect

        from apps.cotizaciones import pdf_generator as cot
        from apps.cuentas_por_cobrar import pdf_generator as cxc
        from apps.reportes import pdf_generator as rep
        from apps.ventas import pdf_financiacion as fin

        for modulo in (cot, cxc, rep, fin):
            with self.subTest(modulo=modulo.__name__):
                fuente = inspect.getsource(modulo)
                self.assertNotIn('get_config()', fuente)
                self.assertIn('config_para_documento(', fuente)


class DocumentoSeGeneraTests(TestCase):
    """El contrato basico sigue funcionando tras los cambios."""

    def test_un_pdf_minimo_se_construye(self):
        from apps.common.pdf.standard import (
            business_header,
            document_title,
            standard_table,
        )

        class Dummy:
            nombre_negocio = 'Prueba'
            rnc = '000'
            telefono = ''
            direccion = ''
            logo = None

        buffer = BytesIO()
        doc = document(buffer)
        elementos = []
        elementos.extend(business_header(Dummy()))
        elementos.extend(document_title('Prueba'))
        elementos.append(standard_table(
            ['Concepto', 'Monto'],
            [['Servicio', money(Decimal('1500.00'))]],
        ))
        doc.build(elementos)

        self.assertTrue(buffer.getvalue().startswith(b'%PDF'))
