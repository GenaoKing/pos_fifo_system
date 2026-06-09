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

    # --- Ventas (POS) -------------------------------------------------------
    ('ventas.crear', 'Registrar ventas', 'ventas', 'Procesar ventas en el POS.'),
    ('ventas.anular', 'Anular ventas', 'ventas', 'Anular ventas dentro del plazo permitido.'),
    ('ventas.aplicar_descuento', 'Aplicar descuentos', 'ventas', 'Aplicar descuentos en ventas.'),
    ('ventas.reimprimir', 'Reimprimir tickets', 'ventas', 'Reimprimir tickets de venta.'),

    # --- Cuentas por cobrar -------------------------------------------------
    ('cuentas_por_cobrar.ver', 'Ver cuentas por cobrar', 'cuentas_por_cobrar',
     'Consultar cartera y cuentas por cobrar.'),

    # --- Reportes -----------------------------------------------------------
    ('reportes.ver', 'Ver reportes', 'reportes', 'Acceder a reportes y dashboard.'),
    ('reportes.consolidado.ver', 'Ver reporte consolidado', 'reportes',
     'Ver reportes consolidados multi-sucursal.'),

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
PERMISOS_CAJERO_DEFAULT = [
    'ventas.crear',
    'ventas.aplicar_descuento',
    'ventas.reimprimir',
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
