"""
Data migration: siembra modulos + planes default y, por cada negocio existente,
crea su suscripcion con los modulos derivados de los flags actuales de
ConfiguracionNegocio (no cambia la conducta). Idempotente.

En BD fresca/de tests (sin negocios) solo siembra modulos + planes.
"""
from django.db import migrations


def seed_suscripciones(apps, schema_editor):
    from apps.suscripciones import seed

    # Alias explicito: la BD que esta migrando `schema_editor`, no el tenant
    # que pueda haber en contexto ambiente (MERGE-C03-ALIAS-ATOMIC). Asegura
    # que la siembra y su transaccion corran sobre la misma BD que el resto
    # de esta migracion.
    seed.bootstrap(
        ModuloModel=apps.get_model('suscripciones', 'Modulo'),
        PlanModel=apps.get_model('suscripciones', 'Plan'),
        NegocioModel=apps.get_model('negocios', 'Negocio'),
        NegocioModuloModel=apps.get_model('suscripciones', 'NegocioModulo'),
        SuscripcionModel=apps.get_model('suscripciones', 'SuscripcionNegocio'),
        ConfiguracionModel=apps.get_model('configuracion', 'ConfiguracionNegocio'),
        using=schema_editor.connection.alias,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('suscripciones', '0001_initial'),
        ('negocios', '0001_initial'),
        ('sucursales', '0003_sucursal_negocio'),
        ('configuracion', '0006_accesorapidopos'),
    ]

    operations = [
        migrations.RunPython(seed_suscripciones, migrations.RunPython.noop),
    ]
