from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('configuracion', '0007_alter_configuracionnegocio_logo'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuracionnegocio',
            name='cantidad_copias_ticket',
            field=models.PositiveSmallIntegerField(
                default=1,
                help_text='Total de tickets a imprimir por venta. Use 2 para cliente + archivo interno.',
                validators=[MinValueValidator(1), MaxValueValidator(5)],
                verbose_name='Cantidad de copias de ticket',
            ),
        ),
    ]
