from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ventas', '0005_venta_sucursal'),
    ]

    operations = [
        migrations.AddField(
            model_name='venta',
            name='condicion_pago',
            field=models.CharField(
                choices=[('CONTADO', 'Contado'), ('CREDITO', 'Credito')],
                default='CONTADO',
                help_text='Contado para ventas pagadas al cierre; credito si genera CxC.',
                max_length=20,
                verbose_name='Condicion de Pago',
            ),
        ),
        migrations.AlterField(
            model_name='pago',
            name='metodo',
            field=models.CharField(
                choices=[
                    ('EFECTIVO', 'Efectivo'),
                    ('TRANSFERENCIA', 'Transferencia'),
                    ('TARJETA', 'Tarjeta'),
                    ('CREDITO', 'Credito'),
                ],
                max_length=20,
                verbose_name='Metodo de Pago',
            ),
        ),
    ]
