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

from ..permissions import MaestroPermisoMixin

from apps.productos.models import Producto, Categoria
from apps.clientes.models import Cliente

from ..serializers.maestros import (
    ProductoSerializer,
    ProductoWriteSerializer,
    CategoriaSerializer,
    CategoriaWriteSerializer,
    ClienteSerializer,
    ClienteWriteSerializer,
)
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


class ProductoViewSet(MaestroPermisoMixin, SyncIncrementalMixin, viewsets.ModelViewSet):
    """
    ViewSet de Productos.

    Lecturas (list, retrieve): autenticación de sucursal (SucursalToken)
    o admin (JWT). Las sucursales lo usan para sync incremental con
    ?desde=, los admins del portal para listar y filtrar.

    Escrituras (create, update, partial_update, destroy): solo admins
    del portal autenticados con JWT. Las sucursales NO pueden escribir
    desde su token de sync.

    Endpoints:
        GET    /api/v1/maestros/productos/           lista
        GET    /api/v1/maestros/productos/?desde=...  sync incremental
        GET    /api/v1/maestros/productos/<id>/       detalle
        POST   /api/v1/maestros/productos/           crear (admin)
        PATCH  /api/v1/maestros/productos/<id>/       editar (admin)
        DELETE /api/v1/maestros/productos/<id>/       borrar (admin)

    Para escrituras desde el portal, ver ProductoWriteSerializer.
    """
    pagination_class = LargePagination
    throttle_scope = 'maestros'
    permiso_base = 'productos'

    def get_serializer_class(self):
        if self.action in ('list', 'retrieve'):
            return ProductoSerializer
        return ProductoWriteSerializer

    def get_queryset(self):
        queryset = Producto.objects.select_related('categoria').all()

        # Guardar queryset base por si necesitamos calcular totales en headers.
        self._base_queryset = queryset

        # Filtros opcionales
        activo = self.request.query_params.get('activo')
        if activo is not None:
            queryset = queryset.filter(activo=activo.lower() == 'true')

        categoria = self.request.query_params.get('categoria')
        if categoria:
            queryset = queryset.filter(categoria_id=categoria)

        # Búsqueda libre por nombre, sku, código de barras (uso del portal).
        search = self.request.query_params.get('search')
        if search:
            from django.db.models import Q
            queryset = queryset.filter(
                Q(nombre__icontains=search)
                | Q(sku__icontains=search)
                | Q(codigo_barras__icontains=search)
            )

        # Patrón heredado: el mixin SyncIncrementalMixin lee self.queryset
        # para aplicar el filtro ?desde=.
        self.queryset = queryset
        return super().get_queryset()

    def create(self, request, *args, **kwargs):
        """POST → 201. Devuelve el producto con el read serializer completo
        (incluye imagen_url, fechas, etc.) para consistencia con el GET."""
        write_serializer = self.get_serializer(data=request.data)
        write_serializer.is_valid(raise_exception=True)
        producto = write_serializer.save()

        read_serializer = ProductoSerializer(
            producto, context=self.get_serializer_context()
        )
        return Response(read_serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        """PUT / PATCH → 200. Devuelve la representación de lectura completa."""
        partial = kwargs.pop('partial', False)
        instance = self.get_object()

        write_serializer = self.get_serializer(
            instance, data=request.data, partial=partial
        )
        write_serializer.is_valid(raise_exception=True)
        producto = write_serializer.save()

        read_serializer = ProductoSerializer(
            producto, context=self.get_serializer_context()
        )
        return Response(read_serializer.data)

class CategoriaViewSet(MaestroPermisoMixin, SyncIncrementalMixin, viewsets.ModelViewSet):
    """
    ViewSet de Categorías.

    Lecturas (list, retrieve): autenticación de sucursal (SucursalToken)
    o admin (JWT). Las sucursales lo usan para sync incremental con ?desde=.

    Escrituras (create, update, partial_update, destroy): solo admins
    del portal autenticados con JWT.

    Endpoints:
        GET    /api/v1/maestros/categorias/           lista
        GET    /api/v1/maestros/categorias/?desde=...  sync incremental
        GET    /api/v1/maestros/categorias/<id>/       detalle
        POST   /api/v1/maestros/categorias/           crear (admin)
        PATCH  /api/v1/maestros/categorias/<id>/       editar (admin)
        DELETE /api/v1/maestros/categorias/<id>/       borrar (admin)

    Filtros:
        ?activa=true/false    → estado
        ?search=<texto>       → nombre (icontains)
    """
    pagination_class = LargePagination
    throttle_scope = 'maestros'
    permiso_base = 'categorias'

    def get_serializer_class(self):
        if self.action in ('list', 'retrieve'):
            return CategoriaSerializer
        return CategoriaWriteSerializer

    def get_queryset(self):
        queryset = Categoria.objects.all()

        activa = self.request.query_params.get('activa')
        if activa is not None:
            queryset = queryset.filter(activa=activa.lower() == 'true')

        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(nombre__icontains=search)

        self.queryset = queryset
        return super().get_queryset()

    def create(self, request, *args, **kwargs):
        write_serializer = self.get_serializer(data=request.data)
        write_serializer.is_valid(raise_exception=True)
        categoria = write_serializer.save()

        read_serializer = CategoriaSerializer(
            categoria, context=self.get_serializer_context()
        )
        return Response(read_serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()

        write_serializer = self.get_serializer(
            instance, data=request.data, partial=partial
        )
        write_serializer.is_valid(raise_exception=True)
        categoria = write_serializer.save()

        read_serializer = CategoriaSerializer(
            categoria, context=self.get_serializer_context()
        )
        return Response(read_serializer.data)


class ClienteViewSet(MaestroPermisoMixin, SyncIncrementalMixin, viewsets.ModelViewSet):
    """
    ViewSet de Clientes.

    Lecturas (list, retrieve): autenticación de sucursal (SucursalToken)
    o admin (JWT).

    Escrituras (create, update, partial_update, destroy): solo admins
    del portal autenticados con JWT.

    Endpoints:
        GET    /api/v1/maestros/clientes/           lista
        GET    /api/v1/maestros/clientes/?desde=...  sync incremental
        GET    /api/v1/maestros/clientes/<id>/       detalle
        POST   /api/v1/maestros/clientes/           crear (admin)
        PATCH  /api/v1/maestros/clientes/<id>/       editar (admin)
        DELETE /api/v1/maestros/clientes/<id>/       borrar (admin)

    Filtros:
        ?tipo=PERSONAL/CORPORATIVO/CONTADO  → tipo de cliente
        ?activo=true/false                  → estado
        ?search=<texto>                     → nombre o cedula_rnc (icontains)
    """
    pagination_class = LargePagination
    throttle_scope = 'maestros'
    permiso_base = 'clientes'

    def get_serializer_class(self):
        if self.action in ('list', 'retrieve'):
            return ClienteSerializer
        return ClienteWriteSerializer

    def get_queryset(self):
        queryset = Cliente.objects.all()

        tipo = self.request.query_params.get('tipo')
        if tipo:
            queryset = queryset.filter(tipo=tipo.upper())

        activo = self.request.query_params.get('activo')
        if activo is not None:
            queryset = queryset.filter(activo=activo.lower() == 'true')

        search = self.request.query_params.get('search')
        if search:
            from django.db.models import Q
            queryset = queryset.filter(
                Q(nombre__icontains=search)
                | Q(cedula_rnc__icontains=search)
            )

        self.queryset = queryset
        return super().get_queryset()

    def create(self, request, *args, **kwargs):
        write_serializer = self.get_serializer(data=request.data)
        write_serializer.is_valid(raise_exception=True)
        cliente = write_serializer.save()

        read_serializer = ClienteSerializer(
            cliente, context=self.get_serializer_context()
        )
        return Response(read_serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()

        write_serializer = self.get_serializer(
            instance, data=request.data, partial=partial
        )
        write_serializer.is_valid(raise_exception=True)
        cliente = write_serializer.save()

        read_serializer = ClienteSerializer(
            cliente, context=self.get_serializer_context()
        )
        return Response(read_serializer.data)
