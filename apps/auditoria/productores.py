"""Matriz viva evento -> productor; evita prometer cobertura inexistente."""

from dataclasses import dataclass

from .models import Auditoria


@dataclass(frozen=True)
class Productor:
    estado: str
    productores: tuple[str, ...] = ()
    nota: str = ''


ACTIVO = 'ACTIVO'
HELPER_COMPAT = 'HELPER_COMPAT'
SIN_PRODUCTOR = 'SIN_PRODUCTOR'


def _activo(*rutas):
    return Productor(ACTIVO, rutas)


def _helper(*rutas):
    return Productor(HELPER_COMPAT, rutas, 'Migrar a audit.event.v1 al tocarlo.')


def _sin(nota):
    return Productor(SIN_PRODUCTOR, (), nota)


MATRIZ_LEGACY = {
    Auditoria.TipoAccion.LOGIN: _activo('apps.usuarios.views:login_view'),
    Auditoria.TipoAccion.LOGOUT: _activo('apps.usuarios.views:logout_view'),
    Auditoria.TipoAccion.INTENTO_LOGIN_FALLIDO: _activo('apps.usuarios.views:login_view'),
    Auditoria.TipoAccion.CREAR: _activo('apps.auditoria.models:Auditoria.registrar'),
    Auditoria.TipoAccion.EDITAR: _activo('apps.auditoria.models:Auditoria.registrar'),
    Auditoria.TipoAccion.ELIMINAR: _sin('Sin productor generico soportado.'),
    Auditoria.TipoAccion.VER: _sin('Las lecturas no se auditan por defecto.'),
    Auditoria.TipoAccion.PRODUCTO_CREADO: _sin('Pendiente del contrato A05/A06.'),
    Auditoria.TipoAccion.PRODUCTO_EDITADO: _sin('Pendiente del contrato A05/A06.'),
    Auditoria.TipoAccion.PRODUCTO_ELIMINADO: _sin('Pendiente del contrato A05/A06.'),
    Auditoria.TipoAccion.PRECIO_MODIFICADO: _helper(
        'apps.auditoria.models:Auditoria.registrar_cambio_precio',
    ),
    Auditoria.TipoAccion.COMPRA_REGISTRADA: _helper(
        'apps.auditoria.models:Auditoria.registrar_compra',
    ),
    Auditoria.TipoAccion.LOTE_CREADO: _sin('No existe productor de dominio.'),
    Auditoria.TipoAccion.AJUSTE_INVENTARIO: _activo(
        'apps.inventario.services.ajustes_service:registrar_ajuste_service',
    ),
    Auditoria.TipoAccion.VENTA_CREADA: _activo(
        'apps.ventas.services.ventas_service:procesar_venta_service',
    ),
    Auditoria.TipoAccion.VENTA_ANULADA: _activo(
        'apps.ventas.services.anulaciones_service:anular_venta_service',
    ),
    Auditoria.TipoAccion.TICKET_IMPRESO: _sin('No existe productor de dominio.'),
    Auditoria.TipoAccion.TICKET_REIMPRESO: _helper(
        'apps.auditoria.models:Auditoria.registrar_reimpresion_ticket',
    ),
    Auditoria.TipoAccion.DESCUENTO_AUTORIZADO: _activo(
        'apps.ventas.services.ventas_service:_consumir_autorizacion_descuento',
    ),
    Auditoria.TipoAccion.RECIBO_CXC_IMPRESO: _sin('Pendiente del productor C02.'),
    Auditoria.TipoAccion.COMPROBANTE_EMITIDO: _activo(
        'apps.ventas.views:comprobante_venta_pdf',
    ),
    Auditoria.TipoAccion.TEST_IMPRESORA: _sin('Pendiente del productor C02.'),
    Auditoria.TipoAccion.USUARIO_CREADO: _sin(
        'Reemplazado por usuarios.usuario.provisionado v1.',
    ),
    Auditoria.TipoAccion.USUARIO_MODIFICADO: _sin(
        'Reemplazado por usuarios.usuario.actualizado v1.',
    ),
    Auditoria.TipoAccion.USUARIO_ACTIVADO: _sin(
        'Reemplazado por usuarios.usuario.activado v1.',
    ),
    Auditoria.TipoAccion.USUARIO_DESACTIVADO: _sin(
        'Reemplazado por usuarios.usuario.desactivado v1.',
    ),
    Auditoria.TipoAccion.PERMISO_ASIGNADO: _sin('Pendiente del contrato A03/CT-02.'),
    Auditoria.TipoAccion.PERMISO_REVOCADO: _sin('Pendiente del contrato A03/CT-02.'),
    Auditoria.TipoAccion.BACKUP_CREADO: _sin('No existe productor audit.event.v1.'),
    Auditoria.TipoAccion.BACKUP_RESTAURADO: _sin('No existe productor audit.event.v1.'),
    Auditoria.TipoAccion.CONFIGURACION: _activo(
        'apps.cuentas_por_cobrar.services:crear_cuenta_para_venta',
    ),
    Auditoria.TipoAccion.ERROR_SISTEMA: _activo(
        'apps.auditoria.middleware:AuditoriaMiddleware.process_exception',
    ),
    Auditoria.TipoAccion.CIERRE_DIARIO: _activo(
        'apps.reportes.management.commands.generar_cierre_diario:Command',
    ),
    Auditoria.TipoAccion.AUDITORIA_PURGADA: _activo(
        'apps.auditoria.models:AuditoriaManager.purgar_hasta',
    ),
}


# Productores CT-01 obligatorios introducidos por A02. Las familias futuras se
# agregan en el bloque que las implemente; no se reservan como si ya existieran.
MATRIZ_V1 = {
    'negocios.negocio.creado': 'apps.negocios.services:crear_negocio',
    'negocios.negocio.actualizado': 'apps.negocios.services:actualizar_negocio',
    'negocios.negocio.desactivado': 'apps.negocios.services:actualizar_negocio',
    'negocios.negocio.reactivado': 'apps.negocios.services:actualizar_negocio',
    'usuarios.usuario.provisionado': 'apps.usuarios.services:provisionar_usuario',
    'usuarios.usuario.actualizado': 'apps.usuarios.services:actualizar_usuario',
    'usuarios.usuario.activado': 'apps.usuarios.services:actualizar_usuario',
    'usuarios.usuario.desactivado': 'apps.usuarios.services:actualizar_usuario',
    'tenant.provisioning.prepared': (
        'apps.tenancy.services:preparar_tenant_provisioning'
    ),
    'tenant.provisioning.pending': (
        'apps.tenancy.services:marcar_estado_provisioning'
    ),
    'tenant.provisioning.db_ready': (
        'apps.tenancy.services:marcar_estado_provisioning'
    ),
    'tenant.provisioning.schema_ready': (
        'apps.tenancy.services:marcar_estado_provisioning'
    ),
    'tenant.provisioning.tenant_ready': (
        'apps.tenancy.services:marcar_estado_provisioning'
    ),
    'tenant.provisioning.control_ready': (
        'apps.tenancy.services:marcar_estado_provisioning'
    ),
    'tenant.provisioning.active': (
        'apps.tenancy.services:marcar_estado_provisioning'
    ),
    'tenant.provisioning.failed': (
        'apps.tenancy.services:marcar_estado_provisioning'
    ),
}


def opciones_legacy_para_visor():
    opciones = []
    labels = dict(Auditoria.TipoAccion.choices)
    for value, spec in MATRIZ_LEGACY.items():
        label = labels[value]
        if spec.estado == SIN_PRODUCTOR:
            label = f'{label} (solo historico; sin productor activo)'
        opciones.append({'value': value, 'label': label, 'estado': spec.estado})
    return opciones
