"""
Invariantes de identidad y aislamiento del control plane.

Tres constraints que faltaban:

1. `Tenant.media_prefix` unico  -> dos tenants podian compartir namespace de
   archivos y sobrescribirse logos e imagenes de producto.
2. `Lower(Identity.email)` unico -> la unicidad de BD era sensible a mayusculas
   en PostgreSQL, pero el login busca `iexact` y toma `.first()`: con
   `Owner@Example.com` y `owner@example.com` coexistiendo, el login elegia una
   u otra segun el orden de las filas.
3. `(Membership.tenant, username)` unico -> dos identidades globales distintas
   podian actuar como el mismo usuario operativo del tenant.

PREFLIGHT, no autocorreccion. Antes de crear las constraints se buscan las
colisiones existentes y, si las hay, la migracion FALLA con el detalle.

Es deliberado que no las resuelva sola:

- Cambiar un `media_prefix` implica MOVER blobs; renombrar la fila sin mover
  los archivos deja al tenant apuntando a un namespace vacio.
- Fusionar dos `Identity` con el mismo email requiere decidir cual conserva las
  memberships y cual se da de baja.
- Dos memberships sobre el mismo username requieren decidir cual es la legitima.

Ninguna de esas decisiones la puede tomar una migracion.
"""
import django.db.models.functions.text
from django.db import migrations, models
from django.db.models.functions import Lower


def verificar_colisiones(apps, schema_editor):
    db = schema_editor.connection.alias

    Tenant = apps.get_model('tenancy', 'Tenant')
    Identity = apps.get_model('tenancy', 'Identity')
    Membership = apps.get_model('tenancy', 'Membership')

    problemas = []

    # 1. media_prefix vacio -> se rellena con el default derivado del key. Esto
    #    SI es seguro automatizar: un prefijo vacio no tiene blobs propios.
    for tenant in Tenant.objects.using(db).all():
        if not (tenant.media_prefix or '').strip(' /'):
            tenant.media_prefix = f'{tenant.tenant_key}/'
            tenant.save(update_fields=['media_prefix'])

    # 2. media_prefix duplicado -> requiere mover archivos: lo decide el operador.
    duplicados_media = (
        Tenant.objects.using(db)
        .values('media_prefix')
        .annotate(total=models.Count('id'))
        .filter(total__gt=1)
    )
    for fila in duplicados_media:
        claves = list(
            Tenant.objects.using(db)
            .filter(media_prefix=fila['media_prefix'])
            .values_list('tenant_key', flat=True)
        )
        problemas.append(
            f'- media_prefix "{fila["media_prefix"]}" compartido por {claves}. '
            f'Asignar un prefijo propio a cada tenant y MOVER sus blobs antes '
            f'de reintentar.'
        )

    # 3. Emails que solo difieren en mayusculas.
    duplicados_email = (
        Identity.objects.using(db)
        .annotate(email_lower=Lower('email'))
        .values('email_lower')
        .annotate(total=models.Count('id'))
        .filter(total__gt=1)
    )
    for fila in duplicados_email:
        emails = list(
            Identity.objects.using(db)
            .annotate(email_lower=Lower('email'))
            .filter(email_lower=fila['email_lower'])
            .values_list('email', flat=True)
        )
        problemas.append(
            f'- Identities con el mismo email ignorando mayusculas: {emails}. '
            f'Fusionar y dar de baja las sobrantes antes de reintentar.'
        )

    # 4. Dos identidades sobre el mismo usuario operativo.
    duplicados_membership = (
        Membership.objects.using(db)
        .values('tenant_id', 'username')
        .annotate(total=models.Count('id'))
        .filter(total__gt=1)
    )
    for fila in duplicados_membership:
        emails = list(
            Membership.objects.using(db)
            .filter(tenant_id=fila['tenant_id'], username=fila['username'])
            .values_list('identity__email', flat=True)
        )
        problemas.append(
            f'- Usuario operativo "{fila["username"]}" reclamado por varias '
            f'identidades: {emails}. Dejar una sola activa antes de reintentar.'
        )

    if problemas:
        raise RuntimeError(
            'No se pueden aplicar las invariantes de tenancy: hay colisiones '
            'que requieren una decision manual.\n\n'
            + '\n'.join(problemas)
            + '\n\nResolver en el control plane y volver a correr migrate.'
        )


def sin_reversa(apps, schema_editor):
    """Quitar las constraints no requiere deshacer el relleno de prefijos."""


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(verificar_colisiones, sin_reversa),
        migrations.AlterField(
            model_name="tenant",
            name="media_prefix",
            field=models.CharField(blank=True, max_length=160, unique=True),
        ),
        migrations.AddConstraint(
            model_name="identity",
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower("email"),
                name="uniq_identity_email_lower",
            ),
        ),
        migrations.AddConstraint(
            model_name="membership",
            constraint=models.UniqueConstraint(
                fields=("tenant", "username"), name="uniq_membership_tenant_username"
            ),
        ),
    ]
