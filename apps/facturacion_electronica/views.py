"""
apps/facturacion_electronica/views.py

Vistas/endpoints HTTP de la app facturacion_electronica.

Por ahora solo el endpoint AJAX `api_estado_ecf_venta` que el
frontend del POS poletea para mostrar el estado del e-CF de una
venta recién cerrada.

Futuro (Bloque adicional): endpoints para reimprimir tickets,
listar ECFs en error desde admin, dashboard de métricas, etc.
"""
from __future__ import annotations

import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from apps.ventas.models import Venta

from .interfaces import EstadosECF
from .models import ECF

logger = logging.getLogger('ecf.views')


# =============================================================================
# Constantes
# =============================================================================

# Estados terminales: el frontend deja de polletear cuando el ECF llega
# a alguno de estos. Mismos que EstadosECF.TERMINALES pero los listamos
# acá para no acoplar el frontend al import del enum.
ESTADOS_TERMINALES_FRONTEND = (
    EstadosECF.APROBADO,
    EstadosECF.APROBADO_CONDICIONAL,
    EstadosECF.RECHAZADO,
)


# =============================================================================
# Endpoint
# =============================================================================

@login_required
@require_http_methods(['GET'])
def api_estado_ecf_venta(request, venta_id: int):
    """
    Retorna el estado del e-CF asociado a una venta.

    URL: GET /facturacion-electronica/api/ecf/estado/<venta_id>/

    Response 200:
        {
            "tiene_ecf": true,
            "ecf_id": 42,
            "estado": "APROBADO",
            "estado_display": "Aprobado",
            "estado_terminal": true,
            "tipo": "32",
            "tipo_display": "Factura de Consumo Electrónica (32)",
            "encf": "E320000000123" | null,
            "codigo_seguridad": "ABC123" | null,
            "qr_url": "https://ecf.dgii.gov.do/..." | null,
            "intentos": 1,
            "fecha_emision": "2026-04-30T14:23:00-04:00" | null,
            "actualizado_en": "2026-04-30T14:23:15-04:00",
            "mensaje_ultimo_evento": "..."
        }

    Response 200 cuando no hay ECF (modulo desactivado o no encolado todavía):
        {
            "tiene_ecf": false,
            "modulo_activo": false,
            "mensaje": "El módulo de e-CF no está activo para esta sucursal."
        }

    Response 404: venta no existe.

    Notas:
    - Si hay múltiples ECFs para la venta (ej: ECF original tipo 32 +
      NC tipo 34), retorna el más reciente que NO sea NC.
      Para inspeccionar la NC, el frontend debe usar otro endpoint
      o admin (no es parte del flujo del POS de venta normal).
    - Si el ECF tiene `qr_url`, lo extraemos del raw_response JSON
      (MSeller lo retorna como `qr_url` en su response inmediata).
    """
    venta = get_object_or_404(Venta, id=venta_id)

    # Primero: ¿hay módulo activo? Si no, ahorramos query.
    from apps.configuracion.utils import get_config
    config = get_config()
    if not config.modulo_ecf:
        return JsonResponse({
            'tiene_ecf': False,
            'modulo_activo': False,
            'mensaje': 'El módulo de e-CF no está activo para esta sucursal.',
        })

    # Buscar ECF original de la venta (tipo 31 o 32). Excluimos NC
    # tipo 34 a propósito: en el flujo de "venta exitosa" no
    # interesa mostrar la NC.
    ecf = (
        ECF.objects
        .filter(venta=venta, tipo__in=('31', '32'))
        .order_by('-creado_en')
        .first()
    )

    if ecf is None:
        # Venta sin ECF asociado todavía. Puede ser:
        # - Hook async no se disparó aún (ventana muy chica post-commit)
        # - Hook falló silenciosamente (logueado en otra parte)
        # - Venta antigua, anterior a la activación del módulo
        return JsonResponse({
            'tiene_ecf': False,
            'modulo_activo': True,
            'mensaje': 'La venta aún no tiene e-CF asociado.',
        })

    # Extraer qr_url del raw_response si existe
    qr_url = _extraer_qr_url(ecf)

    # Mensaje del último evento, útil para que el front lo muestre
    ultimo_evento = ecf.eventos.order_by('-fecha').first()
    mensaje_ultimo = ultimo_evento.mensaje if ultimo_evento else ''

    return JsonResponse({
        'tiene_ecf': True,
        'ecf_id': ecf.id,
        'estado': ecf.estado,
        'estado_display': ecf.get_estado_display(),
        'estado_terminal': ecf.estado in ESTADOS_TERMINALES_FRONTEND,
        'tipo': ecf.tipo,
        'tipo_display': ecf.get_tipo_display(),
        'encf': ecf.encf or None,
        'codigo_seguridad': ecf.codigo_seguridad or None,
        'qr_url': qr_url,
        'intentos': ecf.intentos,
        'fecha_emision': (
            ecf.fecha_emision.isoformat() if ecf.fecha_emision else None
        ),
        'actualizado_en': ecf.actualizado_en.isoformat(),
        'mensaje_ultimo_evento': mensaje_ultimo,
    })


# =============================================================================
# Helpers
# =============================================================================

def _extraer_qr_url(ecf: ECF) -> str | None:
    """
    Extrae el qr_url de la respuesta cruda de MSeller persistida en
    `ecf.xml_respuesta`. MSeller lo retorna como string en el campo
    `qr_url` de su JSON de respuesta.

    Retorna None si no se puede parsear o el campo no existe.
    """
    if not ecf.xml_respuesta:
        return None
    try:
        import json
        raw = json.loads(ecf.xml_respuesta)
    except (ValueError, TypeError):
        return None

    # MSeller retorna qr_url como string directo en su response
    return raw.get('qr_url') or raw.get('qrUrl') or None