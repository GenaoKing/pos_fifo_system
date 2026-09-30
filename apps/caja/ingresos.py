"""Ingresos cobrados del día local, independientes del fondo de caja."""
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from apps.cuentas_por_cobrar.models import PagoCxC
from apps.sucursales.models import get_sucursal_actual
from apps.ventas.models import Pago


def ingresos_del_dia(turno):
    hoy = timezone.localdate()
    sucursal = turno.caja.sucursal or get_sucursal_actual()

    pagos_ventas = Pago.objects.filter(
        venta__fecha_venta__date=hoy, venta__estado='COMPLETADA',
        venta__usuario=turno.usuario,
        metodo__in=['EFECTIVO', 'TRANSFERENCIA', 'TARJETA'],
    )
    pagos_cxc = PagoCxC.objects.filter(
        fecha_pago__date=hoy, estado='APLICADO',
        registrado_por=turno.usuario,
    )

    # Las cajas creadas antes de introducir sucursales conservan sucursal=NULL.
    # Las ventas nuevas, sin embargo, ya reciben la sucursal configurada. Usar
    # directamente turno.caja.sucursal dejaba el total diario en cero en esas
    # instalaciones actualizadas. La sucursal efectiva cubre ese caso y las
    # filas legacy NULL siguen formando parte del alcance local.
    if sucursal is not None:
        pagos_ventas = pagos_ventas.filter(
            Q(venta__sucursal=sucursal) | Q(venta__sucursal__isnull=True),
        )
        pagos_cxc = pagos_cxc.filter(
            Q(cuenta__sucursal=sucursal) | Q(cuenta__sucursal__isnull=True),
        )

    ventas = pagos_ventas.aggregate(total=Sum('monto'))['total'] or Decimal('0.00')
    cobros = pagos_cxc.aggregate(total=Sum('monto'))['total'] or Decimal('0.00')
    return {'fecha': hoy.isoformat(), 'ventas': str(ventas),
            'cxc': str(cobros), 'total': str(ventas + cobros)}
