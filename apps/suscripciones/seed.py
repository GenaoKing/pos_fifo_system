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


class BootstrapAmbiguo(RuntimeError):
    """
    Hay configuracion legacy sin sucursal (`sucursal=NULL`) y no se puede
    atribuir a un negocio de forma inequivoca (SUS-009). No se escribe nada:
    el operador debe asignarle una sucursal antes de migrar.
    """


def _flags_vendibles():
    """[(key, flag_legacy)] de los vendibles que derivan de un flag legacy."""
    return [(m.key, m.flag_legacy) for m in registry.vendibles() if m.flag_legacy]


def derivar_modulos_de_flags(negocio, ConfiguracionModel, configs_legacy=()):
    """
    Set de keys de modulos vendibles activos HOY para el negocio: la UNION de los
    `ConfiguracionNegocio.modulo_*` de sus sucursales, mas las configuraciones
    legacy adoptadas (`configs_legacy`, filas `sucursal=NULL` de una instalacion
    de un solo negocio — SUS-009). Asi la migracion no pierde lo que ya tenia.

    La union define el set del NEGOCIO; la conducta por sucursal la preserva
    aparte `_preservar_overrides_por_sucursal` (SUS-008).
    """
    activos = set()
    # cuentas_por_cobrar no tenia flag: historicamente siempre activo.
    activos.add('cuentas_por_cobrar')

    propias = ConfiguracionModel.objects.filter(sucursal__negocio=negocio)
    for key, flag in _flags_vendibles():
        if propias.filter(**{flag: True}).exists() or any(
            getattr(c, flag, False) for c in configs_legacy
        ):
            activos.add(key)
    return activos


def _resolver_override_sucursal(ModeloRef):
    """
    `SucursalModuloOverride` desde el mismo registro de apps que el llamador:
    el historico en una migracion, el real en runtime. Asi la firma de
    `bootstrap` no cambia (tiene llamadores en apps/tenancy y en una migracion
    ya aplicada). None si el estado historico aun no lo tiene.
    """
    try:
        return ModeloRef._meta.apps.get_model('suscripciones', 'SucursalModuloOverride')
    except (LookupError, ValueError):  # pragma: no cover - estado historico viejo
        return None


def _preservar_overrides_por_sucursal(
    negocio, negocio_set, ConfiguracionModel, ModuloModel,
    SucursalOverrideModel, resumen,
):
    """
    SUS-008 — el set del negocio es la UNION de sus sucursales, asi que una
    sucursal que tenia un flag en OFF lo veria ENCENDIDO tras el bootstrap. Para
    conservar cada estado previo bit por bit, se crea un override negativo
    (`activo=False`) en cada sucursal cuyo flag estaba apagado.
    """
    if SucursalOverrideModel is None:
        return
    for key, flag in _flags_vendibles():
        if key not in negocio_set:
            continue  # nadie lo tenia encendido: no hay nada que compensar
        modulo = ModuloModel.objects.filter(key=key).first()
        if modulo is None:
            continue
        apagadas = ConfiguracionModel.objects.filter(
            sucursal__negocio=negocio, sucursal__isnull=False,
        ).filter(**{flag: False})
        for cfg in apagadas:
            _, creado = SucursalOverrideModel.objects.get_or_create(
                sucursal_id=cfg.sucursal_id, modulo=modulo,
                defaults={'activo': False},
            )
            if creado:
                resumen['overrides_sucursal'] += 1


def bootstrap(
    *,
    ModuloModel,
    PlanModel,
    NegocioModel,
    NegocioModuloModel,
    SuscripcionModel,
    ConfiguracionModel,
):
    """
    Idempotente y atomico (SUS-016): siembra modulos+planes y, por cada negocio
    existente, crea su suscripcion (plan=None, custom) con los modulos derivados
    de sus flags, preservando la conducta por sucursal (SUS-008) y adoptando la
    configuracion legacy sin sucursal solo cuando es inequivoco (SUS-009).

    Devuelve un dict-resumen de lo que cambio. Lanza `BootstrapAmbiguo` si hay
    filas legacy sin sucursal y no hay exactamente un negocio para adoptarlas.
    """
    from django.db import transaction

    resumen = {
        'negocios': 0, 'suscripciones_creadas': 0, 'negocio_modulos': 0,
        'overrides_sucursal': 0, 'legacy_adoptadas': 0,
    }

    SucursalOverrideModel = _resolver_override_sucursal(NegocioModuloModel)

    legacy = list(ConfiguracionModel.objects.filter(sucursal__isnull=True))
    negocios = list(NegocioModel.objects.all())

    # SUS-009: una fila legacy solo se adopta si la atribucion es demostrable
    # (un unico negocio). Con varios negocios —o ninguno— es ambiguo: se aborta
    # sin escribir, en vez de ignorarla en silencio con un `.first()` implicito.
    if legacy and len(negocios) != 1:
        raise BootstrapAmbiguo(
            f'Hay {len(legacy)} configuracion(es) legacy sin sucursal y '
            f'{len(negocios)} negocio(s): no se pueden atribuir sin ambiguedad. '
            f'Asigna una sucursal a cada configuracion antes de migrar.'
        )
    legacy_adoptables = legacy if (legacy and len(negocios) == 1) else []

    with transaction.atomic():
        sembrar_modulos(ModuloModel)
        crear_planes_default(PlanModel, ModuloModel)

        for negocio in negocios:
            resumen['negocios'] += 1
            _, creada = SuscripcionModel.objects.get_or_create(
                negocio=negocio, defaults={'activa': True},
            )
            if creada:
                resumen['suscripciones_creadas'] += 1

            configs_legacy = legacy_adoptables if negocio.pk == negocios[0].pk else []
            resumen['legacy_adoptadas'] += len(configs_legacy)

            negocio_set = derivar_modulos_de_flags(
                negocio, ConfiguracionModel, configs_legacy=configs_legacy,
            )
            for key in negocio_set:
                modulo = ModuloModel.objects.filter(key=key).first()
                if modulo is None:
                    continue
                _, creado = NegocioModuloModel.objects.get_or_create(
                    negocio=negocio, modulo=modulo, defaults={'incluido': True},
                )
                if creado:
                    resumen['negocio_modulos'] += 1

            _preservar_overrides_por_sucursal(
                negocio, negocio_set, ConfiguracionModel, ModuloModel,
                SucursalOverrideModel, resumen,
            )

    return resumen
