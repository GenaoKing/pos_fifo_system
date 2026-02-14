"""
Generador de PDF para Cotizaciones
apps/cotizaciones/pdf_generator.py

Genera PDF profesional con formato similar al de financiacion cooperativa.
"""

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
)
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.pdfgen import canvas
from io import BytesIO
from decimal import Decimal
from django.conf import settings
import os


class CotizacionPDF:
    """Generador de PDF para cotizaciones"""

    def __init__(self, cotizacion):
        self.cotizacion = cotizacion
        self.buffer = BytesIO()
        self.width, self.height = letter
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()

    def _setup_custom_styles(self):
        """Define estilos personalizados"""
        # Titulo principal
        self.styles.add(ParagraphStyle(
            name='CustomTitle',
            parent=self.styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#1e40af'),
            spaceAfter=6,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        ))

        # Subtitulo
        self.styles.add(ParagraphStyle(
            name='CustomSubtitle',
            parent=self.styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#6b7280'),
            spaceAfter=20,
            alignment=TA_CENTER,
        ))

        # Header izquierdo
        self.styles.add(ParagraphStyle(
            name='HeaderLeft',
            parent=self.styles['Normal'],
            fontSize=9,
            textColor=colors.HexColor('#374151'),
            leading=12,
        ))

        # Header derecho
        self.styles.add(ParagraphStyle(
            name='HeaderRight',
            parent=self.styles['Normal'],
            fontSize=9,
            textColor=colors.HexColor('#374151'),
            leading=12,
            alignment=TA_RIGHT,
        ))

        # Seccion header
        self.styles.add(ParagraphStyle(
            name='SectionHeader',
            parent=self.styles['Normal'],
            fontSize=11,
            textColor=colors.HexColor('#1e40af'),
            fontName='Helvetica-Bold',
            spaceAfter=8,
        ))

        # Campo label
        self.styles.add(ParagraphStyle(
            name='FieldLabel',
            parent=self.styles['Normal'],
            fontSize=8,
            textColor=colors.HexColor('#6b7280'),
            leading=10,
        ))

        # Campo valor
        self.styles.add(ParagraphStyle(
            name='FieldValue',
            parent=self.styles['Normal'],
            fontSize=9,
            textColor=colors.HexColor('#111827'),
            fontName='Helvetica-Bold',
            leading=12,
        ))

    def _get_logo_path(self):
        """Obtiene la ruta del logo"""
        logo_path = os.path.join(
            settings.STATIC_ROOT or settings.BASE_DIR / 'static',
            'img',
            'logo-royal.jpeg'
        )
        if os.path.exists(logo_path):
            return logo_path

        # Intentar en staticfiles
        logo_path = os.path.join(
            settings.BASE_DIR,
            'staticfiles',
            'img',
            'logo-royal.jpeg'
        )
        if os.path.exists(logo_path):
            return logo_path

        return None

    def _crear_header(self):
        """Crea el header con logo e info de la empresa"""
        elements = []

        # Obtener info de empresa desde settings

        # Accedemos al diccionario y luego usamos .get() para cada llave
        info = getattr(settings, 'BUSINESS_INFO', {})

        empresa_nombre = info.get('NAME', 'Royal Plastic')
        empresa_direccion = info.get('ADDRESS', 'Santo Domingo, Rep. Dom.')
        empresa_telefono = info.get('PHONE', '')
        empresa_rnc = info.get('RNC', '')
        #empresa_email = getattr(settings, 'EMPRESA_EMAIL', '')
        empresa_ciudad = info.get('CITY', '')
        # Tabla para logo + info empresa
        logo_path = self._get_logo_path()
        data = []

        if logo_path:
            # Con logo
            logo = Image(logo_path, width=1.2*inch, height=1.2*inch)

            info_empresa = f"""
            <b><font size="14" color="#1e40af">{empresa_nombre}</font></b><br/>
            <font size="8" color="#6b7280">
            {empresa_direccion} {empresa_ciudad}<br/>
            Tel: {empresa_telefono}<br/>
            RNC: {empresa_rnc}<br/>
            </font>
            """

            data = [[logo, Paragraph(info_empresa, self.styles['Normal'])]]
        else:
            # Sin logo
            info_empresa = f"""
            <b><font size="16" color="#1e40af">{empresa_nombre}</font></b><br/>
            <font size="9" color="#6b7280">
            {empresa_direccion}<br/>
            Tel: {empresa_telefono} | RNC: {empresa_rnc}<br/>
            </font>
            """
            data = [[Paragraph(info_empresa, self.styles['Normal'])]]

        header_table = Table(data, colWidths=[self.width - 2*inch])
        header_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ]))

        elements.append(header_table)
        elements.append(Spacer(1, 0.2*inch))

        # Titulo documento
        elements.append(Paragraph('COTIZACIÓN', self.styles['CustomTitle']))
        elements.append(Paragraph(
            self.cotizacion.numero_cotizacion,
            self.styles['CustomSubtitle']
        ))

        return elements

    def _crear_info_cotizacion(self):
        """Info basica de la cotizacion"""
        elements = []

        # Fecha y usuario
        fecha_str = self.cotizacion.fecha_creacion.strftime('%d/%m/%Y %I:%M %p')

        data = [
            [
                Paragraph('<b>Fecha:</b>', self.styles['FieldLabel']),
                Paragraph(fecha_str, self.styles['FieldValue']),
                Paragraph('<b>Creado por:</b>', self.styles['FieldLabel']),
                Paragraph(
                    self.cotizacion.usuario.get_full_name() or self.cotizacion.usuario.username,
                    self.styles['FieldValue']
                ),
            ],
        ]

        info_table = Table(data, colWidths=[0.8*inch, 2*inch, 1*inch, 2*inch])
        info_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
            ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))

        elements.append(info_table)
        elements.append(Spacer(1, 0.15*inch))

        return elements

    def _crear_info_cliente(self):
        """Info del cliente"""
        elements = []

        elements.append(Paragraph('INFORMACIÓN DEL CLIENTE', self.styles['SectionHeader']))

        cliente = self.cotizacion.cliente
        info_cliente = []

        # Nombre
        info_cliente.append([
            Paragraph('<b>Nombre:</b>', self.styles['FieldLabel']),
            Paragraph(cliente.nombre, self.styles['FieldValue']),
        ])

        # Cedula/RNC
        if cliente.cedula_rnc:
            info_cliente.append([
                Paragraph('<b>Cédula/RNC:</b>', self.styles['FieldLabel']),
                Paragraph(cliente.cedula_rnc, self.styles['FieldValue']),
            ])

        # Telefono
        if cliente.telefono:
            info_cliente.append([
                Paragraph('<b>Teléfono:</b>', self.styles['FieldLabel']),
                Paragraph(cliente.telefono, self.styles['FieldValue']),
            ])

        # Email
        if hasattr(cliente, 'email') and cliente.email:
            info_cliente.append([
                Paragraph('<b>Email:</b>', self.styles['FieldLabel']),
                Paragraph(cliente.email, self.styles['FieldValue']),
            ])

        cliente_table = Table(info_cliente, colWidths=[1.2*inch, 4.5*inch])
        cliente_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f9fafb')),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
        ]))

        elements.append(cliente_table)
        elements.append(Spacer(1, 0.2*inch))

        return elements

    def _crear_tabla_productos(self):
        """Tabla de productos cotizados"""
        elements = []

        elements.append(Paragraph('DETALLE DE PRODUCTOS', self.styles['SectionHeader']))

        # Headers
        headers = [
            Paragraph('<b>Producto</b>', self.styles['Normal']),
            Paragraph('<b>Cant.</b>', self.styles['Normal']),
            Paragraph('<b>P. Unit.</b>', self.styles['Normal']),
            Paragraph('<b>Subtotal</b>', self.styles['Normal']),
            Paragraph('<b>Desc.</b>', self.styles['Normal']),
            Paragraph('<b>Total</b>', self.styles['Normal']),
        ]

        data = [headers]

        # Detalles
        detalles = self.cotizacion.detalles.select_related('producto').all()

        for detalle in detalles:
            data.append([
                Paragraph(f'<font size="9">{detalle.producto.nombre}</font>', self.styles['Normal']),
                Paragraph(f'<font size="9">{detalle.cantidad}</font>', self.styles['Normal']),
                Paragraph(f'<font size="9">${detalle.precio_unitario:,.2f}</font>', self.styles['Normal']),
                Paragraph(f'<font size="9">${detalle.subtotal:,.2f}</font>', self.styles['Normal']),
                Paragraph(
                    f'<font size="9" color="#dc2626">${detalle.descuento_monto:,.2f}</font>' if detalle.descuento_monto > 0 else '-',
                    self.styles['Normal']
                ),
                Paragraph(f'<font size="9"><b>${detalle.total_linea:,.2f}</b></font>', self.styles['Normal']),
            ])

        # Totales
        data.append([
            '',
            '',
            '',
            '',
            Paragraph('<b>Subtotal:</b>', self.styles['Normal']),
            Paragraph(f'<b>${self.cotizacion.subtotal:,.2f}</b>', self.styles['Normal']),
        ])

        if self.cotizacion.descuento_total > 0:
            data.append([
                '',
                '',
                '',
                '',
                Paragraph('<b>Descuento:</b>', self.styles['Normal']),
                Paragraph(
                    f'<b><font color="#dc2626">-${self.cotizacion.descuento_total:,.2f}</font></b>',
                    self.styles['Normal']
                ),
            ])

        data.append([
            '',
            '',
            '',
            '',
            Paragraph('<b><font size="11">TOTAL:</font></b>', self.styles['Normal']),
            Paragraph(
                f'<b><font size="11" color="#1e40af">${self.cotizacion.total:,.2f}</font></b>',
                self.styles['Normal']
            ),
        ])

        # Crear tabla
        col_widths = [2.8*inch, 0.6*inch, 0.9*inch, 0.9*inch, 0.9*inch, 1*inch]
        productos_table = Table(data, colWidths=col_widths)

        # Estilos
        table_style = TableStyle([
            # Headers
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e40af')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (1, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('TOPPADDING', (0, 0), (-1, 0), 8),

            # Productos
            ('ALIGN', (1, 1), (1, -4), 'CENTER'),  # Cantidad centrada
            ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),  # Precios alineados derecha
            ('VALIGN', (0, 1), (-1, -4), 'MIDDLE'),
            ('FONTSIZE', (0, 1), (-1, -4), 9),
            ('ROWBACKGROUNDS', (0, 1), (-1, -4), [colors.white, colors.HexColor('#f9fafb')]),
            ('BOTTOMPADDING', (0, 1), (-1, -4), 6),
            ('TOPPADDING', (0, 1), (-1, -4), 6),

            # Lineas separadoras
            ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor('#1e40af')),
            ('LINEBELOW', (0, -4), (-1, -4), 0.5, colors.HexColor('#e5e7eb')),

            # Seccion totales
            ('LINEABOVE', (4, -3), (-1, -3), 1, colors.HexColor('#9ca3af')),
            ('ALIGN', (4, -3), (-1, -1), 'RIGHT'),
            ('FONTSIZE', (4, -3), (-1, -1), 10),
            ('BOTTOMPADDING', (4, -3), (-1, -1), 4),
            ('TOPPADDING', (4, -3), (-1, -1), 4),

            # Total final destacado
            ('BACKGROUND', (4, -1), (-1, -1), colors.HexColor('#dbeafe')),
            ('LINEABOVE', (4, -1), (-1, -1), 2, colors.HexColor('#1e40af')),
            ('BOTTOMPADDING', (4, -1), (-1, -1), 8),
            ('TOPPADDING', (4, -1), (-1, -1), 8),

            # Bordes
            ('BOX', (0, 0), (-1, -4), 0.5, colors.HexColor('#9ca3af')),
            ('INNERGRID', (0, 0), (-1, -4), 0.25, colors.HexColor('#e5e7eb')),
        ])

        productos_table.setStyle(table_style)
        elements.append(productos_table)
        elements.append(Spacer(1, 0.3*inch))

        return elements

    def _crear_notas(self):
        """Notas de la cotizacion"""
        elements = []

        if self.cotizacion.notas:
            elements.append(Paragraph('NOTAS', self.styles['SectionHeader']))

            notas_text = self.cotizacion.notas.replace('\n', '<br/>')
            elements.append(Paragraph(
                f'<font size="9">{notas_text}</font>',
                self.styles['Normal']
            ))
            elements.append(Spacer(1, 0.2*inch))

        return elements

    def _crear_footer(self):
        """Footer con terminos y condiciones"""
        elements = []

        # Validez
        validez_text = """
        <font size="8" color="#6b7280">
        <b>Validez:</b> Esta cotización tiene una validez de 15 días a partir de la fecha de emisión.<br/>
        <b>Términos:</b> Los precios están sujetos a disponibilidad de inventario.
        Las condiciones de pago se acordarán al momento de la venta.<br/>
        </font>
        """

        elements.append(Spacer(1, 0.2*inch))
        elements.append(Paragraph(validez_text, self.styles['Normal']))

        # Contacto
        info = getattr(settings, 'BUSINESS_INFO', {})
        empresa_telefono = info.get('PHONE', '')
        

        contacto_text = f"""
        <font size="8" color="#9ca3af">
        Para más información contactar al {empresa_telefono}
        </font>
        """
        elements.append(Spacer(1, 0.1*inch))
        elements.append(Paragraph(contacto_text, self.styles['Normal']))

        return elements

    def generar(self):
        """Genera el PDF completo"""
        doc = SimpleDocTemplate(
            self.buffer,
            pagesize=letter,
            rightMargin=0.75*inch,
            leftMargin=0.75*inch,
            topMargin=0.75*inch,
            bottomMargin=0.75*inch,
        )

        # Construir elementos
        elements = []
        elements.extend(self._crear_header())
        elements.extend(self._crear_info_cotizacion())
        elements.extend(self._crear_info_cliente())
        elements.extend(self._crear_tabla_productos())
        elements.extend(self._crear_notas())
        elements.extend(self._crear_footer())

        # Generar PDF
        doc.build(elements)

        # Retornar buffer
        self.buffer.seek(0)
        return self.buffer


def generar_pdf_cotizacion(cotizacion):
    """
    Funcion helper para generar PDF de cotizacion.

    Args:
        cotizacion: Instancia de Cotizacion

    Returns:
        BytesIO con el contenido del PDF
    """
    pdf = CotizacionPDF(cotizacion)
    return pdf.generar()