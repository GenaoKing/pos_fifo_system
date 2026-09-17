from django.db import models
from django.db.models import Exists, OuterRef
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.contrib.postgres.indexes import GinIndex

from apps.tenancy.media import producto_image_upload_to, producto_thumb_upload_to


class Categoria(models.Model):
    """Categorías para agrupar productos"""
    
    nombre = models.CharField(
        'Nombre',
        max_length=100,
        unique=True,
    )
    descripcion = models.TextField(
        'Descripción',
        blank=True,
        help_text='Descripción opcional de la categoría',
    )
    activa = models.BooleanField(
        'Activa',
        default=True,
        help_text='Indica si la categoría está activa',
    )

    # ← AGREGAR ESTOS DOS CAMPOS NUEVOS:
    tipo_negocio = models.CharField(
        'Tipo de Negocio',
        max_length=50,
        choices=[
            ('general', 'General'),
            ('plasticos', 'Plásticos / Envases'),
            ('autopartes', 'Autopartes / Repuestos'),
            ('otro', 'Otro'),
        ],
        default='general',
        help_text='Tipo de industria/negocio de esta categoría'
    )
    
    atributos_configurados = models.JSONField(
        'Atributos Configurados',
        default=dict,
        blank=True,
        help_text='Definición de atributos personalizados para productos de esta categoría'
    )
    
    # Fechas
    fecha_creacion = models.DateTimeField('Fecha de creación', default=timezone.now)
    fecha_modificacion = models.DateTimeField('Fecha de modificación', auto_now=True)
    
    # Identidad cloud estable. Ver apps/sync/engine.py::_pull_categorias.
    #
    # El pull identificaba por clave natural (nombre), asi que renombrar una
    # categoria en el portal creaba OTRA en la sucursal y dejaba los productos
    # historicos colgando de la vieja. Con este campo la fila se reconoce
    # aunque cambie cualquier atributo visible.
    #
    # Null = fila de origen local todavia no reconciliada con el cloud. La
    # clave natural sigue sirviendo para adoptarla la primera vez.
    origen_cloud_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        verbose_name='ID en cloud',
        help_text='PK de esta fila en la BD cloud. Identidad de sync; no se edita a mano.',
    )
    # Versión autoritativa recibida del cloud. ``fecha_modificacion`` es el
    # timestamp de ESTA copia local y cambia cada vez que el pull la guarda;
    # por eso no puede usarse como precondición CAS al volver a proponer una
    # mutación offline.
    revision_cloud = models.CharField(
        max_length=64,
        blank=True,
        default='',
        editable=False,
        help_text='Revision ISO autoritativa del cloud para CAS de maestros.',
    )

    class Meta:
        verbose_name = 'Categoría'
        verbose_name_plural = 'Categorías'
        ordering = ['nombre']
        db_table = 'categorias'
        indexes = [
            # Cursor de sync: se filtra y ordena por este par en cada pull.
            models.Index(fields=['fecha_modificacion', 'id']),
        ]
    
    def __str__(self):
        return self.nombre

    @property
    def total_productos(self):
        """Retorna el total de productos activos en esta categoría"""
        return self.productos.filter(activo=True).count()

    @classmethod
    def get_sin_clasificar(cls):
        """
        Categoria generica para productos creados como stub (ver
        Producto.pendiente_revision). Molde de Cliente.get_cliente_contado.

        Tiene que ser una fila REAL y activa: baja por el pull de categorias
        antes que los productos en el mismo ciclo (apps/sync/engine.py), asi
        el stub nunca queda diferido en la sucursal esperando una categoria
        que no existe.
        """
        categoria, _ = cls.objects.get_or_create(
            nombre='Sin clasificar',
            defaults={
                'activa': True,
                'descripcion': 'Categoria generica para productos creados '
                               'automaticamente desde una venta de sucursal.',
            },
        )
        return categoria


def productos_vendibles(queryset=None):
    """
    Los productos que se pueden vender. Una sola definicion (PRO-007).

    "Vendible" no era una condicion sino varias, y no coincidian: la busqueda
    general del POS exigia `categoria__activa=True` SOLO si el cliente enviaba
    filtro de categoria; la busqueda por codigo y por id miraban el producto
    pero no su categoria; y el cargador transaccional de la venta filtraba
    unicamente por id. Es decir: un producto activo en una categoria dada de
    baja aparecia en el escaner y se vendia, y un producto inactivo se podia
    materializar en una venta con una pestana vieja o una carrera.

    La baja administrativa tiene que ser una garantia del backend, no una
    convencion de la UI.
    """
    from apps.sync.models import MutacionMaestro

    base = Producto.objects.all() if queryset is None else queryset
    conflictos_producto = MutacionMaestro.objects.filter(
        entidad=MutacionMaestro.Entidad.PRODUCTO,
        entidad_id=OuterRef('pk'),
        estado=MutacionMaestro.Estado.CONFLICTO,
    )
    conflictos_categoria = MutacionMaestro.objects.filter(
        entidad=MutacionMaestro.Entidad.CATEGORIA,
        entidad_id=OuterRef('categoria_id'),
        estado=MutacionMaestro.Estado.CONFLICTO,
    )
    return (
        base.filter(activo=True, categoria__activa=True)
        .annotate(
            _conflicto_maestro_producto=Exists(conflictos_producto),
            _conflicto_maestro_categoria=Exists(conflictos_categoria),
        )
        .filter(
            _conflicto_maestro_producto=False,
            _conflicto_maestro_categoria=False,
        )
    )


def tiene_conflicto_maestro(producto):
    """Indica si una propuesta A05.2a bloquea una venta nueva de este producto."""
    from apps.sync.models import MutacionMaestro

    return MutacionMaestro.objects.filter(
        estado=MutacionMaestro.Estado.CONFLICTO,
    ).filter(
        models.Q(
            entidad=MutacionMaestro.Entidad.PRODUCTO,
            entidad_id=producto.pk,
        ) | models.Q(
            entidad=MutacionMaestro.Entidad.CATEGORIA,
            entidad_id=producto.categoria_id,
        )
    ).exists()


class ProductoQuerySet(models.QuerySet):
    """Mantiene identidad aun en mutaciones masivas del ORM."""

    CAMPOS_INMUTABLES = {'sku', 'origen_cloud_id'}

    @classmethod
    def _validar_campos_mutables(cls, campos):
        bloqueados = cls.CAMPOS_INMUTABLES.intersection(campos)
        if bloqueados:
            raise ValidationError(
                'Campos inmutables de Producto: ' + ', '.join(sorted(bloqueados))
            )

    def update(self, **kwargs):
        self._validar_campos_mutables(kwargs)
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        self._validar_campos_mutables(fields)
        return super().bulk_update(objs, fields, batch_size=batch_size)


class ProductoManager(models.Manager.from_queryset(ProductoQuerySet)):
    pass


class Producto(models.Model):
    """Productos del inventario"""

    objects = ProductoManager()
    
    # Identificadores
    sku = models.CharField(
        'SKU',
        max_length=50,
        unique=True,
        blank=True,
        help_text='Código interno único del producto',
    )
    # Identidad estable de la replica cloud. El SKU sigue siendo la clave
    # natural de adopcion inicial, pero deja de decidir la identidad una vez
    # que este campo queda sellado. No confundir con `origen_sucursal`: ese FK
    # conserva la procedencia historica de los stubs BUG-H y no identifica la
    # fila autoritativa del catalogo.
    origen_cloud_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        editable=False,
        verbose_name='ID en cloud',
        help_text='PK de esta fila en la BD cloud. Identidad de sync; no se edita a mano.',
    )
    # Ver comentario equivalente en ``Categoria.revision_cloud``. La revisión
    # del cloud se conserva aparte del ``auto_now`` de la réplica local.
    revision_cloud = models.CharField(
        max_length=64,
        blank=True,
        default='',
        editable=False,
        help_text='Revision ISO autoritativa del cloud para CAS de maestros.',
    )
    codigo_barras = models.CharField(
        'Código de barras',
        max_length=100,
        unique=True,
        blank=True,
        null=True,
        help_text='Código de barras externo o generado internamente (RP-XXXXXX)',
    )
    
    # Información básica
    nombre = models.CharField(
        'Nombre',
        max_length=200,
    )
    descripcion = models.TextField(
        'Descripción',
        blank=True,
    )
    
    # Categoría
    categoria = models.ForeignKey(
        Categoria,
        on_delete=models.PROTECT,
        related_name='productos',
        verbose_name='Categoría',
    )
    
    ESTADO_CHOICES = [
        ('nuevo', 'Nuevo'),
        ('usado', 'Usado'),
    ]

    estado = models.CharField(
        max_length=10, 
        choices=ESTADO_CHOICES,
        default='nuevo', 
        verbose_name='Estado'
        )
    
    marca = models.CharField(
        max_length=100,
        blank=True, 
        default='', 
        verbose_name='Marca'
        )

    # Precios y stock
    precio_venta = models.DecimalField(
        'Precio de venta',
        max_digits=10,
        decimal_places=2,
        help_text='Precio de venta al público',
    )
    stock_minimo = models.PositiveIntegerField(
        'Stock mínimo',
        default=5,
        help_text='Cantidad mínima antes de alertar',
    )
    
    # Estado
    activo = models.BooleanField(
        'Activo',
        default=True,
        help_text='Indica si el producto está disponible para venta',
    )
    
    # Imagen (opcional)
    imagen = models.ImageField(
        'Imagen',
        upload_to=producto_image_upload_to,
        blank=True,
        null=True,
    )
    # Derivada de `imagen`, la mantiene el modelo. Se guarda el nombre REAL en
    # vez de deducirlo al leer: si el campo esta vacio no hay miniatura, y quien
    # muestre la imagen cae al original en lugar de pedir un archivo que no
    # existe y quedarse sin nada.
    imagen_miniatura = models.ImageField(
        'Miniatura',
        upload_to=producto_thumb_upload_to,
        blank=True,
        null=True,
        editable=False,
        help_text='JPEG de 320 px generado a partir de la imagen. No se edita a mano.',
    )

    # ------------------------------------------------------------------
    # Origen y revision (solo se usan en el CLOUD; en el POS local quedan
    # en su default). Ver apps/api/views/sync.py::_resolver_productos_venta.
    # ------------------------------------------------------------------
    #
    # Una venta que llega con un SKU inexistente ya NO se rechaza entera:
    # el producto nace como stub minimo (nombre y precio del payload,
    # categoria generica) para no perder la venta, y queda marcado para que
    # el dueno lo complete desde el portal. Molde exacto de
    # Cliente.origen_sucursal (apps/clientes/models.py) -- procedencia
    # permanente, no se limpia nunca.
    origen_sucursal = models.ForeignKey(
        'sucursales.Sucursal',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='productos_originados',
        verbose_name='Sucursal de origen',
        help_text='Sucursal donde nacio el producto, si nacio de una venta en sucursal.',
    )
    # Separado de `origen_sucursal` a proposito: la procedencia no cambia
    # nunca, pero este SI se limpia cuando el dueno completa el producto
    # desde el portal (categoria real, precio revisado). Mientras este en
    # True, el pull de la sucursal de origen lo excluye -- ver
    # ProductoViewSet.get_base_queryset -- para no pisar con datos pobres
    # los reales que ya tiene esa sucursal para el mismo SKU.
    pendiente_revision = models.BooleanField(
        'Pendiente de revision',
        default=False,
        db_index=True,
        help_text='Nacio como stub de una venta y todavia no se completo desde el portal.',
    )
    # Solo lo usa el POS local (pull de maestros) para detectar que la foto
    # cambio en el cloud sin tener que descargarla en cada ciclo para
    # comparar. Se sella con la URL que ya se descargo con exito; queda vacia
    # mientras no haya descarga exitosa, asi que una descarga fallida
    # reintenta sola en el proximo ciclo (no se sella nada).
    imagen_origen_url = models.URLField(
        'URL de origen de la imagen',
        max_length=500,
        blank=True,
        default='',
        editable=False,
        help_text='Ultima imagen_url del cloud descargada con exito. Solo POS local.',
    )

    atributos = models.JSONField(
        'Atributos Personalizados',
        default=dict,
        blank=True,
        help_text='Atributos específicos según el tipo de producto (marca, modelo, etc.)'
    )
    
    
    # Fechas
    fecha_creacion = models.DateTimeField('Fecha de creación', default=timezone.now)
    fecha_modificacion = models.DateTimeField('Fecha de modificación', auto_now=True)
    
    class Meta:
        verbose_name = 'Producto'
        verbose_name_plural = 'Productos'
        ordering = ['nombre']
        db_table = 'productos'
        indexes = [
            models.Index(fields=['sku']),
            models.Index(fields=['codigo_barras']),
            models.Index(fields=['categoria', 'activo']),
            # Cursor de sync: se filtra y ordena por este par en cada pull.
            models.Index(fields=['fecha_modificacion', 'id']),
            GinIndex(fields=['atributos'], name='idx_productos_atributos'),
        ]
    
    @property
    def es_vendible(self):
        """Misma regla que `productos_vendibles()`, por instancia."""
        return bool(
            self.activo
            and self.categoria
            and self.categoria.activa
            and not tiene_conflicto_maestro(self)
        )

    def __str__(self):
        return f"{self.sku} - {self.nombre}"

    @classmethod
    def from_db(cls, db, field_names, values):
        # Recordar la imagen tal como venia de la BD permite saber en `save()`
        # si cambio, sin una consulta extra por guardado.
        instancia = super().from_db(db, field_names, values)
        if 'imagen' in field_names:
            instancia._imagen_en_bd = instancia.imagen.name or ''
        return instancia

    def save(self, *args, **kwargs):
        # `sincronizar_miniatura=False` es para quien ya tiene el archivo a mano
        # y prefiere generarla el mismo pasando `fuente` — hoy, la migracion de
        # media a Blob.
        sincronizar = kwargs.pop('sincronizar_miniatura', True)
        self._validar_identidad_inmutable()
        super().save(*args, **kwargs)
        if sincronizar:
            self.sincronizar_miniatura()

    def _validar_identidad_inmutable(self):
        """Impide partir una identidad ya creada cambiando SKU o cloud ID.

        Las superficies publicas omiten `sku` al actualizar para conservar
        compatibilidad con clientes que todavia lo reenvian. Esta guarda es la
        ultima frontera para Admin, scripts y servicios que guardan el modelo
        directamente. La adopcion `NULL -> cloud_id` si esta permitida.
        """
        if self._state.adding or self.pk is None:
            return

        queryset = type(self).objects
        if self._state.db:
            queryset = queryset.using(self._state.db)
        anterior = queryset.filter(pk=self.pk).values(
            'sku', 'origen_cloud_id',
        ).first()
        if anterior is None:
            return

        errores = {}
        if self.sku != anterior['sku']:
            errores['sku'] = 'El SKU es inmutable despues de crear el producto.'
        if (
            anterior['origen_cloud_id'] is not None
            and self.origen_cloud_id != anterior['origen_cloud_id']
        ):
            errores['origen_cloud_id'] = (
                'La identidad cloud es inmutable una vez adoptada.'
            )
        if errores:
            raise ValidationError(errores)

    def sincronizar_miniatura(self, forzar=False, fuente=None):
        """
        Deja `imagen_miniatura` en linea con `imagen`.

        Se llama sola en cada `save()` pero solo trabaja cuando hace falta: si
        la imagen no cambio y ya hay miniatura, no toca el storage. Sin esa
        guarda, cada guardado de un producto se llevaria una lectura del blob
        original por la red.

        Devuelve True si escribio algo.
        """
        from utils.imagenes import guardar_miniatura

        anterior_imagen = getattr(self, '_imagen_en_bd', None)
        imagen_actual = self.imagen.name or ''
        sin_cambios = imagen_actual == anterior_imagen
        if not forzar and sin_cambios and bool(self.imagen_miniatura) == bool(imagen_actual):
            return False

        vieja = self.imagen_miniatura.name or ''
        storage = self.imagen_miniatura.storage

        nueva = guardar_miniatura(self.imagen, fuente=fuente) if self.imagen else ''
        if nueva == vieja:
            self._imagen_en_bd = imagen_actual
            return False

        self.imagen_miniatura = nueva or None
        # `update()` y no `save()`: guardar de nuevo reentraria aca.
        type(self).objects.filter(pk=self.pk).update(imagen_miniatura=self.imagen_miniatura)
        self._imagen_en_bd = imagen_actual

        # La miniatura vieja ya no la referencia nadie. Dejarla acumula basura
        # en el container a razon de un archivo por cada cambio de foto.
        if vieja:
            try:
                storage.delete(vieja)
            except Exception as exc:  # pragma: no cover - borrar no debe tumbar el guardado
                import logging
                logging.getLogger('imagenes').warning(
                    'No se pudo borrar la miniatura anterior "%s": %s', vieja, exc,
                )
        return True

    @property
    def imagen_preview(self):
        """
        La imagen para MOSTRAR: miniatura si existe, original si no.

        Un solo lugar decide, para que la grilla del portal, la lista del POS y
        el punto de venta no se contradigan.
        """
        return self.imagen_miniatura or self.imagen or None

    @property
    def stock_actual(self):
        """Retorna el stock actual del producto sumando todos los lotes activos"""
        from apps.inventario.models import Lote
        return Lote.objects.filter(
            producto=self,
            activo=True
        ).aggregate(
            total=models.Sum('cantidad_actual')
        )['total'] or 0
    
    @property
    def necesita_reposicion(self):
        """Retorna True si el stock está por debajo del mínimo"""
        return self.stock_actual <= self.stock_minimo
    
    @property
    def valuacion_fifo(self):
        """Retorna la valuación FIFO del inventario actual del producto"""
        from apps.inventario.models import Lote
        lotes = Lote.objects.filter(
            producto=self,
            cantidad_actual__gt=0,
            activo=True
        )
        return sum(lote.cantidad_actual * lote.costo_unitario for lote in lotes)
    
    @property
    def es_codigo_interno(self):
        """Retorna True si el código de barras fue generado internamente"""
        return self.codigo_barras and self.codigo_barras.startswith('RP-')

    def imprimir_etiqueta(self, cantidad=1):
        """
        Imprime etiqueta(s) del producto usando impresora Zebra
        
        Args:
            cantidad: Número de etiquetas a imprimir
        
        Returns:
            dict con resultado de la impresión
        """
        from utils.impresoras.zebra import imprimir_etiqueta_producto
        return imprimir_etiqueta_producto(self, cantidad)


    @classmethod
    def generar_sku(cls):
        """Genera SKU secuencial: PROD-0001, PROD-0002, etc."""
        ultimo = cls.objects.order_by('-id').first()
        siguiente_num = (ultimo.id + 1) if ultimo else 1
        return f"PROD-{siguiente_num:04d}"
