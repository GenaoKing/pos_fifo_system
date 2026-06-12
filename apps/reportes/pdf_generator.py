import os

from django.conf import settings
from reportlab.platypus import Spacer

from apps.common.pdf.standard import (
    business_header,
    date,
    document,
    document_title,
    footer_canvas,
    info_grid,
    money,
    section_title,
    standard_table,
    totals_table,
)
from apps.configuracion.utils import get_config

from .models import CierreCaja


class PDFGenerator:
    """Genera PDFs profesionales para reportes locales."""

    @staticmethod
    def _build_path(cierre):
        pdf_dir = os.path.join(settings.MEDIA_ROOT, 'reportes', 'cierres')
        os.makedirs(pdf_dir, exist_ok=True)
        filename = f"cierre_{cierre.fecha.strftime('%Y%m%d')}.pdf"
        return os.path.join(pdf_dir, filename)

    @staticmethod
    def _porcentaje(monto, total):
        if not total:
            return '0.0%'
        return f'{(monto / total * 100):.1f}%'

    @staticmethod
    def generar_cierre_caja(cierre_id):
        """Genera PDF del cierre de caja."""
        cierre = CierreCaja.objects.get(id=cierre_id)
        filepath = PDFGenerator._build_path(cierre)
        config = get_config()

        total_flujo = (
            cierre.total_efectivo
            + cierre.total_transferencia
            + cierre.total_tarjeta
            + getattr(cierre, 'total_cobros_cxc', 0)
        )

        elements = []
        elements.extend(business_header(config))
        elements.extend(document_title('Cierre de caja', date(cierre.fecha)))

        elements.extend([
            section_title('Resumen de ventas'),
            info_grid([
                [('Fecha', date(cierre.fecha)), ('Ventas', cierre.cantidad_ventas)],
                [('Ventas facturadas', money(cierre.total_ventas)), ('Descuentos', money(cierre.total_descuentos))],
                [('Anulaciones', cierre.cantidad_anulaciones), ('Total anulaciones', money(cierre.total_anulaciones))],
            ]),
            Spacer(1, 10),
        ])

        elements.extend([
            section_title('Flujo de caja'),
            standard_table(
                ['Metodo', 'Monto', '% del flujo'],
                [
                    ['Efectivo', money(cierre.total_efectivo), PDFGenerator._porcentaje(cierre.total_efectivo, total_flujo)],
                    ['Transferencia', money(cierre.total_transferencia), PDFGenerator._porcentaje(cierre.total_transferencia, total_flujo)],
                    ['Tarjeta', money(cierre.total_tarjeta), PDFGenerator._porcentaje(cierre.total_tarjeta, total_flujo)],
                    ['Cobros CxC', money(getattr(cierre, 'total_cobros_cxc', 0)), PDFGenerator._porcentaje(getattr(cierre, 'total_cobros_cxc', 0), total_flujo)],
                ],
                col_widths=[0.46, 0.30, 0.24],
                aligns=['LEFT', 'RIGHT', 'CENTER'],
            ),
            Spacer(1, 8),
            totals_table([
                ('Flujo total', money(total_flujo), 'total'),
            ]),
            Spacer(1, 10),
        ])

        cajeros = cierre.resumen_cajeros or {}
        if cajeros:
            rows = []
            for username, data in cajeros.items():
                rows.append([
                    username,
                    data.get('cantidad_ventas', data.get('cantidad', data.get('ventas', 0))),
                    money(data.get('total_ventas', data.get('total', 0))),
                ])
            elements.extend([
                section_title('Resumen por cajero'),
                standard_table(
                    ['Cajero', 'Ventas', 'Total'],
                    rows,
                    col_widths=[0.50, 0.20, 0.30],
                    aligns=['LEFT', 'CENTER', 'RIGHT'],
                ),
            ])

        doc = document(filepath)
        doc.build(
            elements,
            onFirstPage=lambda canvas, doc_obj: footer_canvas(canvas, doc_obj, label='Cierre de caja'),
            onLaterPages=lambda canvas, doc_obj: footer_canvas(canvas, doc_obj, label='Cierre de caja'),
        )
        return filepath
