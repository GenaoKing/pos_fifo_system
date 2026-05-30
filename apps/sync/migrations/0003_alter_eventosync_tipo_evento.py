from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sync', '0002_alter_eventosync_tipo_evento'),
    ]

    operations = [
        migrations.AlterField(
            model_name='eventosync',
            name='tipo_evento',
            field=models.CharField(
                choices=[
                    ('VENTA_CREADA', 'Venta creada'),
                    ('VENTA_ANULADA', 'Venta anulada'),
                    ('APERTURA_CAJA', 'Apertura de caja'),
                    ('MOVIMIENTO_CAJA', 'Movimiento de caja (retiro/gasto/ingreso)'),
                    ('CIERRE_CAJA', 'Cierre de caja'),
                    ('AJUSTE_INVENTARIO', 'Ajuste de inventario'),
                    ('COMPRA_REGISTRADA', 'Compra registrada'),
                    ('CXC_CREADA', 'Cuenta por cobrar creada'),
                    ('CXC_PAGO_REGISTRADO', 'Pago de cuenta por cobrar registrado'),
                    ('CXC_ANULADA', 'Cuenta por cobrar anulada'),
                ],
                db_index=True,
                max_length=32,
                verbose_name='Tipo de evento',
            ),
        ),
    ]
