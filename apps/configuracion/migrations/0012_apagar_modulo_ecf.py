"""Apaga e-CF en toda configuracion existente (2026-10-18).

El modulo todavia no es funcional y `suscripciones.registry` lo marca como no
disponible. Esta migracion alinea el flag legacy guardado:

- En el POS, el flag crudo es el que leen la cola de emision y el JS del punto
  de venta.
- En el cloud se toca `fecha_modificacion` de TODAS las filas, no solo las que
  tenian el flag crudo encendido: el pull de configuracion es incremental y
  entrega el valor derivado del plan (Empresarial incluia e-CF). Sin mover la
  marca, un POS que ya recibio `modulo_ecf=True` nunca volveria a pedirla.
"""
from django.db import migrations
from django.utils import timezone


def apagar_ecf(apps, schema_editor):
    ConfiguracionNegocio = apps.get_model('configuracion', 'ConfiguracionNegocio')
    ConfiguracionNegocio.objects.using(schema_editor.connection.alias).update(
        modulo_ecf=False,
        fecha_modificacion=timezone.now(),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('configuracion', '0011_accesorapidopos_sucursal'),
    ]

    operations = [
        # Sin reversa de datos: volver a encender e-CF es una decision explicita
        # por negocio, no un efecto de deshacer una migracion.
        migrations.RunPython(apagar_ecf, migrations.RunPython.noop),
    ]
