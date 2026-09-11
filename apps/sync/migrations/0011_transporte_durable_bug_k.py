import uuid

from django.db import migrations, models


def poblar_event_ids(apps, schema_editor):
    EventoSync = apps.get_model('sync', 'EventoSync')
    db = schema_editor.connection.alias
    for evento in EventoSync.objects.using(db).filter(event_id__isnull=True).iterator():
        evento.event_id = uuid.uuid4()
        evento.save(using=db, update_fields=['event_id'])


class Migration(migrations.Migration):

    dependencies = [
        ('sync', '0010_logsync_detalle_alter_logsync_tipo'),
    ]

    operations = [
        migrations.AddField(
            model_name='eventosync',
            name='event_id',
            field=models.UUIDField(db_index=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='eventosync',
            name='lease_expires_at',
            field=models.DateTimeField(
                blank=True, db_index=True, null=True,
                verbose_name='Vencimiento del lease',
            ),
        ),
        migrations.AddField(
            model_name='eventosync',
            name='lease_id',
            field=models.UUIDField(
                blank=True, db_index=True, editable=False, null=True,
                verbose_name='Lease de envio',
            ),
        ),
        migrations.AlterField(
            model_name='eventosync',
            name='estado',
            field=models.CharField(
                choices=[
                    ('PENDIENTE', 'Pendiente'),
                    ('SIN_PAYLOAD', 'Sin payload (serializar al enviar)'),
                    ('EN_VUELO', 'En vuelo (lease activo)'),
                    ('CONFIRMADO', 'Confirmado'),
                    ('ERROR', 'Error'),
                    ('DESCARTADO', 'Descartado'),
                ],
                db_index=True,
                default='PENDIENTE',
                max_length=16,
                verbose_name='Estado',
            ),
        ),
        migrations.RunPython(poblar_event_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='eventosync',
            name='event_id',
            field=models.UUIDField(
                db_index=True,
                default=uuid.uuid4,
                editable=False,
                verbose_name='Identidad estable del evento',
                help_text='UUID que se conserva entre reintentos y se envia al cloud.',
            ),
        ),
        migrations.AddConstraint(
            model_name='eventosync',
            constraint=models.UniqueConstraint(
                condition=models.Q(('sucursal__isnull', False)),
                fields=('sucursal', 'event_id'),
                name='uniq_eventosync_sucursal_event_id',
            ),
        ),
        migrations.AddConstraint(
            model_name='eventosync',
            constraint=models.UniqueConstraint(
                condition=models.Q(('sucursal__isnull', True)),
                fields=('event_id',),
                name='uniq_eventosync_legacy_event_id',
            ),
        ),
        migrations.RemoveConstraint(
            model_name='eventosync',
            name='uniq_eventosync_hash_no_vacio',
        ),
        migrations.AddConstraint(
            model_name='eventosync',
            constraint=models.UniqueConstraint(
                condition=(
                    models.Q(('sucursal__isnull', False))
                    & ~models.Q(('hash_payload', ''))
                ),
                fields=('sucursal', 'hash_payload'),
                name='uniq_eventosync_sucursal_hash',
            ),
        ),
        migrations.AddConstraint(
            model_name='eventosync',
            constraint=models.UniqueConstraint(
                condition=(
                    models.Q(('sucursal__isnull', True))
                    & ~models.Q(('hash_payload', ''))
                ),
                fields=('hash_payload',),
                name='uniq_eventosync_legacy_hash',
            ),
        ),
        migrations.CreateModel(
            name='DiferidoSync',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True, primary_key=True, serialize=False,
                    verbose_name='ID',
                )),
                ('tenant_key', models.CharField(blank=True, default='', max_length=100)),
                ('sucursal_codigo', models.CharField(blank=True, default='', max_length=50)),
                ('tabla', models.CharField(db_index=True, max_length=32)),
                ('identidad', models.CharField(blank=True, default='', max_length=200)),
                ('cursor_fecha', models.DateTimeField(blank=True, null=True)),
                ('cursor_id', models.PositiveIntegerField(default=0)),
                ('payload_hash', models.CharField(max_length=64)),
                ('payload', models.JSONField()),
                ('estado', models.CharField(
                    choices=[('PENDIENTE', 'Pendiente'), ('RESUELTO', 'Resuelto')],
                    db_index=True, default='PENDIENTE', max_length=16,
                )),
                ('intentos', models.PositiveIntegerField(default=1)),
                ('ultimo_error', models.TextField(blank=True, default='')),
                ('creado_at', models.DateTimeField(auto_now_add=True)),
                ('actualizado_at', models.DateTimeField(auto_now=True)),
                ('resuelto_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={'ordering': ['creado_at', 'id']},
        ),
        migrations.AddConstraint(
            model_name='diferidosync',
            constraint=models.UniqueConstraint(
                fields=(
                    'tenant_key', 'sucursal_codigo', 'tabla', 'payload_hash',
                ),
                name='uniq_diferido_sync_ambito_payload',
            ),
        ),
        migrations.AddIndex(
            model_name='diferidosync',
            index=models.Index(
                fields=[
                    'tenant_key', 'sucursal_codigo', 'tabla', 'estado',
                ],
                name='sync_dif_ambito_estado_idx',
            ),
        ),
        migrations.AlterField(
            model_name='logsync',
            name='tipo',
            field=models.CharField(
                choices=[
                    ('PUSH', 'Push de eventos'),
                    ('PULL', 'Pull de maestros'),
                    ('PING', 'Verificar conexion'),
                    ('FULL', 'Ciclo completo'),
                    ('CONCILIACION', 'Conciliacion (anti-entropia)'),
                    ('REPARACION', 'Reparacion dirigida'),
                ],
                db_index=True,
                max_length=16,
                verbose_name='Tipo',
            ),
        ),
    ]
