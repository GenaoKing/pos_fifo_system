from __future__ import annotations

import os
from datetime import date as date_cls
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Iterable, Sequence
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


PAGE_SIZE = letter
PAGE_WIDTH, PAGE_HEIGHT = PAGE_SIZE
MARGIN_X = 0.65 * inch
MARGIN_TOP = 0.55 * inch
MARGIN_BOTTOM = 0.7 * inch
CONTENT_WIDTH = PAGE_WIDTH - (2 * MARGIN_X)

PRIMARY = colors.HexColor('#2563eb')
PRIMARY_DARK = colors.HexColor('#1d4ed8')
TEAL = colors.HexColor('#0f766e')
GREEN = colors.HexColor('#16a34a')
AMBER = colors.HexColor('#d97706')
RED = colors.HexColor('#dc2626')
INK = colors.HexColor('#111827')
MUTED = colors.HexColor('#6b7280')
BORDER = colors.HexColor('#d1d5db')
LIGHT = colors.HexColor('#f8fafc')
LIGHT_BLUE = colors.HexColor('#dbeafe')
WHITE = colors.white


STATUS_COLORS = {
    'PAGADA': GREEN,
    'PAGADO': GREEN,
    'COMPLETADA': GREEN,
    'ABIERTA': PRIMARY,
    'PENDIENTE': AMBER,
    'PARCIAL': TEAL,
    'VENCIDA': RED,
    'ANULADA': RED,
    'ANULADO': RED,
    'APLICADO': GREEN,
}


def get_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        'PdfBrand',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=17,
        leading=20,
        textColor=PRIMARY_DARK,
    ))
    styles.add(ParagraphStyle(
        'PdfMuted',
        parent=styles['Normal'],
        fontSize=8,
        leading=10,
        textColor=MUTED,
    ))
    styles.add(ParagraphStyle(
        'PdfTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=23,
        alignment=TA_CENTER,
        textColor=PRIMARY_DARK,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        'PdfSubtitle',
        parent=styles['Normal'],
        fontSize=9,
        leading=11,
        alignment=TA_CENTER,
        textColor=MUTED,
        spaceAfter=12,
    ))
    styles.add(ParagraphStyle(
        'PdfSection',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=14,
        textColor=PRIMARY_DARK,
        spaceBefore=10,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        'PdfLabel',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        textColor=MUTED,
    ))
    styles.add(ParagraphStyle(
        'PdfValue',
        parent=styles['Normal'],
        fontSize=8.5,
        leading=10.5,
        textColor=INK,
    ))
    styles.add(ParagraphStyle(
        'PdfCell',
        parent=styles['Normal'],
        fontSize=8,
        leading=10,
        textColor=INK,
    ))
    styles.add(ParagraphStyle(
        'PdfCellCenter',
        parent=styles['PdfCell'],
        alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        'PdfCellRight',
        parent=styles['PdfCell'],
        alignment=TA_RIGHT,
    ))
    styles.add(ParagraphStyle(
        'PdfHeaderCell',
        parent=styles['PdfCellCenter'],
        fontName='Helvetica-Bold',
        textColor=WHITE,
    ))
    styles.add(ParagraphStyle(
        'PdfNote',
        parent=styles['Normal'],
        fontSize=8,
        leading=11,
        textColor=MUTED,
        alignment=TA_CENTER,
    ))
    return styles


def clean(value) -> str:
    if value is None:
        return '-'
    text = str(value)
    return escape(text).replace('\n', '<br/>')


def para(value, style=None, *, bold: bool = False, color=None):
    style = style or get_styles()['PdfCell']
    text = clean(value)
    if bold:
        text = f'<b>{text}</b>'
    if color:
        hex_value = color.hexval()
        if hex_value.startswith('0x'):
            hex_value = f'#{hex_value[2:]}'
        text = f'<font color="{hex_value}">{text}</font>'
    return Paragraph(text, style)


def money(value) -> str:
    try:
        amount = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal('0.00')
    return f'${amount:,.2f}'


def date(value, include_time: bool = False) -> str:
    if not value:
        return '-'
    if isinstance(value, datetime):
        value = timezone.localtime(value) if timezone.is_aware(value) else value
        return value.strftime('%d/%m/%Y %I:%M %p') if include_time else value.strftime('%d/%m/%Y')
    if isinstance(value, date_cls):
        return value.strftime('%d/%m/%Y')
    return str(value)


def document(target=None, *, pagesize=PAGE_SIZE) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        target or BytesIO(),
        pagesize=pagesize,
        rightMargin=MARGIN_X,
        leftMargin=MARGIN_X,
        topMargin=MARGIN_TOP,
        bottomMargin=MARGIN_BOTTOM,
    )


def _config_or_default(config=None):
    if config is not None:
        return config
    from apps.configuracion.utils import get_config
    return get_config()


def _logo_path(config) -> str | None:
    logo = getattr(config, 'logo', None)
    if not logo:
        return None
    try:
        path = logo.path
    except (ValueError, AttributeError):
        return None
    return path if path and os.path.exists(path) else None


def business_header(config=None, *, width: float = CONTENT_WIDTH):
    config = _config_or_default(config)
    styles = get_styles()
    name = getattr(config, 'nombre_negocio', '') or 'Sistema POS'
    rnc = getattr(config, 'rnc', '') or ''
    phone = getattr(config, 'telefono', '') or ''
    address = getattr(config, 'direccion', '') or ''

    meta = '  |  '.join(part for part in [
        f'RNC: {rnc}' if rnc else '',
        f'Tel: {phone}' if phone else '',
    ] if part)
    lines = [para(name, styles['PdfBrand'])]
    if meta:
        lines.append(para(meta, styles['PdfMuted']))
    if address:
        lines.append(para(address, styles['PdfMuted']))
    info = lines

    logo_path = _logo_path(config)
    if logo_path:
        left = Image(logo_path, width=0.9 * inch, height=0.9 * inch)
        data = [[left, info]]
        col_widths = [1.05 * inch, width - 1.05 * inch]
    else:
        data = [[info]]
        col_widths = [width]

    table = Table(data, colWidths=col_widths)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT),
        ('BOX', (0, 0), (-1, -1), 0.6, BORDER),
        ('LINEBELOW', (0, 0), (-1, -1), 2, PRIMARY),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER' if not logo_path else 'LEFT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    return [table, Spacer(1, 10)]


def document_title(title: str, subtitle: str | None = None):
    styles = get_styles()
    flowables = [Paragraph(clean(title).upper(), styles['PdfTitle'])]
    if subtitle:
        flowables.append(Paragraph(clean(subtitle), styles['PdfSubtitle']))
    return flowables


def section_title(title: str):
    return Paragraph(clean(title).upper(), get_styles()['PdfSection'])


def info_grid(rows: Sequence[Sequence[tuple[str, object]]], *, width: float = CONTENT_WIDTH):
    styles = get_styles()
    data = []
    max_pairs = max((len(row) for row in rows), default=1)
    pair_width = width / max_pairs
    col_widths = []
    for _ in range(max_pairs):
        col_widths.extend([pair_width * 0.34, pair_width * 0.66])

    for row in rows:
        cells = []
        for label, value in row:
            cells.append(para(label, styles['PdfLabel']))
            cells.append(para(value, styles['PdfValue']))
        while len(cells) < max_pairs * 2:
            cells.extend(['', ''])
        data.append(cells)

    table = Table(data or [['']], colWidths=col_widths if data else [width])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT),
        ('BOX', (0, 0), (-1, -1), 0.5, BORDER),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#e5e7eb')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 7),
        ('RIGHTPADDING', (0, 0), (-1, -1), 7),
    ]))
    return table


def _normalize_widths(col_widths: Sequence[float] | None, columns: int, width: float):
    if col_widths:
        total = sum(col_widths)
        if total <= 1.01:
            return [w * width for w in col_widths]
        if total != width:
            factor = width / total
            return [w * factor for w in col_widths]
        return list(col_widths)
    return [width / columns for _ in range(columns)]


def _cell_style(styles, align: str):
    align = (align or 'LEFT').upper()
    if align == 'RIGHT':
        return styles['PdfCellRight']
    if align == 'CENTER':
        return styles['PdfCellCenter']
    return styles['PdfCell']


def standard_table(
    headers: Sequence[object],
    rows: Iterable[Sequence[object]],
    *,
    col_widths: Sequence[float] | None = None,
    aligns: Sequence[str] | None = None,
    status_col: int | None = None,
    width: float = CONTENT_WIDTH,
):
    styles = get_styles()
    rows = list(rows)
    column_count = len(headers) or 1
    widths = _normalize_widths(col_widths, column_count, width)
    aligns = list(aligns or ['LEFT'] * column_count)
    data = [[para(h, styles['PdfHeaderCell'], bold=True) for h in headers]]

    for row in rows:
        rendered = []
        for index, value in enumerate(row):
            style = _cell_style(styles, aligns[index] if index < len(aligns) else 'LEFT')
            color = None
            if status_col is not None and index == status_col:
                color = STATUS_COLORS.get(str(value).upper())
            rendered.append(para(value, style, color=color, bold=bool(color)))
        data.append(rendered)

    if not rows:
        empty = [para('Sin datos', styles['PdfCellCenter'])]
        empty.extend(['' for _ in range(max(column_count - 1, 0))])
        data.append(empty)

    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, LIGHT]),
        ('GRID', (0, 0), (-1, -1), 0.35, BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    return table


def totals_table(items: Sequence[tuple[str, object, str | None]], *, width: float = CONTENT_WIDTH):
    styles = get_styles()
    rows = []
    for item in items:
        label, value, *rest = item
        kind = rest[0] if rest else None
        color = RED if kind == 'negative' else PRIMARY_DARK if kind == 'total' else INK
        rows.append([
            '',
            para(label, styles['PdfCellRight'], bold=True),
            para(value, styles['PdfCellRight'], bold=(kind == 'total'), color=color),
        ])
    table = Table(rows, colWidths=[width - 2.5 * inch, 1.2 * inch, 1.3 * inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (1, -1), (-1, -1), LIGHT_BLUE),
        ('BOX', (1, -1), (-1, -1), 0.5, PRIMARY),
        ('LINEABOVE', (1, -1), (-1, -1), 1.2, PRIMARY),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    return table


def signature_block(left_title, left_name, right_title, right_name, *, width: float = CONTENT_WIDTH):
    styles = get_styles()
    data = [
        ['_' * 32, '', '_' * 32],
        [para(left_title, styles['PdfCellCenter'], bold=True), '', para(right_title, styles['PdfCellCenter'], bold=True)],
        [para(left_name, styles['PdfMuted']), '', para(right_name, styles['PdfMuted'])],
    ]
    table = Table(data, colWidths=[width * 0.42, width * 0.16, width * 0.42])
    table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    return table


def note(text: str):
    return Paragraph(clean(text), get_styles()['PdfNote'])


def footer_canvas(canvas_obj, doc, *, label: str = ''):
    canvas_obj.saveState()
    canvas_obj.setStrokeColor(PRIMARY)
    canvas_obj.setLineWidth(1)
    canvas_obj.line(MARGIN_X, 0.52 * inch, PAGE_WIDTH - MARGIN_X, 0.52 * inch)
    canvas_obj.setFont('Helvetica', 7.5)
    canvas_obj.setFillColor(MUTED)
    canvas_obj.drawString(MARGIN_X, 0.36 * inch, f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    if label:
        canvas_obj.drawCentredString(PAGE_WIDTH / 2, 0.36 * inch, str(label).replace('\n', ' '))
    canvas_obj.drawRightString(PAGE_WIDTH - MARGIN_X, 0.36 * inch, f"Pagina {canvas_obj.getPageNumber()}")
    canvas_obj.restoreState()
