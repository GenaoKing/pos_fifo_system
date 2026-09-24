from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('productos', '0011_producto_imagen_origen_url_producto_origen_sucursal_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='producto',
            name='origen_cloud_id',
            field=models.PositiveIntegerField(
                blank=True,
                db_index=True,
                editable=False,
                help_text='PK de esta fila en la BD cloud. Identidad de sync; no se edita a mano.',
                null=True,
                unique=True,
                verbose_name='ID en cloud',
            ),
        ),
    ]
