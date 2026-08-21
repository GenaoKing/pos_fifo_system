"""
apps/permisos/catalogo.py
Catalogo declarativo de permisos (acciones de negocio gateables).

Esta lista es la fuente de verdad del desarrollador: agregar una accion
controlable = agregar una linea aqui y aplicar el codigo en la vista/endpoint.
El comando `manage.py sync_permisos` (y una data migration) hacen upsert de
esta lista en la tabla Permiso.

Convencion de codigos: '<modulo>.<accion>' en minusculas.
Acciones CRUD estandar: ver / crear / editar / eliminar.
"""

# Cada entrada: (codigo, nombre, modulo, descripcion)
CATALOGO = [
    # --- Clientes -----------------------------------------------------------
    ('clientes.ver', 'Ver clientes', 'clientes', 'Listar y consultar clientes.'),
    ('clientes.crear', 'Crear clientes', 'clientes', 'Registrar nuevos clientes.'),
    ('clientes.editar', 'Editar clientes', 'clientes', 'Modificar datos de clientes.'),
    ('clientes.editar_limite_credito', 'Editar limite de credito', 'clientes',
     'Cambiar el limite de credito de un cliente. Separado de `clientes.editar`: quien corrige un telefono no deberia poder ampliar credito.'),
    ('clientes.eliminar', 'Eliminar clientes', 'clientes', 'Dar de baja clientes.'),

    # --- Productos ----------------------------------------------------------
    ('productos.ver', 'Ver productos', 'productos', 'Listar y consultar productos.'),
    ('productos.crear', 'Crear productos', 'productos', 'Registrar nuevos productos.'),
    ('productos.editar', 'Editar productos', 'productos', 'Modificar productos.'),
    ('productos.eliminar', 'Eliminar productos', 'productos', 'Dar de baja productos.'),

    # --- Categorias ---------------------------------------------------------
    ('categorias.ver', 'Ver categorias', 'categorias', 'Listar y consultar categorias.'),
    ('categorias.crear', 'Crear categorias', 'categorias', 'Registrar categorias.'),
    ('categorias.editar', 'Editar categorias', 'categorias', 'Modificar categorias.'),
    ('categorias.eliminar', 'Eliminar categorias', 'categorias', 'Eliminar categorias.'),

    # --- Compras / Inventario ----------------------------------------------
    ('compras.ver', 'Ver compras', 'compras', 'Consultar compras e ingresos de mercancia.'),
    ('compras.registrar', 'Registrar compras', 'compras', 'Registrar compras (ingreso de lotes FIFO).'),
    ('inventario.ver', 'Ver inventario', 'inventario', 'Consultar stock y lotes.'),
    ('inventario.ajustar', 'Ajustar inventario', 'inventario', 'Ajustes manuales de inventario.'),

    # --- Caja ---------------------------------------------------------------
    ('caja.operar', 'Operar caja', 'caja',
     'Abrir y cerrar el propio turno, registrar movimientos y ver su estado. '
     'Lo que hace una cajera todos los dias.'),
    ('caja.administrar', 'Administrar caja', 'caja',
     'Ver historial/turnos de otros, registrar movimientos de caja (retiros/ingresos).'),

    # --- Auditoria ----------------------------------------------------------
    ('auditoria.ver', 'Ver auditoria', 'auditoria', 'Consultar el registro de auditoria.'),

    # --- Configuracion ------------------------------------------------------
    ('configuracion.administrar', 'Administrar configuracion', 'configuracion',
     'Acceder y modificar la configuracion del negocio.'),

    # --- Ventas (POS) -------------------------------------------------------
    ('ventas.crear', 'Registrar ventas', 'ventas', 'Procesar ventas en el POS.'),
    ('ventas.anular', 'Anular ventas', 'ventas', 'Anular ventas dentro del plazo permitido.'),
    ('ventas.aplicar_descuento', 'Aplicar descuentos', 'ventas', 'Aplicar descuentos en ventas.'),
    ('ventas.autorizar_descuento', 'Autorizar descuentos', 'ventas',
     'Emitir la autorizacion que habilita un descuento por encima de la '
     'tolerancia configurada. Quien lo tiene tambien descuenta sin pedir '
     'autorizacion a nadie: el gate solo aplica a quien NO lo tiene.'),
    ('ventas.reimprimir', 'Reimprimir tickets', 'ventas', 'Reimprimir tickets de venta.'),

    # --- Cuentas por cobrar -------------------------------------------------
    ('cuentas_por_cobrar.ver', 'Ver cuentas por cobrar', 'cuentas_por_cobrar',
     'Consultar cartera y cuentas por cobrar.'),
    ('cuentas_por_cobrar.cobrar', 'Registrar abonos CxC', 'cuentas_por_cobrar',
     'Registrar abonos a cuentas por cobrar.'),
    ('cuentas_por_cobrar.anular_pago', 'Anular abonos CxC', 'cuentas_por_cobrar',
     'Anular/revertir abonos registrados (reversa LIFO).'),
    ('cuentas_por_cobrar.autorizar_exceso_credito', 'Autorizar exceso de credito',
     'cuentas_por_cobrar',
     'Emitir la autorizacion puntual que permite superar el limite de credito '
     'de un cliente en una venta.'),

    # --- Reportes -----------------------------------------------------------
    ('reportes.ver', 'Ver reportes', 'reportes', 'Acceder a reportes y dashboard.'),
    ('reportes.sucursal.ver', 'Ver reportes de su sucursal', 'reportes',
     'Ver reportes on-demand acotados a las sucursales asignadas.'),
    ('reportes.consolidado.ver', 'Ver reporte consolidado', 'reportes',
     'Consolidar reportes de TODAS las sucursales. Solo consolida si la '
     'asignacion del rol es global (sin sucursal); acotada a una, vale por '
     'esa sucursal unicamente.'),

    # --- Sucursales ---------------------------------------------------------
    ('sucursales.ver', 'Ver sucursales', 'sucursales', 'Listar sucursales del negocio.'),

    # --- Administracion de permisos (meta) ----------------------------------
    ('permisos.administrar', 'Administrar roles y permisos', 'permisos',
     'Crear/editar roles y asignar permisos a usuarios del negocio.'),

    # --- Administracion de suscripcion/modulos (operador SaaS) ---------------
    ('suscripciones.administrar', 'Administrar suscripcion y modulos', 'suscripciones',
     'Asignar planes y modulos (entitlements) a los negocios.'),
]


# Permisos que recibe por defecto el rol "Cajero" al hacer el seed inicial.
#
# Se alinean con la conducta REAL del POS local (no con el viejo `permisos_cajera`,
# que era codigo muerto e incoherente):
#   - vender, aplicar descuento y reimprimir: el cajero los hace hoy (sin gate).
#   - anular: NO. La regla real (apps/ventas/services/anulaciones_service.py:
#     _puede_anular) gatea las anulaciones a ADMIN/SYSADMIN. Por eso 'ventas.anular'
#     NO esta aqui. Ver docs/RBAC_PERMISOS.md (seccion "Rol Cajero por defecto").
#   - CxC ver/cobrar: la cajera consulta cartera y registra abonos hoy (antes
#     del gate granular solo habia @login_required). 'anular_pago' NO: la
#     reversa de abonos es operacion sensible (default solo Administrador).
PERMISOS_CAJERO_DEFAULT = [
    'ventas.crear',
    'caja.operar',
    'ventas.aplicar_descuento',
    'ventas.reimprimir',
    'cuentas_por_cobrar.ver',
    'cuentas_por_cobrar.cobrar',
    # El dashboard personal del cajero (sus ventas del dia) ahora se gatea con
    # este permiso en vez de con el flag legacy `es_cajera`. Va en el default
    # para que una instalacion existente no pierda la pantalla de inicio.
    'reportes.ver',
]


def codigos_catalogo():
    """Set con todos los codigos del catalogo."""
    return {fila[0] for fila in CATALOGO}


def sembrar_catalogo(PermisoModel):
    """
    Upsert del catalogo en la tabla Permiso. Idempotente.

    Acepta el modelo Permiso real o el historico (apps.get_model en migraciones).
    Retorna (creados, actualizados).
    """
    creados = 0
    actualizados = 0
    for codigo, nombre, modulo, descripcion in CATALOGO:
        obj, created = PermisoModel.objects.update_or_create(
            codigo=codigo,
            defaults={'nombre': nombre, 'modulo': modulo, 'descripcion': descripcion},
        )
        if created:
            creados += 1
        else:
            actualizados += 1
    return creados, actualizados
