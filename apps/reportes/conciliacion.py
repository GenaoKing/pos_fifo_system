"""Cuadre por fechas: flujos diarios y arqueos al cierre, sin persistir PDFs."""
from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from reportlab.lib.pagesizes import landscape, letter
from reportlab.platypus import KeepTogether, Spacer, TableStyle

from apps.caja.models import MovimientoCaja, TurnoCaja
from apps.common.pdf import standard as pdf
from apps.configuracion.utils import config_para_documento
from apps.cuentas_por_cobrar.models import PagoCxC
from apps.ventas.models import Pago, Venta

ZERO = Decimal('0.00')
METODOS = ('EFECTIVO', 'TRANSFERENCIA', 'TARJETA')
CAMPOS = ('ventas', 'credito', 'cobros_ventas', 'cobros_cxc', 'ingresos',
          'reposiciones', 'gastos', 'retiros', 'neto', 'esperado', 'contado', 'diferencia')


def generar_conciliacion(inicio, fin, alcance):
    """Agrupa en SQL por día local; incluye días sin actividad y usa Decimal.

    Ventas/pagos de venta usan fecha_venta (contrato de reportes); CxC su
    fecha_pago. Arqueos se atribuyen a fecha_cierre, incluso turnos nocturnos.
    Los arqueos guardados nunca se reconstruyen a partir de fechas de pagos.
    """
    dias = {}
    fecha = inicio
    while fecha <= fin:
        dias[fecha] = dict.fromkeys(CAMPOS, ZERO) | {'fecha': fecha, 'cierres': 0}
        fecha += timedelta(days=1)

    def por_dia(qs, campo_fecha):
        return qs.filter(**{f'{campo_fecha}__date__range': (inicio, fin)}).order_by().annotate(
            dia=TruncDate(campo_fecha))

    ventas = alcance.filtrar(Venta.objects.filter(estado='COMPLETADA'))
    for fila in por_dia(ventas, 'fecha_venta').values('dia').annotate(total=Sum('total')):
        dias[fila['dia']]['ventas'] = fila['total']

    pagos = alcance.filtrar(Pago.objects.filter(venta__estado='COMPLETADA'), 'venta__sucursal')
    metodos = {m: {'metodo': m.title(), 'ventas': ZERO, 'cxc': ZERO, 'total': ZERO} for m in METODOS}
    for fila in por_dia(pagos, 'venta__fecha_venta').values('dia', 'metodo').annotate(total=Sum('monto')):
        if fila['metodo'] == 'CREDITO':
            dias[fila['dia']]['credito'] += fila['total']
        elif fila['metodo'] in METODOS:
            dias[fila['dia']]['cobros_ventas'] += fila['total']
            metodos[fila['metodo']]['ventas'] += fila['total']

    cobros = alcance.filtrar(PagoCxC.objects.filter(estado='APLICADO'), 'cuenta__sucursal')
    for fila in por_dia(cobros, 'fecha_pago').values('dia', 'metodo').annotate(total=Sum('monto')):
        dias[fila['dia']]['cobros_cxc'] += fila['total']
        if fila['metodo'] in metodos:
            metodos[fila['metodo']]['cxc'] += fila['total']

    movimientos = alcance.filtrar(MovimientoCaja.objects.all(), 'turno__caja__sucursal')
    tipos = {'INGRESO': 'reposiciones', 'GASTO': 'gastos', 'RETIRO': 'retiros'}
    for fila in por_dia(movimientos, 'fecha').values('dia', 'tipo').annotate(total=Sum('monto')):
        dias[fila['dia']][tipos[fila['tipo']]] += fila['total']

    turnos = alcance.filtrar(TurnoCaja.objects.all(), 'caja__sucursal')
    for fila in por_dia(turnos.filter(estado='CERRADO'), 'fecha_cierre').values('dia').annotate(
        esperado=Sum('monto_esperado'), contado=Sum('monto_contado'),
        diferencia=Sum('diferencia'), cierres=Count('pk'),
    ):
        for campo in ('esperado', 'contado', 'diferencia', 'cierres'):
            dias[fila['dia']][campo] = fila[campo] or ZERO

    for dia in dias.values():
        dia['ingresos'] = dia['cobros_ventas'] + dia['cobros_cxc']
        dia['neto'] = dia['ingresos'] + dia['reposiciones'] - dia['gastos'] - dia['retiros']
    for metodo in metodos.values():
        metodo['total'] = metodo['ventas'] + metodo['cxc']
    totales = {campo: sum((dia[campo] for dia in dias.values()), ZERO) for campo in CAMPOS}
    totales['cierres'] = sum(dia['cierres'] for dia in dias.values())
    return {'inicio': inicio, 'fin': fin, 'dias': list(dias.values()),
            'totales': totales, 'metodos': list(metodos.values()),
            'turnos_abiertos': turnos.filter(estado='ABIERTO', fecha_apertura__date__lte=fin).count()}


def generar_pdf(reporte, sucursal, ambito):
    buffer = BytesIO()
    doc = pdf.document(buffer, pagesize=landscape(letter))
    width = doc.width

    def tabla(headers, rows):
        table = pdf.standard_table(headers, rows, width=width,
                                   aligns=['LEFT'] + ['RIGHT'] * (len(headers) - 1))
        table.setStyle(TableStyle([
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        return table

    elements = pdf.business_header(config_para_documento(sucursal), width=width)
    elements += pdf.document_title('Cuadre por período',
                                   f"{pdf.date(reporte['inicio'])} al {pdf.date(reporte['fin'])} · {ambito}")
    elements += [pdf.note('Ingresos = cobros de ventas + cobros CxC. El crédito pendiente no es ingreso. '
                         'Incluye la inicial una sola vez. Importes vigentes al generar; excluye anulados.')]
    elements += [pdf.info_grid([[
        ('Cobros de ventas', pdf.money(reporte['totales']['cobros_ventas'])),
        ('Cobros CxC', pdf.money(reporte['totales']['cobros_cxc'])),
        ('Ingresos recibidos', pdf.money(reporte['totales']['ingresos'])),
    ]], width=width)]
    sections = [
        ('Ventas y cobros por día', ['Fecha', 'Ventas facturadas', 'Crédito generado', 'Cobros ventas', 'Cobros CxC', 'Ingresos'],
         ['ventas', 'credito', 'cobros_ventas', 'cobros_cxc', 'ingresos']),
        ('Movimientos y flujo neto', ['Fecha', 'Ingresos', 'Reposiciones', 'Gastos', 'Retiros', 'Flujo neto'],
         ['ingresos', 'reposiciones', 'gastos', 'retiros', 'neto']),
    ]
    for titulo, headers, campos in sections:
        rows = [[pdf.date(d['fecha'])] + [pdf.money(d[c]) for c in campos] for d in reporte['dias']]
        rows.append(['TOTAL'] + [pdf.money(reporte['totales'][c]) for c in campos])
        elements += [pdf.section_title(titulo), tabla(headers, rows), Spacer(1, 10)]
    elements += [pdf.note('El flujo neto incluye todos los medios de pago; no es utilidad ni saldo físico. '
                         'Las reposiciones no incluyen fondos de apertura.')]
    elements += [pdf.section_title('Ingresos por medio de pago'), tabla(
        ['Medio', 'Cobros ventas', 'Cobros CxC', 'Total'],
        [[m['metodo'], pdf.money(m['ventas']), pdf.money(m['cxc']), pdf.money(m['total'])] for m in reporte['metodos']],
        )]
    rows = [[pdf.date(d['fecha']), str(d['cierres']), pdf.money(d['esperado']),
             pdf.money(d['contado']), pdf.money(d['diferencia'])] for d in reporte['dias'] if d['cierres']]
    if rows:
        t = reporte['totales']
        rows.append(['TOTAL', str(t['cierres']), pdf.money(t['esperado']), pdf.money(t['contado']), pdf.money(t['diferencia'])])
        elements += [pdf.section_title('Arqueos de efectivo por fecha de cierre'), tabla(
            ['Fecha', 'Cierres', 'Esperado', 'Contado', 'Diferencia'], rows)]
    elements += [KeepTogether([pdf.note(f"Turnos aún abiertos iniciados hasta el fin del período: {reporte['turnos_abiertos']}. "
                         'Los arqueos son importes guardados al cerrar. Su suma incluye fondos reutilizados '
                         'entre turnos y no equivale al ingreso del período.'), Spacer(1, 18),
                 pdf.signature_block('Preparado por', '', 'Revisado por', '', width=width)])]
    doc.build(elements, onFirstPage=pdf.footer_canvas, onLaterPages=pdf.footer_canvas)
    return buffer.getvalue()
