import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clientes', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='cliente',
            name='plazo_credito_dias',
            field=models.PositiveIntegerField(
                default=30,
                help_text='Dias de vencimiento para ventas a credito con vencimiento unico',
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(365),
                ],
                verbose_name='Plazo de Credito (dias)',
            ),
        ),
    ]
