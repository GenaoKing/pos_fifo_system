"""
apps/api/views/maestros.py
ViewSets para datos maestros (cloud → sucursal).

Estos endpoints exponen Producto, Categoría y Cliente como recursos
de solo lectura. La sucursal los consume para sincronizar sus datos
locales con el cloud.

Sync incremental:
    GET /api/v1/maestros/productos/?desde=2026-04-01T00:00:00
    → Retorna solo productos modificados desde esa fecha
    
    La sucursal guarda el timestamp de su última sync y lo envía
    en cada request para recibir solo los cambios.

Todos los modelos ya tienen `fecha_modificacion` con auto_now=True.
"""

from django.utils.dateparse import parse_datetime
from rest_framework import viewsets, status
from rest_framework.response import Response

from apps.productos.models import Producto, Categoria
from apps.clientes.models import Cliente

from ..serializers.maestros import (
    ProductoSerializer,
    CategoriaSerializer,
    ClienteSerializer,
)
from ..permissions import EsSoloLectura
from ..pagination import LargePagination


class SyncIncrementalMixin:
    """
    Mixin que agrega filtro `?desde=<timestamp>` para sync incremental.
    
    Uso:
        GET /api/v1/maestros/productos/?desde=2026-04-01T00:00:00
        → Filtra por fecha_modificacion > desde
        
        GET /api/v1/maestros/productos/
        → Sin filtro, retorna todos (sync completa / primera carga)
    
    La respuesta incluye headers con metadata de sync:
        X-Sync-Timestamp: timestamp del servidor al momento de la respuesta
        X-Total-Count: total de registros (sin paginar)
    """

    def get_queryset(self):
        queryset = super().get_queryset()
        desde = self.request.query_params.get('desde')

        if desde:
            timestamp = parse_datetime(desde)
            if timestamp is None:
                # Intentar solo fecha (sin hora)
                from django.utils.dateparse import parse_date
                fecha = parse_date(desde)
                if fecha:
                    from django.utils import timezone as tz
                    import datetime
                    timestamp = tz.make_aware(
                        datetime.datetime.combine(fecha, datetime.time.min)
                    )

            if timestamp:
                queryset = queryset.filter(fecha_modificacion__gt=timestamp)

        return queryset

    def list(self, request, *args, **kwargs):
        """Override list para incluir headers de sync."""
        response = super().list(request, *args, **kwargs)

        from django.utils import timezone
        response['X-Sync-Timestamp'] = timezone.now().isoformat()
        response['X-Total-Count'] = self.get_queryset().count()

        return response


class ProductoViewSet(SyncIncrementalMixin, viewsets.ReadOnlyModelViewSet):
    """
    ViewSet de Productos (solo lectura).
    
    Endpoints:
        GET /api/v1/maestros/productos/           → Lista paginada
        GET /api/v1/maestros/productos/<id>/       → Detalle
        GET /api/v1/maestros/productos/?desde=...  → Sync incremental
    
    Filtros adicionales:
        ?activo=true/false    → Filtrar por estado
        ?categoria=<id>       → Filtrar por categoría
    
    Ejemplo de uso desde sucursal:
        GET /api/v1/maestros/productos/?desde=2026-04-01T00:00:00
        Authorization: Token abc123...
        
        Response:
        {
            "count": 3,
            "next": null,
            "previous": null,
            "results": [
                {
                    "id": 45,
                    "sku": "PROD-0045",
                    "nombre": "Vaso desechable 16oz",
                    "precio_venta": "15.00",
                    "fecha_modificacion": "2026-04-15T10:30:00-04:00",
                    ...
                }
            ]
        }
        Headers:
            X-Sync-Timestamp: 2026-04-17T14:30:00-04:00
            X-Total-Count: 3
    """
    serializer_class = ProductoSerializer
    permission_classes = [EsSoloLectura]
    pagination_class = LargePagination
    throttle_scope = 'maestros'

    def get_queryset(self):
        queryset = Producto.objects.select_related('categoria').all()

        # Guardar el queryset base antes de aplicar filtro desde
        self._base_queryset = queryset

        # Filtros opcionales
        activo = self.request.query_params.get('activo')
        if activo is not None:
            queryset = queryset.filter(activo=activo.lower() == 'true')

        categoria = self.request.query_params.get('categoria')
        if categoria:
            queryset = queryset.filter(categoria_id=categoria)

        # Guardar para que el mixin aplique el filtro desde
        self.queryset = queryset
        return super().get_queryset()


class CategoriaViewSet(SyncIncrementalMixin, viewsets.ReadOnlyModelViewSet):
    """
    ViewSet de Categorías (solo lectura).
    
    Endpoints:
        GET /api/v1/maestros/categorias/           → Lista paginada
        GET /api/v1/maestros/categorias/<id>/       → Detalle
        GET /api/v1/maestros/categorias/?desde=...  → Sync incremental
    
    Filtros adicionales:
        ?activa=true/false    → Filtrar por estado
    """
    serializer_class = CategoriaSerializer
    permission_classes = [EsSoloLectura]
    pagination_class = LargePagination
    throttle_scope = 'maestros'

    def get_queryset(self):
        queryset = Categoria.objects.all()

        activa = self.request.query_params.get('activa')
        if activa is not None:
            queryset = queryset.filter(activa=activa.lower() == 'true')

        self.queryset = queryset
        return super().get_queryset()


class ClienteViewSet(SyncIncrementalMixin, viewsets.ReadOnlyModelViewSet):
    """
    ViewSet de Clientes (solo lectura).
    
    Endpoints:
        GET /api/v1/maestros/clientes/           → Lista paginada
        GET /api/v1/maestros/clientes/<id>/       → Detalle
        GET /api/v1/maestros/clientes/?desde=...  → Sync incremental
    
    Filtros adicionales:
        ?tipo=PERSONAL/CORPORATIVO/CONTADO  → Filtrar por tipo
        ?activo=true/false                  → Filtrar por estado
    """
    serializer_class = ClienteSerializer
    permission_classes = [EsSoloLectura]
    pagination_class = LargePagination
    throttle_scope = 'maestros'

    def get_queryset(self):
        queryset = Cliente.objects.all()

        tipo = self.request.query_params.get('tipo')
        if tipo:
            queryset = queryset.filter(tipo=tipo.upper())

        activo = self.request.query_params.get('activo')
        if activo is not None:
            queryset = queryset.filter(activo=activo.lower() == 'true')

        self.queryset = queryset
        return super().get_queryset()