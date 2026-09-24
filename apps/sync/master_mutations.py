"""Recepción cloud de propuestas A05.3 de Producto y Categoría.

Las propuestas no son ``EventoSync``: no representan un hecho financiero y su
aplicación requiere comparar una revisión del catálogo, revalidar el permiso
vigente y conservar una divergencia para una decisión humana posterior. Este
módulo es invocado solamente por el receptor cloud autenticado en
``apps.api.views.sync``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.auditoria.models import Auditoria
from apps.auditoria.services import registrar_mutacion
from apps.productos.models import Categoria, Producto
from apps.sync.models import MutacionMaestro


@dataclass(frozen=True)
class ResultadoRecepcionMaestro:
    estado: str
    codigo: str = ''
    mensaje: str = ''
    cloud_entidad_id: int | None = None
    cloud_revision: str = ''

    def serializar(self, mutacion_id):
        resultado = {
            'mutacion_id': str(mutacion_id),
            'estado': self.estado,
        }
        if self.codigo:
            resultado['codigo'] = self.codigo
        if self.mensaje:
            resultado['error'] = self.mensaje
        if self.cloud_entidad_id is not None:
            resultado['cloud_entidad_id'] = self.cloud_entidad_id
        if self.cloud_revision:
            resultado['cloud_revision'] = self.cloud_revision
        return resultado


class RechazoMaestro(RuntimeError):
    def __init__(self, codigo, mensaje):
        self.codigo = codigo
        super().__init__(mensaje)


class ConflictoMaestro(RuntimeError):
    def __init__(self, codigo, mensaje, *, cloud_entidad_id=None, cloud_revision=''):
        self.codigo = codigo
        self.cloud_entidad_id = cloud_entidad_id
        self.cloud_revision = cloud_revision
        super().__init__(mensaje)


def _revision(valor):
    """Representación CAS canónica, compatible con DRF ``Z`` y ISO Python."""
    if not valor:
        return ''
    try:
        return datetime.fromisoformat(str(valor).replace('Z', '+00:00')).isoformat()
    except (TypeError, ValueError):
        return str(valor)


def _revision_actual(entidad):
    return _revision(getattr(entidad, 'fecha_modificacion', None))


def _tenant_key(sucursal):
    negocio = getattr(sucursal, 'negocio', None)
    return str(getattr(negocio, 'slug', '') or '')


def _permiso_requerido(entidad, operacion):
    base = 'productos' if entidad == MutacionMaestro.Entidad.PRODUCTO else 'categorias'
    if operacion == MutacionMaestro.Operacion.CREAR:
        return f'{base}.crear'
    if operacion in (
        MutacionMaestro.Operacion.ACTIVAR,
        MutacionMaestro.Operacion.DESACTIVAR,
    ):
        return f'{base}.eliminar'
    return f'{base}.editar'


def _modelo(entidad):
    return Producto if entidad == MutacionMaestro.Entidad.PRODUCTO else Categoria


def _actor_actual(username, sucursal, using):
    User = get_user_model()
    actor = (
        User.objects.using(using)
        .filter(username__iexact=username, activo=True)
        .first()
    )
    if actor is None:
        raise RechazoMaestro(
            'MASTER_ACTOR_UNKNOWN',
            'El actor de la propuesta no existe o ya no está activo en cloud.',
        )

    negocio_sucursal = getattr(sucursal, 'negocio_id', None)
    negocio_actor = getattr(actor, 'negocio_id', None)
    if negocio_sucursal is not None and negocio_actor != negocio_sucursal:
        raise RechazoMaestro(
            'MASTER_ACTOR_SCOPE_MISMATCH',
            'El actor no pertenece al ámbito de la sucursal autenticada.',
        )
    return actor


def _es_replay(ledger, propuesta, sucursal):
    return (
        ledger.sucursal_id == sucursal.pk
        and ledger.entidad == propuesta['entidad']
        and ledger.entidad_id == propuesta['entidad_local_id']
        and ledger.operacion == propuesta['operacion']
        and ledger.revision_base == propuesta['revision_base']
        and ledger.delta == propuesta['delta']
        and ledger.actor_username == propuesta['actor_username']
        # En una creación el ledger se completa después con la PK cloud. La
        # propuesta original necesariamente trae ``None`` y sigue siendo el
        # mismo replay aunque ya conozcamos su resultado.
        and (
            propuesta['operacion'] == MutacionMaestro.Operacion.CREAR
            or ledger.cloud_entidad_id == propuesta.get('cloud_entidad_id')
        )
        and ledger.cloud_categoria_id == propuesta.get('categoria_cloud_id')
    )


def _resultado_ledger(ledger, *, replay=False):
    if ledger.estado == MutacionMaestro.Estado.CONFIRMADA:
        return ResultadoRecepcionMaestro(
            estado='DUPLICADA' if replay else 'CONFIRMADA',
            cloud_entidad_id=ledger.cloud_entidad_id,
            cloud_revision=ledger.cloud_revision,
        )
    if ledger.estado == MutacionMaestro.Estado.CONFLICTO:
        return ResultadoRecepcionMaestro(
            estado='CONFLICTO',
            codigo=ledger.codigo_resultado or 'MASTER_CONFLICT',
            mensaje=ledger.conflicto_detalle,
            cloud_entidad_id=ledger.cloud_entidad_id,
            cloud_revision=ledger.cloud_revision,
        )
    return ResultadoRecepcionMaestro(
        estado='RECHAZADA',
        codigo=ledger.codigo_resultado or 'MASTER_REJECTED',
        mensaje=ledger.ultimo_error,
        cloud_entidad_id=ledger.cloud_entidad_id,
        cloud_revision=ledger.cloud_revision,
    )


def _crear_ledger(propuesta, sucursal, actor):
    return MutacionMaestro(
        mutacion_id=propuesta['mutacion_id'],
        entidad=propuesta['entidad'],
        entidad_id=propuesta['entidad_local_id'],
        operacion=propuesta['operacion'],
        revision_base=propuesta['revision_base'],
        delta=propuesta['delta'],
        actor=actor,
        actor_username=propuesta['actor_username'],
        sucursal=sucursal,
        sucursal_codigo=str(sucursal.codigo),
        tenant_key=_tenant_key(sucursal),
        cloud_entidad_id=propuesta.get('cloud_entidad_id'),
        cloud_categoria_id=propuesta.get('categoria_cloud_id'),
    )


def _reservar_ledger(propuesta, sucursal, using):
    """Reserva el UUID con savepoint para cerrar la carrera entre receptores.

    El primer ``select`` de replay es una optimización. La garantía real es la
    constraint única: si dos requests llegan juntas, el segundo insert revierte
    solo su savepoint, vuelve a leer el ledger ya confirmado y responde como
    replay en vez de dejar un 500/ACK incierto innecesario.
    """
    ledger = _crear_ledger(propuesta, sucursal, actor=None)
    try:
        with transaction.atomic(using=using):
            ledger.save(using=using)
        return ledger, None
    except IntegrityError:
        existente = (
            MutacionMaestro.objects.using(using)
            .select_for_update()
            .filter(mutacion_id=propuesta['mutacion_id'])
            .first()
        )
        return None, existente


def _auditar_resultado(*, ledger, actor, sucursal, resultado, accion, codigo, detalle, using):
    """La decisión cloud también queda en CT-01, ligada al UUID de propuesta."""
    return registrar_mutacion(
        accion=accion,
        actor=actor,
        entidad=ledger,
        antes={},
        despues={
            'estado': ledger.estado,
            'codigo': codigo,
            'cloud_entidad_id': ledger.cloud_entidad_id,
            'cloud_categoria_id': ledger.cloud_categoria_id,
            'cloud_revision': ledger.cloud_revision,
        },
        resultado=resultado,
        canal=Auditoria.Canal.SYNC,
        tenant=_tenant_key(sucursal),
        sucursal=sucursal,
        correlacion_id=ledger.mutacion_id,
        idempotencia_key=str(ledger.mutacion_id),
        metadata={
            'master_mutation_id': str(ledger.mutacion_id),
            'source_actor_username': ledger.actor_username,
            'source_entity': ledger.entidad,
            'source_entity_local_id': ledger.entidad_id,
            'source_operation': ledger.operacion,
        },
        error={'code': codigo, 'message': detalle} if resultado != Auditoria.Resultado.SUCCEEDED else None,
        using=using,
    )


def _cambios_dominio(propuesta, using):
    """Extrae solo los ``after`` autorizados y resuelve FKs cloud por identidad."""
    cambios = {
        campo: valor['after']
        for campo, valor in propuesta['delta'].items()
        if campo != 'id'
    }
    if propuesta['entidad'] != MutacionMaestro.Entidad.PRODUCTO:
        return cambios

    if 'categoria_id' not in cambios:
        return cambios
    cloud_id = propuesta.get('categoria_cloud_id')
    try:
        Categoria.objects.using(using).select_for_update().get(pk=cloud_id)
    except Categoria.DoesNotExist as exc:
        raise ConflictoMaestro(
            'MASTER_CATEGORY_NOT_FOUND',
            'La categoría cloud referida por la propuesta ya no existe.',
        ) from exc
    # El serializer resuelve la FK contra el alias activo del tenant; mantener
    # la PK (en vez de inyectar una instancia) conserva su validación estándar.
    cambios['categoria'] = cloud_id
    cambios.pop('categoria_id', None)
    return cambios


def _validar_y_aplicar(propuesta, entidad, using):
    """Valida con los serializers del portal y persiste en el alias del tenant."""
    # Importación diferida: serializers.sync importa MutacionMaestro y este
    # módulo se invoca desde views.sync después de que Django terminó de cargar.
    from apps.api.serializers.maestros import (
        CategoriaWriteSerializer,
        ProductoWriteSerializer,
    )

    es_creacion = propuesta['operacion'] == MutacionMaestro.Operacion.CREAR
    cambios = _cambios_dominio(propuesta, using)
    serializer_cls = (
        ProductoWriteSerializer
        if propuesta['entidad'] == MutacionMaestro.Entidad.PRODUCTO
        else CategoriaWriteSerializer
    )
    serializer = serializer_cls(
        data=cambios,
        partial=not es_creacion,
    ) if es_creacion else serializer_cls(entidad, data=cambios, partial=True)
    if not serializer.is_valid():
        tiene_unico = any(
            getattr(error, 'code', '') == 'unique'
            for errores in serializer.errors.as_data().values()
            for error in errores
        )
        if tiene_unico:
            raise ConflictoMaestro(
                'MASTER_UNIQUE_CONFLICT',
                'La propuesta colisiona con una identidad única ya existente en cloud.',
            )
        raise RechazoMaestro(
            'MASTER_VALIDATION_ERROR',
            'La propuesta no cumple las reglas vigentes del catálogo cloud.',
        )

    datos = dict(serializer.validated_data)
    campo_estado = (
        'activo'
        if propuesta['entidad'] == MutacionMaestro.Entidad.PRODUCTO
        else 'activa'
    )
    tiene_estado = campo_estado in datos
    estado_deseado = datos.pop(campo_estado, True)
    motivo_inactivacion = datos.pop('motivo_inactivacion', None)
    try:
        with transaction.atomic(using=using):
            if es_creacion:
                entidad = _modelo(propuesta['entidad'])(**datos)
            else:
                for campo, valor in datos.items():
                    setattr(entidad, campo, valor)
                # Conserva la regla del serializer portal para un stub BUG-G:
                # una categoría explícita es una revisión sustantiva.
                if isinstance(entidad, Producto) and entidad.pendiente_revision and 'categoria' in datos:
                    entidad.pendiente_revision = False
            if tiene_estado:
                entidad.establecer_estado_operativo(
                    estado_deseado, motivo=motivo_inactivacion,
                )
            entidad.save(using=using)
    except IntegrityError as exc:
        raise ConflictoMaestro(
            'MASTER_UNIQUE_CONFLICT',
            'La propuesta colisiona con una identidad única ya existente en cloud.',
        ) from exc
    return entidad


def _registrar_decision_no_exitosa(ledger, *, actor, sucursal, codigo, detalle, conflicto, using,
                                   cloud_entidad_id=None, cloud_revision=''):
    if conflicto:
        ledger.estado = MutacionMaestro.Estado.CONFLICTO
        ledger.conflicto_detalle = str(detalle)[:2000]
        ledger.conflicto_at = timezone.now()
        accion = 'sync.maestro.conflicto'
        resultado = Auditoria.Resultado.FAILED
    else:
        ledger.estado = MutacionMaestro.Estado.RECHAZADA
        ledger.ultimo_error = str(detalle)[:2000]
        accion = 'sync.maestro.rechazado'
        resultado = Auditoria.Resultado.DENIED
    ledger.codigo_resultado = str(codigo)[:100]
    if cloud_entidad_id is not None:
        ledger.cloud_entidad_id = cloud_entidad_id
    if cloud_revision:
        ledger.cloud_revision = str(cloud_revision)[:64]
    ledger.save(using=using)
    _auditar_resultado(
        ledger=ledger,
        actor=actor,
        sucursal=sucursal,
        resultado=resultado,
        accion=accion,
        codigo=codigo,
        detalle=detalle,
        using=using,
    )
    return _resultado_ledger(ledger)


def recibir_mutacion_maestro(*, propuesta, sucursal, using):
    """Aplica una propuesta en cloud con replay, RBAC actual y CAS.

    La respuesta es un veredicto por propuesta; un conflicto o una revocación
    es resultado de negocio persistido, no un 500 que el POS deba reintentar a
    ciegas. Fallos de infraestructura sí escapan para que el endpoint informe
    ``ERROR`` y el lease local vuelva a intentar con el mismo UUID.
    """
    with transaction.atomic(using=using):
        existente = (
            MutacionMaestro.objects.using(using)
            .select_for_update()
            .filter(mutacion_id=propuesta['mutacion_id'])
            .first()
        )
        if existente is not None:
            if _es_replay(existente, propuesta, sucursal):
                return _resultado_ledger(existente, replay=True)
            return ResultadoRecepcionMaestro(
                estado='CONFLICTO',
                codigo='MASTER_MUTATION_ID_CONFLICT',
                mensaje='El UUID de mutación ya pertenece a otra propuesta.',
            )

        ledger, colision = _reservar_ledger(propuesta, sucursal, using)
        if colision is not None:
            if _es_replay(colision, propuesta, sucursal):
                return _resultado_ledger(colision, replay=True)
            return ResultadoRecepcionMaestro(
                estado='CONFLICTO',
                codigo='MASTER_MUTATION_ID_CONFLICT',
                mensaje='El UUID de mutación ya pertenece a otra propuesta.',
            )

        actor = None
        try:
            actor = _actor_actual(propuesta['actor_username'], sucursal, using)
            ledger.actor = actor
            ledger.save(using=using, update_fields=['actor', 'actualizado_at'])
            permiso = _permiso_requerido(propuesta['entidad'], propuesta['operacion'])
            if not actor.tiene_permiso(permiso, sucursal=sucursal):
                raise RechazoMaestro(
                    'MASTER_PERMISSION_REVOKED',
                    'El actor ya no conserva el permiso vigente para esta propuesta.',
                )

            es_creacion = propuesta['operacion'] == MutacionMaestro.Operacion.CREAR
            entidad = None
            if not es_creacion:
                try:
                    entidad = (
                        _modelo(propuesta['entidad']).objects.using(using)
                        .select_for_update().get(pk=propuesta['cloud_entidad_id'])
                    )
                except _modelo(propuesta['entidad']).DoesNotExist as exc:
                    raise ConflictoMaestro(
                        'MASTER_ENTITY_NOT_FOUND',
                        'El maestro cloud ya no existe.',
                        cloud_entidad_id=propuesta['cloud_entidad_id'],
                    ) from exc
                revision_actual = _revision_actual(entidad)
                if _revision(propuesta['revision_base']) != revision_actual:
                    raise ConflictoMaestro(
                        'MASTER_REVISION_CONFLICT',
                        'El maestro cambió en cloud antes de aplicar la propuesta.',
                        cloud_entidad_id=entidad.pk,
                        cloud_revision=revision_actual,
                    )

            entidad = _validar_y_aplicar(propuesta, entidad, using)
            revision = _revision_actual(entidad)
            ledger.estado = MutacionMaestro.Estado.CONFIRMADA
            ledger.cloud_entidad_id = entidad.pk
            ledger.cloud_revision = revision
            ledger.codigo_resultado = ''
            ledger.ultimo_error = ''
            ledger.conflicto_detalle = ''
            ledger.save(using=using)
            _auditar_resultado(
                ledger=ledger,
                actor=actor,
                sucursal=sucursal,
                resultado=Auditoria.Resultado.SUCCEEDED,
                accion='sync.maestro.aplicado',
                codigo='',
                detalle='',
                using=using,
            )
            return _resultado_ledger(ledger)
        except ConflictoMaestro as exc:
            return _registrar_decision_no_exitosa(
                ledger,
                actor=actor,
                sucursal=sucursal,
                codigo=exc.codigo,
                detalle=str(exc),
                conflicto=True,
                cloud_entidad_id=exc.cloud_entidad_id,
                cloud_revision=exc.cloud_revision,
                using=using,
            )
        except RechazoMaestro as exc:
            return _registrar_decision_no_exitosa(
                ledger,
                actor=actor,
                sucursal=sucursal,
                codigo=exc.codigo,
                detalle=str(exc),
                conflicto=False,
                using=using,
            )
