"""
Unicidad EFECTIVA de las asignaciones de rol (PER-008).

`unique_together = (usuario, rol, sucursal)` no protegia la asignacion global:
en PostgreSQL y en SQLite dos NULL no colisionan, asi que podian coexistir N
filas identicas con `sucursal=NULL`. La consecuencia no era cosmetica —
revocar una devolvia 204 y el usuario conservaba el permiso por la otra, y el
`update_or_create` del pull de sync podia levantar `MultipleObjectsReturned` y
congelar el cursor.

Se reemplaza por dos indices unicos PARCIALES, uno por caso.

--------------------------------------------------------------------------
Como se resuelven los duplicados que ya existen
--------------------------------------------------------------------------
**Gana la revocacion.** Si de un grupo duplicado alguna fila esta inactiva, la
fila superviviente queda inactiva.

Es deliberado y es la unica lectura defendible: una fila inactiva significa que
alguien reviso esa asignacion y decidio quitarla. Conservar la activa
restauraria en silencio un privilegio que un operador cree retirado — que es
exactamente el sintoma que reporta el hallazgo. Si el permiso hacia falta, se
vuelve a otorgar desde el portal y queda registrado; el error en esta direccion
es recuperable, en la otra no se nota.

Se conserva la fila mas antigua del grupo para no perder `fecha_creacion`.
"""
from django.conf import settings
from django.db import migrations, models


def deduplicar_asignaciones(apps, schema_editor):
    Asignacion = apps.get_model('permisos', 'AsignacionRol')
    db = schema_editor.connection.alias

    grupos = {}
    # `.using(db)` explicito: con DB-per-tenant el manager por defecto ruta por
    # el router, no por la conexion que el schema_editor esta migrando.
    filas = Asignacion.objects.using(db).order_by('id').values_list(
        'id', 'usuario_id', 'rol_id', 'sucursal_id', 'activo',
    )
    for pk, usuario_id, rol_id, sucursal_id, activo in filas.iterator(chunk_size=2000):
        grupos.setdefault((usuario_id, rol_id, sucursal_id), []).append((pk, activo))

    a_borrar = []
    a_desactivar = []
    for integrantes in grupos.values():
        if len(integrantes) == 1:
            continue
        sobrevive, activo_sobrevive = integrantes[0]
        alguno_revocado = any(not activo for _, activo in integrantes)
        if alguno_revocado and activo_sobrevive:
            a_desactivar.append(sobrevive)
        a_borrar.extend(pk for pk, _ in integrantes[1:])

    for i in range(0, len(a_desactivar), 500):
        Asignacion.objects.using(db).filter(
            pk__in=a_desactivar[i:i + 500]
        ).update(activo=False)

    for i in range(0, len(a_borrar), 500):
        Asignacion.objects.using(db).filter(pk__in=a_borrar[i:i + 500]).delete()


def sin_reversa(apps, schema_editor):
    """Al revertir se quitan los indices; las filas colapsadas no vuelven."""


class Migration(migrations.Migration):

    dependencies = [
        ("permisos", "0008_permisos_productos_portal_cajera"),
        ("sucursales", "0003_sucursal_negocio"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="asignacionrol",
            unique_together=set(),
        ),
        migrations.RunPython(deduplicar_asignaciones, sin_reversa),
        migrations.AddConstraint(
            model_name="asignacionrol",
            constraint=models.UniqueConstraint(
                condition=models.Q(("sucursal__isnull", True)),
                fields=("usuario", "rol"),
                name="asignacion_unica_global",
            ),
        ),
        migrations.AddConstraint(
            model_name="asignacionrol",
            constraint=models.UniqueConstraint(
                condition=models.Q(("sucursal__isnull", False)),
                fields=("usuario", "rol", "sucursal"),
                name="asignacion_unica_por_sucursal",
            ),
        ),
    ]
