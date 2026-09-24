"""Checkpoint de solo lectura del bootstrap y sync de suscripciones.

SUS-016 no se resuelve ejecutando ``bootstrap_suscripciones`` ante cada
hallazgo: eso puede convertir un diagnóstico de una instalación a medio
aprovisionar en una mutación comercial no revisada. Este módulo toma una foto
determinista del ámbito actual para que el operador pueda guardar, comparar y
aprobar el siguiente paso explícitamente.
"""
from collections import OrderedDict
from hashlib import sha256
import json

from django.conf import settings
from django.db.models import Count

from . import registry, seed


SCHEMA_VERSION = 'suscripciones.sync-checkpoint.v1'


def construir_checkpoint(*, using=None):
    """Devuelve el estado verificable de UNA instalación, sin escribir nada.

    ``using`` existe para las pruebas y para quien ya tenga un contexto tenant;
    el comando normal usa el alias activo o ``default``. La huella no contiene
    reloj: el mismo estado material produce el mismo checkpoint y permite
    comparar dos ejecuciones sin inventar una tabla/ledger nuevo.
    """
    alias = using or _alias_activo()
    problemas = []

    from apps.configuracion.models import ConfiguracionNegocio
    from apps.negocios.models import Negocio
    from apps.sucursales.models import Sucursal
    from apps.sync.models import DiferidoSync, LogSync, VersionMaestro
    from .models import Modulo, NegocioModulo, Plan, SuscripcionNegocio

    sucursal, instalacion = _identificar_instalacion(Sucursal, alias, problemas)
    catalogo = _revisar_catalogo(Modulo, alias, problemas)
    planes = _revisar_planes(Plan, alias, problemas)
    negocios = _revisar_negocios(
        Negocio, SuscripcionNegocio, NegocioModulo, alias, problemas,
    )
    configuracion_legacy = _revisar_configuracion_legacy(
        ConfiguracionNegocio, negocios['cantidad'], alias, problemas,
    )
    sync = _revisar_sync(
        DiferidoSync, LogSync, VersionMaestro, alias, sucursal, problemas,
    )

    reporte = OrderedDict([
        ('schema_version', SCHEMA_VERSION),
        ('modo', 'SOLO_LECTURA'),
        ('instalacion', instalacion),
        ('catalogo', catalogo),
        ('planes_preset', planes),
        ('negocios', negocios),
        ('configuracion_legacy', configuracion_legacy),
        ('sync', sync),
        ('problemas', problemas),
    ])
    reporte['estado'] = 'PARCIAL' if problemas else 'LISTO'
    reporte['checkpoint'] = _huella(reporte)
    return reporte


def _alias_activo():
    from apps.tenancy.context import get_current_tenant_alias

    return get_current_tenant_alias() or 'default'


def _identificar_instalacion(Sucursal, alias, problemas):
    from apps.tenancy.context import get_current_tenant_key

    codigo = getattr(settings, 'SUCURSAL_CODIGO', '') or ''
    sucursal = None
    if codigo:
        sucursal = (
            Sucursal.objects.using(alias)
            .select_related('negocio')
            .filter(codigo=codigo)
            .first()
        )
        if sucursal is None:
            _problema(
                problemas,
                'SUCURSAL_NO_RESUELTA',
                'SUCURSAL_CODIGO no identifica una sucursal en esta instalación.',
                sucursal_codigo=codigo,
            )

    tenant_key = get_current_tenant_key() or None
    return sucursal, OrderedDict([
        ('database_alias', alias),
        ('tenant_key', tenant_key),
        ('sucursal_codigo', codigo or None),
        ('sucursal_resuelta', str(sucursal) if sucursal else None),
    ])


def _revisar_catalogo(Modulo, alias, problemas):
    esperado = {modulo.key: modulo.core for modulo in registry.CATALOGO_MODULOS}
    actual = dict(Modulo.objects.using(alias).values_list('key', 'core'))
    faltantes = sorted(set(esperado) - set(actual))
    fantasmas = sorted(set(actual) - set(esperado))
    core_divergente = sorted(
        key for key in set(esperado) & set(actual)
        if esperado[key] != actual[key]
    )

    if faltantes:
        _problema(
            problemas,
            'CATALOGO_INCOMPLETO',
            'Hay módulos declarados en código que no existen en la base.',
            modulos=faltantes,
        )
    if fantasmas:
        _problema(
            problemas,
            'CATALOGO_FANTASMA',
            'Hay módulos en la base que no existen en el registro de código.',
            modulos=fantasmas,
        )
    if core_divergente:
        _problema(
            problemas,
            'CATALOGO_CORE_DIVERGENTE',
            'El atributo core difiere entre el registro y la base.',
            modulos=core_divergente,
        )

    return OrderedDict([
        ('esperados', len(esperado)),
        ('en_base', len(actual)),
        ('faltantes_en_base', faltantes),
        ('fantasmas_en_base', fantasmas),
        ('core_divergente', core_divergente),
    ])


def _revisar_planes(Plan, alias, problemas):
    planes = {
        plan.slug: plan
        for plan in (
            Plan.objects.using(alias)
            .prefetch_related('modulos')
            .filter(slug__in=seed.TIERS)
        )
    }
    resultado = []
    for slug, (_nombre, _descripcion, keys, version) in seed.TIERS.items():
        plan = planes.get(slug)
        if plan is None:
            _problema(
                problemas,
                'PRESET_AUSENTE',
                'Falta un plan preset requerido por el catálogo actual.',
                plan=slug,
            )
            resultado.append({
                'slug': slug,
                'estado': 'AUSENTE',
                'version_esperada': version,
                'version_actual': None,
                'modulos_esperados': sorted(keys),
                'modulos_actuales': [],
            })
            continue

        actuales = sorted(modulo.key for modulo in plan.modulos.all())
        fila = {
            'slug': slug,
            'activo': plan.activo,
            'version_esperada': version,
            'version_actual': plan.preset_version,
            'modulos_esperados': sorted(keys),
            'modulos_actuales': actuales,
        }
        if plan.preset_version is None:
            fila['estado'] = 'PERSONALIZADO'
        elif plan.preset_version != version:
            fila['estado'] = 'DESACTUALIZADO'
            _problema(
                problemas,
                'PRESET_DESACTUALIZADO',
                'El preset administrado no tiene la versión declarada en código.',
                plan=slug,
                version_esperada=version,
                version_actual=plan.preset_version,
            )
        elif actuales != sorted(keys):
            fila['estado'] = 'DRIFT'
            _problema(
                problemas,
                'PRESET_DRIFT',
                'El preset administrado declara una versión vigente con módulos distintos.',
                plan=slug,
                modulos_esperados=sorted(keys),
                modulos_actuales=actuales,
            )
        else:
            fila['estado'] = 'VIGENTE'
        resultado.append(fila)
    return resultado


def _revisar_negocios(Negocio, SuscripcionNegocio, NegocioModulo, alias, problemas):
    negocios = list(Negocio.objects.using(alias).order_by('pk'))
    ids = [negocio.pk for negocio in negocios]
    suscripciones = {
        suscripcion.negocio_id: suscripcion
        for suscripcion in (
            SuscripcionNegocio.objects.using(alias)
            .select_related('plan')
            .filter(negocio_id__in=ids)
        )
    }
    overrides = dict(
        NegocioModulo.objects.using(alias)
        .filter(negocio_id__in=ids)
        .values('negocio_id')
        .annotate(cantidad=Count('id'))
        .values_list('negocio_id', 'cantidad')
    )

    if not negocios:
        _problema(
            problemas,
            'SIN_NEGOCIOS',
            'La instalación no tiene ningún negocio que pueda quedar aprovisionado.',
        )

    detalle = []
    for negocio in negocios:
        suscripcion = suscripciones.get(negocio.pk)
        fila = {
            'id': negocio.pk,
            'slug': negocio.slug,
            'activo': negocio.activo,
            'overrides': overrides.get(negocio.pk, 0),
        }
        if suscripcion is None:
            fila.update({'estado': 'SIN_SUSCRIPCION', 'plan': None, 'suscripcion_activa': None})
            _problema(
                problemas,
                'NEGOCIO_SIN_SUSCRIPCION',
                'El bootstrap no dejó una suscripción explícita para este negocio.',
                negocio=negocio.slug,
            )
        elif not suscripcion.activa:
            fila.update({
                'estado': 'SUSPENDIDA',
                'plan': suscripcion.plan.slug if suscripcion.plan_id else None,
                'suscripcion_activa': False,
            })
        elif suscripcion.plan_id:
            fila.update({
                'estado': 'CON_PLAN',
                'plan': suscripcion.plan.slug,
                'suscripcion_activa': True,
            })
        else:
            fila.update({'estado': 'CUSTOM', 'plan': None, 'suscripcion_activa': True})
        detalle.append(fila)

    return OrderedDict([('cantidad', len(negocios)), ('detalle', detalle)])


def _revisar_configuracion_legacy(ConfiguracionNegocio, negocios, alias, problemas):
    cantidad = ConfiguracionNegocio.objects.using(alias).filter(sucursal__isnull=True).count()
    ambigua = bool(cantidad and negocios != 1)
    if ambigua:
        _problema(
            problemas,
            'CONFIGURACION_LEGACY_AMBIGUA',
            'Las configuraciones legacy sin sucursal no se pueden atribuir con seguridad.',
            configuraciones=cantidad,
            negocios=negocios,
        )
    return {
        'sin_sucursal': cantidad,
        'adopcion_inequivoca': bool(cantidad and negocios == 1),
        'ambigua': ambigua,
    }


def _revisar_sync(DiferidoSync, LogSync, VersionMaestro, alias, sucursal, problemas):
    """Incluye solo señales existentes; no reintenta, limpia ni crea cursores."""
    from apps.sync.engine import SyncEngine

    tenant_key, sucursal_codigo = SyncEngine._ambito_diferidos()
    diferidos = list(
        DiferidoSync.objects.using(alias)
        .filter(
            tenant_key=tenant_key,
            sucursal_codigo=sucursal_codigo,
            estado='PENDIENTE',
        )
        .values('tabla')
        .annotate(cantidad=Count('id'))
        .order_by('tabla')
    )
    if diferidos:
        _problema(
            problemas,
            'SYNC_DIFERIDOS_PENDIENTES',
            'Hay elementos de pull capturados pero aún no aplicados.',
            por_tabla=diferidos,
        )

    cursores_bloqueados = list(
        VersionMaestro.objects.using(alias)
        .filter(bloqueado_desde__isnull=False)
        .order_by('tabla')
        .values('tabla', 'bloqueado_desde', 'bloqueado_detalle')
    )
    for cursor in cursores_bloqueados:
        cursor['bloqueado_desde'] = cursor['bloqueado_desde'].isoformat()
        _problema(
            problemas,
            'SYNC_CURSOR_BLOQUEADO',
            'Un cursor de pull no puede avanzar hasta recibir un payload aplicable.',
            tabla=cursor['tabla'],
            detalle=cursor['bloqueado_detalle'] or None,
        )

    ciclos = LogSync.objects.using(alias).filter(tipo='FULL')
    if sucursal is not None:
        ciclos = ciclos.filter(sucursal_id=sucursal.pk)
    ultimo = ciclos.order_by('-inicio', '-pk').first()
    ultimo_ciclo = None
    if ultimo is not None:
        ultimo_ciclo = {
            'resultado': ultimo.resultado,
            'inicio': ultimo.inicio.isoformat(),
            'mensaje': ultimo.mensaje or None,
        }
        if ultimo.resultado in {'PARCIAL', 'FALLO'}:
            _problema(
                problemas,
                f'SYNC_ULTIMO_CICLO_{ultimo.resultado}',
                'El último ciclo completo no terminó exitosamente.',
                resultado=ultimo.resultado,
            )

    return OrderedDict([
        ('ambito_diferidos', {
            'tenant_key': tenant_key or None,
            'sucursal_codigo': sucursal_codigo or None,
        }),
        ('diferidos_pendientes', diferidos),
        ('cursores_bloqueados', cursores_bloqueados),
        ('ultimo_ciclo_completo', ultimo_ciclo),
    ])


def _problema(problemas, codigo, mensaje, **contexto):
    problemas.append(OrderedDict([
        ('codigo', codigo),
        ('mensaje', mensaje),
        *[(clave, valor) for clave, valor in contexto.items()],
    ]))


def _huella(reporte):
    """Digest de la foto semántica, estable entre ejecuciones idénticas."""
    serializado = json.dumps(
        reporte, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
    )
    return sha256(serializado.encode('utf-8')).hexdigest()
