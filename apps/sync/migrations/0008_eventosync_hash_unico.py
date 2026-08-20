"""
Idempotencia del sync con respaldo de BD.

Agrega una constraint unica parcial sobre `EventoSync.hash_payload` (excluyendo
el hash vacio de los eventos SIN_PAYLOAD).

Por que: el receptor cloud consultaba el hash y DESPUES abria la transaccion del
handler. Dos requests concurrentes con el mismo hash podian pasar los dos por
esa consulta y aplicar el efecto dos veces (pago CxC o movimiento de caja
duplicado). Con la constraint, el INSERT del evento -- que corre dentro de la
misma transaccion que el handler -- actua como reserva: el segundo falla y su
transaccion revierte entera.

ANTES de crear la constraint hay que colapsar los duplicados que el bug ya haya
dejado. Dos filas con el mismo hash representan el MISMO hecho aplicado dos
veces (todos los payloads llevan una PK local o un timestamp propio, asi que dos
hechos distintos no colisionan). Se conserva la fila mas antigua y se eliminan
las posteriores.

OJO: esto NO deshace los objetos de negocio que la doble aplicacion haya creado
en cloud (pagos o movimientos duplicados). Solo libera la tabla de eventos. La
migracion emite un WARNING con los hashes colapsados para que el operador pueda
auditarlos; revisar ese log al promover a produccion.
"""
import logging

from django.db import migrations, models

logger = logging.getLogger('sync')


def colapsar_hashes_duplicados(apps, schema_editor):
    EventoSync = apps.get_model('sync', 'EventoSync')

    # `.using(db)` EXPLICITO, no el manager por defecto. El cloud es
    # multi-tenant (una BD por tenant, ver apps/tenancy): esta migracion corre
    # una vez por cada base. Sin fijar el alias, las consultas pasan por
    # `TenantDatabaseRouter`, que decide segun el tenant activo del contexto —
    # y eso puede no ser la base que `schema_editor` esta migrando. En el mejor
    # caso levanta TenantContextError; en el peor borra filas de OTRA base.
    db = schema_editor.connection.alias
    eventos = EventoSync.objects.using(db)

    duplicados = (
        eventos
        .exclude(hash_payload='')
        .values('hash_payload')
        .annotate(total=models.Count('id'))
        .filter(total__gt=1)
    )

    total_borrados = 0
    for fila in duplicados:
        hash_payload = fila['hash_payload']
        ids = list(
            eventos
            .filter(hash_payload=hash_payload)
            .order_by('id')
            .values_list('id', flat=True)
        )
        a_borrar = ids[1:]
        eventos.filter(id__in=a_borrar).delete()
        total_borrados += len(a_borrar)
        logger.warning(
            'Migracion sync.0008 [%s]: hash %s tenia %d filas; se conservo '
            'id=%s y se eliminaron %s. Revisar si el hecho se aplico mas de '
            'una vez.',
            db, hash_payload[:12], fila['total'], ids[0], a_borrar,
        )

    if total_borrados:
        logger.warning(
            'Migracion sync.0008 [%s]: %d evento(s) duplicado(s) colapsados.',
            db, total_borrados,
        )


def sin_reversa(apps, schema_editor):
    """No se pueden resucitar filas borradas; el rollback solo quita la constraint."""


class Migration(migrations.Migration):

    dependencies = [
        ('sync', '0007_versionmaestro_bloqueado_desde_and_more'),
    ]

    operations = [
        migrations.RunPython(colapsar_hashes_duplicados, sin_reversa),
        migrations.AddConstraint(
            model_name='eventosync',
            constraint=models.UniqueConstraint(
                condition=models.Q(('hash_payload', ''), _negated=True),
                fields=('hash_payload',),
                name='uniq_eventosync_hash_no_vacio',
            ),
        ),
    ]
