"""Preflight dirigido para historiales de migracion de sucursales heredados.

El router de tenancy puede registrar una migracion como aplicada aunque haya
filtrado todas sus operaciones. Este modulo no repara por si mismo: solo
describe con precision el estado para que el comando explicito pueda hacerlo.
"""

from dataclasses import dataclass

from django.core.management.base import CommandError
from django.db import connections
from django.db.migrations.loader import MigrationLoader
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


_MIGRACIONES_SUCURSALES_CONOCIDAS = (
    '0001_initial',
    '0002_sucursal_ultima_sync_sucursal_usuario_servicio',
    '0003_sucursal_negocio',
    '0004_sucursal_dual_home_preflight',
)


def materializar_sucursal_fantasma(alias, migraciones_registradas):
    """Crea la tabla en su estado historico sin romper dependencias ya aplicadas.

    En una base heredada, auditoria.0002 y otras migraciones pueden depender de
    sucursales.0001 y estar registradas. Desregistrar sucursales causa
    InconsistentMigrationHistory antes de que ``migrate`` pueda recrear la tabla.
    Se materializa solo la tabla ausente con el modelo del ultimo estado
    registrado; el historial permanece intacto y ``migrate`` aplica lo pendiente.
    """
    registradas = tuple(migraciones_registradas)
    if (
        not registradas
        or registradas != _MIGRACIONES_SUCURSALES_CONOCIDAS[:len(registradas)]
    ):
        raise CommandError(
            f'{alias}: historial de sucursales no reconocido; no se materializo '
            'ninguna tabla.'
        )

    connection = connections[alias]
    if TABLA_SUCURSAL in connection.introspection.table_names():
        raise CommandError(f'{alias}: {TABLA_SUCURSAL} ya existe; no se repara.')

    estado = MigrationLoader(None).project_state(
        [(APP_LABEL, registradas[-1])]
    )
    sucursal = estado.apps.get_model(APP_LABEL, 'Sucursal')
    if sucursal._meta.db_table != TABLA_SUCURSAL:
        raise CommandError(f'{alias}: nombre de tabla inesperado; no se repara.')

    with connection.schema_editor() as editor:
        editor.create_model(sucursal)

    if TABLA_SUCURSAL not in connection.introspection.table_names():
        raise CommandError(f'{alias}: la tabla no se materializo.')
