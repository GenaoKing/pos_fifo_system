"""
Generador de PDF del estado de cuenta de un cliente (CxC).
apps/cuentas_por_cobrar/pdf_generator.py

Sigue el patron de apps/cotizaciones/pdf_generator.py (reportlab platypus,
BytesIO, ConfiguracionNegocio para el header).
"""
from decimal import Decimal
from io import BytesIO

from django.utils import timezone

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.configuracion.models import ConfiguracionNegocio

AZUL = colors.HexColor('#1e40af')
GRIS = colors.HexColor('#6b7280')
GRIS_CLARO = colors.HexColor('#f3f4f6')


def _money(value) -> str:
    return f"${Decimal(str(value or 0)):,.2f}"


class EstadoCuentaPDF:
    """PDF del estado de cuenta: resumen del cliente, cuentas, cuotas pendientes y abonos."""

    def __init__(self, cliente, cuentas, resumen):
        self.cliente = cliente
        self.cuentas = list(cuentas)
        self.resumen = resumen
        self.config = ConfiguracionNegocio.load()
        self.styles = getSampleStyleSheet()
        self.styles.add(ParagraphStyle(
            name='TituloEstado',
            parent=self.styles['Heading1'],
            fontSize=18,
            textColor=AZUL,
            alignment=TA_CENTER,
            spaceAfter=2,
        ))
        self.styles.add(ParagraphStyle(
            name='SubtituloEstado',
            parent=self.styles['Normal'],
            fontSize=9,
            textColor=GRIS,
            alignment=TA_CENTER,
            spaceAfter=12,
        ))
        self.styles.add(ParagraphStyle(
            name='SeccionEstado',
            parent=self.styles['Normal'],
            fontSize=11,
            textColor=AZUL,
            fontName='Helvetica-Bold',
            spaceBefore=10,
            spaceAfter=6,
        ))
        self.styles.add(ParagraphStyle(
            name='CeldaDerecha',
            parent=self.styles['Normal'],
            fontSize=8,
            alignment=TA_RIGHT,
        ))

    def generar(self) -> BytesIO:
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            topMargin=0.6 * inch,
            bottomMargin=0.6 * inch,
            leftMargin=0.7 * inch,
            rightMargin=0.7 * inch,
        )
        elements = []

        negocio = self.config.nombre_negocio or 'Sistema POS'
        elements.append(Paragraph(negocio, self.styles['TituloEstado']))
        linea_negocio = ' · '.join(
            p for p in [
                f'RNC: {self.config.rnc}' if self.config.rnc else '',
                self.config.telefono or '',
                self.config.direccion or '',
            ] if p
        )
        elements.append(Paragraph(linea_negocio or ' ', self.styles['SubtituloEstado']))

        elements.append(Paragraph('ESTADO DE CUENTA', self.styles['SeccionEstado']))
        fecha = timezone.localtime().strftime('%d/%m/%Y %I:%M %p')
        datos_cliente = [
            ['Cliente:', self.cliente.nombre, 'Fecha:', fecha],
            ['Cedula/RNC:', self.cliente.cedula_rnc or '-', 'Limite de credito:', _money(self.resumen['limite_credito'])],
            ['Saldo pendiente:', _money(self.resumen['saldo_pendiente']), 'Disponible:', _money(self.resumen['credito_disponible'])],
            ['Monto vencido:', _money(self.resumen['monto_vencido']), 'Proximo vencimiento:',
             self.resumen['proximo_vencimiento'].strftime('%d/%m/%Y') if self.resumen['proximo_vencimiento'] else '-'],
        ]
        tabla_cliente = Table(datos_cliente, colWidths=[1.3 * inch, 2.2 * inch, 1.5 * inch, 2.0 * inch])
        tabla_cliente.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
            ('TEXTCOLOR', (0, 0), (0, -1), GRIS),
            ('TEXTCOLOR', (2, 0), (2, -1), GRIS),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(tabla_cliente)

        elements.append(Paragraph('CUENTAS', self.styles['SeccionEstado']))
        filas = [['Venta', 'Emision', 'Vence', 'Capital', 'Interes', 'Total', 'Saldo', 'Estado']]
        for cuenta in self.cuentas:
            filas.append([
                cuenta.venta.numero_venta,
                cuenta.fecha_emision.strftime('%d/%m/%Y'),
                cuenta.fecha_limite.strftime('%d/%m/%Y'),
                _money(cuenta.saldo_original),
                _money(cuenta.monto_interes),
                _money(cuenta.monto_financiado),
                _money(cuenta.saldo),
                cuenta.estado,
            ])
        tabla_cuentas = Table(filas, repeatRows=1)
        tabla_cuentas.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BACKGROUND', (0, 0), (-1, 0), AZUL),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (3, 0), (6, -1), 'RIGHT'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, GRIS_CLARO]),
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#d1d5db')),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(tabla_cuentas)

        cuotas_pendientes = [
            (cuenta, cuota)
            for cuenta in self.cuentas
            for cuota in cuenta.cuotas.all()
            if cuota.estado in ('PENDIENTE', 'PARCIAL', 'VENCIDA')
        ]
        if cuotas_pendientes:
            elements.append(Paragraph('CUOTAS PENDIENTES', self.styles['SeccionEstado']))
            filas = [['Venta', 'Cuota', 'Vence', 'Monto', 'Saldo', 'Estado']]
            for cuenta, cuota in cuotas_pendientes:
                filas.append([
                    cuenta.venta.numero_venta,
                    str(cuota.numero),
                    cuota.fecha_vencimiento.strftime('%d/%m/%Y'),
                    _money(cuota.monto),
                    _money(cuota.saldo),
                    cuota.estado,
                ])
            tabla_cuotas = Table(filas, repeatRows=1)
            tabla_cuotas.setStyle(TableStyle([
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('BACKGROUND', (0, 0), (-1, 0), AZUL),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (3, 0), (4, -1), 'RIGHT'),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, GRIS_CLARO]),
                ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#d1d5db')),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
            ]))
            elements.append(tabla_cuotas)

        abonos = [
            (cuenta, pago)
            for cuenta in self.cuentas
            for pago in cuenta.pagos_cxc.all()
        ]
        if abonos:
            abonos.sort(key=lambda par: par[1].fecha_pago, reverse=True)
            elements.append(Paragraph('ABONOS', self.styles['SeccionEstado']))
            filas = [['Fecha', 'Venta', 'Metodo', 'Referencia', 'Monto', 'Estado']]
            for cuenta, pago in abonos[:50]:
                filas.append([
                    timezone.localtime(pago.fecha_pago).strftime('%d/%m/%Y %H:%M'),
                    cuenta.venta.numero_venta,
                    pago.metodo,
                    pago.referencia or '-',
                    _money(pago.monto),
                    pago.estado,
                ])
            tabla_abonos = Table(filas, repeatRows=1)
            tabla_abonos.setStyle(TableStyle([
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('BACKGROUND', (0, 0), (-1, 0), AZUL),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (4, 0), (4, -1), 'RIGHT'),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, GRIS_CLARO]),
                ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#d1d5db')),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
            ]))
            elements.append(tabla_abonos)

        elements.append(Spacer(1, 14))
        elements.append(Paragraph(
            'Los abonos ANULADOS no afectan el saldo. Capital e interes se informan por separado.',
            self.styles['SubtituloEstado'],
        ))

        doc.build(elements)
        buffer.seek(0)
        return buffer
