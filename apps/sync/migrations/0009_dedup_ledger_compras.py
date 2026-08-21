"""
Colapsa el ledger cloud duplicado de compras.

Una compra escribia DOS filas de `InventarioMovimientoSync` por cada linea:

  - una desde `COMPRA_REGISTRADA`, con `movimiento_id_local = NULL` y
    deduplicacion por clave natural;
  - otra desde `INVENTARIO_MOVIMIENTO_REGISTRADO`, con el ID real del
    `MovimientoLote` de la sucursal.

`_handler_compra` ya no escribe ledger: la autoridad son los movimientos, que
traen identidad estable. Esta migracion elimina las filas huerfanas con
`movimiento_id_local = NULL` **solo cuando existe su gemela con ID** para el
mismo hecho (sucursal + referencia + SKU + lote + tipo).

Conservador a proposito: si una linea solo tiene la fila sin ID — porque su
evento de movimiento nunca llego — se deja como esta. Borrarla perderia el
unico registro de esa entrada. Esas filas quedan visibles en el WARNING final
para revisarlas a mano.
"""
import logging

from django.db import migrations

logger = logging.getLogger('sync')


def colapsar_ledger_de_compras(apps, schema_editor):
    InventarioMovimientoSync = apps.get_model('sync', 'InventarioMovimientoSync')

    # Alias explicito: el cloud es multi-tenant (una BD por tenant), asi que
    # esta migracion corre una vez por base y no puede depender del router.
    db = schema_editor.connection.alias
    ledger = InventarioMovimientoSync.objects.using(db)

    sin_id = ledger.filter(movimiento_id_local__isnull=True, tipo='COMPRA')

    borrados = 0
    sin_gemela = 0

    for fila in sin_id.iterator():
        gemela_existe = ledger.filter(
            sucursal_id=fila.sucursal_id,
            movimiento_id_local__isnull=False,
            referencia_tipo=fila.referencia_tipo,
            referencia_id=fila.referencia_id,
            producto_sku=fila.producto_sku,
            lote_numero=fila.lote_numero,
            tipo=fila.tipo,
        ).exists()

        if gemela_existe:
            ledger.filter(pk=fila.pk).delete()
            borrados += 1
        else:
            sin_gemela += 1

    if borrados:
        logger.warning(
            'Migracion sync.0009 [%s]: %d fila(s) duplicada(s) de ledger de '
            'compra eliminadas (se conservo la que tiene movimiento_id_local).',
            db, borrados,
        )
    if sin_gemela:
        logger.warning(
            'Migracion sync.0009 [%s]: %d fila(s) de compra sin movimiento '
            'equivalente; se CONSERVAN. Revisar si su evento de movimiento '
            'nunca llego al cloud.',
            db, sin_gemela,
        )


def sin_reversa(apps, schema_editor):
    """No se pueden resucitar filas borradas."""


class Migration(migrations.Migration):

    dependencies = [
        ('sync', '0008_eventosync_hash_unico'),
    ]

    operations = [
        migrations.RunPython(colapsar_ledger_de_compras, sin_reversa),
    ]
