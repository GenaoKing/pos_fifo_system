"""
Generador de PDF para cotizaciones.
"""
from io import BytesIO

from reportlab.platypus import Spacer

from apps.common.pdf.standard import (
    CONTENT_WIDTH,
    business_header,
    date,
    document,
    document_title,
    footer_canvas,
    info_grid,
    money,
    note,
    section_title,
    standard_table,
    totals_table,
)
from apps.configuracion.utils import config_para_documento


class CotizacionPDF:
    """Generador de PDF para cotizaciones."""

    def __init__(self, cotizacion):
        self.cotizacion = cotizacion
        # COM-001: el encabezado sale de la sucursal del OBJETO, no de
        # `SUCURSAL_CODIGO`. Con settings apuntando a A, un documento de B
        # se imprimia con el nombre, el RNC, la direccion y el logo de A.
        self.config = config_para_documento(cotizacion.sucursal)
        self.buffer = BytesIO()

    def _header(self):
        return [
            *business_header(self.config),
            *document_title('Cotizacion', self.cotizacion.numero_cotizacion),
        ]

    def _info_cotizacion(self):
        usuario = self.cotizacion.usuario.get_full_name() or self.cotizacion.usuario.username
        return [
            section_title('Datos de la cotizacion'),
            info_grid([
                [('Fecha', date(self.cotizacion.fecha_creacion, include_time=True)), ('Creado por', usuario)],
                [('Estado', self.cotizacion.get_estado_display()), ('Numero', self.cotizacion.numero_cotizacion)],
            ]),
            Spacer(1, 10),
        ]

    def _info_cliente(self):
        cliente = self.cotizacion.cliente
        rows = [
            [('Cliente', cliente.nombre), ('Cedula/RNC', cliente.cedula_rnc or '-')],
            [('Telefono', cliente.telefono or '-'), ('Direccion', cliente.direccion or '-')],
        ]
        return [
            section_title('Informacion del cliente'),
            info_grid(rows),
            Spacer(1, 10),
        ]

    def _tabla_productos(self):
        rows = []
        for detalle in self.cotizacion.detalles.select_related('producto').all():
            rows.append([
                detalle.producto.nombre,
                detalle.cantidad,
                money(detalle.precio_unitario),
                money(detalle.subtotal),
                money(detalle.descuento_monto) if detalle.descuento_monto else '-',
                money(detalle.total_linea),
            ])

        return [
            section_title('Detalle de productos'),
            standard_table(
                ['Producto', 'Cant.', 'P. Unit.', 'Subtotal', 'Desc.', 'Total'],
                rows,
                col_widths=[0.38, 0.08, 0.13, 0.14, 0.12, 0.15],
                aligns=['LEFT', 'CENTER', 'RIGHT', 'RIGHT', 'RIGHT', 'RIGHT'],
            ),
            Spacer(1, 8),
            totals_table([
                ('Subtotal', money(self.cotizacion.subtotal), None),
                ('Descuento', f"-{money(self.cotizacion.descuento_total)}", 'negative')
                if self.cotizacion.descuento_total else ('Descuento', money(0), None),
                ('Total', money(self.cotizacion.total), 'total'),
            ]),
            Spacer(1, 12),
        ]

    def _notas(self):
        flowables = []
        if self.cotizacion.notas:
            flowables.extend([
                section_title('Notas'),
                info_grid([[('Notas', self.cotizacion.notas)]], width=CONTENT_WIDTH),
                Spacer(1, 10),
            ])
        return flowables

    def _footer_note(self):
        telefono = getattr(self.config, 'telefono', '') or '-'
        return [
            Spacer(1, 8),
            note(
                'Validez: Esta cotizacion tiene una validez de 15 dias. '
                'Los precios estan sujetos a disponibilidad de inventario. '
                f'Contacto: {telefono}.'
            ),
        ]

    def generar(self):
        doc = document(self.buffer)
        elements = []
        elements.extend(self._header())
        elements.extend(self._info_cotizacion())
        elements.extend(self._info_cliente())
        elements.extend(self._tabla_productos())
        elements.extend(self._notas())
        elements.extend(self._footer_note())

        doc.build(
            elements,
            onFirstPage=lambda canvas, doc_obj: footer_canvas(canvas, doc_obj, label='Cotizacion'),
            onLaterPages=lambda canvas, doc_obj: footer_canvas(canvas, doc_obj, label='Cotizacion'),
        )
        self.buffer.seek(0)
        return self.buffer


def generar_pdf_cotizacion(cotizacion):
    """Funcion helper para generar PDF de cotizacion."""
    return CotizacionPDF(cotizacion).generar()
