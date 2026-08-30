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

import logging

from django.db.models import Q
from django.utils.dateparse import parse_datetime
from rest_framework import exceptions, serializers, viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from ..permissions import MaestroPermisoMixin, _es_token_de_sucursal, requiere_alguno

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

logger = logging.getLogger('pos_system')

# 10 MB. Los originales de este cliente promedian 3.2 MB (foto de celular sin
# comprimir); el tope solo esta para frenar un archivo verdaderamente
# anormal, no para exigir que el cajero comprima -- el frontend ya comprime
# antes de subir (ver pos-cloud-dashboard).
_LIMITE_IMAGEN_BYTES = 10 * 1024 * 1024


class SubirImagenProductoSerializer(serializers.Serializer):
    """
    `ImageField` de DRF ya valida con Pillow que el archivo sea una imagen de
    verdad (abre y lee las dimensiones); no hay que reimplementar eso.
    """
    imagen = serializers.ImageField()

    def validate_imagen(self, value):
        if value.size > _LIMITE_IMAGEN_BYTES:
            raise serializers.ValidationError(
                f'La imagen pesa {value.size / 1_000_000:.1f} MB; el limite es '
                f'{_LIMITE_IMAGEN_BYTES / 1_000_000:.0f} MB.'
            )
        return value


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
                queryset = self._filtrar_keyset(queryset, timestamp)
                # Orden TOTAL alineado con el cursor. Sin esto la paginacion es
                # inestable: los endpoints ordenan por `nombre` (Meta.ordering)
                # mientras el corte es temporal, asi que los registros no llegan
                # en orden de cursor y PostgreSQL no garantiza consistencia
                # entre paginas. Ver BUG-B en docs/BUGS.md.
                #
                # SOLO se reordena en modo sync: sin `?desde=` el portal sigue
                # viendo su listado alfabetico intacto.
                queryset = queryset.order_by('fecha_modificacion', 'id')

        return queryset

    def _filtrar_keyset(self, queryset, timestamp):
        """
        Filtro keyset sobre la tupla (fecha_modificacion, id).

        `?desde=` solo daba `fecha_modificacion__gt`, estrictamente mayor: dos
        registros guardados en el mismo instante hacian que el segundo se
        perdiera cuando el cursor quedaba en ese valor. Con `?desde_id=` el
        corte pasa a ser "posterior en la tupla", que es un orden total.

        `desde_id` es opcional: un cliente viejo que no lo manda obtiene el
        comportamiento anterior, que sigue siendo correcto.
        """
        desde_id = self.request.query_params.get('desde_id')
        if not desde_id:
            return queryset.filter(fecha_modificacion__gt=timestamp)

        try:
            desde_id = int(desde_id)
        except (TypeError, ValueError):
            return queryset.filter(fecha_modificacion__gt=timestamp)

        return queryset.filter(
            Q(fecha_modificacion__gt=timestamp)
            | Q(fecha_modificacion=timestamp, id__gt=desde_id)
        )

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
        queryset = Producto.objects.select_related('categoria', 'origen_sucursal').all()

        # Anti-clobber (BUG-G, docs/BUGS.md): un stub creado desde una venta
        # con SKU desconocido (ver _resolver_productos_venta) nace con
        # nombre/precio minimos y categoria generica. Si el pull de la
        # sucursal que lo origino lo bajara asi, pisaria con ese stub pobre
        # los datos reales que esa sucursal ya tiene para el mismo SKU --
        # exactamente el dano que el sync de maestros existe para evitar.
        # Se excluye SOLO para tokens de sucursal (sync incremental); el
        # portal admin lo ve siempre, para poder completarlo. En cuanto se
        # completa (ver ProductoWriteSerializer.update), fecha_modificacion
        # avanza y baja normal en el proximo ciclo.
        if _es_token_de_sucursal(self.request):
            queryset = queryset.filter(pendiente_revision=False)

        # Filtros opcionales
        activo = self.request.query_params.get('activo')
        if activo is not None:
            queryset = queryset.filter(activo=activo.lower() == 'true')

        categoria = self.request.query_params.get('categoria')
        if categoria:
            queryset = queryset.filter(categoria_id=categoria)

        pendiente_revision = self.request.query_params.get('pendiente_revision')
        if pendiente_revision is not None:
            queryset = queryset.filter(pendiente_revision=pendiente_revision.lower() == 'true')

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

    def get_permissions(self):
        # La action de foto acepta `productos.editar` O el permiso acotado
        # `productos.fotografiar` (pensado para la cajera: sube fotos, no
        # cambia precio ni categoria). Fuera de MaestroPermisoMixin porque
        # ese mapea UN permiso por accion, y aca hace falta el OR.
        if self.action == 'imagen':
            return [IsAuthenticated(), requiere_alguno('productos.editar', 'productos.fotografiar')()]
        return super().get_permissions()

    @action(detail=True, methods=['post', 'delete'], url_path='imagen')
    def imagen(self, request, pk=None):
        producto = self.get_object()
        if request.method == 'DELETE':
            return self._eliminar_imagen(producto)
        return self._subir_imagen(request, producto)

    def _subir_imagen(self, request, producto):
        serializer = SubirImagenProductoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # El ORIGINAL anterior (no la miniatura -- esa la borra sola
        # `sincronizar_miniatura` dentro de `producto.save()`) queda huerfano
        # en el storage si no se borra aca: cada foto nueva dejaria un
        # archivo mas en Blob que nadie referencia.
        anterior = producto.imagen.name or ''
        storage = producto.imagen.storage

        producto.imagen = serializer.validated_data['imagen']
        producto.save()

        if anterior and anterior != (producto.imagen.name or ''):
            try:
                storage.delete(anterior)
            except Exception as exc:  # pragma: no cover - borrar no debe tumbar la respuesta
                logger.warning('No se pudo borrar la imagen anterior "%s": %s', anterior, exc)

        return Response(self._read(producto).data)

    def _eliminar_imagen(self, producto):
        if producto.imagen:
            storage = producto.imagen.storage
            nombre_original = producto.imagen.name
            nombre_miniatura = producto.imagen_miniatura.name if producto.imagen_miniatura else ''

            producto.imagen = None
            producto.imagen_miniatura = None
            producto.save(sincronizar_miniatura=False)

            for nombre in (nombre_original, nombre_miniatura):
                if not nombre:
                    continue
                try:
                    storage.delete(nombre)
                except Exception as exc:  # pragma: no cover
                    logger.warning('No se pudo borrar "%s" al eliminar la imagen: %s', nombre, exc)

        return Response(self._read(producto).data)


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

    def perform_destroy(self, instance):
        """
        El generico CONTADO no se borra (CLI-007).

        `ModelViewSet` conservaba el `destroy` fisico estandar, asi que un
        DELETE sobre el generico sin referencias lo eliminaba. `get_cliente_contado()`
        pasaba entonces a crearlo de nuevo con otro PK, y las ventas historicas
        quedaban apuntando a una fila que ya no es "el" generico.
        """
        if getattr(instance, 'es_contado', False):
            raise exceptions.PermissionDenied(
                'El cliente CONTADO es la identidad generica del sistema y no '
                'se puede eliminar.'
            )
        super().perform_destroy(instance)

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
