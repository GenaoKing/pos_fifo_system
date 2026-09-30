"""Preflight y reparaciones explicitas de historiales dual-home heredados.

El router de tenancy puede registrar una migracion como aplicada aunque haya
filtrado todas sus operaciones. Este modulo no repara por si mismo: solo
describe con precision el estado para que el comando explicito pueda hacerlo.
"""

from dataclasses import dataclass

from django.core.management.base import CommandError
from django.db import connections, transaction
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


TABLA_AUDITORIA = 'auditoria_auditoria'
# This is the exact control-plane history observed before auditoria became
# dual-home. A later or incomplete history could mean a lost table containing
# real audit evidence, and MUST NOT be reconstructed by this repair.
AUDITORIA_CONTROL_HISTORIAL_LEGACY = (
    '0001_initial',
    '0002_auditoria_sucursal_and_more',
    '0003_alter_auditoria_accion',
    '0004_alter_auditoria_accion',
)


@dataclass(frozen=True)
class EstadoAuditoriaControl:
    alias: str
    tabla_presente: bool
    migraciones_registradas: tuple[str, ...]

    @property
    def reparacion_requerida(self):
        return not self.tabla_presente and bool(self.migraciones_registradas)

    def mensaje_reparacion(self):
        return (
            f'{self.alias}: {TABLA_AUDITORIA} no existe pero django_migrations '
            f'registra auditoria: {", ".join(self.migraciones_registradas)}. '
            'No se reparo automaticamente. Primero ejecute: python manage.py '
            'reparar_auditoria_dual_home --database default --dry-run. '
            'El reparador solo acepta el historial legacy exacto 0001..0004.'
        )


def inspeccionar_auditoria_control(alias='default'):
    if alias != 'default':
        raise CommandError('La reparacion de auditoria dual-home solo admite --database default.')
    connection = connections[alias]
    tablas = set(connection.introspection.table_names())
    registradas = ()
    if TABLA_MIGRACIONES in tablas:
        registradas = tuple(
            MigrationRecorder(connection).migration_qs.filter(app='auditoria')
            .order_by('name').values_list('name', flat=True)
        )
    return EstadoAuditoriaControl(alias, TABLA_AUDITORIA in tablas, registradas)


def plan_auditoria_control(estado):
    """Validate the exact legacy shape, including FK tables, without any DDL."""
    if estado.alias != 'default':
        raise CommandError('La reparacion de auditoria dual-home solo admite --database default.')
    if estado.tabla_presente:
        raise CommandError(f'default: {TABLA_AUDITORIA} ya existe; no se repara.')
    if estado.migraciones_registradas != AUDITORIA_CONTROL_HISTORIAL_LEGACY:
        raise CommandError(
            'default: historial de auditoria no reconocido; se exige exactamente '
            '0001_initial, 0002_auditoria_sucursal_and_more, '
            '0003_alter_auditoria_accion, 0004_alter_auditoria_accion. '
            'No se materializo ninguna tabla.'
        )
    historical = MigrationLoader(None).project_state([
        ('auditoria', estado.migraciones_registradas[-1]),
    ])
    model = historical.apps.get_model('auditoria', 'Auditoria')
    if model._meta.db_table != TABLA_AUDITORIA:
        raise CommandError('Nombre historico de tabla de auditoria inesperado; no se repara.')
    referencias = sorted({
        field.remote_field.model._meta.db_table
        for field in model._meta.local_fields
        if field.remote_field is not None
    })
    presentes = set(connections[estado.alias].introspection.table_names())
    faltantes = sorted(set(referencias) - presentes)
    if faltantes:
        orientacion = (
            ' Ejecute primero reparar_sucursales_dual_home --database default --dry-run.'
            if TABLA_SUCURSAL in faltantes else ''
        )
        raise CommandError(
            f'default: faltan tablas referenciadas por auditoria: {", ".join(faltantes)}. '
            'No se materializo ninguna tabla.' + orientacion
        )
    return model, {
        'accion': 'reparar_auditoria_dual_home',
        'alias': estado.alias,
        'tabla': TABLA_AUDITORIA,
        'estado_historico': estado.migraciones_registradas[-1],
        'migraciones_registradas': list(estado.migraciones_registradas),
        'columnas': [field.column for field in model._meta.local_fields],
        'tablas_referenciadas': referencias,
        'indices': [index.name for index in model._meta.indexes],
        'modifica_historial': False,
        'aplica_migraciones_pendientes': False,
    }


def materializar_auditoria_control_fantasma(alias, migraciones_registradas):
    """Create only the known missing control table; preserve all migration rows.

    A PostgreSQL advisory transaction lock serializes explicit repair attempts.
    Re-read after acquiring it so an intervening migration or repair cannot be
    overwritten. Table, indexes and deferred FK creation commit atomically.
    """
    if alias != 'default':
        raise CommandError('La reparacion de auditoria dual-home solo admite --database default.')
    connection = connections[alias]
    if connection.vendor != 'postgresql':
        raise CommandError('Este reparador dirigido requiere PostgreSQL.')
    with transaction.atomic(using=alias):
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [9302026004])
        estado = inspeccionar_auditoria_control(alias)
        if estado.migraciones_registradas != tuple(migraciones_registradas):
            raise CommandError('El historial de auditoria cambio desde el preflight; no se repara.')
        model, plan = plan_auditoria_control(estado)
        with connection.schema_editor() as editor:
            editor.create_model(model)
        final = inspeccionar_auditoria_control(alias)
        if not final.tabla_presente or final.migraciones_registradas != estado.migraciones_registradas:
            raise CommandError('La postcondicion de auditoria fallo; se revierte la materializacion.')
        return plan
