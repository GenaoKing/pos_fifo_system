"""
apps/inventario/services/ajustes_service.py

Unica autoridad para ajustar el saldo de un lote (merma, dano, conteo,
correccion, devolucion).

Por que existe
--------------
Antes habia DOS autoridades para el mismo hecho y ninguna completa:

- `AjusteInventario.save()` mutaba el lote y creaba un `MovimientoLote` tipo
  AJUSTE, en cualquier llamada a save() — tambien al editar.
- El endpoint HTTP creaba ADEMAS su propio `MovimientoLote` con el tipo real
  (MERMA/DANO) y volvia a escribir el lote con un valor calculado antes.

Resultado: un solo ajuste dejaba dos movimientos en el ledger (con tipos
distintos), y volver a guardar el ajuste — aunque solo cambiara el motivo —
aplicaba la cantidad otra vez sobre el stock.

Garantias de este service
-------------------------
1. Bloquea el lote ANTES de leer su saldo (`select_for_update`), asi que dos
   ajustes simultaneos se serializan y ninguno pisa al otro.
2. Revalida la suficiencia DESPUES del lock, no antes.
3. Escribe exactamente UN `MovimientoLote`, con el tipo que corresponde al
   ajuste.
4. Todo (ajuste + lote + movimiento + auditoria + outbox) en una transaccion.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction

from apps.auditoria.models import Auditoria
from apps.auditoria.services import registrar_mutacion
from apps.sync import events as sync_events

from ..models import AjusteInventario, Lote, MovimientoLote
from .exceptions import (
    AjusteInvalidoError,
    LoteNoEncontradoError,
    PermisoAjusteDenegadoError,
    StockInsuficienteLoteError,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

logger = logging.getLogger('inventario.service')


# Tipos de ajuste que SUMAN al lote. El resto resta.
TIPOS_ENTRADA = ('DEVOLUCION',)

# Tipo de ajuste -> tipo de MovimientoLote que queda en el ledger.
# El modelo escribia siempre 'AJUSTE' y el endpoint intentaba conservar el tipo
# real; con un solo movimiento, el tipo real es el que manda.
TIPO_MOVIMIENTO_POR_AJUSTE = {
    'MERMA': 'MERMA',
    'DANO': 'DANO',
    'CONTEO': 'AJUSTE',
    'CORRECCION': 'AJUSTE',
    'DEVOLUCION': 'AJUSTE',
}

MOTIVO_MINIMO = 10


def registrar_ajuste_service(
    *,
    usuario: 'AbstractUser',
    lote_id: int,
    tipo: str,
    cantidad: int,
    motivo: str,
    ip_address: str | None = None,
    sucursal=None,
) -> AjusteInventario:
    """
    Aplica un ajuste de inventario sobre un lote.

    Args:
        usuario: quien registra el ajuste. Se re-autoriza `inventario.ajustar`
            contra la sucursal del LOTE (INV-RBAC-SCOPE), bajo el lock.
        lote_id: PK del lote a ajustar.
        tipo: uno de `TIPO_MOVIMIENTO_POR_AJUSTE`.
        cantidad: magnitud POSITIVA. El signo lo decide el tipo.
        motivo: texto obligatorio, minimo 10 caracteres.
        ip_address: para auditoria.
        sucursal: sucursal operativa del solicitante. Solo se usa como scope de
            respaldo para lotes legacy sin sucursal propia (ver `_puede_ajustar`).

    Returns:
        El `AjusteInventario` aplicado.

    Raises:
        AjusteInvalidoError, StockInsuficienteLoteError, LoteNoEncontradoError,
        PermisoAjusteDenegadoError.
    """
    tipo = (tipo or '').strip().upper()
    motivo = (motivo or '').strip()

    if tipo not in TIPO_MOVIMIENTO_POR_AJUSTE:
        raise AjusteInvalidoError(
            f'Tipo de ajuste "{tipo}" no valido. '
            f'Validos: {", ".join(sorted(TIPO_MOVIMIENTO_POR_AJUSTE))}.'
        )

    try:
        cantidad = int(cantidad)
    except (TypeError, ValueError):
        raise AjusteInvalidoError('La cantidad debe ser un numero entero.')

    if cantidad <= 0:
        raise AjusteInvalidoError('La cantidad debe ser mayor a cero.')

    if len(motivo) < MOTIVO_MINIMO:
        raise AjusteInvalidoError(
            f'El motivo es obligatorio (minimo {MOTIVO_MINIMO} caracteres).'
        )

    es_entrada = tipo in TIPOS_ENTRADA
    cantidad_ajuste = cantidad if es_entrada else -cantidad

    with transaction.atomic():
        # El lock va ANTES de leer el saldo. Con la lectura previa al atomic,
        # dos requests validaban contra el mismo saldo y ambas pasaban.
        try:
            lote = Lote.objects.select_for_update().get(id=lote_id, activo=True)
        except (Lote.DoesNotExist, ValueError, TypeError):
            raise LoteNoEncontradoError(
                f'No existe un lote activo con id={lote_id}.'
            )

        # INV-RBAC-SCOPE: se re-autoriza `inventario.ajustar` contra la sucursal
        # del LOTE ya bloqueado — no solo el decorador de la vista, que resuelve
        # la sucursal del OPERADOR. Bajo el lock, `lote.sucursal` es la identidad
        # definitiva del lote.
        if not _puede_ajustar(usuario, lote=lote, sucursal_operador=sucursal):
            raise PermisoAjusteDenegadoError(
                'No tienes permiso para ajustar inventario en la sucursal de '
                'este lote.'
            )

        cantidad_anterior = lote.cantidad_actual

        # Revalidacion BAJO el lock: este saldo ya es el definitivo.
        if not es_entrada and cantidad_anterior < cantidad:
            raise StockInsuficienteLoteError(
                f'Stock insuficiente. El lote tiene {cantidad_anterior} '
                f'unidades disponibles.'
            )

        cantidad_nueva = cantidad_anterior + cantidad_ajuste

        ajuste = AjusteInventario.objects.create(
            lote=lote,
            tipo=tipo,
            cantidad=cantidad_ajuste,
            motivo=motivo,
            usuario=usuario,
        )

        lote.cantidad_actual = cantidad_nueva
        lote.save(update_fields=['cantidad_actual'])

        # UN movimiento, con el tipo real del ajuste.
        MovimientoLote.objects.create(
            lote=lote,
            tipo=TIPO_MOVIMIENTO_POR_AJUSTE[tipo],
            cantidad=cantidad_ajuste,
            cantidad_anterior=cantidad_anterior,
            cantidad_nueva=cantidad_nueva,
            referencia_tipo='AjusteInventario',
            referencia_id=ajuste.id,
            usuario=usuario,
            notas=f'{ajuste.get_tipo_display()}: {motivo}',
        )

        # Auditoría CT-01 dentro del atomic (atómica con el ajuste): identidad =
        # la sucursal del LOTE ya bloqueado (INV-RBAC-SCOPE), mismo `using`. Un
        # fallo de auditoría revierte el ajuste (invariante CT-01).
        registrar_mutacion(
            accion='inventario.ajuste.creado',
            actor=usuario,
            entidad=ajuste,
            antes={'cantidad_actual': str(cantidad_anterior)},
            despues={
                'cantidad_actual': str(cantidad_nueva),
                'tipo': tipo,
                'cantidad_ajuste': str(cantidad_ajuste),
                'motivo': motivo,
                'lote': lote.numero_lote,
            },
            resultado=Auditoria.Resultado.SUCCEEDED,
            canal=Auditoria.Canal.POS_LOCAL,
            tenant=None,
            sucursal=lote.sucursal,
            metadata={'ip_address': ip_address} if ip_address else None,
            using=ajuste._state.db or 'default',
        )

        # Outbox transaccional: el ajuste es un hecho de negocio.
        sync_events.evento_ajuste_inventario(ajuste)

        # El snapshot es foto de estado y O(N): se queda post-commit.
        transaction.on_commit(
            lambda s=lote.sucursal: sync_events.evento_inventario_snapshot(sucursal=s)
        )

    logger.info(
        'Ajuste %s aplicado sobre lote %s: %s -> %s (%s)',
        tipo, lote.numero_lote, cantidad_anterior, cantidad_nueva, motivo,
    )
    return ajuste


def _puede_ajustar(usuario: 'AbstractUser', *, lote: Lote, sucursal_operador=None) -> bool:
    """
    Quién puede ajustar un lote (INV-RBAC-SCOPE / CT-02).

    Se resuelve con el motor RBAC: `inventario.ajustar` en la sucursal del lote.
    ADMIN/SYSADMIN siguen pasando porque el motor les concede acceso total, no por
    un chequeo de rol aquí.

    Scope:
      - Con sucursal en el lote -> se autoriza contra ESA sucursal. Es lo que
        impide ajustar cross-branch en una BD compartida.
      - Lote legacy sin sucursal (anterior a la Fase 2) -> cae al scope operativo
        del solicitante (`sucursal_operador`).
    """
    comprobar = getattr(usuario, 'tiene_permiso', None)
    if comprobar is None:
        # Usuario sin el modelo propio (AnonymousUser o doble de test).
        return False
    scope = lote.sucursal or sucursal_operador
    return bool(comprobar('inventario.ajustar', sucursal=scope))
