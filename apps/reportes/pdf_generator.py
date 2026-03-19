from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from django.conf import settings
from decimal import Decimal
import os
from datetime import datetime

from .models import CierreCaja, InventarioValorizado


class PDFGenerator:
    """
    Genera PDFs profesionales para reportes
    """
    
    @staticmethod
    def _get_header_footer(canvas_obj, doc):
        """Añade encabezado y pie de página"""
        canvas_obj.saveState()
        
        # Encabezado
        canvas_obj.setFont('Helvetica-Bold', 16)
        canvas_obj.drawString(inch, 10.5 * inch, "Royal Plastic")
        
        canvas_obj.setFont('Helvetica', 10)
        canvas_obj.drawString(inch, 10.3 * inch, "Santo Domingo, Republica Dominicana")
        
        # Línea separadora
        canvas_obj.setStrokeColor(colors.HexColor('#2563eb'))
        canvas_obj.setLineWidth(2)
        canvas_obj.line(inch, 10.2 * inch, 7.5 * inch, 10.2 * inch)
        
        # Pie de página
        canvas_obj.setFont('Helvetica', 8)
        canvas_obj.drawString(
            inch, 0.5 * inch,
            f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )
        
        page_num = canvas_obj.getPageNumber()
        canvas_obj.drawRightString(
            7.5 * inch, 0.5 * inch,
            f"Pagina {page_num}"
        )
        
        canvas_obj.restoreState()
    
    @staticmethod
    def generar_cierre_caja(cierre_id):
        """Genera PDF del cierre de caja"""
        cierre = CierreCaja.objects.get(id=cierre_id)
        
        # Crear directorio
        pdf_dir = os.path.join(settings.MEDIA_ROOT, 'reportes', 'cierres')
        os.makedirs(pdf_dir, exist_ok=True)
        
        # Nombre del archivo
        filename = f"cierre_{cierre.fecha.strftime('%Y%m%d')}.pdf"
        filepath = os.path.join(pdf_dir, filename)
        
        # Crear documento
        doc = SimpleDocTemplate(
            filepath,
            pagesize=letter,
            rightMargin=inch,
            leftMargin=inch,
            topMargin=1.5 * inch,
            bottomMargin=inch
        )
        
        # Estilos
        styles = getSampleStyleSheet()
        
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=20,
            textColor=colors.HexColor('#1e40af'),
            spaceAfter=30,
            alignment=TA_CENTER
        )
        
        # Contenido
        story = []
        
        # Título
        story.append(Paragraph(
            f"CIERRE DE CAJA<br/>{cierre.fecha.strftime('%d de %B, %Y')}",
            title_style
        ))
        story.append(Spacer(1, 0.3 * inch))
        
        # Resumen de Ventas
        ventas_data = [
            ['Concepto', 'Valor'],
            ['Cantidad de Ventas', str(cierre.cantidad_ventas)],
            ['Total Vendido', f"${cierre.total_ventas:,.2f}"],
            ['Total Descuentos', f"${cierre.total_descuentos:,.2f}"],
            ['Anulaciones', f"{cierre.cantidad_anulaciones} (${cierre.total_anulaciones:,.2f})"],
        ]
        
        ventas_table = Table(ventas_data, colWidths=[3 * inch, 2.5 * inch])
        ventas_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2563eb')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold', 11),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONT', (0, 1), (-1, -1), 'Helvetica', 10),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f3f4f6')]),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('BACKGROUND', (0, 2), (-1, 2), colors.HexColor('#dbeafe')),
            ('FONT', (0, 2), (-1, 2), 'Helvetica-Bold', 10),
        ]))
        
        story.append(ventas_table)
        story.append(Spacer(1, 0.3 * inch))
        
        # Desglose por método de pago
        total_pagos = (
            cierre.total_efectivo + 
            cierre.total_transferencia + 
            cierre.total_tarjeta
        )
        
        pagos_data = [
            ['Metodo', 'Monto', '% del Total'],
            [
                'Efectivo',
                f"${cierre.total_efectivo:,.2f}",
                f"{(cierre.total_efectivo / total_pagos * 100) if total_pagos > 0 else 0:.1f}%"
            ],
            [
                'Transferencia',
                f"${cierre.total_transferencia:,.2f}",
                f"{(cierre.total_transferencia / total_pagos * 100) if total_pagos > 0 else 0:.1f}%"
            ],
            [
                'Tarjeta',
                f"${cierre.total_tarjeta:,.2f}",
                f"{(cierre.total_tarjeta / total_pagos * 100) if total_pagos > 0 else 0:.1f}%"
            ],
            ['TOTAL', f"${total_pagos:,.2f}", '100.0%'],
        ]
        
        pagos_table = Table(pagos_data, colWidths=[2.5 * inch, 2 * inch, 1.5 * inch])
        pagos_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#16a34a')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold', 11),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
            ('FONT', (0, 1), (-1, -2), 'Helvetica', 10),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f0fdf4')]),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#16a34a')),
            ('TEXTCOLOR', (0, -1), (-1, -1), colors.whitesmoke),
            ('FONT', (0, -1), (-1, -1), 'Helvetica-Bold', 11),
        ]))
        
        story.append(pagos_table)
        
        # Construir PDF
        doc.build(story, onFirstPage=PDFGenerator._get_header_footer, 
                 onLaterPages=PDFGenerator._get_header_footer)
        
        return filepath