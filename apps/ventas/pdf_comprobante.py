"""
Generador de PDF de comprobante de venta formal (tamano Carta).

Para ventas que necesitan algo mas formal que el ticket termico de 80mm: los
mismos totales que el ticket, presentados como documento con logo, datos del
cliente y bloque de firmas, listo para imprimir en la impresora de oficina.

No es un comprobante fiscal: no desglosa ITBIS ni trae eNCF/codigo de
seguridad DGII. Para eso existe apps.facturacion_electronica -- si algun dia
hace falta el desglose aqui, el calculo fiscal ya existe y es reutilizable en
apps.facturacion_electronica.services.venta_to_ecf._calcular_linea (maneja
los dos modos de ConfiguracionNegocio.itbis_incluido_en_precio).
"""
import io

from reportlab.platypus import Spacer

from apps.common.pdf.standard import (
    RED,
    business_header,
    date,
    document,
    document_title,
    footer_canvas,
    get_styles,
    info_grid,
    money,
    note,
    para,
    section_title,
    signature_block,
    standard_table,
    totals_table,
)
from apps.configuracion.utils import config_para_documento


def generar_comprobante_venta(venta):
    """
    Genera el PDF de comprobante de venta. Devuelve un BytesIO posicionado
    al inicio, listo para `HttpResponse(buffer.getvalue(), ...)`.
    """
    buffer = io.BytesIO()
    # COM-001: el comprobante se encabeza con la identidad fiscal de la
    # sucursal que hizo la venta, no con la del settings del proceso -- si no,
    # una venta de la sucursal B sale con el nombre, RNC y logo de la A.
    config = config_para_documento(getattr(venta, 'sucursal', None))
    nombre_negocio = getattr(config, 'nombre_negocio', '') or 'Sistema POS'

    elements = []
    elements.extend(business_header(config))
    elements.extend(document_title('Comprobante de venta', venta.numero_venta))

    if venta.estado == 'ANULADA':
        elements.extend([
            para(
                f'VENTA ANULADA el {date(venta.fecha_anulacion)} — este '
                f'documento no ampara una entrega.',
                get_styles()['PdfNote'],
                bold=True,
                color=RED,
            ),
            Spacer(1, 10),
        ])

    elements.extend([
        section_title('Datos del comprobante'),
        info_grid([
            [('No. comprobante', venta.numero_venta),
             ('Fecha', date(venta.fecha_venta, include_time=True))],
            [('Cajero', venta.usuario.get_full_name() or venta.usuario.username),
             ('Condicion de pago', venta.get_condicion_pago_display())],
            [('Estado', venta.get_estado_display())],
        ]),
        Spacer(1, 10),
    ])

    cliente = venta.cliente
    if cliente is not None:
        cliente_rows = [
            [('Cliente', cliente.nombre), ('Cedula/RNC', cliente.cedula_rnc or '-')],
            [('Telefono', cliente.telefono or '-'), ('Direccion', cliente.direccion or '-')],
        ]
        cliente_nombre = cliente.nombre
    else:
        cliente_rows = [[('Cliente', 'Cliente contado')]]
        cliente_nombre = 'Cliente contado'

    elements.extend([
        section_title('Datos del cliente'),
        info_grid(cliente_rows),
        Spacer(1, 10),
    ])

    detalle_rows = []
    for idx, detalle in enumerate(venta.detalles.select_related('producto').all(), 1):
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
            ('Descuento', f'-{money(venta.descuento_total)}', 'negative')
            if venta.descuento_total else ('Descuento', money(0), None),
            ('Total', money(venta.total), 'total'),
        ]),
        Spacer(1, 12),
    ])

    pagos = list(venta.pagos.all())
    if pagos:
        pago_rows = [
            [('Forma', pago.get_metodo_display()), ('Monto', money(pago.monto))]
            for pago in pagos
        ]
    else:
        pago_rows = [[('Forma', '-'), ('Monto', money(0))]]
    if venta.condicion_pago == 'CREDITO':
        pago_rows.append([('Condicion', 'Venta a credito — ver estado de cuenta')])

    elements.extend([
        section_title('Forma de pago'),
        info_grid(pago_rows),
        Spacer(1, 10),
    ])

    if venta.notas:
        elements.extend([
            section_title('Notas'),
            info_grid([[('Notas', venta.notas)]]),
            Spacer(1, 12),
        ])

    elements.extend([
        Spacer(1, 22),
        signature_block('Entregado por', nombre_negocio, 'Recibido por', cliente_nombre),
        Spacer(1, 12),
        note('Este documento no constituye un comprobante fiscal.'),
    ])

    doc = document(buffer)
    doc.build(
        elements,
        onFirstPage=lambda canvas, doc_obj: footer_canvas(canvas, doc_obj, label='Comprobante de venta'),
        onLaterPages=lambda canvas, doc_obj: footer_canvas(canvas, doc_obj, label='Comprobante de venta'),
    )
    buffer.seek(0)
    return buffer
