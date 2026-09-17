"""Resolución humana CAS para propuestas negativas de maestros (A06).

El receptor A05.3 conserva el resultado técnico original en
``MutacionMaestro``.  Este módulo ejecuta una decisión explícita sin convertir
un conflicto en last-write-wins: compara otra vez la revisión cloud observada,
vuelve a validar el delta local y deja tanto una auditoría CT-01 como un ledger
de resolución inmutable.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.auditoria.models import Auditoria
from apps.auditoria.services import registrar_mutacion
from apps.sync.master_mutations import (
    ConflictoMaestro,
    RechazoMaestro,
    _modelo,
    _revision,
    _revision_actual,
    _validar_y_aplicar,
)
from apps.sync.models import MutacionMaestro, ResolucionConflictoMaestro


class ResolucionConflictoError(RuntimeError):
    """Error de dominio presentado por la superficie A06."""

    def __init__(self, codigo, mensaje, *, status_code=409):
        self.codigo = codigo
        self.status_code = status_code
        super().__init__(mensaje)


@dataclass(frozen=True)
class ResultadoResolucionConflicto:
    mutacion: MutacionMaestro
    resuelta: bool


def permiso_resolucion(entidad):
    return {
        MutacionMaestro.Entidad.PRODUCTO: 'productos.editar',
        MutacionMaestro.Entidad.CATEGORIA: 'categorias.editar',
    }[entidad]


def _propuesta_desde_ledger(ledger):
    return {
        'entidad': ledger.entidad,
        'entidad_local_id': ledger.entidad_id,
        'operacion': ledger.operacion,
        'revision_base': ledger.revision_base,
        'actor_username': ledger.actor_username,
        'cloud_entidad_id': ledger.cloud_entidad_id,
        'categoria_cloud_id': ledger.cloud_categoria_id,
        'delta': ledger.delta,
    }


def _auditar(*, ledger, actor, accion, resultado, antes, despues, using, error=None):
    return registrar_mutacion(
        accion=accion,
        actor=actor,
        entidad=ledger,
        antes=antes,
        despues=despues,
        resultado=resultado,
        canal=Auditoria.Canal.PORTAL_API,
        tenant=ledger.tenant_key,
        sucursal=ledger.sucursal,
        correlacion_id=ledger.mutacion_id,
        idempotencia_key=str(ledger.mutacion_id),
        metadata={
            'master_mutation_id': str(ledger.mutacion_id),
            'source_actor_username': ledger.actor_username,
            'resolution_actor_username': getattr(actor, 'username', ''),
            'source_entity': ledger.entidad,
            'source_entity_local_id': ledger.entidad_id,
            'source_operation': ledger.operacion,
        },
        error=error,
        using=using,
    )


def _registrar_conflicto_renovado(*, ledger, actor, cloud_revision, using, codigo,
                                  detalle):
    anterior = {
        'estado': ledger.estado,
        'cloud_revision': ledger.cloud_revision,
        'codigo': ledger.codigo_resultado,
    }
    ledger.estado = MutacionMaestro.Estado.CONFLICTO
    ledger.codigo_resultado = codigo
    ledger.conflicto_detalle = str(detalle)[:2000]
    ledger.ultimo_error = ''
    ledger.cloud_revision = str(cloud_revision or '')[:64]
    ledger.conflicto_at = timezone.now()
    ledger.save(using=using, update_fields=[
        'estado', 'codigo_resultado', 'conflicto_detalle', 'ultimo_error',
        'cloud_revision', 'conflicto_at', 'actualizado_at',
    ])
    _auditar(
        ledger=ledger,
        actor=actor,
        accion='sync.maestro.conflicto.reintento',
        resultado=Auditoria.Resultado.FAILED,
        antes=anterior,
        despues={
            'estado': ledger.estado,
            'cloud_revision': ledger.cloud_revision,
            'codigo': ledger.codigo_resultado,
        },
        using=using,
        error={'code': codigo, 'message': detalle},
    )


def _registrar_rechazo_renovado(*, ledger, actor, using, codigo, detalle):
    anterior = {
        'estado': ledger.estado,
        'codigo': ledger.codigo_resultado,
    }
    ledger.estado = MutacionMaestro.Estado.RECHAZADA
    ledger.codigo_resultado = codigo
    ledger.ultimo_error = str(detalle)[:2000]
    ledger.conflicto_detalle = ''
    ledger.save(using=using, update_fields=[
        'estado', 'codigo_resultado', 'ultimo_error', 'conflicto_detalle',
        'actualizado_at',
    ])
    _auditar(
        ledger=ledger,
        actor=actor,
        accion='sync.maestro.conflicto.reintento',
        resultado=Auditoria.Resultado.DENIED,
        antes=anterior,
        despues={'estado': ledger.estado, 'codigo': ledger.codigo_resultado},
        using=using,
        error={'code': codigo, 'message': detalle},
    )


def resolver_conflicto_maestro(*, ledger, actor, accion, motivo,
                               cloud_revision_observada, using):
    """Resuelve una propuesta negativa sin sobrescribir una revisión nueva.

    El llamador debe entregar el ledger bloqueado.  La segunda comprobación de
    permiso es intencional: la vista API es la entrada habitual, pero el
    servicio no queda seguro por una convención de su llamador.
    """
    if ledger.estado not in {
        MutacionMaestro.Estado.CONFLICTO,
        MutacionMaestro.Estado.RECHAZADA,
    }:
        raise ResolucionConflictoError(
            'MASTER_CONFLICT_NOT_PENDING',
            'La propuesta ya no requiere una resolución humana.',
        )
    if hasattr(ledger, 'resolucion_conflicto'):
        raise ResolucionConflictoError(
            'MASTER_CONFLICT_ALREADY_RESOLVED',
            'La propuesta ya tiene una resolución registrada.',
        )
    if not actor.tiene_permiso(permiso_resolucion(ledger.entidad), sucursal=ledger.sucursal):
        raise ResolucionConflictoError(
            'MASTER_CONFLICT_PERMISSION_DENIED',
            'No conserva el permiso vigente para resolver este maestro.',
            status_code=403,
        )
    if ledger.cloud_entidad_id is None:
        raise ResolucionConflictoError(
            'MASTER_CONFLICT_ENTITY_UNRESOLVED',
            'El conflicto no identifica una entidad cloud para comparar su revisión.',
        )

    modelo = _modelo(ledger.entidad)
    try:
        entidad = modelo.objects.using(using).select_for_update().get(
            pk=ledger.cloud_entidad_id,
        )
    except modelo.DoesNotExist:
        _registrar_conflicto_renovado(
            ledger=ledger,
            actor=actor,
            cloud_revision='',
            using=using,
            codigo='MASTER_ENTITY_NOT_FOUND',
            detalle='El maestro cloud ya no existe al intentar resolverlo.',
        )
        return ResultadoResolucionConflicto(mutacion=ledger, resuelta=False)

    revision_actual = _revision_actual(entidad)
    if _revision(cloud_revision_observada) != revision_actual:
        _registrar_conflicto_renovado(
            ledger=ledger,
            actor=actor,
            cloud_revision=revision_actual,
            using=using,
            codigo='MASTER_REVISION_CONFLICT',
            detalle='El maestro cambió en cloud mientras se revisaba el conflicto.',
        )
        return ResultadoResolucionConflicto(mutacion=ledger, resuelta=False)

    antes = {
        'estado': ledger.estado,
        'cloud_revision': ledger.cloud_revision,
        'codigo': ledger.codigo_resultado,
    }
    if accion == ResolucionConflictoMaestro.Accion.APLICAR_LOCAL:
        try:
            entidad = _validar_y_aplicar(_propuesta_desde_ledger(ledger), entidad, using)
        except ConflictoMaestro as exc:
            _registrar_conflicto_renovado(
                ledger=ledger,
                actor=actor,
                cloud_revision=exc.cloud_revision or revision_actual,
                using=using,
                codigo=exc.codigo,
                detalle=str(exc),
            )
            return ResultadoResolucionConflicto(mutacion=ledger, resuelta=False)
        except RechazoMaestro as exc:
            _registrar_rechazo_renovado(
                ledger=ledger,
                actor=actor,
                using=using,
                codigo=exc.codigo,
                detalle=str(exc),
            )
            return ResultadoResolucionConflicto(mutacion=ledger, resuelta=False)

    revision_resultante = _revision_actual(entidad)
    ResolucionConflictoMaestro.objects.using(using).create(
        mutacion=ledger,
        accion=accion,
        motivo=motivo,
        actor=actor,
        actor_username=getattr(actor, 'username', ''),
        cloud_revision_observada=_revision(cloud_revision_observada),
        cloud_revision_resultante=revision_resultante,
        cloud_entidad_id=entidad.pk,
    )
    _auditar(
        ledger=ledger,
        actor=actor,
        accion='sync.maestro.conflicto.resuelto',
        resultado=Auditoria.Resultado.SUCCEEDED,
        antes=antes,
        despues={
            'accion': accion,
            'cloud_entidad_id': entidad.pk,
            'cloud_revision': revision_resultante,
        },
        using=using,
    )
    return ResultadoResolucionConflicto(mutacion=ledger, resuelta=True)
