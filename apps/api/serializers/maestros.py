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

from rest_framework import exceptions, serializers
from apps.productos.models import Producto, Categoria
from apps.clientes.models import Cliente


class CategoriaSerializer(serializers.ModelSerializer):
    """
    Serializer completo de Categoría.

    Usado para:
    - Sync cloud → sucursal (datos maestros)
    - La sucursal hace update_or_create usando 'id' como lookup
    - Listado/detalle en el portal (total_productos para display)
    """
    total_productos = serializers.IntegerField(read_only=True)

    class Meta:
        model = Categoria
        fields = [
            'id',
            'nombre',
            'descripcion',
            'activa',
            'tipo_negocio',
            'atributos_configurados',
            'total_productos',
            'fecha_creacion',
            'fecha_modificacion',
        ]
        read_only_fields = fields


class CategoriaWriteSerializer(serializers.ModelSerializer):
    """
    Serializer para CREATE/UPDATE de Categoría desde el portal admin.

    Campos editables:
        nombre              — único, se normaliza con strip
        descripcion
        activa
        tipo_negocio        — choice del modelo
        atributos_configurados — JSONField dict; define los atributos
                                 disponibles para productos de la categoría
    """

    class Meta:
        model = Categoria
        fields = [
            'nombre',
            'descripcion',
            'activa',
            'tipo_negocio',
            'atributos_configurados',
        ]
        extra_kwargs = {
            'descripcion': {'required': False, 'allow_blank': True},
            'activa': {'required': False},
            'tipo_negocio': {'required': False},
            'atributos_configurados': {'required': False},
        }

    def validate_nombre(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('El nombre no puede estar vacío.')
        return value

    def validate_atributos_configurados(self, value):
        if value is None:
            return value
        if not isinstance(value, dict):
            raise serializers.ValidationError(
                'Los atributos configurados deben ser un objeto JSON.'
            )
        return value


class ProductoSerializer(serializers.ModelSerializer):
    """
    Serializer completo de Producto.
    
    Notas:
    - categoria_nombre: campo denormalizado para display en sucursal
    - imagen_url: URL absoluta del original (si existe)
    - imagen_thumb_url: URL de la miniatura de 320 px. Es la que debe pintar
      cualquier grilla: los originales vienen del celular del cliente y pesan
      megabytes, mientras la miniatura ronda los 20 KB. Cae al original cuando
      el producto todavia no tiene miniatura generada.
    - stock_actual y valuacion_fifo NO se incluyen — son datos locales
    - pendiente_revision / origen_sucursal_nombre: el producto nació como
      stub de una venta con SKU desconocido (ver
      apps.api.views.sync._resolver_productos_venta, BUG-G en docs/BUGS.md).
      El portal los usa para el badge "Revisar" y el aviso en el modal.
    """
    categoria_nombre = serializers.CharField(
        source='categoria.nombre',
        read_only=True
    )
    imagen_url = serializers.SerializerMethodField()
    imagen_thumb_url = serializers.SerializerMethodField()
    origen_sucursal_nombre = serializers.CharField(
        source='origen_sucursal.nombre',
        read_only=True,
        default=None,
    )

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
            'imagen_thumb_url',
            'atributos',
            'pendiente_revision',
            'origen_sucursal_nombre',
            'fecha_creacion',
            'fecha_modificacion',
        ]
        read_only_fields = fields

    def get_imagen_url(self, obj):
        return self._url(obj.imagen)

    def get_imagen_thumb_url(self, obj):
        return self._url(obj.imagen_preview)

    def _url(self, campo):
        if not campo:
            return None
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(campo.url)
        return campo.url

class ProductoWriteSerializer(serializers.ModelSerializer):
    """
    Serializer para CREATE/UPDATE de Producto desde el portal admin.

    Campos editables (versus inventario que NO se sincroniza):
        sku           creates only — bloqueado en updates
        nombre, descripcion, marca
        precio_venta  caso de uso principal
        codigo_barras
        categoria
        activo, estado
        stock_minimo  define alertas
        atributos     JSONField con presets futuros

    Campos read-only (calculados o de inventario):
        stock_actual, costo_promedio, lotes, fechas, imagen_url
    """

    class Meta:
        model = Producto
        fields = [
            'sku',
            'nombre',
            'descripcion',
            'precio_venta',
            'codigo_barras',
            'categoria',
            'activo',
            'estado',
            'marca',
            'stock_minimo',
            'atributos',
        ]
        extra_kwargs = {
            'descripcion': {'required': False, 'allow_blank': True},
            'codigo_barras': {
                'required': False, 'allow_blank': True, 'allow_null': True,
            },
            'marca': {'required': False, 'allow_blank': True},
            'stock_minimo': {'required': False},
            'atributos': {'required': False},
        }

    def update(self, instance, validated_data):
        # SKU no se cambia después de creado — rompería la sincronización
        # con sucursal porque ahí es la clave de update_or_create.
        validated_data.pop('sku', None)

        # Anti-clobber (BUG-G, docs/BUGS.md): un stub nacido de una venta con
        # SKU desconocido queda `pendiente_revision=True` y oculto del pull
        # hacia la sucursal que lo originó (ver ProductoViewSet). Solo se
        # libera cuando alguien lo completa de verdad -- y "de verdad" se
        # define como "mandó una categoría", porque el modal del portal
        # siempre la incluye en el submit de edición. NO cualquier PATCH:
        # `toggleProduct` (activar/desactivar) manda solo {activo}, y si eso
        # bastara para liberar el stub, un simple clic lo bajaría a la
        # sucursal con su nombre/precio/categoría genéricos todavía puestos,
        # pisando el producto real que esa sucursal ya tiene para el SKU.
        if instance.pendiente_revision and 'categoria' in validated_data:
            validated_data['pendiente_revision'] = False

        return super().update(instance, validated_data)

    def validate_precio_venta(self, value):
        if value is None or value <= 0:
            raise serializers.ValidationError(
                'El precio debe ser mayor a cero.'
            )
        return value

    def validate_stock_minimo(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError(
                'El stock mínimo no puede ser negativo.'
            )
        return value

    def validate_atributos(self, value):
        if value is None:
            return value
        if not isinstance(value, dict):
            raise serializers.ValidationError(
                'Los atributos deben ser un objeto JSON (clave-valor).'
            )
        # Validación liviana: claves deben ser strings y no vacías.
        for k in value.keys():
            if not isinstance(k, str) or not k.strip():
                raise serializers.ValidationError(
                    'Las claves de atributos deben ser texto no vacío.'
                )
        return value


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
            'plazo_credito_dias',
            'condiciones_pago',
            'notas',
            'activo',
            'es_contado',
            'fecha_creacion',
            'fecha_modificacion',
        ]
        read_only_fields = fields


class ClienteWriteSerializer(serializers.ModelSerializer):
    """
    Serializer para CREATE/UPDATE de Cliente desde el portal admin.

    Campos editables:
        tipo                — PERSONAL / CORPORATIVO / CONTADO
        nombre
        cedula_rnc          — único; se valida formato básico
        telefono, direccion, notas
        limite_credito      — >= 0
        condiciones_pago
        activo

    Restricción: solo puede existir un cliente CONTADO (el genérico).
    No se permite crear ni cambiar tipo a CONTADO desde el portal.
    """

    class Meta:
        model = Cliente
        fields = [
            'tipo',
            'nombre',
            'cedula_rnc',
            'telefono',
            'direccion',
            'limite_credito',
            'plazo_credito_dias',
            'condiciones_pago',
            'notas',
            'activo',
        ]
        extra_kwargs = {
            'cedula_rnc': {'required': False, 'allow_blank': True, 'allow_null': True},
            'telefono': {'required': False, 'allow_blank': True, 'allow_null': True},
            'direccion': {'required': False, 'allow_blank': True, 'allow_null': True},
            'condiciones_pago': {'required': False, 'allow_blank': True, 'allow_null': True},
            'notas': {'required': False, 'allow_blank': True, 'allow_null': True},
            'limite_credito': {'required': False},
            'plazo_credito_dias': {'required': False},
            'activo': {'required': False},
            'tipo': {'required': False},
        }

    # Campos cuya edicion requiere un permiso propio, ademas de
    # `clientes.editar`. Se autoriza por CAMPO y antes de persistir.
    CAMPOS_FINANCIEROS = {
        'limite_credito': 'clientes.editar_limite_credito',
    }

    def validate(self, attrs):
        attrs = super().validate(attrs)
        self._autorizar_campos_financieros(attrs)
        self._proteger_generico(attrs)
        return attrs

    def _autorizar_campos_financieros(self, attrs):
        """
        Ampliar el credito no viene incluido en "editar un cliente" (CLI-003).

        El catalogo ya separa `clientes.editar_limite_credito` —y la vista
        Django local ya lo exigia—, pero el mixin de permisos del portal mapea
        todo `update`/`partial_update` a `clientes.editar`. Con eso, un operador
        podia eludir por completo el flujo de override de credito: primero subia
        el limite por PATCH, despues vendia a credito sin dejar ninguna
        excepcion crediticia registrada.
        """
        request = self.context.get('request')
        usuario = getattr(request, 'user', None)
        if usuario is None:
            return

        from apps.permisos.decorators import sucursal_del_request

        sucursal = sucursal_del_request(request) if request is not None else None

        for campo, permiso in self.CAMPOS_FINANCIEROS.items():
            if campo not in attrs:
                continue
            # Solo importa si el valor CAMBIA: un PATCH que reenvia el limite
            # actual sin tocarlo no es una decision financiera.
            if self.instance is not None:
                actual = getattr(self.instance, campo, None)
                if actual is not None and attrs[campo] == actual:
                    continue
            if not usuario.tiene_permiso(permiso, sucursal=sucursal):
                raise exceptions.PermissionDenied(
                    f'Cambiar "{campo}" requiere el permiso "{permiso}".'
                )

    def _proteger_generico(self, attrs):
        """
        El cliente CONTADO generico no se edita desde el portal (CLI-007).

        `validate_tipo` solo corre cuando `tipo` viene en el payload, asi que un
        PATCH parcial sobre la fila que YA es CONTADO la renombraba o
        desactivaba sin pasar por ninguna validacion. Ventas historicas y
        cotizaciones apuntan a esa fila.
        """
        if self.instance is not None and getattr(self.instance, 'es_contado', False):
            raise exceptions.PermissionDenied(
                'El cliente CONTADO es la identidad generica del sistema y no '
                'se puede modificar desde el portal.'
            )

    def validate_nombre(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('El nombre no puede estar vacío.')
        return value

    def validate_tipo(self, value):
        # El cliente CONTADO genérico se gestiona internamente, no desde el portal.
        if value == 'CONTADO':
            raise serializers.ValidationError(
                'No se puede asignar el tipo CONTADO desde el portal. '
                'Use PERSONAL o CORPORATIVO.'
            )
        return value

    def validate_limite_credito(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError(
                'El límite de crédito no puede ser negativo.'
            )
        return value

    def validate_plazo_credito_dias(self, value):
        if value is not None and (value < 1 or value > 365):
            raise serializers.ValidationError(
                'El plazo de credito debe estar entre 1 y 365 dias.'
            )
        return value

    def update(self, instance, validated_data):
        plazo_anterior = instance.plazo_credito_dias
        cliente = super().update(instance, validated_data)

        if (
            'plazo_credito_dias' in validated_data
            and int(plazo_anterior) != int(cliente.plazo_credito_dias)
        ):
            request = self.context.get('request')
            usuario = getattr(request, 'user', None)
            if usuario is not None and not getattr(usuario, 'is_authenticated', False):
                usuario = None

            from apps.cuentas_por_cobrar.services import reprogramar_cxc_por_plazo_cliente

            reprogramar_cxc_por_plazo_cliente(
                cliente,
                usuario=usuario,
                origen='portal_cliente_update',
                plazo_anterior=int(plazo_anterior),
            )

        return cliente
