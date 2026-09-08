"""
El cliente generico CONTADO pasa a ser singleton (CLI-007).

El modelo admitia cualquier numero de filas `tipo='CONTADO'`, y la vista local
permitia tanto crear un segundo generico como convertir un cliente real. Con dos
filas exactas, `get_cliente_contado()` levantaba `MultipleObjectsReturned` y
cotizaciones —y todo lo que llama al helper— devolvia 500.

--------------------------------------------------------------------------
Como se resuelven los duplicados que ya existen
--------------------------------------------------------------------------
Aca hay que distinguir dos casos que la constraint no distingue, porque tienen
consecuencias opuestas:

1. **Duplicados del generico** (mismo rol, sin identificacion propia). Son
   intercambiables por definicion: dos filas "CLIENTE CONTADO" no representan a
   dos personas distintas. Se consolidan sobre la mas antigua —repuntando
   ventas, cuentas y cotizaciones— y las sobrantes se eliminan.

2. **Un cliente REAL convertido a CONTADO.** Tiene nombre propio o cedula/RNC, y
   sus ventas son de esa persona. Reasignarlas al generico seria falsificar la
   historia comercial. Aca la migracion **ABORTA** y pide una decision humana.

Es la misma regla que en `sync.0008`: cuando se puede reconstruir sin perder
informacion, se limpia; cuando no, se detiene.
"""
from django.db import migrations, models

NOMBRE_GENERICO = 'CLIENTE CONTADO'


def _parece_generico(nombre, cedula):
    """True si la fila es un placeholder y no una persona real."""
    if cedula:
        return False
    return (nombre or '').strip().upper() == NOMBRE_GENERICO


def consolidar_contado(apps, schema_editor):
    Cliente = apps.get_model('clientes', 'Cliente')
    db = schema_editor.connection.alias

    filas = list(
        Cliente.objects.using(db)
        .filter(tipo='CONTADO')
        .order_by('id')
        .values('id', 'nombre', 'cedula_rnc')
    )
    if len(filas) <= 1:
        return

    reales = [
        f for f in filas
        if not _parece_generico(f['nombre'], f['cedula_rnc'])
    ]
    if reales:
        detalle = ', '.join(
            f"id={f['id']} nombre={f['nombre']!r} cedula={f['cedula_rnc']!r}"
            for f in reales
        )
        raise RuntimeError(
            'Hay clientes con identidad propia marcados como CONTADO: '
            f'{detalle}. Reasignar sus ventas al generico falsificaria la '
            'historia comercial. Corregir su `tipo` a PERSONAL/CORPORATIVO '
            'antes de aplicar esta migracion.'
        )

    sobreviviente = filas[0]['id']
    sobrantes = [f['id'] for f in filas[1:]]

    Venta = apps.get_model('ventas', 'Venta')
    Cuenta = apps.get_model('cuentas_por_cobrar', 'CuentaPorCobrar')
    Cotizacion = apps.get_model('cotizaciones', 'Cotizacion')

    for modelo in (Venta, Cuenta, Cotizacion):
        modelo.objects.using(db).filter(cliente_id__in=sobrantes).update(
            cliente_id=sobreviviente,
        )

    Cliente.objects.using(db).filter(id__in=sobrantes).delete()


def sin_reversa(apps, schema_editor):
    """Al revertir se quita el indice; las filas consolidadas no vuelven."""


class Migration(migrations.Migration):

    dependencies = [
        ("clientes", "0005_cliente_origen_cloud_id"),
        ("sucursales", "0003_sucursal_negocio"),
        ("ventas", "0008_venta_descuento_autorizacion"),
        ("cuentas_por_cobrar", "0007_pagocxc_turno_caja"),
        ("cotizaciones", "0002_cotizacion_sucursal_unique"),
    ]

    operations = [
        migrations.RunPython(consolidar_contado, sin_reversa),
        migrations.AddConstraint(
            model_name="cliente",
            constraint=models.UniqueConstraint(
                condition=models.Q(("tipo", "CONTADO")),
                fields=("tipo",),
                name="cliente_contado_singleton",
            ),
        ),
    ]
