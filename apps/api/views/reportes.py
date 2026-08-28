"""
Endpoints de reportes consolidados para el portal cloud.

La app local `apps.reportes` sigue siendo el modulo POS/Django para dashboard,
cierres y PDFs locales. Esta capa API usa servicios query-based sobre la BD
cloud y no depende de snapshots locales como `ReporteManager`.
"""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.api.services.reporting import (
    ReportingError,
    _estado_sync,
    build_cierre_consolidado,
    build_comparativo,
    build_inventario_consolidado,
    build_top_productos,
    build_ventas_hoy,
    build_ventas_por_cajero,
)
from apps.negocios.utils import resolver_negocio

from rest_framework import status

from ..permissions import requiere_permiso


def _service_response(builder, request, *args, **kwargs):
    """
    Resuelve el tenant UNA vez y lo pasa tipado al builder.

    Antes cada vista pasaba el negocio ya resuelto (o `None`), y el builder
    trataba `None` como "todas las sucursales". Como el resolver devolvia `None`
    tambien ante un usuario huerfano o un `?negocio=` inexistente, un fallo de
    resolucion se convertia en el reporte consolidado de todos los negocios
    (NEG-001, NEG-002). Ahora un fallo es un 403 explicito.
    """
    resolucion = resolver_negocio(request)
    if not resolucion.permitido:
        return Response(
            {'error': resolucion.motivo or 'Sin acceso.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        data = builder(request.query_params, *args, resolucion=resolucion, **kwargs)
    except ReportingError as exc:
        return Response(exc.detail, status=exc.status_code)
    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, requiere_permiso('reportes.ver')])
def ventas_hoy(request, codigo_sucursal=None):
    return _service_response(
        build_ventas_hoy, request, codigo_sucursal=codigo_sucursal,
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated, requiere_permiso('reportes.consolidado.ver')])
def comparativo_sucursales(request):
    return _service_response(build_comparativo, request)


@api_view(['GET'])
@permission_classes([IsAuthenticated, requiere_permiso('reportes.ver')])
def ventas_por_cajero(request):
    return _service_response(build_ventas_por_cajero, request)


@api_view(['GET'])
@permission_classes([IsAuthenticated, requiere_permiso('reportes.ver')])
def top_productos(request):
    return _service_response(build_top_productos, request)


@api_view(['GET'])
@permission_classes([IsAuthenticated, requiere_permiso('reportes.consolidado.ver')])
def cierre_consolidado(request):
    return _service_response(build_cierre_consolidado, request)


@api_view(['GET'])
@permission_classes([IsAuthenticated, requiere_permiso('reportes.consolidado.ver')])
def inventario_consolidado(request):
    # Sin scope por negocio: Producto no tiene FK negocio; su aislamiento es
    # DB-per-tenant. Ver build_inventario_consolidado (snapshot local, no consolidado).
    return _service_response(build_inventario_consolidado, request)
