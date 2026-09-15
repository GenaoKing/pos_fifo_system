"""Preflight dirigido para historiales de migracion de sucursales heredados.

El router de tenancy puede registrar una migracion como aplicada aunque haya
filtrado todas sus operaciones. Este modulo no repara por si mismo: solo
describe con precision el estado para que el comando explicito pueda hacerlo.
"""

from dataclasses import dataclass

from django.db import connections
from django.db.migrations.recorder import MigrationRecorder


APP_LABEL = 'sucursales'
TABLA_SUCURSAL = 'sucursales_sucursal'
TABLA_MIGRACIONES = 'django_migrations'


@dataclass(frozen=True)
class EstadoSucursales:
    """Foto de solo lectura que hace visible un historial fantasma."""

    alias: str
    tabla_presente: bool
    migraciones_registradas: tuple[str, ...]

    @property
    def reparacion_requerida(self):
        return not self.tabla_presente and bool(self.migraciones_registradas)

    @property
    def base_sin_inicializar(self):
        return not self.tabla_presente and not self.migraciones_registradas

    def mensaje_reparacion(self, comando):
        migraciones = ', '.join(self.migraciones_registradas)
        return (
            f'{self.alias}: {TABLA_SUCURSAL} no existe pero django_migrations '
            f'registra {APP_LABEL}: {migraciones}. Es el historial fantasma '
            f'de una app ahora dual-home. No se reparo automaticamente. '
            f'Primero ejecute: {comando}'
        )


def inspeccionar_sucursales(alias):
    """Devuelve el estado sin escribir, incluso para una BD totalmente vacia."""
    connection = connections[alias]
    tablas = set(connection.introspection.table_names())
    if TABLA_MIGRACIONES not in tablas:
        migraciones = ()
    else:
        migraciones = tuple(
            MigrationRecorder(connection).migration_qs.filter(app=APP_LABEL)
            .order_by('name').values_list('name', flat=True)
        )
    return EstadoSucursales(
        alias=alias,
        tabla_presente=TABLA_SUCURSAL in tablas,
        migraciones_registradas=migraciones,
    )


def desregistrar_migraciones_sucursales(alias):
    """Quita solo el historial que impide que Django materialice la app."""
    connection = connections[alias]
    return MigrationRecorder(connection).migration_qs.filter(app=APP_LABEL).delete()[0]
