"""
apps/ventas/services/anulaciones_service.py

Servicio de anulación de venta. Encapsula la lógica de negocio de
api_anular_venta del view original.

Responsabilidades:
- Validar permisos del usuario (ADMIN/SYSADMIN)
- Validar motivo de anulación
- Verificar que la venta puede anularse (estado, plazo)
- Devolver stock vía FIFO
- Marcar la venta como ANULADA + persistir auditoría
- Disparar hooks post-commit:
    * sync engine (existente)
    * encolado de NC tipo 34 si la venta tenía ECF aprobado (NUEVO)

Patrón de uso desde el view:
    try:
        venta = anular_venta_service(
            usuario=request.user,
            venta_id=data['venta_id'],
            motivo=data.get('motivo', ''),
            ip_address=get_client_ip(request),
        )
        return JsonResponse({'success': True, 'venta': {...}})
    except ErrorVentaBase as exc:
        return JsonResponse(
            {'success': False, 'error': str(exc)},
            status=exc.status_code,
        )
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.auditoria.models import Auditoria
from apps.configuracion.utils import get_config, modulo_activo
from apps.sync import events as sync_events

from ..models import Venta
from .exceptions import (
    AnulacionNoPermitidaError,
    FIFORollbackError,
    MotivoAnulacionInvalidoError,
    PermisoDenegadoError,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

logger = logging.getLogger('ventas.service')


# =============================================================================
# Punto de entrada principal
# =============================================================================

def anular_venta_service(
    *,
    usuario: 'AbstractUser',
    venta_id: int,
    motivo: str,
    ip_address: str | None = None,
) -> Venta:
    """
    Anula una venta existente. Devuelve stock FIFO, marca la venta
    como ANULADA, registra auditoría y encola la NC tipo 34 si
    corresponde.

    Args:
        usuario: usuario que solicita la anulación. Debe ser ADMIN/SYSADMIN.
        venta_id: ID de la Venta a anular.
        motivo: texto del motivo (mínimo 10 caracteres no-blancos).
        ip_address: IP del cliente, para auditoría.

    Returns:
        Instancia Venta actualizada (estado='ANULADA').

    Raises:
        PermisoDenegadoError: rol del usuario no autorizado.
        MotivoAnulacionInvalidoError: motivo vacío o muy corto.
        AnulacionNoPermitidaError: venta ya anulada o fuera de plazo.
        FIFORollbackError: la devolución FIFO falló (rollback automático).
        Cualquier otra excepción propaga.
    """
    # Validación de permisos. Decoradores de Django no alcanzan acá:
    # services no tienen acceso al request, validamos por atributo del User.
    if not _puede_anular(usuario):
        raise PermisoDenegadoError(
            'No tienes permisos para anular ventas. '
            'Requiere rol ADMIN o SYSADMIN.'
        )

    motivo = (motivo or '').strip()
    if not motivo:
        raise MotivoAnulacionInvalidoError(
            'El motivo de anulación es obligatorio.'
        )
    if len(motivo) < 10:
        raise MotivoAnulacionInvalidoError(
            'El motivo debe tener al menos 10 caracteres.'
        )

    with transaction.atomic():
        venta = get_object_or_404(Venta, id=venta_id)

        if not venta.puede_anularse():
            if venta.estado == 'ANULADA':
                raise AnulacionNoPermitidaError('Esta venta ya fue anulada.')
            config = get_config()
            raise AnulacionNoPermitidaError(
                f'Esta venta superó el límite de '
                f'{config.dias_anulacion} días para anulación.'
            )

        _devolver_stock_fifo(venta=venta, usuario=usuario)

        venta.estado = 'ANULADA'
        venta.motivo_anulacion = motivo
        venta.anulada_por = usuario
        venta.fecha_anulacion = timezone.now()
        venta.save()

        if getattr(venta, 'condicion_pago', 'CONTADO') == 'CREDITO':
            from apps.cuentas_por_cobrar.services import anular_cuenta_por_venta

            anular_cuenta_por_venta(
                venta=venta,
                usuario=usuario,
                ip_address=ip_address,
            )

        # Auditoría dentro del atomic
        Auditoria.registrar_anulacion_venta(
            venta=venta,
            usuario=usuario,
            motivo=motivo,
            ip_address=ip_address,
        )

        # ------------------ Hooks post-commit
        # Sync engine (existente)
        transaction.on_commit(
            lambda v=venta: sync_events.evento_venta_anulada(v)
        )

        # Encolado de NC tipo 34 (Semana 3): si la venta tiene ECF
        # aprobado, hay que reflejar la anulación con DGII vía Nota
        # de Crédito Electrónica. Si no hay ECF aprobado, no hay
        # nada que anular fiscalmente — la venta puede tener un ECF
        # en PENDIENTE/RECHAZADO/ERROR; en esos casos no se emite NC
        # (la propia cola se encarga de no enviar el ECF original).
        if modulo_activo('ecf'):
            transaction.on_commit(
                lambda v=venta, m=motivo: _hook_encolar_nota_credito(v, m)
            )

    return venta


# =============================================================================
# Helpers internos
# =============================================================================

def _puede_anular(usuario: 'AbstractUser') -> bool:
    """
    Centraliza la regla de quién puede anular. Hoy: ADMIN o SYSADMIN.
    Si en el futuro hay un sistema de permisos más granular, este
    es el único punto a cambiar.
    """
    rol = getattr(usuario, 'rol', None)
    return rol in ('ADMIN', 'SYSADMIN')


def _devolver_stock_fifo(*, venta: Venta, usuario: 'AbstractUser') -> None:
    """
    Llama a anular_venta_devolver_stock y traduce su retorno a
    excepción tipada si falló. La función puede dejar mutaciones
    parciales antes de retornar success=False; transaction.atomic()
    de la capa superior se encarga del rollback al levantar la
    excepción.
    """
    from apps.inventario.fifo_logic import anular_venta_devolver_stock

    resultado = anular_venta_devolver_stock(
        venta_id=venta.id,
        usuario=usuario,
    )
    if not resultado.get('success'):
        raise FIFORollbackError(
            f'Error al devolver stock: '
            f'{resultado.get("error", "Error desconocido")}'
        )


def _hook_encolar_nota_credito(venta: Venta, motivo: str) -> None:
    """
    Hook post-commit que encola la NC tipo 34. Solo se emite si la
    venta tiene un ECF en estado APROBADO o APROBADO_CONDICIONAL.

    Razones para no emitir NC en otros estados:
    - PENDIENTE / ENVIADO / EN_PROCESO: el ECF original todavía no
      llegó a DGII como aprobado. La cola de emisión detecta que la
      venta está ANULADA y no envía el ECF original. No hace falta
      una NC porque DGII nunca registró el ECF.
    - RECHAZADO / ERROR: el ECF nunca fue válido fiscalmente, no hay
      nada que rectificar.

    Si algo falla aquí, NO se hace rollback (post-commit). Se loguea.
    """
    try:
        from apps.facturacion_electronica.services.cola_emision import (
            encolar_nota_credito,
        )
        encolar_nota_credito(venta=venta, motivo=motivo)
    except Exception as exc:
        logger.exception(
            f'Falló encolar NC tipo 34 para venta={venta.numero_venta}: {exc}'
        )
