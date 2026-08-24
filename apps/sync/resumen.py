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

Corolario: el campo que se suma dentro de cada dia tiene que ser INMUTABLE
respecto de esa fecha. `CuentaPorCobrar.saldo` es un balance vivo que un pago
posterior (en cualquier fecha, incluso hoy, fuera de la ventana) sigue
mutando -- sumarlo agrupado por `fecha_emision` filtra por cuando la cuenta
NACIO pero mide un valor que cambia depues, produciendo divergencias
atribuidas al dia equivocado. `saldo_original` se escribe una sola vez al
crear la cuenta y no vuelve a cambiar: es lo que hay que comparar. La cobranza
(los pagos) ya la vigila `cxc_pagos`, agrupada por `fecha_pago`.

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
from datetime import date, datetime, time, timedelta
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
          'ventas':    {'2026-08-18': {'count': 10, 'suma': '15400.00', 'anuladas': 1, 'max_ref': 'V-000123'}},
          'cxc':       {'2026-08-18': {'count': 2, 'saldo_original': '3500.00'}},
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


def _limites(desde, hasta, zona):
    """
    [desde, hasta] (date, en `zona`) -> [inicio, fin) como datetime aware en
    UTC, listo para filtrar un DateTimeField directo. Equivalente exacto a
    `TruncDate(campo, tzinfo=zona) in [desde, hasta]`, pero permite filtrar
    ANTES de truncar: sobre un campo indexado, Postgres puede usar el indice
    en vez de recalcular TruncDate fila por fila sobre toda la tabla.
    """
    inicio = datetime.combine(desde, time.min, tzinfo=zona)
    fin = datetime.combine(hasta + timedelta(days=1), time.min, tzinfo=zona)
    return inicio, fin


def _resumen_ventas(desde, hasta, zona, sucursal):
    from apps.ventas.models import Venta

    inicio, fin = _limites(desde, hasta, zona)
    qs = Venta.objects.filter(fecha_venta__gte=inicio, fecha_venta__lt=fin)
    if sucursal is not None:
        qs = qs.filter(sucursal=sucursal)

    filas = (
        qs.annotate(dia=TruncDate('fecha_venta', tzinfo=zona))
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

    # `saldo_original`, NO `saldo`: `saldo` es el balance vivo, mutado por
    # cualquier pago posterior (incluso de hoy, fuera de la ventana). Sumarlo
    # agrupado por `fecha_emision` produce divergencias fantasma atribuidas al
    # dia en que la cuenta nacio, no al dia real del cambio. `saldo_original`
    # se escribe una vez al crear la cuenta y no cambia mas.
    qs = CuentaPorCobrar.objects.filter(fecha_emision__gte=desde, fecha_emision__lte=hasta)
    if sucursal is not None:
        qs = qs.filter(sucursal=sucursal)

    filas = (
        qs.values('fecha_emision')
        .annotate(count=Count('id'), saldo_original=Sum('saldo_original'))
    )

    resultado = {}
    for fila in filas:
        dia = fila['fecha_emision']
        clave = dia.isoformat() if isinstance(dia, date) else str(dia)
        resultado[clave] = {
            'count': fila['count'],
            'saldo_original': _dec(fila['saldo_original']),
        }
    return resultado


def _resumen_cxc_pagos(desde, hasta, zona, sucursal):
    from apps.cuentas_por_cobrar.models import PagoCxC

    # `monto` solo cuenta pagos APLICADO: un pago anulado no debe seguir
    # sumando saldo cobrado en ninguno de los dos lados. Si la anulacion no
    # replico, el conteo total (con anulados) SI diverge y lo delata; por eso
    # se reporta tambien `count` sobre el total, no solo sobre lo aplicado.
    inicio, fin = _limites(desde, hasta, zona)
    qs = PagoCxC.objects.filter(fecha_pago__gte=inicio, fecha_pago__lt=fin)
    if sucursal is not None:
        qs = qs.filter(cuenta__sucursal=sucursal)

    filas = (
        qs.annotate(dia=TruncDate('fecha_pago', tzinfo=zona))
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
    'cxc': ('saldo_original',),
    'cxc_pagos': ('monto',),
}
# Comparacion exacta de texto -- sin tolerancia, no son montos. `max_ref`
# atrapa el caso "mismo count y suma, pero el ultimo correlativo no
# coincide" (p. ej. un numero_venta se aplico con un valor distinto).
_CAMPOS_TEXTO = {
    'ventas': ('max_ref',),
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

            for campo in _CAMPOS_TEXTO.get(tipo, ()):
                lv = str(fila_local.get(campo, '') or '')
                cv = str(fila_cloud.get(campo, '') or '')
                if lv != cv:
                    divergencias.append({
                        'tipo': tipo, 'dia': dia, 'campo': campo,
                        'local': lv, 'cloud': cv,
                    })

    return divergencias
