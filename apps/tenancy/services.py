"""Servicios de ciclo de vida del control plane de tenants."""

from django.db import transaction
from django.utils import timezone

from apps.auditoria.models import Auditoria
from apps.auditoria.services import redactar_payload, registrar_mutacion

from .models import Tenant


def _snapshot_tenant(tenant):
    return {
        'tenant_key': tenant.tenant_key,
        'slug': tenant.slug,
        'nombre': tenant.nombre,
        'rnc': tenant.rnc_canonico,
        'db_name': tenant.db_name,
        'media_prefix': tenant.media_prefix,
        'plan_slug': tenant.plan_slug,
        'activo': tenant.activo,
    }


def preparar_tenant_provisioning(
    *, tenant_key, slug, nombre, rnc='', plan_slug='', actor=None,
):
    """Crea/actualiza la identidad control-plane junto con su evento CT-01."""
    with transaction.atomic(using='default'):
        tenant = (
            Tenant.objects.using('default').select_for_update()
            .filter(tenant_key=tenant_key).first()
        )
        creado = tenant is None
        antes = {} if creado else _snapshot_tenant(tenant)
        activo_previo = False if creado else tenant.activo
        if creado:
            tenant = Tenant(
                tenant_key=tenant_key,
                slug=slug,
                nombre=nombre,
                rnc=rnc,
                db_name=f'tnt_{tenant_key}',
                media_prefix=f'{tenant_key}/',
                plan_slug=plan_slug,
                activo=False,
                estado_provisioning=Tenant.EstadoProvisioning.PENDING,
            )
        else:
            if tenant.slug != slug:
                raise ValueError('El slug existente no coincide con la identidad solicitada.')
            tenant.nombre = nombre
            tenant.rnc = rnc
            tenant.plan_slug = plan_slug
        tenant.save(using='default')
        registrar_mutacion(
            accion='tenant.provisioning.prepared',
            actor=actor,
            entidad=tenant,
            antes=antes,
            despues=_snapshot_tenant(tenant),
            resultado=Auditoria.Resultado.SUCCEEDED,
            canal=Auditoria.Canal.COMMAND,
            tenant=tenant,
            metadata={'recoverable': True, 'cross_db_atomicity': False},
            using='default',
        )
    return tenant, creado, activo_previo


def divergencias_identidad(tenant, negocio, configuraciones):
    """Compara la autoridad del control plane con sus proyecciones tenant."""
    if negocio is None:
        return [{'code': 'NEGOCIO_MISSING', 'field': 'negocio'}]

    diferencias = []
    comparaciones = (
        ('slug', tenant.slug, negocio.slug),
        ('nombre', tenant.nombre, negocio.nombre),
        ('rnc', tenant.rnc_canonico, negocio.rnc_canonico),
        ('activo', tenant.activo, negocio.activo),
    )
    for campo, esperado, actual in comparaciones:
        if esperado != actual:
            diferencias.append({
                'code': 'NEGOCIO_DRIFT',
                'field': campo,
                'expected': esperado,
                'actual': actual,
            })

    configuraciones = list(configuraciones)
    if not configuraciones:
        diferencias.append({'code': 'CONFIG_MISSING', 'field': 'configuracion'})
    for config in configuraciones:
        codigo = getattr(getattr(config, 'sucursal', None), 'codigo', None)
        for campo, esperado, actual in (
            ('nombre_negocio', tenant.nombre, config.nombre_negocio),
            ('rnc', tenant.rnc_canonico or '', config.rnc or ''),
        ):
            if esperado != actual:
                diferencias.append({
                    'code': 'CONFIG_DRIFT',
                    'branch': codigo,
                    'field': campo,
                    'expected': esperado,
                    'actual': actual,
                })
    return diferencias


def marcar_estado_provisioning(
    tenant,
    estado,
    *,
    activo=None,
    error=None,
    incrementar_intento=False,
):
    """Persiste y audita un checkpoint reanudable en ``default``.

    La BD tenant conserva su propia transaccion: no se promete un commit
    atomico distribuido entre ambas bases.
    """
    with transaction.atomic(using='default'):
        actual = (
            Tenant.objects.using('default')
            .select_for_update()
            .get(pk=tenant.pk)
        )
        antes = {
            'estado': actual.estado_provisioning,
            'activo': actual.activo,
            'intentos': actual.provisioning_intentos,
        }
        actual.estado_provisioning = estado
        actual.provisioning_actualizado = timezone.now()
        actual.provisioning_error = (
            redactar_payload(str(error)) if error is not None else ''
        )
        if incrementar_intento:
            actual.provisioning_intentos += 1
        if activo is not None:
            actual.activo = bool(activo)
        actual.save(using='default', update_fields=[
            'estado_provisioning',
            'provisioning_actualizado',
            'provisioning_error',
            'provisioning_intentos',
            'activo',
        ])
        registrar_mutacion(
            accion=f'tenant.provisioning.{estado.lower()}',
            actor=None,
            entidad=actual,
            antes=antes,
            despues={
                'estado': actual.estado_provisioning,
                'activo': actual.activo,
                'intentos': actual.provisioning_intentos,
            },
            resultado=(
                Auditoria.Resultado.FAILED
                if estado == Tenant.EstadoProvisioning.FAILED
                else Auditoria.Resultado.SUCCEEDED
            ),
            canal=Auditoria.Canal.COMMAND,
            tenant=actual,
            metadata={'recoverable': True, 'cross_db_atomicity': False},
            error=(
                {'code': type(error).__name__, 'message': str(error)}
                if error is not None else None
            ),
            using='default',
        )
    return actual
