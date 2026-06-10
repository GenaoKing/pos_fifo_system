from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('permisos', '0002_seed_rbac'),
    ]

    operations = [
        migrations.AddField(
            model_name='asignacionrol',
            name='fecha_modificacion',
            field=models.DateTimeField(
                auto_now=True,
                verbose_name='Fecha de modificacion',
            ),
        ),
    ]
