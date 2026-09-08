from io import BytesIO
from decimal import Decimal

from django.test import SimpleTestCase

from apps.common.pdf.standard import (
    business_header,
    date,
    document,
    document_title,
    info_grid,
    money,
    standard_table,
)


class DummyConfig:
    nombre_negocio = 'Royal Test'
    rnc = ''
    telefono = ''
    direccion = ''
    logo = None


class PdfStandardTests(SimpleTestCase):
    def test_helpers_formatean_valores_basicos(self):
        # COM-003: el simbolo era `$` a secas, indistinguible de dolares en un
        # documento comercial o fiscal dominicano.
        self.assertEqual(money(None), 'RD$0.00')
        self.assertEqual(money(Decimal('1234.5')), 'RD$1,234.50')
        self.assertEqual(date(None), '-')

    def test_standard_table_y_header_generan_pdf_sin_datos(self):
        buffer = BytesIO()
        doc = document(buffer)
        elements = []
        elements.extend(business_header(DummyConfig()))
        elements.extend(document_title('Prueba', 'Contrato visual'))
        elements.append(info_grid([[('Cliente', 'Texto largo sin logo')]]))
        elements.append(standard_table(
            ['Producto', 'Cant.', 'Total'],
            [],
            col_widths=[0.6, 0.15, 0.25],
            aligns=['LEFT', 'CENTER', 'RIGHT'],
        ))

        doc.build(elements)
        self.assertTrue(buffer.getvalue().startswith(b'%PDF'))
