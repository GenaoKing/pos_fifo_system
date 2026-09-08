"""
Generador de PDF formal para facturas de financiacion cooperativa.
"""
import io

from reportlab.platypus import Spacer

from apps.common.pdf.standard import (
    business_header,
    date,
    document,
    document_title,
    footer_canvas,
    info_grid,
    money,
    note,
    section_title,
    signature_block,
    standard_table,
    totals_table,
)
from apps.configuracion.utils import config_para_documento


def generar_factura_cooperativa(venta, financiacion, detalles, pagos):
    """
    Genera PDF de factura formal para financiacion cooperativa.
    """
    buffer = io.BytesIO()
    # COM-001: la factura se encabeza con la identidad fiscal de la
    # sucursal que hizo la venta, no con la del settings del proceso.
    config = config_para_documento(getattr(venta, 'sucursal', None))
    nombre_negocio = getattr(config, 'nombre_negocio', '') or 'Sistema POS'

    elements = []
    elements.extend(business_header(config))
    elements.extend(document_title('Factura', venta.numero_venta))

    elements.extend([
        section_title('Datos de factura'),
        info_grid([
            [('No. factura', venta.numero_venta), ('Fecha', date(venta.fecha_venta, include_time=True))],
            [('Cooperativa', financiacion.nombre_cooperativa), ('Codigo aprob.', financiacion.codigo_aprobacion or 'N/A')],
        ]),
        Spacer(1, 10),
    ])

    cliente_rows = [
        [('Cliente', financiacion.nombre_cliente), ('Cedula', financiacion.cedula_cliente)],
        [('Telefono', financiacion.telefono_cliente or '-'), ('Direccion', financiacion.direccion_cliente or '-')],
    ]
    elements.extend([
        section_title('Datos del cliente'),
        info_grid(cliente_rows),
        Spacer(1, 10),
    ])

    detalle_rows = []
    for idx, detalle in enumerate(detalles, 1):
        detalle_rows.append([
            idx,
            detalle.producto.nombre,
            detalle.cantidad,
            money(detalle.precio_unitario),
            money(detalle.descuento_monto) if detalle.descuento_monto else '-',
            money(detalle.total_linea),
        ])

    elements.extend([
        section_title('Detalle de productos'),
        standard_table(
            ['#', 'Producto', 'Cant.', 'P. Unit.', 'Desc.', 'Total'],
            detalle_rows,
            col_widths=[0.07, 0.42, 0.09, 0.15, 0.12, 0.15],
            aligns=['CENTER', 'LEFT', 'CENTER', 'RIGHT', 'RIGHT', 'RIGHT'],
        ),
        Spacer(1, 8),
        totals_table([
            ('Subtotal', money(venta.subtotal), None),
            ('Descuento', f"-{money(venta.descuento_total)}", 'negative')
            if venta.descuento_total else ('Descuento', money(0), None),
            ('Total', money(venta.total), 'total'),
        ]),
        Spacer(1, 12),
    ])

    metodos = ', '.join(
        f'{p.get_metodo_display()} ({money(p.monto)})'
        for p in pagos
    )
    elements.extend([
        section_title('Metodo de pago'),
        info_grid([
            [('Forma', f'Financiacion Cooperativa - {financiacion.nombre_cooperativa}')],
            [('Pagos registrados', metodos or '-')],
        ]),
        Spacer(1, 10),
    ])

    if financiacion.notas:
        elements.extend([
            section_title('Notas'),
            info_grid([[('Notas', financiacion.notas)]]),
            Spacer(1, 12),
        ])

    elements.extend([
        Spacer(1, 22),
        signature_block('Entregado por', nombre_negocio, 'Recibido por', financiacion.nombre_cliente),
        Spacer(1, 12),
        note('Documento generado por Sistema POS.'),
    ])

    doc = document(buffer)
    doc.build(
        elements,
        onFirstPage=lambda canvas, doc_obj: footer_canvas(canvas, doc_obj, label='Factura financiacion'),
        onLaterPages=lambda canvas, doc_obj: footer_canvas(canvas, doc_obj, label='Factura financiacion'),
    )
    buffer.seek(0)
    return buffer
