"""
apps/sync/resumen.py

Agregados diarios para la Fase 3 (anti-entropia) de docs/ROADMAP_SYNC_CONFIABLE.md.

`calcular_resumen` corre en LOS DOS LADOS: en la sucursal (comando `conciliar`)
y en el cloud (endpoint `GET /api/v1/sync/resumen/`). Es la misma funcion en
la misma base de codigo, asi que una diferencia entre los dos resultados es
una divergencia real del dato, no un artefacto de tener dos implementaciones
que decidieron distinto por separado.

## Por que "hechos sin evento" (Fase 0/1) no alcanza

`verificar_sync` responde "de lo que hay en esta BD local, que no tiene
evento". No responde "lo que el cloud realmente tiene coincide con lo que la
sucursal cree que mando". Un evento puede existir, viajar, y aun asi el hecho
en el cloud terminar distinto (un handler que corta por clave natural y omite
en silencio, un dato que se edito despues por otra via). La conciliacion
compara el ESTADO AGREGADO final, no el trayecto.

## La regla de oro: agrupar por fecha de DOMINIO, nunca por fecha de aplicacion

`fecha_venta`, `fecha_emision`, `fecha_pago` son fechas del HECHO. La
`fecha_creacion` de un registro en el cloud es el momento en que el handler
APLICO el evento -- que puede ser horas o dias despues si el evento estuvo en
cola. Agrupar por esa fecha produce divergencias fantasma permanentes para
todo hecho aplicado con retraso. Por eso cada agregado usa el campo de fecha
que ya tiene el propio modelo de negocio (ver cada funcion abajo).

## Zona horaria

`fecha_venta` y `fecha_pago` son `DateTimeField` (aware, en UTC en la BD).
Truncar a "dia" requiere la zona LOCAL de la sucursal: dos lados con distinta
nocion de "hoy" divergirian siempre en la frontera de medianoche. Por eso la
sucursal manda su zona (`?tz=<IANA>`) y el cloud trunca con esa misma zona,
en vez de asumir la suya propia.

`fecha_emision` de CuentaPorCobrar es un `DateField` puesto con
`timezone.localdate()` en el momento de creacion: ya es una fecha local, sin
componente horario que truncar. Se agrupa tal cual.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import TruncDate


class TZInvalidaError(ValueError):
    """La zona horaria pedida no es un nombre IANA valido."""


def resolver_zona(nombre):
    try:
        return ZoneInfo(nombre)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise TZInvalidaError(f'Zona horaria invalida: "{nombre}"') from exc


def calcular_resumen(desde, hasta, tz, sucursal=None):
    """
    Agregados diarios de los hechos de negocio que la Fase 1 sincroniza,
    entre `desde` y `hasta` (date, inclusive) en la zona `tz` (nombre IANA).

    `sucursal`: instancia de Sucursal para filtrar. En la sucursal local es
    siempre None (una sola sucursal por BD). En el cloud es la sucursal del
    token que hizo el request -- una base de tenant puede en teoria alojar
    mas de una sucursal en el futuro, y este filtro ya lo deja correcto.

    Devuelve:
        {
          'ventas':    {'2026-08-18': {'count': 10, 'suma': '15400.00', 'anuladas': 1}},
          'cxc':       {'2026-08-18': {'count': 2, 'saldo': '3500.00'}},
          'cxc_pagos': {'2026-08-18': {'count': 3, 'monto': '1200.00'}},
        }

    Los dias sin actividad no aparecen (ni en cero): la comparacion itera la
    union de claves de ambos lados, asi que un dia ausente en un lado y
    presente en el otro con datos ES la divergencia.
    """
    zona = resolver_zona(tz)
    return {
        'ventas': _resumen_ventas(desde, hasta, zona, sucursal),
        'cxc': _resumen_cxc(desde, hasta, sucursal),
        'cxc_pagos': _resumen_cxc_pagos(desde, hasta, zona, sucursal),
    }


def _resumen_ventas(desde, hasta, zona, sucursal):
    from apps.ventas.models import Venta

    qs = Venta.objects.all()
    if sucursal is not None:
        qs = qs.filter(sucursal=sucursal)

    filas = (
        qs.annotate(dia=TruncDate('fecha_venta', tzinfo=zona))
        .filter(dia__gte=desde, dia__lte=hasta)
        .values('dia')
        .annotate(
            count=Count('id'),
            suma=Sum('total'),
            anuladas=Count('id', filter=Q(estado='ANULADA')),
            max_ref=Max('numero_venta'),
        )
    )

    resultado = {}
    for fila in filas:
        resultado[fila['dia'].isoformat()] = {
            'count': fila['count'],
            'suma': _dec(fila['suma']),
            'anuladas': fila['anuladas'],
            'max_ref': fila['max_ref'] or '',
        }
    return resultado


def _resumen_cxc(desde, hasta, sucursal):
    from apps.cuentas_por_cobrar.models import CuentaPorCobrar

    qs = CuentaPorCobrar.objects.filter(fecha_emision__gte=desde, fecha_emision__lte=hasta)
    if sucursal is not None:
        qs = qs.filter(sucursal=sucursal)

    filas = (
        qs.values('fecha_emision')
        .annotate(count=Count('id'), saldo=Sum('saldo'))
    )

    resultado = {}
    for fila in filas:
        dia = fila['fecha_emision']
        clave = dia.isoformat() if isinstance(dia, date) else str(dia)
        resultado[clave] = {
            'count': fila['count'],
            'saldo': _dec(fila['saldo']),
        }
    return resultado


def _resumen_cxc_pagos(desde, hasta, zona, sucursal):
    from apps.cuentas_por_cobrar.models import PagoCxC

    # `monto` solo cuenta pagos APLICADO: un pago anulado no debe seguir
    # sumando saldo cobrado en ninguno de los dos lados. Si la anulacion no
    # replico, el conteo total (con anulados) SI diverge y lo delata; por eso
    # se reporta tambien `count` sobre el total, no solo sobre lo aplicado.
    qs = PagoCxC.objects.all()
    if sucursal is not None:
        qs = qs.filter(cuenta__sucursal=sucursal)

    filas = (
        qs.annotate(dia=TruncDate('fecha_pago', tzinfo=zona))
        .filter(dia__gte=desde, dia__lte=hasta)
        .values('dia')
        .annotate(
            count=Count('id'),
            monto=Sum('monto', filter=Q(estado='APLICADO')),
        )
    )

    resultado = {}
    for fila in filas:
        resultado[fila['dia'].isoformat()] = {
            'count': fila['count'],
            'monto': _dec(fila['monto']),
        }
    return resultado


def _dec(valor):
    """Decimal -> string. Nunca float: perderia precision en el JSON."""
    return str(valor if valor is not None else Decimal('0.00'))


# ---------------------------------------------------------------------------
# Comparacion
# ---------------------------------------------------------------------------

# Que campos de cada tipo se comparan, y con que tolerancia. Los conteos
# comparan exacto; los montos toleran diferencias de centavos por redondeo de
# tipos numericos distintos entre el JSON y el Decimal local.
_CAMPOS_ENTEROS = {
    'ventas': ('count', 'anuladas'),
    'cxc': ('count',),
    'cxc_pagos': ('count',),
}
_CAMPOS_MONTO = {
    'ventas': ('suma',),
    'cxc': ('saldo',),
    'cxc_pagos': ('monto',),
}
_TOLERANCIA_MONTO = Decimal('0.01')


def comparar_resumenes(local, cloud):
    """
    Compara dos resumenes con la MISMA forma que devuelve `calcular_resumen`.

    Devuelve una lista de divergencias, cada una:
        {'tipo': 'ventas', 'dia': '2026-08-18', 'campo': 'count',
         'local': 10, 'cloud': 8}

    Un dia presente en un solo lado se compara contra los defaults (0 / '0.00')
    del otro: si local tiene una venta el 18 y cloud no tiene NADA ese dia,
    es exactamente la divergencia que hay que ver.
    """
    divergencias = []
    for tipo in ('ventas', 'cxc', 'cxc_pagos'):
        dias_local = local.get(tipo) or {}
        dias_cloud = cloud.get(tipo) or {}
        for dia in sorted(set(dias_local) | set(dias_cloud)):
            fila_local = dias_local.get(dia, {})
            fila_cloud = dias_cloud.get(dia, {})

            for campo in _CAMPOS_ENTEROS.get(tipo, ()):
                lv = int(fila_local.get(campo, 0) or 0)
                cv = int(fila_cloud.get(campo, 0) or 0)
                if lv != cv:
                    divergencias.append({
                        'tipo': tipo, 'dia': dia, 'campo': campo,
                        'local': lv, 'cloud': cv,
                    })

            for campo in _CAMPOS_MONTO.get(tipo, ()):
                lv = Decimal(str(fila_local.get(campo, '0') or '0'))
                cv = Decimal(str(fila_cloud.get(campo, '0') or '0'))
                if abs(lv - cv) > _TOLERANCIA_MONTO:
                    divergencias.append({
                        'tipo': tipo, 'dia': dia, 'campo': campo,
                        'local': str(lv), 'cloud': str(cv),
                    })

    return divergencias
