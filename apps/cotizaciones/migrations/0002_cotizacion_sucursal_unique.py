from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('cotizaciones', '0001_initial'),
        ('sucursales', '0003_sucursal_negocio'),
    ]

    operations = [
        migrations.AlterField(
            model_name='cotizacion',
            name='numero_cotizacion',
            field=models.CharField(max_length=50, verbose_name='Numero de Cotizacion'),
        ),
        migrations.AddField(
            model_name='cotizacion',
            name='sucursal',
            field=models.ForeignKey(
                blank=True,
                help_text='Sucursal donde se creo la cotizacion. Null para cotizaciones legacy.',
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='cotizaciones',
                to='sucursales.sucursal',
                verbose_name='Sucursal',
            ),
        ),
        migrations.AddIndex(
            model_name='cotizacion',
            index=models.Index(fields=['sucursal', 'estado'], name='cotizacione_sucursa_6f3593_idx'),
        ),
        migrations.AddConstraint(
            model_name='cotizacion',
            constraint=models.UniqueConstraint(
                fields=('sucursal', 'numero_cotizacion'),
                name='unique_cotizacion_por_sucursal_numero',
            ),
        ),
    ]
