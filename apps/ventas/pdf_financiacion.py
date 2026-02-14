"""
Generador de PDF formal para facturas de financiacion cooperativa.
apps/ventas/pdf_financiacion.py

Genera un PDF tipo factura profesional con:
- Logo de la empresa en color
- Datos del negocio
- Datos del cliente (cooperativa)
- Detalle de productos
- Totales
- Espacio para firmas
- Codigo de aprobacion
"""

import io
import os
from decimal import Decimal
from datetime import datetime

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch, mm
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from django.conf import settings


# Colores corporativos
COLOR_PRIMARIO = HexColor('#1a56db')
COLOR_SECUNDARIO = HexColor('#374151')
COLOR_GRIS_CLARO = HexColor('#f3f4f6')
COLOR_BORDE = HexColor('#d1d5db')
COLOR_TEXTO = HexColor('#111827')


def generar_factura_cooperativa(venta, financiacion, detalles, pagos):
    """
    Genera PDF de factura formal para financiacion cooperativa.

    Args:
        venta: instancia de Venta
        financiacion: instancia de FinanciacionCooperativa
        detalles: QuerySet de DetalleVenta
        pagos: QuerySet de Pago

    Returns:
        io.BytesIO con el PDF generado
    """
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    elements = []

    # Estilos personalizados
    style_titulo = ParagraphStyle(
        'Titulo',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=COLOR_PRIMARIO,
        spaceAfter=6,
        alignment=TA_CENTER,
    )

    style_subtitulo = ParagraphStyle(
        'Subtitulo',
        parent=styles['Normal'],
        fontSize=10,
        textColor=COLOR_SECUNDARIO,
        alignment=TA_CENTER,
    )

    style_seccion = ParagraphStyle(
        'Seccion',
        parent=styles['Heading2'],
        fontSize=12,
        textColor=COLOR_PRIMARIO,
        spaceBefore=12,
        spaceAfter=6,
    )

    style_normal = ParagraphStyle(
        'NormalCustom',
        parent=styles['Normal'],
        fontSize=9,
        textColor=COLOR_TEXTO,
    )

    style_small = ParagraphStyle(
        'Small',
        parent=styles['Normal'],
        fontSize=8,
        textColor=COLOR_SECUNDARIO,
    )

    # ===========================
    # ENCABEZADO CON LOGO
    # ===========================
    nombre_negocio = getattr(settings, 'NOMBRE_NEGOCIO', 'Royal Plastic')
    rnc_negocio = getattr(settings, 'RNC_NEGOCIO', '')
    direccion_negocio = getattr(settings, 'DIRECCION_NEGOCIO', 'Santo Domingo, RD')
    telefono_negocio = getattr(settings, 'TELEFONO_NEGOCIO', '')

    # Intentar cargar logo
    logo_path = os.path.join(settings.STATIC_ROOT or settings.BASE_DIR, 'static', 'img', 'logo.png')
    header_data = []

    if os.path.exists(logo_path):
        logo = Image(logo_path, width=1.5 * inch, height=1.5 * inch)
        header_left = logo
    else:
        header_left = Paragraph(
            f'<b>{nombre_negocio}</b>',
            style_titulo
        )

    header_right_text = f"""
    <b>{nombre_negocio}</b><br/>
    RNC: {rnc_negocio}<br/>
    {direccion_negocio}<br/>
    Tel: {telefono_negocio}
    """
    header_right = Paragraph(header_right_text, style_normal)

    header_table = Table(
        [[header_left, header_right]],
        colWidths=[2.5 * inch, 4.5 * inch]
    )
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 12))

    # Titulo FACTURA
    elements.append(Paragraph('FACTURA', style_titulo))
    elements.append(Spacer(1, 6))

    # ===========================
    # DATOS DE FACTURA Y CLIENTE
    # ===========================
    fecha_str = venta.fecha_venta.strftime('%d/%m/%Y %I:%M %p')

    info_factura = [
        ['No. Factura:', venta.numero_venta, 'Fecha:', fecha_str],
        ['Cooperativa:', financiacion.nombre_cooperativa, 'Codigo Aprob.:', financiacion.codigo_aprobacion or 'N/A'],
    ]

    info_table = Table(info_factura, colWidths=[1.2 * inch, 2.3 * inch, 1.2 * inch, 2.3 * inch])
    info_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ('TEXTCOLOR', (0, 0), (0, -1), COLOR_PRIMARIO),
        ('TEXTCOLOR', (2, 0), (2, -1), COLOR_PRIMARIO),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 10))

    # Datos del cliente
    elements.append(Paragraph('DATOS DEL CLIENTE', style_seccion))

    cliente_data = [
        ['Nombre:', financiacion.nombre_cliente],
        ['Cedula:', financiacion.cedula_cliente],
    ]
    if financiacion.telefono_cliente:
        cliente_data.append(['Telefono:', financiacion.telefono_cliente])
    if financiacion.direccion_cliente:
        cliente_data.append(['Direccion:', financiacion.direccion_cliente])

    cliente_table = Table(cliente_data, colWidths=[1.2 * inch, 5.8 * inch])
    cliente_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('TEXTCOLOR', (0, 0), (0, -1), COLOR_SECUNDARIO),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('BACKGROUND', (0, 0), (-1, -1), COLOR_GRIS_CLARO),
        ('BOX', (0, 0), (-1, -1), 0.5, COLOR_BORDE),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(cliente_table)
    elements.append(Spacer(1, 12))

    # ===========================
    # DETALLE DE PRODUCTOS
    # ===========================
    elements.append(Paragraph('DETALLE DE PRODUCTOS', style_seccion))

    # Header de tabla
    productos_header = ['#', 'Producto', 'Cant.', 'P. Unit.', 'Desc.', 'Total']
    productos_data = [productos_header]

    for idx, detalle in enumerate(detalles, 1):
        productos_data.append([
            str(idx),
            detalle.producto.nombre,
            str(detalle.cantidad),
            f'${detalle.precio_unitario:,.2f}',
            f'${detalle.descuento_monto:,.2f}' if detalle.descuento_monto > 0 else '-',
            f'${detalle.total_linea:,.2f}',
        ])

    productos_table = Table(
        productos_data,
        colWidths=[0.4 * inch, 3.0 * inch, 0.6 * inch, 1.0 * inch, 0.8 * inch, 1.2 * inch]
    )
    productos_table.setStyle(TableStyle([
        # Header
        ('BACKGROUND', (0, 0), (-1, 0), COLOR_PRIMARIO),
        ('TEXTCOLOR', (0, 0), (-1, 0), HexColor('#ffffff')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        # Body
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('ALIGN', (0, 1), (0, -1), 'CENTER'),
        ('ALIGN', (2, 1), (2, -1), 'CENTER'),
        ('ALIGN', (3, 1), (-1, -1), 'RIGHT'),
        # Grid
        ('GRID', (0, 0), (-1, -1), 0.5, COLOR_BORDE),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [HexColor('#ffffff'), COLOR_GRIS_CLARO]),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(productos_table)
    elements.append(Spacer(1, 10))

    # ===========================
    # TOTALES
    # ===========================
    totales_data = [
        ['', '', '', '', 'Subtotal:', f'${venta.subtotal:,.2f}'],
    ]
    if venta.descuento_total > 0:
        totales_data.append(
            ['', '', '', '', 'Descuento:', f'-${venta.descuento_total:,.2f}']
        )
    totales_data.append(
        ['', '', '', '', 'TOTAL:', f'${venta.total:,.2f}']
    )

    totales_table = Table(
        totales_data,
        colWidths=[0.4 * inch, 3.0 * inch, 0.6 * inch, 1.0 * inch, 0.8 * inch, 1.2 * inch]
    )
    totales_table.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (4, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (4, 0), (4, -1), 'Helvetica-Bold'),
        ('FONTNAME', (4, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (4, -1), (-1, -1), 11),
        ('TEXTCOLOR', (4, -1), (-1, -1), COLOR_PRIMARIO),
        ('LINEABOVE', (4, -1), (-1, -1), 1, COLOR_PRIMARIO),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(totales_table)
    elements.append(Spacer(1, 20))

    # ===========================
    # METODO DE PAGO
    # ===========================
    metodos_str = ', '.join(
        f'{p.get_metodo_display()} (${p.monto:,.2f})'
        for p in pagos
    )
    elements.append(Paragraph(
        f'<b>Metodo de Pago:</b> Financiacion Cooperativa - {financiacion.nombre_cooperativa}',
        style_normal
    ))
    if metodos_str:
        elements.append(Paragraph(f'<b>Pagos registrados:</b> {metodos_str}', style_small))
    elements.append(Spacer(1, 20))

    # ===========================
    # NOTAS
    # ===========================
    if financiacion.notas:
        elements.append(Paragraph('NOTAS', style_seccion))
        elements.append(Paragraph(financiacion.notas, style_normal))
        elements.append(Spacer(1, 15))

    # ===========================
    # FIRMAS
    # ===========================
    elements.append(Spacer(1, 30))

    firmas_data = [
        ['_' * 30, '', '_' * 30],
        ['Entregado por', '', 'Recibido por'],
        [nombre_negocio, '', financiacion.nombre_cliente],
    ]

    firmas_table = Table(firmas_data, colWidths=[2.5 * inch, 2.0 * inch, 2.5 * inch])
    firmas_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 2), (-1, 2), 8),
        ('TEXTCOLOR', (0, 2), (-1, 2), COLOR_SECUNDARIO),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(firmas_table)

    # ===========================
    # PIE DE PAGINA
    # ===========================
    elements.append(Spacer(1, 20))
    elements.append(Paragraph(
        'Documento generado por Sistema POS - Royal Plastic',
        ParagraphStyle('Footer', parent=style_small, alignment=TA_CENTER)
    ))

    # Generar PDF
    doc.build(elements)
    buffer.seek(0)
    return buffer