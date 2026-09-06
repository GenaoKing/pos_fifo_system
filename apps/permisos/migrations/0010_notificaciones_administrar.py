from django.db import migrations


def aplicar(apps, schema_editor):
    Permiso = apps.get_model('permisos', 'Permiso')
    Rol = apps.get_model('permisos', 'Rol')

    permiso, _ = Permiso.objects.update_or_create(
        codigo='notificaciones.administrar',
        defaults={
            'nombre': 'Administrar notificaciones',
            'modulo': 'notificaciones',
            'descripcion': 'Configurar eventos, roles, excepciones de usuario y umbrales.',
        },
    )
    # Solo el rol de sistema Administrador. Los roles personalizados no cambian.
    for rol in Rol.objects.filter(es_sistema=True, slug='administrador'):
        rol.permisos.add(permiso)


class Migration(migrations.Migration):

    dependencies = [('permisos', '0009_asignacion_unicidad_efectiva')]

    operations = [migrations.RunPython(aplicar, migrations.RunPython.noop)]
