"""Ingresos cobrados del día local, independientes del fondo de caja."""
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.cuentas_por_cobrar.models import PagoCxC
from apps.ventas.models import Pago


def ingresos_del_dia(turno):
    hoy = timezone.localdate()
    ventas = Pago.objects.filter(
        venta__fecha_venta__date=hoy, venta__estado='COMPLETADA',
        venta__usuario=turno.usuario, venta__sucursal=turno.caja.sucursal,
        metodo__in=['EFECTIVO', 'TRANSFERENCIA', 'TARJETA'],
    ).aggregate(total=Sum('monto'))['total'] or Decimal('0.00')
    cobros = PagoCxC.objects.filter(
        fecha_pago__date=hoy, estado='APLICADO',
        registrado_por=turno.usuario, cuenta__sucursal=turno.caja.sucursal,
    ).aggregate(total=Sum('monto'))['total'] or Decimal('0.00')
    return {'fecha': hoy.isoformat(), 'ventas': str(ventas),
            'cxc': str(cobros), 'total': str(ventas + cobros)}
