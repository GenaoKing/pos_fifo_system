from decimal import Decimal

from django.db import migrations


# CXC-MIG-ALIAS: la data migration debe escribir en la BD que se esta migrando,
# no en `default`. Sin `.using(schema_editor.connection.alias)`, el manager
# resuelve la conexion por el router; en un grafo tenant construido desde cero,
# el router puede mandar el get_or_create a `default` y sembrar los metodos en la
# BD equivocada (o fallar). Se fija el alias explicito, igual que hacen las data
# migrations tenant-aware del proyecto (ver negocios.0002).
def crear_metodos_default(apps, schema_editor):
    MetodoPlazoCredito = apps.get_model('cuentas_por_cobrar', 'MetodoPlazoCredito')
    alias = schema_editor.connection.alias
    MetodoPlazoCredito.objects.using(alias).get_or_create(
        nombre='Credito 30 dias',
        defaults={
            'tipo': 'VENCIMIENTO_UNICO',
            'dias_vencimiento': 30,
            'cantidad_cuotas': 1,
            'frecuencia': 'MENSUAL',
            'inicial_minima_porcentaje': Decimal('0.00'),
            'activo': True,
        },
    )
    MetodoPlazoCredito.objects.using(alias).get_or_create(
        nombre='3 cuotas mensuales',
        defaults={
            'tipo': 'CUOTAS',
            'dias_vencimiento': 30,
            'cantidad_cuotas': 3,
            'frecuencia': 'MENSUAL',
            'inicial_minima_porcentaje': Decimal('0.00'),
            'activo': True,
        },
    )


def revertir_metodos_default(apps, schema_editor):
    MetodoPlazoCredito = apps.get_model('cuentas_por_cobrar', 'MetodoPlazoCredito')
    alias = schema_editor.connection.alias
    MetodoPlazoCredito.objects.using(alias).filter(
        nombre__in=['Credito 30 dias', '3 cuotas mensuales']
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('cuentas_por_cobrar', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(crear_metodos_default, revertir_metodos_default),
    ]
