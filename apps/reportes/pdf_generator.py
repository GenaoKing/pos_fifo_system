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

from .almacenamiento import ruta_cierre
from .models import CierreCaja


class PDFGenerator:
    """Genera PDFs profesionales para reportes locales."""

    @staticmethod
    def _build_path(cierre):
        """
        Ruta del PDF: privada, con prefijo de tenant y nombre no enumerable.

        Antes componia `MEDIA_ROOT/reportes/cierres/cierre_YYYYMMDD.pdf`: una
        ruta publica (RPT-001) y ademas identica para todos los tenants que
        cerraran el mismo dia, con lo que el segundo pisaba al primero
        (RPT-007).
        """
        return ruta_cierre(cierre)

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

        ambito = cierre.sucursal.nombre if cierre.sucursal_id else 'Consolidado'
        sello = f'{cierre.estado} v{cierre.version}'

        elements = []
        elements.extend(business_header(config))
        # El titulo dice lo que el documento ES (RPT-008). "Cierre de caja"
        # invitaba a leerlo como una conciliacion de efectivo fisico que este
        # resumen nunca hizo: el arqueo real vive en apps/caja.
        elements.extend(document_title(
            'Resumen diario de ventas y cobros', date(cierre.fecha),
        ))

        elements.extend([
            section_title('Resumen de ventas'),
            info_grid([
                [('Fecha', date(cierre.fecha)), ('Ambito', ambito)],
                [('Ventas', cierre.cantidad_ventas), ('Estado', sello)],
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

        # Conciliacion de caja fisica: separada de la facturacion de arriba,
        # para que se lea como lo que es (RPT-008).
        elements.extend([
            section_title('Arqueo de caja del dia'),
            info_grid([
                [('Turnos cerrados', cierre.turnos_cerrados),
                 ('Turnos abiertos', cierre.turnos_abiertos)],
                [('Diferencia de arqueo', money(cierre.diferencia_arqueo)),
                 ('Dia conciliado', 'Si' if cierre.conciliado else 'NO')],
            ]),
            Spacer(1, 10),
        ])

        cajeros = cierre.resumen_cajeros or {}
        if cajeros:
            rows = []
            for clave, data in cajeros.items():
                # `data['nombre']` primero: la clave del dict era el ID interno
                # del usuario y el PDF lo imprimia como "Cajero", asi que el
                # lector veia numeros donde espera nombres (RPT-015).
                rows.append([
                    data.get('nombre') or clave,
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

        etiqueta = 'Resumen diario'
        doc = document(filepath)
        doc.build(
            elements,
            onFirstPage=lambda canvas, doc_obj: footer_canvas(canvas, doc_obj, label=etiqueta),
            onLaterPages=lambda canvas, doc_obj: footer_canvas(canvas, doc_obj, label=etiqueta),
        )
        return filepath
