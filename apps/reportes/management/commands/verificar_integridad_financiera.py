"""
Preflight read-only de las constraints financieras (DB-CONSTRAINTS, COT-008).

Las migraciones que agregan los `CheckConstraint` de ventas, inventario y
cotizaciones validan las filas existentes al aplicarse: en PostgreSQL,
`ADD CONSTRAINT ... CHECK` aborta el `migrate` si alguna fila lo viola, con un
error que NO dice cual. Este comando corre las MISMAS condiciones como consultas
de solo lectura y reporta las filas infractoras por adelantado, para corregir los
datos (o decidir) ANTES de tocar el esquema.

No muta nada. Sale con codigo != 0 si encuentra violaciones (para el preflight
de un pipeline o un runbook).

Uso:
    python manage.py verificar_integridad_financiera                  # local
    python manage.py verificar_integridad_financiera --tenant demo    # un tenant
    python manage.py verificar_integridad_financiera --todos-los-tenants
"""
from decimal import Decimal

from django.apps import apps as django_apps
from django.core.management.base import BaseCommand
from django.db import models

from apps.tenancy.management.base import TenantCommandMixin


# (app_label, modelo, id_hallazgo, descripcion, Q_violacion)
#
# `Q_violacion` es la NEGACION de la constraint: describe las filas que la
# restriccion rechazaria. Se mantiene alineada a mano con las Meta.constraints
# de cada modelo — si cambia una, cambia aca.
ESPECIFICACIONES = [
    ('ventas', 'Venta', 'DB-CONSTRAINTS',
     'total < 0.01', models.Q(total__lt=Decimal('0.01'))),
    ('ventas', 'Venta', 'DB-CONSTRAINTS',
     'subtotal < 0', models.Q(subtotal__lt=Decimal('0.00'))),
    ('ventas', 'Venta', 'DB-CONSTRAINTS',
     'descuento_total < 0', models.Q(descuento_total__lt=Decimal('0.00'))),
    ('ventas', 'DetalleVenta', 'DB-CONSTRAINTS',
     'cantidad < 1', models.Q(cantidad__lt=1)),
    ('ventas', 'DetalleVenta', 'DB-CONSTRAINTS',
     'precio_unitario < 0.01', models.Q(precio_unitario__lt=Decimal('0.01'))),
    ('ventas', 'DetalleVenta', 'DB-CONSTRAINTS',
     'descuento_monto < 0', models.Q(descuento_monto__lt=Decimal('0.00'))),
    ('ventas', 'DetalleVenta', 'DB-CONSTRAINTS',
     'descuento_monto > subtotal', models.Q(descuento_monto__gt=models.F('subtotal'))),
    ('ventas', 'DetalleVenta', 'DB-CONSTRAINTS',
     'total_linea < 0', models.Q(total_linea__lt=Decimal('0.00'))),
    ('ventas', 'Pago', 'DB-CONSTRAINTS',
     'monto < 0.01', models.Q(monto__lt=Decimal('0.01'))),
    ('inventario', 'Compra', 'DB-CONSTRAINTS',
     'total < 0', models.Q(total__lt=Decimal('0.00'))),
    ('inventario', 'DetalleCompra', 'DB-CONSTRAINTS',
     'cantidad < 1', models.Q(cantidad__lt=1)),
    ('inventario', 'DetalleCompra', 'DB-CONSTRAINTS',
     'costo_unitario < 0.01', models.Q(costo_unitario__lt=Decimal('0.01'))),
    ('inventario', 'Lote', 'DB-CONSTRAINTS',
     'cantidad_inicial < 1', models.Q(cantidad_inicial__lt=1)),
    ('inventario', 'Lote', 'DB-CONSTRAINTS',
     'costo_unitario < 0.01', models.Q(costo_unitario__lt=Decimal('0.01'))),
    ('cotizaciones', 'Cotizacion', 'COT-008',
     'subtotal < 0', models.Q(subtotal__lt=Decimal('0.00'))),
    ('cotizaciones', 'Cotizacion', 'COT-008',
     'descuento_total < 0', models.Q(descuento_total__lt=Decimal('0.00'))),
    ('cotizaciones', 'Cotizacion', 'COT-008',
     'total < 0', models.Q(total__lt=Decimal('0.00'))),
    ('cotizaciones', 'Cotizacion', 'COT-015',
     'CONVERTIDA sin venta',
     models.Q(estado='CONVERTIDA') & models.Q(venta__isnull=True)),
    ('cotizaciones', 'DetalleCotizacion', 'COT-008',
     'cantidad < 1', models.Q(cantidad__lt=1)),
    ('cotizaciones', 'DetalleCotizacion', 'COT-008',
     'precio_unitario < 0.01', models.Q(precio_unitario__lt=Decimal('0.01'))),
    ('cotizaciones', 'DetalleCotizacion', 'COT-008',
     'descuento_monto < 0', models.Q(descuento_monto__lt=Decimal('0.00'))),
    ('cotizaciones', 'DetalleCotizacion', 'COT-008',
     'descuento_monto > subtotal', models.Q(descuento_monto__gt=models.F('subtotal'))),
    ('cotizaciones', 'DetalleCotizacion', 'COT-008',
     'total_linea < 0', models.Q(total_linea__lt=Decimal('0.00'))),
]


class Command(TenantCommandMixin, BaseCommand):
    help = 'Reporta filas que violarian las constraints financieras (read-only).'

    def add_arguments(self, parser):
        self.add_tenant_argument(parser, required=False)
        parser.add_argument(
            '--todos-los-tenants',
            action='store_true',
            help='Recorre todos los tenants activos del control plane.',
        )

    def handle(self, *args, **options):
        if options.get('todos_los_tenants'):
            from apps.tenancy.models import Tenant

            tenants = Tenant.objects.using('default').filter(activo=True).order_by(
                'tenant_key'
            )
            total = 0
            for tenant in tenants:
                total += self.run_in_tenant(
                    tenant, lambda t=tenant: self._verificar(t.tenant_key)
                )
            if total:
                self._fail(total)
            return

        tenant_key = options.get('tenant')
        if tenant_key:
            tenant = self.get_tenant(tenant_key)
            violaciones = self.run_in_tenant(
                tenant, lambda: self._verificar(tenant_key)
            )
        else:
            violaciones = self._verificar(None)

        if violaciones:
            self._fail(violaciones)

    def _verificar(self, etiqueta):
        prefijo = f'[{etiqueta}] ' if etiqueta else ''
        total = 0
        for app_label, model_name, hallazgo, descripcion, violacion in ESPECIFICACIONES:
            modelo = django_apps.get_model(app_label, model_name)
            infractoras = list(
                modelo.objects.filter(violacion).values_list('pk', flat=True)[:20]
            )
            if not infractoras:
                continue
            total += len(infractoras)
            self.stderr.write(self.style.ERROR(
                f'{prefijo}{hallazgo} {app_label}.{model_name}: {descripcion} '
                f'-> filas {infractoras}'
            ))
        if not total:
            self.stdout.write(self.style.SUCCESS(
                f'{prefijo}Integridad financiera OK: ninguna fila viola las constraints.'
            ))
        return total

    def _fail(self, total):
        from django.core.management.base import CommandError

        raise CommandError(
            f'Se encontraron {total} fila(s) que violan una constraint financiera. '
            'Corregi los datos antes de aplicar las migraciones de constraints.'
        )
