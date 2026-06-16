import apps.tenancy.media
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0006_producto_estado_producto_marca'),
    ]

    operations = [
        migrations.AlterField(
            model_name='producto',
            name='imagen',
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to=apps.tenancy.media.producto_image_upload_to,
                verbose_name='Imagen',
            ),
        ),
    ]
