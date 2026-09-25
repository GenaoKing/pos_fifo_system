"""
apps/common/tests/test_auditoria_common.py

Regresion de los hallazgos de `docs/exploracion/AUDITORIA_CODIGO_APPS_COMMON.md`.
"""
from decimal import Decimal
from io import BytesIO

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image as PILImage

from apps.common.pdf.standard import (
    LOGO_MAX_BYTES,
    MAX_TEXTO,
    SIMBOLO_MONEDA,
    TABLA_MAX_FILAS,
    ImporteInvalido,
    TablaInvalida,
    _logo_flowable,
    _logo_source,
    business_header,
    clean,
    document,
    footer_canvas,
    info_grid,
    money,
    standard_table,
    totals_table,
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
        El invariante: ningun generador de PDF puede volver a llamar
        `get_config()` para armar su encabezado.
        """
        import inspect

        from apps.cotizaciones import pdf_generator as cot
        from apps.cuentas_por_cobrar import pdf_generator as cxc
        from apps.reportes import pdf_generator as rep
        from apps.ventas import pdf_comprobante as comp
        from apps.ventas import pdf_financiacion as fin

        for modulo in (cot, cxc, rep, fin, comp):
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


def _png(size, color='white'):
    buffer = BytesIO()
    PILImage.new('RGB', size, color=color).save(buffer, format='PNG')
    buffer.seek(0)
    return buffer


class _LogoStub:
    """FieldFile falso para ejercitar `_logo_source` sin storage real."""

    def __init__(self, contenido=None, *, size=None, fallar_size=False,
                 fallar_open=False, fallar_read=False, path_excepcion=NotImplementedError):
        self.name = 'demo/config/logo.png'
        self._contenido = contenido or b''
        self._pos = 0
        self._size = size
        self._fallar_size = fallar_size
        self._fallar_open = fallar_open
        self._fallar_read = fallar_read
        self._path_excepcion = path_excepcion

    def __bool__(self):
        return True

    @property
    def path(self):
        raise self._path_excepcion('sin ruta local')

    @property
    def size(self):
        if self._fallar_size:
            raise TimeoutError('blob no disponible')
        if self._size is not None:
            return self._size
        return len(self._contenido)

    def open(self, mode='rb'):
        if self._fallar_open:
            raise TimeoutError('blob no disponible')
        self._pos = 0
        return self

    def read(self, size=-1):
        if self._fallar_read:
            raise TimeoutError('blob no disponible')
        if size is None or size < 0:
            datos = self._contenido[self._pos:]
            self._pos = len(self._contenido)
        else:
            datos = self._contenido[self._pos:self._pos + size]
            self._pos += len(datos)
        return datos

    def close(self):
        pass


class LogoRobustezTests(TestCase):
    """COM-007, COM-008 y COM-009: el logo se valida y se degrada, no rompe."""

    def test_logo_corrupto_se_descarta_sin_reventar(self):
        """
        Un blob `b'no-es-una-imagen'` llegaba directo a `Image()` de ReportLab
        y lanzaba `UnidentifiedImageError` sin capturar: TODO el documento
        fallaba por un logo dañado.
        """
        logo = _LogoStub(b'no-es-una-imagen')

        class Config:
            nombre_negocio = 'Con logo roto'
            rnc = ''
            telefono = ''
            direccion = ''

        Config.logo = logo

        with self.assertLogs('common.pdf', level='WARNING') as registro:
            fuente = _logo_source(Config())
        self.assertIsNone(fuente)
        self.assertTrue(any('invalido o corrupto' in m for m in registro.output))

        # El documento completo se sigue generando, sin logo.
        buffer = BytesIO()
        doc = document(buffer)
        doc.build(business_header(Config()))
        self.assertTrue(buffer.getvalue().startswith(b'%PDF'))

    def test_fallo_de_storage_se_distingue_de_ausencia_y_se_registra(self):
        """
        Un `TimeoutError` al abrir el blob se tragaba entero y `_logo_source`
        devolvia `None` exactamente igual que "no hay logo configurado":
        ni el operador ni monitoreo se enteraban de que Azure Blob fallo.
        """
        logo = _LogoStub(fallar_open=True)

        class Config:
            logo_obj = logo

        Config.logo = logo

        with self.assertLogs('common.pdf', level='WARNING') as registro:
            fuente = _logo_source(Config())
        self.assertIsNone(fuente)
        self.assertTrue(any('No se pudo abrir el logo remoto' in m for m in registro.output))

    def test_ausencia_de_logo_no_registra_nada(self):
        """Un negocio sin logo configurado no es un fallo: no debe alertar."""
        class Config:
            logo = None

        with self.assertNoLogs('common.pdf', level='WARNING'):
            fuente = _logo_source(Config())
        self.assertIsNone(fuente)

    def test_logo_remoto_declarado_sobre_el_limite_se_rechaza_sin_leerlo(self):
        """
        `ConfiguracionNegocio.logo` no declaraba validator de tamaño: el
        tamaño del archivo que alguien subiera determinaba cuanta memoria
        consumia el worker en CADA documento generado con ese logo.
        """
        logo = _LogoStub(b'x' * 100, size=LOGO_MAX_BYTES + 1)

        class Config:
            pass

        Config.logo = logo

        with self.assertLogs('common.pdf', level='WARNING') as registro:
            fuente = _logo_source(Config())
        self.assertIsNone(fuente)
        self.assertTrue(any('supera el limite' in m for m in registro.output))
        # Rechazado por el tamaño DECLARADO, sin intentar leer el contenido.
        self.assertEqual(logo._pos, 0)

    def test_logo_remoto_sin_size_declarado_se_acota_durante_la_lectura(self):
        """
        Defensa en profundidad: aunque el storage no reporte `.size` (o
        mienta), la lectura por bloques igual corta al llegar al limite en
        vez de materializar el archivo completo en memoria.
        """
        contenido_grande = b'x' * (LOGO_MAX_BYTES + 1024)

        class _SinSize(_LogoStub):
            @property
            def size(self):
                raise AttributeError('este backend no expone size')

        class Config:
            pass

        Config.logo = _SinSize(contenido_grande)

        fuente = _logo_source(Config())
        self.assertIsNone(fuente)

    def test_logo_valido_se_acepta(self):
        logo = _LogoStub(_png((40, 40)).getvalue())

        class Config:
            pass

        Config.logo = logo

        fuente = _logo_source(Config())
        self.assertIsInstance(fuente, BytesIO)


class LogoProporcionTests(TestCase):
    """COM-014: un logo no cuadrado ya no se deforma a 0.9x0.9in."""

    def test_logo_horizontal_mantiene_proporcion(self):
        origen = _png((200, 100))
        flowable = _logo_flowable(origen)

        self.assertAlmostEqual(flowable.drawWidth, 0.9 * 72, places=2)
        self.assertAlmostEqual(flowable.drawHeight, 0.45 * 72, places=2)

    def test_logo_vertical_mantiene_proporcion(self):
        origen = _png((100, 200))
        flowable = _logo_flowable(origen)

        self.assertAlmostEqual(flowable.drawHeight, 0.9 * 72, places=2)
        self.assertAlmostEqual(flowable.drawWidth, 0.45 * 72, places=2)

    def test_logo_cuadrado_sigue_ocupando_la_caja_completa(self):
        origen = _png((80, 80))
        flowable = _logo_flowable(origen)

        self.assertAlmostEqual(flowable.drawWidth, 0.9 * 72, places=2)
        self.assertAlmostEqual(flowable.drawHeight, 0.9 * 72, places=2)


class TablaFormaTests(TestCase):
    """COM-005: la forma de la tabla se valida antes de construir el PDF."""

    def test_una_fila_con_columnas_de_mas_no_desborda_en_silencio(self):
        """
        La reproduccion del hallazgo: dos headers y una fila de tres valores
        producian tres columnas de 259.2pt (777.6pt totales sobre los 518.4pt
        disponibles) sin que nada lo avisara.
        """
        with self.assertRaises(TablaInvalida):
            standard_table(['Producto', 'Total'], [['Vaso', '10', 'sobra']])

    def test_una_fila_con_columnas_de_menos_tambien_se_rechaza(self):
        with self.assertRaises(TablaInvalida):
            standard_table(['Producto', 'Cant.', 'Total'], [['Vaso', '10']])

    def test_aligns_con_cantidad_incorrecta_se_rechaza(self):
        with self.assertRaises(TablaInvalida):
            standard_table(['A', 'B'], [['1', '2']], aligns=['LEFT'])

    def test_status_col_fuera_de_rango_se_rechaza(self):
        with self.assertRaises(TablaInvalida):
            standard_table(['A', 'B'], [['1', '2']], status_col=5)

    def test_una_tabla_valida_sigue_generando_pdf(self):
        tabla = standard_table(
            ['Producto', 'Cant.', 'Total'],
            [['Vaso', '10', money(Decimal('100'))]],
            col_widths=[0.5, 0.2, 0.3],
        )
        buffer = BytesIO()
        doc = document(buffer)
        doc.build([tabla])
        self.assertTrue(buffer.getvalue().startswith(b'%PDF'))


class EstructurasVaciasTests(TestCase):
    """COM-006: precondiciones invalidas fallan con un error propio, no crudo."""

    def test_info_grid_con_fila_vacia_no_revienta_por_zero_division(self):
        with self.assertRaises(TablaInvalida):
            info_grid([[]])

    def test_totals_table_vacia_se_rechaza(self):
        with self.assertRaises(TablaInvalida):
            totals_table([])

    def test_col_widths_con_cero_se_rechaza(self):
        with self.assertRaises(TablaInvalida):
            standard_table(['A', 'B'], [['1', '2']], col_widths=[0, 300])

    def test_col_widths_negativo_se_rechaza(self):
        with self.assertRaises(TablaInvalida):
            standard_table(['A', 'B'], [['1', '2']], col_widths=[-10, 300])

    def test_col_widths_no_finito_se_rechaza(self):
        with self.assertRaises(TablaInvalida):
            standard_table(['A', 'B'], [['1', '2']], col_widths=[float('nan'), 300])

    def test_col_widths_con_cantidad_incorrecta_se_rechaza(self):
        with self.assertRaises(TablaInvalida):
            standard_table(['A', 'B', 'C'], [['1', '2', '3']], col_widths=[100, 200])

    def test_business_header_con_ancho_angosto_omite_el_logo_sin_romper(self):
        """
        Restar un ancho fijo de logo sin comprobar el ancho total dejaba la
        columna de info en negativo con un `width` angosto (p. ej. ticket
        termico). Ahora se omite el logo en vez de fallar.
        """
        class Config:
            nombre_negocio = 'Ticket'
            rnc = ''
            telefono = ''
            direccion = ''
            logo = _LogoStub(_png((40, 40)).getvalue())

        buffer = BytesIO()
        doc = document(buffer, pagesize=(3 * 72, 6 * 72))
        doc.build(business_header(Config(), width=1.5 * 72))
        self.assertTrue(buffer.getvalue().startswith(b'%PDF'))


class TablaTopeFilasTests(TestCase):
    """COM-012: `standard_table` acota las filas y no materializa el iterable.

    El hallazgo: `list(rows)` incondicional consumia un generador de miles de
    filas completo antes de maquetar, con la memoria del worker atada al tamano
    del dato. Ahora se recorre perezosamente y se corta en un tope, informando
    el excedente en vez de romper el documento.
    """

    def _filas_infinitas(self):
        # Si standard_table volviera a hacer `list(rows)`, iterar esto colgaria
        # el test / agotaria la memoria: es la prueba de que el corte es real.
        indice = 0
        while True:
            indice += 1
            yield [f'Item {indice}', str(indice)]

    def test_iterable_no_acotado_se_corta_en_el_tope(self):
        tabla = standard_table(['Nombre', 'N'], self._filas_infinitas(), max_rows=10)
        # header + 10 filas dibujadas + 1 fila de aviso.
        self.assertEqual(len(tabla._cellvalues), 12)

    def test_excedente_agrega_fila_de_aviso(self):
        tabla = standard_table(['Nombre', 'N'], self._filas_infinitas(), max_rows=3)
        aviso = tabla._cellvalues[-1][0].getPlainText()
        self.assertIn('primeras 3 filas', aviso)

    def test_debajo_del_tope_no_agrega_aviso(self):
        tabla = standard_table(['Nombre', 'N'], [['a', '1'], ['b', '2']], max_rows=10)
        self.assertEqual(len(tabla._cellvalues), 3)  # header + 2 filas

    def test_tope_por_defecto_generoso_no_trunca_documento_real(self):
        self.assertEqual(TABLA_MAX_FILAS, 5000)
        filas = [[f'x{i}', str(i)] for i in range(500)]
        tabla = standard_table(['Nombre', 'N'], filas)  # sin max_rows explicito
        self.assertEqual(len(tabla._cellvalues), 501)  # header + 500, sin aviso

    def test_max_rows_negativo_se_rechaza(self):
        with self.assertRaises(TablaInvalida):
            standard_table(['A', 'B'], [['1', '2']], max_rows=-1)

    def test_tabla_truncada_sigue_generando_pdf(self):
        tabla = standard_table(['Nombre', 'N'], self._filas_infinitas(), max_rows=5)
        buffer = BytesIO()
        doc = document(buffer)
        doc.build([tabla])
        self.assertTrue(buffer.getvalue().startswith(b'%PDF'))
