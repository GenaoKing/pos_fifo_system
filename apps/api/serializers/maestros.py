"""
apps/api/serializers/maestros.py
Serializers para datos maestros (cloud → sucursal).

Estos serializers se usan para sync incremental: la sucursal pide
los registros modificados desde un timestamp y recibe los datos
completos para hacer update_or_create local.

Principio: incluir TODOS los campos que la sucursal necesita para
reconstruir el registro. Campos computados (stock, valuación) se
excluyen porque son locales a cada sucursal.
"""

from rest_framework import serializers
from apps.productos.models import Producto, Categoria
from apps.clientes.models import Cliente


class CategoriaSerializer(serializers.ModelSerializer):
    """
    Serializer completo de Categoría.
    
    Usado para:
    - Sync cloud → sucursal (datos maestros)
    - La sucursal hace update_or_create usando 'id' como lookup
    """

    class Meta:
        model = Categoria
        fields = [
            'id',
            'nombre',
            'descripcion',
            'activa',
            'tipo_negocio',
            'atributos_configurados',
            'fecha_creacion',
            'fecha_modificacion',
        ]
        read_only_fields = fields


class ProductoSerializer(serializers.ModelSerializer):
    """
    Serializer completo de Producto.
    
    Notas:
    - categoria_nombre: campo denormalizado para display en sucursal
    - imagen_url: URL absoluta de la imagen (si existe)
    - stock_actual y valuacion_fifo NO se incluyen — son datos locales
    """
    categoria_nombre = serializers.CharField(
        source='categoria.nombre',
        read_only=True
    )
    imagen_url = serializers.SerializerMethodField()

    class Meta:
        model = Producto
        fields = [
            'id',
            'sku',
            'codigo_barras',
            'nombre',
            'descripcion',
            'categoria',          # FK id para update_or_create
            'categoria_nombre',   # Denormalizado para display
            'estado',
            'marca',
            'precio_venta',
            'stock_minimo',
            'activo',
            'imagen_url',
            'atributos',
            'fecha_creacion',
            'fecha_modificacion',
        ]
        read_only_fields = fields

    def get_imagen_url(self, obj):
        if obj.imagen:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.imagen.url)
            return obj.imagen.url
        return None


class ClienteSerializer(serializers.ModelSerializer):
    """
    Serializer completo de Cliente.
    
    Notas:
    - es_contado: propiedad computada, útil para la sucursal
    - total_compras y monto_total_compras NO se incluyen — son locales
    """
    es_contado = serializers.BooleanField(read_only=True)

    class Meta:
        model = Cliente
        fields = [
            'id',
            'tipo',
            'nombre',
            'cedula_rnc',
            'telefono',
            'direccion',
            'limite_credito',
            'condiciones_pago',
            'notas',
            'activo',
            'es_contado',
            'fecha_creacion',
            'fecha_modificacion',
        ]
        read_only_fields = fields