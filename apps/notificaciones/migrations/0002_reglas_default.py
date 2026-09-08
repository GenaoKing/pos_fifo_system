from django.db import migrations


def aplicar(apps, schema_editor):
    Rol = apps.get_model('permisos', 'Rol')
    Regla = apps.get_model('notificaciones', 'ReglaNotificacionRol')
    for rol in Rol.objects.filter(es_sistema=True, slug='administrador'):
        for tipo in ('caja.apertura', 'caja.cierre'):
            Regla.objects.get_or_create(
                rol=rol,
                tipo_evento=tipo,
                defaults={'activa': True, 'enviar_push': True, 'parametros': {}},
            )


class Migration(migrations.Migration):

    dependencies = [
        ('notificaciones', '0001_initial'),
        ('permisos', '0010_notificaciones_administrar'),
    ]

    operations = [migrations.RunPython(aplicar, migrations.RunPython.noop)]
