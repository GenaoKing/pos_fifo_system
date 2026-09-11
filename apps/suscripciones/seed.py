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
#
# El ultimo elemento es la VERSION del preset (SUS-017): subirla cuando se
# edita la lista de modulos de un tier es lo que le permite a
# `sincronizar_planes_preset` (via `manage.py sync_modulos`) detectar que un
# plan gestionado quedo desactualizado y re-aplicarlo. `crear_planes_default`
# -usado por `bootstrap()` para aprovisionar un tenant nuevo- ignora la
# version: ahi no hay nada que resincronizar todavia.
TIERS = {
    'basico': (
        'Basico',
        'POS basico: ventas, inventario, clientes y caja.',
        ['impresion_termica', 'barcode_scanner'],
        1,
    ),
    'pro': (
        'Pro',
        'Basico + cuentas por cobrar, cotizaciones, reportes, dashboard y etiquetas.',
        ['impresion_termica', 'barcode_scanner', 'cuentas_por_cobrar',
         'cotizaciones', 'reportes_ondemand', 'dashboard', 'etiquetas_zebra'],
        1,
    ),
    'empresarial': (
        'Empresarial',
        'Todo, incluyendo e-CF y financiacion cooperativa.',
        ['impresion_termica', 'barcode_scanner', 'cuentas_por_cobrar',
         'cotizaciones', 'reportes_ondemand', 'dashboard', 'etiquetas_zebra',
         'ecf', 'financiacion'],
        1,
    ),
}


def _alias_activo():
    """Alias del tenant en contexto, o `default` fuera de tenancy (SUS-016)."""
    from apps.tenancy.context import get_current_tenant_alias
    return get_current_tenant_alias() or 'default'


def sembrar_modulos(ModuloModel, using=None):
    """Upsert de la tabla Modulo desde el registro. Idempotente."""
    manager = ModuloModel.objects if using is None else ModuloModel.objects.using(using)
    for m in registry.CATALOGO_MODULOS:
        manager.update_or_create(
            key=m.key,
            defaults={'nombre': m.nombre, 'descripcion': m.descripcion, 'core': m.core},
        )


def crear_planes_default(PlanModel, ModuloModel, using=None):
    """Crea los planes Basico/Pro/Empresarial. No pisa la edicion manual: solo
    asigna modulos cuando el plan se crea (o esta vacio).

    Usado por `bootstrap()` (aprovisionar un tenant nuevo) y por la migracion
    historica `0002_seed_suscripciones`: por eso ignora la version del preset
    (`sincronizar_planes_preset`, mas abajo, es la version consciente de
    version -- y de `PlanModel` corriente, no historico -- que usa
    `manage.py sync_modulos`)."""
    plan_manager = PlanModel.objects if using is None else PlanModel.objects.using(using)
    modulo_manager = ModuloModel.objects if using is None else ModuloModel.objects.using(using)
    for slug, (nombre, descripcion, keys_, _version) in TIERS.items():
        plan, _ = plan_manager.get_or_create(
            slug=slug,
            defaults={'nombre': nombre, 'descripcion': descripcion, 'activo': True},
        )
        if plan.modulos.count() == 0:
            plan.modulos.set(modulo_manager.filter(key__in=keys_))


def sincronizar_planes_preset(PlanModel, ModuloModel, using=None, *, reportar=None):
    """
    SUS-017 — a diferencia de `crear_planes_default`, esto SI resincroniza:
    editar la lista de modulos de un tier en `TIERS` (y subir su version) y
    volver a correr esto actualiza cualquier plan gestionado que quedo atras.
    Antes `sync_modulos` prometia sincronizar los planes default y no lo
    hacia -- quitar un modulo de un tier y re-correrlo no lo restauraba.

    Un plan con `preset_version=None` es un plan PERSONALIZADO (desenganchado
    a proposito, por Admin o por el backfill de la migracion que introdujo
    este campo): nunca se toca aca.

    Solo tiene sentido con el `PlanModel` corriente (necesita el campo
    `preset_version`); no se invoca desde la migracion historica de seed.

    `reportar(slug, accion)`, si se pasa, informa que paso con cada tier:
    'creado' | 'actualizado' | 'sin_cambios' | 'personalizado'.
    """
    plan_manager = PlanModel.objects if using is None else PlanModel.objects.using(using)
    modulo_manager = ModuloModel.objects if using is None else ModuloModel.objects.using(using)
    for slug, (nombre, descripcion, keys_, version) in TIERS.items():
        plan = plan_manager.filter(slug=slug).first()
        if plan is None:
            plan = plan_manager.create(
                slug=slug, nombre=nombre, descripcion=descripcion, activo=True,
                preset_version=version,
            )
            plan.modulos.set(modulo_manager.filter(key__in=keys_))
            if reportar:
                reportar(slug, 'creado')
            continue
        if plan.preset_version is None:
            if reportar:
                reportar(slug, 'personalizado')
            continue
        if plan.preset_version == version:
            if reportar:
                reportar(slug, 'sin_cambios')
            continue
        plan.modulos.set(modulo_manager.filter(key__in=keys_))
        plan.preset_version = version
        plan.save(using=using, update_fields=['preset_version'])
        if reportar:
            reportar(slug, 'actualizado')


class BootstrapAmbiguo(RuntimeError):
    """
    Hay configuracion legacy sin sucursal (`sucursal=NULL`) y no se puede
    atribuir a un negocio de forma inequivoca (SUS-009). No se escribe nada:
    el operador debe asignarle una sucursal antes de migrar.
    """


def _flags_vendibles():
    """[(key, flag_legacy)] de los vendibles que derivan de un flag legacy."""
    return [(m.key, m.flag_legacy) for m in registry.vendibles() if m.flag_legacy]


def derivar_modulos_de_flags(negocio, ConfiguracionModel, configs_legacy=(), using=None):
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

    config_manager = ConfiguracionModel.objects if using is None else ConfiguracionModel.objects.using(using)
    propias = config_manager.filter(sucursal__negocio=negocio)
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
    SucursalOverrideModel, resumen, using=None,
):
    """
    SUS-008 — el set del negocio es la UNION de sus sucursales, asi que una
    sucursal que tenia un flag en OFF lo veria ENCENDIDO tras el bootstrap. Para
    conservar cada estado previo bit por bit, se crea un override negativo
    (`activo=False`) en cada sucursal cuyo flag estaba apagado.
    """
    if SucursalOverrideModel is None:
        return
    config_manager = ConfiguracionModel.objects if using is None else ConfiguracionModel.objects.using(using)
    modulo_manager = ModuloModel.objects if using is None else ModuloModel.objects.using(using)
    override_manager = (
        SucursalOverrideModel.objects if using is None else SucursalOverrideModel.objects.using(using)
    )
    for key, flag in _flags_vendibles():
        if key not in negocio_set:
            continue  # nadie lo tenia encendido: no hay nada que compensar
        modulo = modulo_manager.filter(key=key).first()
        if modulo is None:
            continue
        apagadas = config_manager.filter(
            sucursal__negocio=negocio, sucursal__isnull=False,
        ).filter(**{flag: False})
        for cfg in apagadas:
            _, creado = override_manager.get_or_create(
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
    using=None,
):
    """
    Idempotente y atomico (SUS-016): siembra modulos+planes y, por cada negocio
    existente, crea su suscripcion (plan=None, custom) con los modulos derivados
    de sus flags, preservando la conducta por sucursal (SUS-008) y adoptando la
    configuracion legacy sin sucursal solo cuando es inequivoco (SUS-009).

    `using`: alias de BD donde correr todo el bootstrap. Si se omite, se
    resuelve el tenant activo en contexto (o `default` fuera de tenancy) —
    MERGE-C03-ALIAS-ATOMIC: un `transaction.atomic()` sin alias se abre sobre
    `default` aunque el router, bajo `with_tenant`, mande las escrituras a la
    BD del tenant; esa escritura queda fuera de la transaccion y un fallo a
    mitad de camino no la revierte. Aca la transaccion y cada consulta usan el
    mismo alias explicito.

    Devuelve un dict-resumen de lo que cambio. Lanza `BootstrapAmbiguo` si hay
    filas legacy sin sucursal y no hay exactamente un negocio para adoptarlas.
    """
    from django.db import transaction

    alias = using or _alias_activo()

    resumen = {
        'negocios': 0, 'suscripciones_creadas': 0, 'negocio_modulos': 0,
        'overrides_sucursal': 0, 'legacy_adoptadas': 0,
    }

    SucursalOverrideModel = _resolver_override_sucursal(NegocioModuloModel)

    legacy = list(ConfiguracionModel.objects.using(alias).filter(sucursal__isnull=True))
    negocios = list(NegocioModel.objects.using(alias).all())

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

    with transaction.atomic(using=alias):
        sembrar_modulos(ModuloModel, using=alias)
        crear_planes_default(PlanModel, ModuloModel, using=alias)

        for negocio in negocios:
            resumen['negocios'] += 1
            _, creada = SuscripcionModel.objects.using(alias).get_or_create(
                negocio=negocio, defaults={'activa': True},
            )
            if creada:
                resumen['suscripciones_creadas'] += 1

            configs_legacy = legacy_adoptables if negocio.pk == negocios[0].pk else []
            resumen['legacy_adoptadas'] += len(configs_legacy)

            negocio_set = derivar_modulos_de_flags(
                negocio, ConfiguracionModel, configs_legacy=configs_legacy, using=alias,
            )
            for key in negocio_set:
                modulo = ModuloModel.objects.using(alias).filter(key=key).first()
                if modulo is None:
                    continue
                _, creado = NegocioModuloModel.objects.using(alias).get_or_create(
                    negocio=negocio, modulo=modulo, defaults={'incluido': True},
                )
                if creado:
                    resumen['negocio_modulos'] += 1

            _preservar_overrides_por_sucursal(
                negocio, negocio_set, ConfiguracionModel, ModuloModel,
                SucursalOverrideModel, resumen, using=alias,
            )


class PlanDesconocido(ValueError):
    """
    SUS-014 — el slug no corresponde a ningun `Plan` real en la base
    consultada. `bootstrap_tenant --plan <slug>` acepta texto libre y lo
    escribe en `Tenant.plan_slug` (control plane) sin validarlo contra la
    base del tenant; si el Plan no existe ahi, la asignacion se omite en
    silencio (`.first()` sin resultado) y el control plane queda anunciando
    un plan que la suscripcion operativa nunca tuvo.
    """


def validar_plan_slug(slug, *, using=None):
    """
    Valida un slug de plan ANTES de tocar el control plane y la base del
    tenant (SUS-014). Uso esperado en `bootstrap_tenant` (Codex): llamarla
    con el alias de la base tenant ANTES de escribir `Tenant.plan_slug` y
    ANTES de asignar la suscripcion ahi — si el slug no existe, no se toca
    ninguna de las dos bases.

    Un slug vacio es valido: significa "sin plan asignado explicitamente",
    el mismo caso que hoy deja `Tenant.plan_slug=''`.
    """
    if not slug:
        return

    from .models import Plan

    manager = Plan.objects if using is None else Plan.objects.using(using)
    if not manager.filter(slug=slug).exists():
        existentes = sorted(manager.values_list('slug', flat=True))
        raise PlanDesconocido(
            f'"{slug}" no es un Plan existente en esta base. '
            f'Planes disponibles: {", ".join(existentes) or "(ninguno)"}'
        )

    return resumen
