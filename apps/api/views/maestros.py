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
        X-Total-Count: total FILTRADO (incluye ?desde= y los filtros del viewset),
                       antes de paginar. No es el total global del recurso.
    """

    def get_base_queryset(self):
        """Queryset base del viewset, con sus filtros propios pero SIN el ?desde=.

        Cada viewset de maestros lo sobreescribe devolviendo su queryset ya
        filtrado (sin mutar atributos compartidos). Fallback seguro a la
        resolucion estandar de DRF si un viewset no lo define.
        """
        return super().get_queryset()

    def get_queryset(self):
        queryset = self.get_base_queryset()
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
        # Total filtrado (incluye ?desde= y los filtros del viewset), sin paginar.
        response['X-Total-Count'] = self.get_queryset().count()

        return response


class ReadAfterWriteMixin:
    """create/update que devuelven SIEMPRE la representacion de LECTURA.

    Los maestros usan un write serializer para validar/guardar y un read
    serializer (con campos calculados: imagen_url, fechas, etc.) para responder,
    por consistencia con el GET. El viewset declara `read_serializer_class`.

    Contrato: POST -> 201 con read serializer; PUT/PATCH -> 200 con read serializer.
    """
    read_serializer_class = None

    def _read(self, instance):
        cls = self.read_serializer_class or self.get_serializer_class()
        return cls(instance, context=self.get_serializer_context())

    def create(self, request, *args, **kwargs):
        write_serializer = self.get_serializer(data=request.data)
        write_serializer.is_valid(raise_exception=True)
        instance = write_serializer.save()
        return Response(self._read(instance).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        write_serializer = self.get_serializer(
            instance, data=request.data, partial=partial
        )
        write_serializer.is_valid(raise_exception=True)
        instance = write_serializer.save()
        return Response(self._read(instance).data)


class ProductoViewSet(MaestroPermisoMixin, ReadAfterWriteMixin, SyncIncrementalMixin, viewsets.ModelViewSet):
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
    read_serializer_class = ProductoSerializer

    def get_serializer_class(self):
        if self.action in ('list', 'retrieve'):
            return ProductoSerializer
        return ProductoWriteSerializer

    def get_base_queryset(self):
        queryset = Producto.objects.select_related('categoria').all()

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

        return queryset


class CategoriaViewSet(MaestroPermisoMixin, ReadAfterWriteMixin, SyncIncrementalMixin, viewsets.ModelViewSet):
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
    read_serializer_class = CategoriaSerializer

    def get_serializer_class(self):
        if self.action in ('list', 'retrieve'):
            return CategoriaSerializer
        return CategoriaWriteSerializer

    def get_base_queryset(self):
        queryset = Categoria.objects.all()

        activa = self.request.query_params.get('activa')
        if activa is not None:
            queryset = queryset.filter(activa=activa.lower() == 'true')

        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(nombre__icontains=search)

        return queryset


class ClienteViewSet(MaestroPermisoMixin, ReadAfterWriteMixin, SyncIncrementalMixin, viewsets.ModelViewSet):
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
    read_serializer_class = ClienteSerializer

    def get_serializer_class(self):
        if self.action in ('list', 'retrieve'):
            return ClienteSerializer
        return ClienteWriteSerializer

    def get_base_queryset(self):
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

        return queryset
