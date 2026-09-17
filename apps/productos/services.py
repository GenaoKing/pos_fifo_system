"""Mutaciones locales de maestros A05.2a.

Estas funciones son deliberadamente distintas del outbox ``EventoSync``: los
productos y las categorias son propuestas de catalogo, no hechos financieros.
El receptor cloud/CAS aun no existe (A05.3); por eso esta capa solamente deja
la propuesta durable y auditada para que dicho receptor la consuma despues.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import uuid

from django.db import IntegrityError, transaction

from apps.auditoria.models import Auditoria
from apps.auditoria.services import registrar_mutacion
from apps.sync.models import MutacionMaestro

from .models import Categoria, Producto
from .utils import generar_codigo_barra_interno


class MutacionMaestroError(RuntimeError):
    """Error estable de la cola A05.2a, sin exponer detalles de infraestructura."""


class PermisoMutacionMaestroDenegado(MutacionMaestroError):
    pass


class SucursalMutacionMaestroNoResuelta(MutacionMaestroError):
    pass


class IdempotenciaMutacionMaestroInvalida(MutacionMaestroError):
    pass


class IdempotenciaMutacionMaestroEnConflicto(MutacionMaestroError):
    """El UUID existe, pero no pertenece a la misma intencion autorizada."""


class CodigoBarrasDuplicado(MutacionMaestroError):
    pass


class NombreCategoriaDuplicado(MutacionMaestroError):
    pass


@dataclass(frozen=True)
class ResultadoMutacionMaestro:
    entidad: Producto | Categoria
    mutacion: MutacionMaestro
    repetida: bool


def _uuid_mutacion(valor) -> uuid.UUID:
    if valor in (None, ''):
        return uuid.uuid4()
    try:
        return uuid.UUID(str(valor))
    except (TypeError, ValueError, AttributeError) as exc:
        raise IdempotenciaMutacionMaestroInvalida(
            'El identificador de mutacion debe ser un UUID valido.'
        ) from exc


def _revision(entidad) -> str:
    """Revisión CAS que recibió del cloud, no el ``auto_now`` local.

    Una copia local cambia ``fecha_modificacion`` tanto cuando el operador la
    edita como cuando aplica un pull. Usarla como precondición remota haría que
    toda propuesta posterior al primer pull pareciera escrita sobre una versión
    que el cloud nunca tuvo.
    """
    return str(getattr(entidad, 'revision_cloud', '') or '')


def _snapshot_producto(producto: Producto) -> dict:
    return {
        'id': producto.pk,
        'sku': producto.sku,
        'codigo_barras': producto.codigo_barras,
        'nombre': producto.nombre,
        'descripcion': producto.descripcion,
        'categoria_id': producto.categoria_id,
        'precio_venta': str(producto.precio_venta),
        'stock_minimo': producto.stock_minimo,
        'activo': producto.activo,
        'atributos': producto.atributos or {},
        'estado': producto.estado,
        'marca': producto.marca,
        'origen_cloud_id': producto.origen_cloud_id,
        'revision': _revision(producto),
    }


def _snapshot_categoria(categoria: Categoria) -> dict:
    return {
        'id': categoria.pk,
        'nombre': categoria.nombre,
        'descripcion': categoria.descripcion,
        'activa': categoria.activa,
        'tipo_negocio': categoria.tipo_negocio,
        'atributos_configurados': categoria.atributos_configurados or {},
        'origen_cloud_id': categoria.origen_cloud_id,
        'revision': _revision(categoria),
    }


def _delta(antes: dict, despues: dict) -> dict:
    """Representacion CAS serializable; la revision vive en columnas propias."""
    return {
        campo: {'before': antes.get(campo), 'after': despues.get(campo)}
        for campo in despues
        if campo != 'revision' and antes.get(campo) != despues.get(campo)
    }


def _tenant_key(actor, sucursal) -> str:
    negocio = getattr(sucursal, 'negocio', None) or getattr(actor, 'negocio', None)
    return str(getattr(negocio, 'slug', '') or '')


def _validar_contexto(*, actor, sucursal, permiso: str, using: str) -> None:
    if sucursal is None:
        raise SucursalMutacionMaestroNoResuelta(
            'No se puede mutar un maestro local sin sucursal resuelta.'
        )
    if getattr(sucursal._state, 'db', None) != using:
        raise MutacionMaestroError('La sucursal no pertenece a la base de la mutacion.')
    if actor is None or getattr(actor._state, 'db', None) != using:
        raise MutacionMaestroError('El actor no pertenece a la base de la mutacion.')
    if not actor.tiene_permiso(permiso, sucursal=sucursal):
        raise PermisoMutacionMaestroDenegado('Permiso denegado.')


def _entidad_repetida(mutacion: MutacionMaestro, *, using: str):
    modelo = (
        Producto
        if mutacion.entidad == MutacionMaestro.Entidad.PRODUCTO
        else Categoria
    )
    return modelo.objects.using(using).get(pk=mutacion.entidad_id)


def _resultado_si_repetida(
    mutacion_id: uuid.UUID,
    *,
    actor,
    sucursal,
    entidad_tipo: str,
    entidad_id: int | None,
    operacion: str | None,
    using: str,
):
    """Recupera solo el replay de la misma intencion POS.

    ``mutacion_id`` es globalmente unico para que la constraint tambien cubra
    carreras reales. No es, sin embargo, una autorizacion: una clave conocida
    de otra sucursal/actor, entidad u operacion debe ser una colision visible,
    nunca una respuesta de replay ajena.
    """
    existente = (
        MutacionMaestro.objects.using(using)
        .filter(mutacion_id=mutacion_id)
        .first()
    )
    if existente is None:
        return None

    mismo_contexto = (
        existente.sucursal_id == sucursal.pk
        and existente.actor_id == actor.pk
        and existente.entidad == entidad_tipo
    )
    misma_entidad = entidad_id is None or existente.entidad_id == entidad_id
    misma_operacion = operacion is None or existente.operacion == operacion
    if not (mismo_contexto and misma_entidad and misma_operacion):
        raise IdempotenciaMutacionMaestroEnConflicto(
            'El identificador de mutacion ya pertenece a otra intencion autorizada.'
        )

    return ResultadoMutacionMaestro(
        entidad=_entidad_repetida(existente, using=using),
        mutacion=existente,
        repetida=True,
    )


def _ejecutar_mutacion(
    *,
    actor,
    sucursal,
    permiso: str,
    entidad_tipo: str,
    entidad_id: int | None,
    operacion: str,
    accion_auditoria: str,
    mutacion_id,
    mutar: Callable[[], tuple[Producto | Categoria, dict, dict]],
    using: str = 'default',
) -> ResultadoMutacionMaestro:
    """Ejecuta dominio, auditoria CT-01 y outbox en una sola transaccion."""
    identificador = _uuid_mutacion(mutacion_id)
    _validar_contexto(
        actor=actor, sucursal=sucursal, permiso=permiso, using=using,
    )
    operacion_replay = operacion

    try:
        with transaction.atomic(using=using):
            repetida = _resultado_si_repetida(
                identificador,
                actor=actor,
                sucursal=sucursal,
                entidad_tipo=entidad_tipo,
                entidad_id=entidad_id,
                operacion=operacion_replay,
                using=using,
            )
            if repetida is not None:
                return repetida

            entidad, antes, despues = mutar()
            operacion_final = operacion
            delta = _delta(antes, despues)
            evento = registrar_mutacion(
                accion=accion_auditoria,
                actor=actor,
                entidad=entidad,
                antes=antes,
                despues=despues,
                resultado=Auditoria.Resultado.SUCCEEDED,
                canal=Auditoria.Canal.POS_LOCAL,
                tenant=_tenant_key(actor, sucursal),
                sucursal=sucursal,
                correlacion_id=identificador,
                idempotencia_key=str(identificador),
                metadata={
                    'master_mutation_id': str(identificador),
                    'entity': entidad_tipo,
                    'operation': operacion_final,
                },
                using=using,
            )
            cola = MutacionMaestro.objects.using(using).create(
                mutacion_id=identificador,
                entidad=entidad_tipo,
                entidad_id=entidad.pk,
                operacion=operacion_final,
                revision_base=antes.get('revision', ''),
                revision_resultante=despues.get('revision', ''),
                delta=delta,
                actor=actor,
                actor_username=str(getattr(actor, 'username', '') or ''),
                sucursal=sucursal,
                sucursal_codigo=str(sucursal.codigo),
                tenant_key=_tenant_key(actor, sucursal),
                auditoria_event_id=evento.event_id,
            )
            return ResultadoMutacionMaestro(entidad=entidad, mutacion=cola, repetida=False)
    except IntegrityError:
        # Dos reintentos simultaneos pueden llegar antes de que la segunda
        # transaccion observe el UUID. La constraint convierte el segundo en
        # replay, sin duplicar maestro ni auditoria.
        repetida = _resultado_si_repetida(
            identificador,
            actor=actor,
            sucursal=sucursal,
            entidad_tipo=entidad_tipo,
            entidad_id=entidad_id,
            operacion=operacion_replay,
            using=using,
        )
        if repetida is not None:
            return repetida
        raise


def _generar_sku(*, using: str) -> str:
    ultimo = Producto.objects.using(using).order_by('-id').first()
    siguiente_num = (ultimo.id + 1) if ultimo else 1
    return f'PROD-{siguiente_num:04d}'


def crear_producto_local(*, actor, sucursal, datos: dict, mutacion_id=None, using='default'):
    def mutar():
        producto = Producto(
            sku=_generar_sku(using=using),
            codigo_barras=generar_codigo_barra_interno(),
            nombre=datos['nombre'],
            descripcion=datos.get('descripcion', ''),
            categoria_id=datos['categoria_id'],
            precio_venta=datos['precio_venta'],
            stock_minimo=datos.get('stock_minimo', 5),
            activo=True,
            atributos=datos.get('atributos', {}),
            estado=datos.get('estado', 'nuevo'),
            marca=datos.get('marca', ''),
        )
        producto.save(using=using)
        despues = _snapshot_producto(producto)
        return producto, {}, despues

    return _ejecutar_mutacion(
        actor=actor, sucursal=sucursal, permiso='productos.crear',
        entidad_tipo=MutacionMaestro.Entidad.PRODUCTO,
        entidad_id=None,
        operacion=MutacionMaestro.Operacion.CREAR,
        accion_auditoria='productos.producto.creado', mutacion_id=mutacion_id,
        mutar=mutar, using=using,
    )


def editar_producto_local(*, actor, sucursal, producto_id: int, datos: dict, mutacion_id=None, using='default'):
    def mutar():
        producto = Producto.objects.using(using).select_for_update().get(pk=producto_id)
        codigo_barras = datos['codigo_barras']
        if (
            Producto.objects.using(using).filter(codigo_barras=codigo_barras)
            .exclude(pk=producto.pk).exists()
        ):
            raise CodigoBarrasDuplicado('Ya existe otro producto con ese codigo de barras.')
        antes = _snapshot_producto(producto)
        producto.codigo_barras = codigo_barras
        producto.nombre = datos['nombre']
        producto.descripcion = datos.get('descripcion', '')
        producto.categoria_id = datos['categoria_id']
        producto.precio_venta = datos['precio_venta']
        producto.stock_minimo = datos.get('stock_minimo', 5)
        producto.activo = datos.get('activo', True)
        producto.atributos = datos.get('atributos', {})
        producto.estado = datos.get('estado', 'nuevo')
        producto.marca = datos.get('marca', '')
        producto.save(using=using)
        return producto, antes, _snapshot_producto(producto)

    return _ejecutar_mutacion(
        actor=actor, sucursal=sucursal, permiso='productos.editar',
        entidad_tipo=MutacionMaestro.Entidad.PRODUCTO,
        entidad_id=producto_id,
        operacion=MutacionMaestro.Operacion.ACTUALIZAR,
        accion_auditoria='productos.producto.actualizado', mutacion_id=mutacion_id,
        mutar=mutar, using=using,
    )


def cambiar_estado_producto_local(
    *, actor, sucursal, producto_id: int, activo: bool, mutacion_id=None, using='default',
):
    def mutar():
        producto = Producto.objects.using(using).select_for_update().get(pk=producto_id)
        antes = _snapshot_producto(producto)
        producto.activo = activo
        producto.save(using=using)
        return producto, antes, _snapshot_producto(producto)

    return _ejecutar_mutacion(
        actor=actor, sucursal=sucursal, permiso='productos.eliminar',
        entidad_tipo=MutacionMaestro.Entidad.PRODUCTO,
        entidad_id=producto_id,
        operacion=(
            MutacionMaestro.Operacion.ACTIVAR
            if activo else MutacionMaestro.Operacion.DESACTIVAR
        ),
        accion_auditoria='productos.producto.estado_actualizado',
        mutacion_id=mutacion_id, mutar=mutar, using=using,
    )


def crear_categoria_local(*, actor, sucursal, datos: dict, mutacion_id=None, using='default'):
    def mutar():
        nombre = datos['nombre']
        if Categoria.objects.using(using).filter(nombre=nombre).exists():
            raise NombreCategoriaDuplicado('Ya existe una categoria con ese nombre.')
        categoria = Categoria.objects.using(using).create(
            nombre=nombre,
            descripcion=datos.get('descripcion', ''),
            activa=True,
        )
        return categoria, {}, _snapshot_categoria(categoria)

    return _ejecutar_mutacion(
        actor=actor, sucursal=sucursal, permiso='categorias.crear',
        entidad_tipo=MutacionMaestro.Entidad.CATEGORIA,
        entidad_id=None,
        operacion=MutacionMaestro.Operacion.CREAR,
        accion_auditoria='productos.categoria.creada', mutacion_id=mutacion_id,
        mutar=mutar, using=using,
    )


def editar_categoria_local(*, actor, sucursal, categoria_id: int, datos: dict, mutacion_id=None, using='default'):
    def mutar():
        categoria = Categoria.objects.using(using).select_for_update().get(pk=categoria_id)
        nombre = datos['nombre']
        if Categoria.objects.using(using).filter(nombre=nombre).exclude(pk=categoria.pk).exists():
            raise NombreCategoriaDuplicado('Ya existe otra categoria con ese nombre.')
        antes = _snapshot_categoria(categoria)
        categoria.nombre = nombre
        categoria.descripcion = datos.get('descripcion', '')
        categoria.activa = datos.get('activa', True)
        categoria.save(using=using)
        return categoria, antes, _snapshot_categoria(categoria)

    return _ejecutar_mutacion(
        actor=actor, sucursal=sucursal, permiso='categorias.editar',
        entidad_tipo=MutacionMaestro.Entidad.CATEGORIA,
        entidad_id=categoria_id,
        operacion=MutacionMaestro.Operacion.ACTUALIZAR,
        accion_auditoria='productos.categoria.actualizada', mutacion_id=mutacion_id,
        mutar=mutar, using=using,
    )


def cambiar_estado_categoria_local(
    *, actor, sucursal, categoria_id: int, activa: bool, mutacion_id=None, using='default',
):
    def mutar():
        categoria = Categoria.objects.using(using).select_for_update().get(pk=categoria_id)
        antes = _snapshot_categoria(categoria)
        categoria.activa = activa
        categoria.save(using=using)
        return categoria, antes, _snapshot_categoria(categoria)

    return _ejecutar_mutacion(
        actor=actor, sucursal=sucursal, permiso='categorias.eliminar',
        entidad_tipo=MutacionMaestro.Entidad.CATEGORIA,
        entidad_id=categoria_id,
        operacion=(
            MutacionMaestro.Operacion.ACTIVAR
            if activa else MutacionMaestro.Operacion.DESACTIVAR
        ),
        accion_auditoria='productos.categoria.estado_actualizado',
        mutacion_id=mutacion_id, mutar=mutar, using=using,
    )
