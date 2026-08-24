"""
apps/sync/conciliacion.py

Orquesta la Fase 3 (anti-entropia): compara el resumen agregado local contra
el del cloud y reporta divergencias. Lo usan el comando `conciliar` y el
daemon `sincronizar` (una vez al dia).

No reimplementa deteccion de "hechos sin evento": eso ya lo hace
`verificar_sync` (Fase 0/1), probado y en produccion. Cuando se pide
`--backfill`, esta funcion invoca ese mismo comando por `call_command` sobre
la ventana conciliada. Encolar de mas es seguro -- `verificar_sync` ya lo
documenta: el cloud deduplica por hash y cada handler corta por clave
natural -- asi que no hace falta acotar el backfill a los dias exactos que
divergieron.

Limite conocido: una divergencia donde el cloud tiene MENOS que la sucursal
casi siempre es BUG-A (hecho sin evento, backfilleable). Una divergencia
donde el cloud tiene MAS, o donde el evento SI viajo pero el hecho se editó
distinto despues, no se repara con backfill de eventos -- queda reportada
para revision manual.
"""
from datetime import timedelta

from django.core.management import call_command
from django.utils import timezone

from . import resumen as resumen_mod


def ventana_conciliacion(dias):
    """
    [hoy_local - dias, AYER]. Excluye el dia en curso: sus ventas todavia
    estan en vuelo (encoladas, no confirmadas) y compararlas produciria
    divergencias que no son reales, solo eventos que no han tenido tiempo de
    llegar.
    """
    hoy = timezone.localdate()
    hasta = hoy - timedelta(days=1)
    desde = hoy - timedelta(days=dias)
    if desde > hasta:
        desde = hasta
    return desde, hasta


def conciliar(dias, engine=None, backfill=False, ejecutar=False):
    """
    Corre una conciliacion completa. Devuelve un dict:

        {
          'estado': 'OK' | 'DIVERGENTE' | 'NO_SOPORTADO' | 'ERROR',
          'desde': date, 'hasta': date, 'tz': str,
          'divergencias': [...],
          'mensaje': str,
          'backfill': {'encolados': N} | None,
        }

    No persiste nada por si sola -- eso lo decide quien llama (el comando
    imprime; el daemon ademas escribe LogSync). Mantenerla pura simplifica
    los tests: no hay que tocar la BD para verificar la logica de comparacion.
    """
    from .engine import SyncEngine

    tz = timezone.get_current_timezone_name()
    desde, hasta = ventana_conciliacion(dias)

    resultado = {
        'estado': 'ERROR',
        'desde': desde,
        'hasta': hasta,
        'tz': tz,
        'divergencias': [],
        'mensaje': '',
        'backfill': None,
    }

    local = resumen_mod.calcular_resumen(desde, hasta, tz)

    engine = engine or SyncEngine()
    cloud, error = engine.obtener_resumen(desde, hasta, tz)

    if error == 'no_soportado':
        resultado['estado'] = 'NO_SOPORTADO'
        resultado['mensaje'] = (
            'El cloud no expone /api/v1/sync/resumen/ (version anterior a la '
            'Fase 3 de anti-entropia). Nada que conciliar todavia.'
        )
        return resultado

    if error:
        resultado['estado'] = 'ERROR'
        resultado['mensaje'] = error
        return resultado

    divergencias = resumen_mod.comparar_resumenes(local, cloud)
    resultado['divergencias'] = divergencias
    resultado['estado'] = 'DIVERGENTE' if divergencias else 'OK'

    if divergencias:
        resultado['mensaje'] = f'{len(divergencias)} divergencia(s) en la ventana {desde}..{hasta}.'
    else:
        resultado['mensaje'] = f'Sin divergencias en la ventana {desde}..{hasta}.'

    if backfill and divergencias:
        resultado['backfill'] = _backfill_via_verificar_sync(dias, ejecutar)

    return resultado


def _backfill_via_verificar_sync(dias, ejecutar):
    """
    Reusa `verificar_sync --backfill` sobre la misma ventana. `call_command`
    en vez de importar sus funciones privadas: es la unidad de reuso mas
    estable que expone -- su interfaz de comando -- y evita acoplar este
    modulo nuevo a la implementacion interna de un comando que ya tiene su
    propia suite de tests protegiendola.
    """
    from io import StringIO

    salida = StringIO()
    call_command(
        'verificar_sync',
        dias=dias,
        backfill=True,
        ejecutar=ejecutar,
        stdout=salida,
    )
    return {'ejecutado': ejecutar, 'salida': salida.getvalue()}
