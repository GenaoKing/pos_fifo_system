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
    Auditoria.TipoAccion.CREAR: _sin(
        'No queda productor de dominio activo; C05 p6 migro cotizaciones a CT-01.',
    ),
    Auditoria.TipoAccion.EDITAR: _activo(
        'apps.clientes.views:editar_cliente',
        'apps.clientes.views:toggle_estado_cliente',
        'apps.inventario.views:compra_editar',
        'apps.ventas.services.ventas_service:_marcar_cotizacion_convertida',
    ),
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
    Auditoria.TipoAccion.AJUSTE_INVENTARIO: _sin(
        'Migrado a inventario.ajuste.creado (audit.event.v1, C05 p6).',
    ),
    Auditoria.TipoAccion.VENTA_CREADA: _sin(
        'Migrado a ventas.venta.creada (audit.event.v1, C05 p6).',
    ),
    Auditoria.TipoAccion.VENTA_ANULADA: _sin(
        'Migrado a ventas.venta.anulada (audit.event.v1, C05 p6).',
    ),
    Auditoria.TipoAccion.TICKET_IMPRESO: _sin('No existe productor de dominio.'),
    Auditoria.TipoAccion.TICKET_REIMPRESO: _helper(
        'apps.auditoria.models:Auditoria.registrar_reimpresion_ticket',
    ),
    Auditoria.TipoAccion.DESCUENTO_AUTORIZADO: _sin(
        'Migrado a ventas.descuento.autorizado (audit.event.v1, C05 p6).',
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
    Auditoria.TipoAccion.CONFIGURACION: _sin(
        'La antigua ruta CxC migro a cuentas_por_cobrar.cuenta.creada (C05 p6).',
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
    # CFG-017 / SUS-015 (C03, tras integrar CT-01). `configuracion.negocio.*`
    # solo cubre el Admin: es la unica interfaz de escritura de
    # ConfiguracionNegocio hoy (no hay viewset API). `suscripciones.*.creado`
    # de SuscripcionNegocio no se registra: ese viewset no permite POST
    # (`http_method_names`), asi que esa accion nunca se emite.
    'configuracion.negocio.creado': 'apps.configuracion.admin:ConfiguracionNegocioAdmin.save_model',
    'configuracion.negocio.actualizado': 'apps.configuracion.admin:ConfiguracionNegocioAdmin.save_model',
    'suscripciones.suscripcion.actualizado': (
        'apps.api.views.suscripciones:GuardDegradacionMixin._aplicar'
    ),
    'suscripciones.override_negocio.creado': (
        'apps.api.views.suscripciones:GuardDegradacionMixin._aplicar'
    ),
    'suscripciones.override_negocio.actualizado': (
        'apps.api.views.suscripciones:GuardDegradacionMixin._aplicar'
    ),
    'suscripciones.override_negocio.eliminado': (
        'apps.api.views.suscripciones:GuardDegradacionMixin._aplicar'
    ),
    # C05 p6: productores financieros/comerciales migrados desde el adaptador
    # legacy. Cada accion persiste el mismo envelope audit.event.v1; el
    # EventoSync de dominio sigue siendo un riel independiente.
    'ventas.venta.creada': 'apps.ventas.services.ventas_service:procesar_venta_service',
    'ventas.venta.anulada': (
        'apps.ventas.services.anulaciones_service:anular_venta_service'
    ),
    'ventas.descuento.autorizado': (
        'apps.ventas.services.ventas_service:_consumir_autorizacion_descuento'
    ),
    'inventario.ajuste.creado': (
        'apps.inventario.services.ajustes_service:registrar_ajuste_service'
    ),
    'cuentas_por_cobrar.cuenta.creada': (
        'apps.cuentas_por_cobrar.services:crear_cuenta_para_venta'
    ),
    'cuentas_por_cobrar.credito.override_autorizado': (
        'apps.cuentas_por_cobrar.services:crear_cuenta_para_venta'
    ),
    'cuentas_por_cobrar.abono.registrado': (
        'apps.cuentas_por_cobrar.services:registrar_pago_cxc_service'
    ),
    'cuentas_por_cobrar.abono.anulado': (
        'apps.cuentas_por_cobrar.services:anular_pago_cxc_service'
    ),
    'cuentas_por_cobrar.cuenta.anulada': (
        'apps.cuentas_por_cobrar.services:anular_cuenta_por_venta'
    ),
    'cuentas_por_cobrar.plazo.reprogramado': (
        'apps.cuentas_por_cobrar.services:reprogramar_cxc_por_plazo_cliente'
    ),
    'cotizaciones.cotizacion.creada': (
        'apps.cotizaciones.views:guardar_cotizacion'
    ),
    'cotizaciones.cotizacion.convertida': (
        'apps.cotizaciones.views:marcar_convertida'
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
