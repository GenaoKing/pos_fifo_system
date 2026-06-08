"""
apps/suscripciones/seed.py
Siembra de modulos + planes default y bootstrap de entitlements para negocios
existentes (derivando el estado actual de los flags de ConfiguracionNegocio para
no cambiar la conducta).

Escrito para funcionar con modelos reales y con los historicos de una migracion.
"""
from . import registry

# Composicion de los tiers por defecto (solo modulos VENDIBLES; el core lo agrega
# el resolutor). Ajustable segun el negocio comercial.
TIERS = {
    'basico': (
        'Basico',
        'POS basico: ventas, inventario, clientes y caja.',
        ['impresion_termica', 'barcode_scanner'],
    ),
    'pro': (
        'Pro',
        'Basico + cuentas por cobrar, cotizaciones, reportes, dashboard y etiquetas.',
        ['impresion_termica', 'barcode_scanner', 'cuentas_por_cobrar',
         'cotizaciones', 'reportes_ondemand', 'dashboard', 'etiquetas_zebra'],
    ),
    'empresarial': (
        'Empresarial',
        'Todo, incluyendo e-CF y financiacion cooperativa.',
        ['impresion_termica', 'barcode_scanner', 'cuentas_por_cobrar',
         'cotizaciones', 'reportes_ondemand', 'dashboard', 'etiquetas_zebra',
         'ecf', 'financiacion'],
    ),
}


def sembrar_modulos(ModuloModel):
    """Upsert de la tabla Modulo desde el registro. Idempotente."""
    for m in registry.CATALOGO_MODULOS:
        ModuloModel.objects.update_or_create(
            key=m.key,
            defaults={'nombre': m.nombre, 'descripcion': m.descripcion, 'core': m.core},
        )


def crear_planes_default(PlanModel, ModuloModel):
    """Crea los planes Basico/Pro/Empresarial. No pisa la edicion manual: solo
    asigna modulos cuando el plan se crea (o esta vacio)."""
    for slug, (nombre, descripcion, keys_) in TIERS.items():
        plan, _ = PlanModel.objects.get_or_create(
            slug=slug,
            defaults={'nombre': nombre, 'descripcion': descripcion, 'activo': True},
        )
        if plan.modulos.count() == 0:
            plan.modulos.set(ModuloModel.objects.filter(key__in=keys_))


def derivar_modulos_de_flags(negocio, ConfiguracionModel):
    """
    Set de keys de modulos vendibles activos HOY para el negocio, segun los
    `ConfiguracionNegocio.modulo_*` de sus sucursales (union). Asi la migracion
    preserva exactamente lo que el negocio ya tenia.
    """
    activos = set()
    # cuentas_por_cobrar no tenia flag: historicamente siempre activo.
    activos.add('cuentas_por_cobrar')

    configs = ConfiguracionModel.objects.filter(sucursal__negocio=negocio)
    for m in registry.vendibles():
        if not m.flag_legacy:
            continue
        if configs.filter(**{m.flag_legacy: True}).exists():
            activos.add(m.key)
    return activos


def bootstrap(
    *,
    ModuloModel,
    PlanModel,
    NegocioModel,
    NegocioModuloModel,
    SuscripcionModel,
    ConfiguracionModel,
):
    """Idempotente: siembra modulos+planes y, por cada negocio existente, crea su
    suscripcion (plan=None, custom) con los modulos derivados de sus flags."""
    sembrar_modulos(ModuloModel)
    crear_planes_default(PlanModel, ModuloModel)

    for negocio in NegocioModel.objects.all():
        SuscripcionModel.objects.get_or_create(
            negocio=negocio, defaults={'activa': True}
        )
        for key in derivar_modulos_de_flags(negocio, ConfiguracionModel):
            modulo = ModuloModel.objects.filter(key=key).first()
            if modulo is not None:
                NegocioModuloModel.objects.get_or_create(
                    negocio=negocio, modulo=modulo, defaults={'incluido': True},
                )
