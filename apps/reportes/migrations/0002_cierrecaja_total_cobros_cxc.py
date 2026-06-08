from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reportes', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='cierrecaja',
            name='total_cobros_cxc',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0.00'),
                help_text='Total cobrado de cuentas por cobrar en el dia',
                max_digits=12,
            ),
        ),
    ]
