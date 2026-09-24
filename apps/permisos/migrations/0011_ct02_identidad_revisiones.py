import uuid

from django.db import migrations, models
import django.db.models.deletion


def poblar_cloud_ids(apps, schema_editor):
    """Asigna una identidad distinta a cada fila previa a CT-02.

    Un ``default=uuid.uuid4`` en ``AddField`` se evalúa una vez al alterar una
    tabla existente; por eso no se puede declarar ``unique=True`` hasta que
    cada fila histórica tenga su propio UUID.
    """
    db = schema_editor.connection.alias

    for model_name in ('Rol', 'AsignacionRol'):
        Modelo = apps.get_model('permisos', model_name)
        for fila in Modelo.objects.using(db).filter(cloud_id__isnull=True).iterator():
            Modelo.objects.using(db).filter(
                pk=fila.pk,
                cloud_id__isnull=True,
            ).update(cloud_id=uuid.uuid4())


def sin_reversa(apps, schema_editor):
    """Al revertir se quitan las columnas; no hay identidades que restaurar."""


class Migration(migrations.Migration):

    dependencies = [
        ('negocios', '0002_negocio_rnc_canonico_negocio_negocio_nombre_no_vacio_and_more'),
        ('permisos', '0010_notificaciones_administrar'),
    ]

    operations = [
        migrations.AddField(
            model_name='rol',
            name='cloud_id',
            field=models.UUIDField(editable=False, null=True),
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
            field=models.UUIDField(editable=False, null=True),
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
        migrations.RunPython(poblar_cloud_ids, sin_reversa),
        migrations.AlterField(
            model_name='rol',
            name='cloud_id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AlterField(
            model_name='asignacionrol',
            name='cloud_id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
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
