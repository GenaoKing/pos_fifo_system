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
from apps.sync import events as sync_events

from ..models import AjusteInventario, Lote, MovimientoLote
from .exceptions import (
    AjusteInvalidoError,
    LoteNoEncontradoError,
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
) -> AjusteInventario:
    """
    Aplica un ajuste de inventario sobre un lote.

    Args:
        usuario: quien registra el ajuste.
        lote_id: PK del lote a ajustar.
        tipo: uno de `TIPO_MOVIMIENTO_POR_AJUSTE`.
        cantidad: magnitud POSITIVA. El signo lo decide el tipo.
        motivo: texto obligatorio, minimo 10 caracteres.
        ip_address: para auditoria.

    Returns:
        El `AjusteInventario` aplicado.

    Raises:
        AjusteInvalidoError, StockInsuficienteLoteError, LoteNoEncontradoError.
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

        Auditoria.registrar_ajuste_inventario(
            ajuste=ajuste,
            usuario=usuario,
            ip_address=ip_address,
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
