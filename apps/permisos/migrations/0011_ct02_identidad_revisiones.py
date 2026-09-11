import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('negocios', '0002_negocio_rnc_canonico_negocio_negocio_nombre_no_vacio_and_more'),
        ('permisos', '0010_notificaciones_administrar'),
    ]

    operations = [
        migrations.AddField(
            model_name='rol',
            name='cloud_id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AddField(
            model_name='rol',
            name='revision',
            field=models.PositiveBigIntegerField(default=1, editable=False),
        ),
        migrations.AddField(
            model_name='rol',
            name='deleted_at',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='rol',
            name='origen_cloud',
            field=models.BooleanField(
                default=False,
                editable=False,
                help_text='La fila local fue adoptada o creada por el pull RBAC cloud.',
            ),
        ),
        migrations.AddField(
            model_name='asignacionrol',
            name='cloud_id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AddField(
            model_name='asignacionrol',
            name='revision',
            field=models.PositiveBigIntegerField(default=1, editable=False),
        ),
        migrations.AddField(
            model_name='asignacionrol',
            name='deleted_at',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='asignacionrol',
            name='origen_cloud',
            field=models.BooleanField(
                default=False,
                editable=False,
                help_text='La fila local fue adoptada o creada por el pull RBAC cloud.',
            ),
        ),
        migrations.CreateModel(
            name='EstadoRBAC',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('catalog_revision', models.PositiveBigIntegerField(default=1)),
                ('assignments_revision', models.PositiveBigIntegerField(default=1)),
                ('fecha_modificacion', models.DateTimeField(auto_now=True)),
                ('negocio', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='estado_rbac', to='negocios.negocio')),
            ],
            options={
                'verbose_name': 'Estado RBAC',
                'verbose_name_plural': 'Estados RBAC',
                'db_table': 'permisos_estado_rbac',
            },
        ),
    ]
