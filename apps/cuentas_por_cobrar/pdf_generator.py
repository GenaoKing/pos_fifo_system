"""
Generador de PDF del estado de cuenta de un cliente (CxC).
"""
from io import BytesIO

from django.utils import timezone
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
    standard_table,
)
from apps.configuracion.utils import get_config


class EstadoCuentaPDF:
    """PDF del estado de cuenta: resumen del cliente, cuentas, cuotas pendientes y abonos."""

    def __init__(self, cliente, cuentas, resumen):
        self.cliente = cliente
        self.cuentas = list(cuentas)
        self.resumen = resumen
        self.config = get_config()

    def _resumen_cliente(self):
        proximo = self.resumen.get('proximo_vencimiento')
        return [
            section_title('Resumen de credito'),
            info_grid([
                [('Cliente', self.cliente.nombre), ('Fecha', date(timezone.localtime(), include_time=True))],
                [('Cedula/RNC', self.cliente.cedula_rnc or '-'), ('Limite de credito', money(self.resumen['limite_credito']))],
                [('Saldo pendiente', money(self.resumen['saldo_pendiente'])), ('Disponible', money(self.resumen['credito_disponible']))],
                [('Monto vencido', money(self.resumen['monto_vencido'])), ('Proximo vencimiento', date(proximo))],
            ]),
            Spacer(1, 10),
        ]

    def _tabla_cuentas(self):
        rows = []
        for cuenta in self.cuentas:
            rows.append([
                cuenta.venta.numero_venta,
                date(cuenta.fecha_emision),
                date(cuenta.fecha_limite),
                money(cuenta.saldo_original),
                money(cuenta.monto_interes),
                money(cuenta.monto_financiado),
                money(cuenta.saldo),
                cuenta.estado,
            ])
        return [
            section_title('Cuentas'),
            standard_table(
                ['Venta', 'Emision', 'Vence', 'Capital', 'Interes', 'Total', 'Saldo', 'Estado'],
                rows,
                col_widths=[0.16, 0.11, 0.11, 0.13, 0.12, 0.13, 0.13, 0.11],
                aligns=['LEFT', 'CENTER', 'CENTER', 'RIGHT', 'RIGHT', 'RIGHT', 'RIGHT', 'CENTER'],
                status_col=7,
            ),
            Spacer(1, 10),
        ]

    def _tabla_cuotas(self):
        cuotas = [
            (cuenta, cuota)
            for cuenta in self.cuentas
            for cuota in cuenta.cuotas.all()
            if cuota.estado in ('PENDIENTE', 'PARCIAL', 'VENCIDA')
        ]
        if not cuotas:
            return []
        rows = [
            [
                cuenta.venta.numero_venta,
                cuota.numero,
                date(cuota.fecha_vencimiento),
                money(cuota.monto),
                money(cuota.saldo),
                cuota.estado,
            ]
            for cuenta, cuota in cuotas
        ]
        return [
            section_title('Cuotas pendientes'),
            standard_table(
                ['Venta', 'Cuota', 'Vence', 'Monto', 'Saldo', 'Estado'],
                rows,
                col_widths=[0.22, 0.10, 0.16, 0.18, 0.18, 0.16],
                aligns=['LEFT', 'CENTER', 'CENTER', 'RIGHT', 'RIGHT', 'CENTER'],
                status_col=5,
            ),
            Spacer(1, 10),
        ]

    def _tabla_abonos(self):
        abonos = [
            (cuenta, pago)
            for cuenta in self.cuentas
            for pago in cuenta.pagos_cxc.all()
        ]
        if not abonos:
            return []
        abonos.sort(key=lambda par: par[1].fecha_pago, reverse=True)

        # El corte a 50 era SILENCIOSO: un estado de cuenta de un cliente con
        # mucha actividad parecia completo y no lo era, lo que complica
        # conciliaciones y disputas. Se conserva el tope (el PDF tiene que
        # seguir siendo manejable) pero ahora se declara.
        TOPE_ABONOS = 50
        mostrados = abonos[:TOPE_ABONOS]
        omitidos = len(abonos) - len(mostrados)

        rows = [
            [
                date(pago.fecha_pago, include_time=True),
                cuenta.venta.numero_venta,
                pago.metodo,
                pago.referencia or '-',
                money(pago.monto),
                pago.estado,
            ]
            for cuenta, pago in mostrados
        ]

        titulo = 'Abonos'
        if omitidos:
            titulo = (
                f'Abonos (mostrando los {len(mostrados)} mas recientes de '
                f'{len(abonos)}; {omitidos} no listados)'
            )

        return [
            section_title(titulo),
            standard_table(
                ['Fecha', 'Venta', 'Metodo', 'Referencia', 'Monto', 'Estado'],
                rows,
                col_widths=[0.22, 0.16, 0.15, 0.20, 0.14, 0.13],
                aligns=['CENTER', 'LEFT', 'CENTER', 'LEFT', 'RIGHT', 'CENTER'],
                status_col=5,
            ),
            Spacer(1, 10),
        ]

    def generar(self) -> BytesIO:
        buffer = BytesIO()
        doc = document(buffer)
        elements = []
        elements.extend(business_header(self.config))
        elements.extend(document_title('Estado de cuenta', self.cliente.nombre))
        elements.extend(self._resumen_cliente())
        elements.extend(self._tabla_cuentas())
        elements.extend(self._tabla_cuotas())
        elements.extend(self._tabla_abonos())
        elements.append(note(
            'Los abonos anulados no afectan el saldo. Capital e interes se informan por separado.'
        ))

        doc.build(
            elements,
            onFirstPage=lambda canvas, doc_obj: footer_canvas(canvas, doc_obj, label='Estado de cuenta CxC'),
            onLaterPages=lambda canvas, doc_obj: footer_canvas(canvas, doc_obj, label='Estado de cuenta CxC'),
        )
        buffer.seek(0)
        return buffer
