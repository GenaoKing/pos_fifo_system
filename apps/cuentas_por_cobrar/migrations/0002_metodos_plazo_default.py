from decimal import Decimal

from django.db import migrations


def crear_metodos_default(apps, schema_editor):
    MetodoPlazoCredito = apps.get_model('cuentas_por_cobrar', 'MetodoPlazoCredito')
    MetodoPlazoCredito.objects.get_or_create(
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
    MetodoPlazoCredito.objects.get_or_create(
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
    MetodoPlazoCredito.objects.filter(nombre__in=['Credito 30 dias', '3 cuotas mensuales']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('cuentas_por_cobrar', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(crear_metodos_default, revertir_metodos_default),
    ]
