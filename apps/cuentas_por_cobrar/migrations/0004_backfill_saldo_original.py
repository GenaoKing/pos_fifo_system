"""Backfill de saldo_original en cuentas existentes.

Las cuentas creadas antes del interes de financiamiento no tienen capital
registrado; se reconstruye como total - monto_inicial (con interes 0), que es
exactamente la formula que usaba recalcular_estado() antes de este cambio.
Corre igual en la base local y en la cloud (comparten modelos).
"""
from django.db import migrations
from django.db.models import F


def backfill(apps, schema_editor):
    CuentaPorCobrar = apps.get_model('cuentas_por_cobrar', 'CuentaPorCobrar')
    CuentaPorCobrar.objects.filter(saldo_original=0).update(
        saldo_original=F('total') - F('monto_inicial'),
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('cuentas_por_cobrar', '0003_interes_financiamiento'),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
