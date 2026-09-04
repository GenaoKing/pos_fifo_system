"""
Modelos para el sistema de auditoría
apps/auditoria/models.py
"""
from django.db import models
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericForeignKey
from django.conf import settings
from django.utils import timezone


class AuditoriaInmutable(RuntimeError):
    """Se intento modificar o borrar un hecho ya registrado."""


class AuditoriaQuerySet(models.QuerySet):
    """
    QuerySet que no deja reescribir la historia (AUD-002).

    `update()` y `delete()` estaban disponibles sin barrera: una fila
    `CREATE / Original` se pudo cambiar por ORM a `DELETE / Alterado` y luego
    eliminar, sin impedimento. La misma cuenta de alto privilegio que ejecuta
    una accion podia borrar su rastro.

    La purga por retencion no pasa por aca: usa `purgar_hasta()`, que es
    explicito, deja constancia del propio borrado y no se invoca por accidente
    desde una vista.
    """

    def update(self, *args, **kwargs):
        raise AuditoriaInmutable(
            'El historial de auditoria es append-only: no se puede actualizar.'
        )

    def delete(self, *args, **kwargs):
        raise AuditoriaInmutable(
            'El historial de auditoria es append-only: no se puede borrar. '
            'Para retencion, usar Auditoria.objects.purgar_hasta().'
        )

    def _borrar_de_verdad(self):
        """Escotilla interna de la purga controlada."""
        return super().delete()


class AuditoriaManager(models.Manager.from_queryset(AuditoriaQuerySet)):
    def purgar_hasta(self, fecha_corte, *, motivo, usuario=None):
        """
        Purga controlada por retencion.

        Es el UNICO camino de borrado, y deja constancia de si mismo: registra
        cuantas filas se eliminaron, hasta que fecha y por que. Un historial que
        se puede vaciar sin dejar rastro del vaciado no es un historial.
        """
        modelo = self.model
        # Se fijan los ids ANTES de registrar la constancia: si se filtrara por
        # fecha despues, la propia fila de la purga —creada ahora, y por tanto
        # anterior a un corte futuro— se borraria a si misma.
        objetivo_ids = list(
            self.filter(fecha_hora__lt=fecha_corte).values_list('pk', flat=True)
        )
        cantidad = len(objetivo_ids)

        modelo.registrar(
            accion=modelo.TipoAccion.AUDITORIA_PURGADA,
            descripcion=(
                f'Purga de auditoria: {cantidad} registros anteriores a '
                f'{fecha_corte.isoformat()}. Motivo: {motivo}'
            ),
            usuario=usuario,
            nivel_importancia=modelo.NivelImportancia.CRITICA,
            metadata={
                'registros_eliminados': cantidad,
                'fecha_corte': fecha_corte.isoformat(),
                'motivo': motivo,
            },
        )
        self.filter(pk__in=objetivo_ids)._borrar_de_verdad()
        return cantidad


class Auditoria(models.Model):
    """
    Registro de auditoría para todas las acciones críticas del sistema.
    Permite trazabilidad completa de quién hizo qué, cuándo y desde dónde.

    from apps.auditoria.models import Auditoria, get_client_ip

    # Acción simple
    Auditoria.registrar(
        accion=Auditoria.TipoAccion.CREAR,
        descripcion="Producto creado: Coca Cola 500ml",
        usuario=request.user,
        ip_address=get_client_ip(request)
    )

    # Acción con objeto relacionado
    Auditoria.registrar(
        accion=Auditoria.TipoAccion.EDITAR,
        descripcion=f"Precio modificado: {producto.nombre}",
        usuario=request.user,
        content_object=producto,
        datos_anteriores={'precio': '10.00'},
        datos_nuevos={'precio': '12.00'},
        ip_address=get_client_ip(request),
        nivel_importancia='ALTA'
    )

    # Registrar error
    Auditoria.registrar_error(
        descripcion="Error al procesar pago",
        usuario=request.user,
        detalle_error=str(exception),
        nivel_importancia='CRITICA'
    )
    """

    class TipoAccion(models.TextChoices):
        # Autenticación
        LOGIN = 'LOGIN', 'Inicio de sesión'
        LOGOUT = 'LOGOUT', 'Cierre de sesión'
        INTENTO_LOGIN_FALLIDO = 'LOGIN_FAIL', 'Intento de login fallido'

        # CRUD Genérico
        CREAR = 'CREATE', 'Creación'
        EDITAR = 'UPDATE', 'Edición'
        ELIMINAR = 'DELETE', 'Eliminación'
        VER = 'VIEW', 'Visualización'

        # Productos
        PRODUCTO_CREADO = 'PROD_CREATE', 'Producto creado'
        PRODUCTO_EDITADO = 'PROD_UPDATE', 'Producto editado'
        PRODUCTO_ELIMINADO = 'PROD_DELETE', 'Producto eliminado'
        PRECIO_MODIFICADO = 'PRECIO_UPDATE', 'Precio modificado'

        # Inventario
        COMPRA_REGISTRADA = 'COMPRA_CREATE', 'Compra registrada'
        LOTE_CREADO = 'LOTE_CREATE', 'Lote creado'
        AJUSTE_INVENTARIO = 'AJUSTE_INV', 'Ajuste de inventario'

        # Ventas
        VENTA_CREADA = 'VENTA_CREATE', 'Venta realizada'
        VENTA_ANULADA = 'VENTA_CANCEL', 'Venta anulada'
        TICKET_IMPRESO = 'TICKET_PRINT', 'Ticket impreso'
        TICKET_REIMPRESO = 'TICKET_REPRINT', 'Ticket reimpreso'
        DESCUENTO_AUTORIZADO = 'DESC_AUTH', 'Descuento autorizado'
        RECIBO_CXC_IMPRESO = 'RECIBO_CXC_PRINT', 'Recibo CxC impreso'
        TEST_IMPRESORA = 'PRINTER_TEST', 'Prueba de impresora'

        # Usuarios
        USUARIO_CREADO = 'USER_CREATE', 'Usuario creado'
        USUARIO_MODIFICADO = 'USER_UPDATE', 'Usuario modificado'
        USUARIO_ACTIVADO = 'USER_ENABLE', 'Usuario activado'
        USUARIO_DESACTIVADO = 'USER_DISABLE', 'Usuario desactivado'
        PERMISO_ASIGNADO = 'PERM_ASSIGN', 'Permiso asignado'
        PERMISO_REVOCADO = 'PERM_REVOKE', 'Permiso revocado'

        # Sistema
        BACKUP_CREADO = 'BACKUP_CREATE', 'Backup creado'
        BACKUP_RESTAURADO = 'BACKUP_RESTORE', 'Backup restaurado'
        CONFIGURACION = 'CONFIG', 'Configuración modificada'
        ERROR_SISTEMA = 'ERROR', 'Error del sistema'
        CIERRE_DIARIO = 'CIERRE_DIARIO', 'Resumen diario generado'
        AUDITORIA_PURGADA = 'AUDIT_PURGE', 'Auditoria purgada por retencion'

    # === APPEND-ONLY ===
    objects = AuditoriaManager()

    # === QUIÉN realizó la acción ===
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='acciones_auditoria',
        verbose_name='Usuario',
        help_text='Usuario que realizó la acción (null si fue el sistema)'
    )

    # === QUÉ acción se realizó ===
    accion = models.CharField(
        max_length=20,
        choices=TipoAccion.choices,
        verbose_name='Acción',
        db_index=True
    )

    descripcion = models.TextField(
        verbose_name='Descripción',
        help_text='Descripción detallada de la acción'
    )

    # === SOBRE QUÉ se realizó la acción (relación genérica) ===
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Tipo de objeto'
    )

    object_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='ID del objeto'
    )

    content_object = GenericForeignKey('content_type', 'object_id')

    # === CONTEXTO ADICIONAL ===
    datos_anteriores = models.JSONField(
        null=True,
        blank=True,
        verbose_name='Datos anteriores',
        help_text='Estado del objeto antes de la modificación'
    )

    datos_nuevos = models.JSONField(
        null=True,
        blank=True,
        verbose_name='Datos nuevos',
        help_text='Estado del objeto después de la modificación'
    )

    metadata = models.JSONField(
        null=True,
        blank=True,
        verbose_name='Metadata adicional',
        help_text='Información adicional relevante'
    )

    # === CUÁNDO se realizó ===
    # No usa `auto_now_add`: ese flag reescribe el campo con un `timezone.now()`
    # nuevo DENTRO de `super().save()`, DESPUES de que `save()` ya firmo el
    # hash con el valor anterior (abajo). El instante firmado y el persistido
    # diferian por microsegundos, asi que `integridad_ok()` de un registro
    # recien creado daba False. En Windows el reloj es lo bastante grueso para
    # que ambos `now()` coincidan y el bug quedaba oculto; en el Linux de CI
    # divergian siempre. Con `default` el valor se fija una sola vez, antes de
    # firmar, y es el mismo que llega a la base.
    fecha_hora = models.DateTimeField(
        default=timezone.now,
        editable=False,
        verbose_name='Fecha y hora',
        db_index=True
    )

    # === DÓNDE se realizó (información del cliente) ===
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name='Dirección IP'
    )

    user_agent = models.TextField(
        blank=True,
        verbose_name='User Agent',
        help_text='Navegador y sistema operativo'
    )

    # === RESULTADO ===
    exito = models.BooleanField(
        default=True,
        verbose_name='Acción exitosa',
        help_text='Indica si la acción se completó con éxito'
    )

    mensaje_error = models.TextField(
        blank=True,
        verbose_name='Mensaje de error',
        help_text='Detalle del error si la acción falló'
    )

    sucursal = models.ForeignKey(
        'sucursales.Sucursal',
        on_delete=models.SET_NULL,
        related_name='registros_auditoria',
        verbose_name='Sucursal',
        blank=True,
        null=True,
        help_text='Sucursal donde se genero este registro'
    )

    # === SNAPSHOT DEL ACTOR (AUD-003) ===
    #
    # La FK sola no sirve como evidencia: es mutable y se anula al borrar al
    # usuario. Renombrar `audit_admin` a `actor_renombrado` cambiaba como se
    # presentaba un hecho de hace meses, y una baja convertia una accion humana
    # en "Sistema", indistinguible de un job automatico. Estos campos se
    # congelan al crear el registro y no vuelven a tocarse.
    actor_username = models.CharField(
        'Usuario (snapshot)',
        max_length=150,
        blank=True,
        help_text='Username tal como existia al momento del hecho.',
    )

    actor_nombre = models.CharField(
        'Nombre (snapshot)',
        max_length=300,
        blank=True,
        help_text='Nombre visible tal como existia al momento del hecho.',
    )

    ACTOR_USUARIO = 'USUARIO'
    ACTOR_SISTEMA = 'SISTEMA'
    ACTOR_TIPOS = [
        (ACTOR_USUARIO, 'Usuario'),
        (ACTOR_SISTEMA, 'Sistema'),
    ]

    actor_tipo = models.CharField(
        'Tipo de actor',
        max_length=10,
        choices=ACTOR_TIPOS,
        default=ACTOR_SISTEMA,
        help_text=(
            'USUARIO = lo hizo una persona (aunque su cuenta ya no exista). '
            'SISTEMA = lo hizo un proceso automatico.'
        ),
    )

    # === INTEGRIDAD (AUD-002) ===
    hash_integridad = models.CharField(
        'Hash de integridad',
        max_length=64,
        blank=True,
        editable=False,
        help_text=(
            'SHA-256 de los campos inmutables del registro. Permite detectar '
            'una alteracion hecha por fuera de la aplicacion.'
        ),
    )

    # === IMPORTANCIA ===
    class NivelImportancia(models.TextChoices):
        BAJA = 'BAJA', 'Baja'
        MEDIA = 'MEDIA', 'Media'
        ALTA = 'ALTA', 'Alta'
        CRITICA = 'CRITICA', 'Crítica'

    nivel_importancia = models.CharField(
        max_length=10,
        choices=NivelImportancia.choices,
        default=NivelImportancia.MEDIA,
        verbose_name='Nivel de importancia',
        db_index=True
    )

    # === METADATA ===
    class Meta:
        verbose_name = 'Registro de auditoría'
        verbose_name_plural = 'Registros de auditoría'
        ordering = ['-fecha_hora']
        indexes = [
            models.Index(fields=['usuario', '-fecha_hora']),
            models.Index(fields=['accion', '-fecha_hora']),
            models.Index(fields=['content_type', 'object_id']),
            models.Index(fields=['-fecha_hora']),
            models.Index(fields=['nivel_importancia', '-fecha_hora']),
            models.Index(fields=['exito', '-fecha_hora']),
            models.Index(fields=['sucursal', '-fecha_hora']),
        ]

    # === APPEND-ONLY + SNAPSHOT + INTEGRIDAD ===

    CAMPOS_FIRMADOS = (
        'accion', 'descripcion', 'actor_username', 'actor_tipo',
        'object_id', 'exito', 'nivel_importancia',
    )

    def calcular_hash(self):
        """SHA-256 sobre los campos que no deben cambiar nunca."""
        import hashlib
        from datetime import timezone as dt_timezone

        partes = [str(getattr(self, campo) or '') for campo in self.CAMPOS_FIRMADOS]
        # La fecha se firma en UTC y con precision fija: asi el hash no depende
        # de como cada driver o zona horaria represente el MISMO instante al
        # releerlo de la base. `isoformat()` crudo variaba el sufijo de offset
        # entre psycopg y el valor en memoria, y eso rompia la verificacion en
        # CI aunque la fila no se hubiera tocado.
        if self.fecha_hora:
            dt = self.fecha_hora
            if timezone.is_aware(dt):
                dt = dt.astimezone(dt_timezone.utc)
            partes.append(dt.strftime('%Y-%m-%dT%H:%M:%S.%f'))
        else:
            partes.append('')
        partes.append(str(self.content_type_id or ''))
        partes.append(str(self.usuario_id or ''))
        return hashlib.sha256('|'.join(partes).encode('utf-8')).hexdigest()

    def integridad_ok(self):
        """False si la fila fue alterada por fuera de la aplicacion."""
        if not self.hash_integridad:
            # Registro anterior al cambio: no se puede afirmar ni negar.
            return None
        return self.hash_integridad == self.calcular_hash()

    def _congelar_actor(self):
        """Copia la identidad del actor al momento del hecho."""
        if self.usuario_id and self.usuario is not None:
            self.actor_tipo = self.ACTOR_USUARIO
            if not self.actor_username:
                self.actor_username = self.usuario.username or ''
            if not self.actor_nombre:
                nombre = ''
                if hasattr(self.usuario, 'get_full_name'):
                    nombre = self.usuario.get_full_name() or ''
                self.actor_nombre = nombre or self.actor_username
        elif self.actor_username:
            # Actor humano cuya cuenta ya no existe (o que no es un Usuario del
            # POS). Sigue siendo una persona, no el sistema.
            self.actor_tipo = self.ACTOR_USUARIO
        else:
            self.actor_tipo = self.ACTOR_SISTEMA

    def save(self, *args, **kwargs):
        """
        Solo permite INSERT.

        El modelo no sobrescribia `save()` ni `delete()`, asi que un hecho ya
        registrado se podia reescribir entero (AUD-002).
        """
        if self.pk is not None and not self._state.adding:
            raise AuditoriaInmutable(
                'Un registro de auditoria no se puede modificar despues de creado.'
            )

        if self.fecha_hora is None:
            from django.utils import timezone as _tz
            self.fecha_hora = _tz.now()

        self._congelar_actor()
        self.hash_integridad = self.calcular_hash()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditoriaInmutable(
            'Un registro de auditoria no se puede borrar. Para retencion, usar '
            'Auditoria.objects.purgar_hasta().'
        )

    @property
    def actor_display(self):
        """
        Como se presenta el actor, sin inventar.

        Antes un `usuario_id` nulo se mostraba literalmente como "Sistema",
        igual que un evento realmente automatico: no habia forma de distinguir
        una accion humana cuyo autor fue dado de baja de un job del sistema.
        """
        if self.actor_tipo == self.ACTOR_SISTEMA and not self.actor_username:
            return 'Sistema'
        if self.usuario_id and self.usuario is not None:
            return self.actor_nombre or self.actor_username or self.usuario.username
        if self.actor_username:
            return f'{self.actor_nombre or self.actor_username} (cuenta eliminada)'
        return 'Sistema'

    def __str__(self):
        from django.utils import timezone as _tz

        momento = _tz.localtime(self.fecha_hora) if self.fecha_hora else None
        cuando = momento.strftime('%d/%m/%Y %H:%M') if momento else 's/f'
        return f"{self.get_accion_display()} - {self.actor_display} - {cuando}"

    # === MÉTODOS ESTÁTICOS PARA REGISTRAR ACCIONES ===

    @staticmethod
    def derivar_sucursal(objeto):
        """
        Sucursal de un objeto de dominio, siguiendo las rutas conocidas.

        `registrar_venta`, `registrar_anulacion_venta` y
        `registrar_ajuste_inventario` no recibian ni derivaban sucursal, aunque
        `Venta`, `Lote` y `Ajuste` ya la conocen: el registro quedaba con
        `sucursal_id = NULL` y el historial no se podia filtrar por tienda
        (AUD-004). Confiar en que cada caller la recuerde es justamente lo que
        no funciono; esta es la regla unica.
        """
        if objeto is None:
            return None

        directa = getattr(objeto, 'sucursal', None)
        if directa is not None:
            return directa

        # Rutas indirectas conocidas: ajuste -> lote, pago -> venta,
        # abono -> cuenta, movimiento -> turno -> caja.
        for camino in (
            ('lote', 'sucursal'),
            ('venta', 'sucursal'),
            ('cuenta', 'sucursal'),
            ('compra', 'sucursal'),
            ('caja', 'sucursal'),
            ('turno', 'caja', 'sucursal'),
        ):
            actual = objeto
            for paso in camino:
                actual = getattr(actual, paso, None)
                if actual is None:
                    break
            if actual is not None:
                return actual

        return None

    @classmethod
    def registrar(cls, accion, descripcion, usuario=None, **kwargs):
        """
        Método principal para registrar cualquier acción en el sistema.

        Args:
            accion: TipoAccion (ej: Auditoria.TipoAccion.VENTA_CREADA)
            descripcion: str - Descripción de la acción
            usuario: Usuario - Usuario que realizó la acción (opcional)
            **kwargs: Argumentos adicionales
                - content_object: Objeto relacionado
                - datos_anteriores: dict con datos anteriores
                - datos_nuevos: dict con datos nuevos
                - metadata: dict con información adicional
                - ip_address: str con IP
                - user_agent: str con user agent
                - nivel_importancia: str (BAJA, MEDIA, ALTA, CRITICA)
                - exito: bool
                - mensaje_error: str

        Returns:
            Auditoria: Instancia creada

        Ejemplo:
            from apps.auditoria.models import Auditoria

            Auditoria.registrar(
                accion=Auditoria.TipoAccion.VENTA_CREADA,
                descripcion=f'Venta #{venta.numero_venta} por ${venta.total}',
                usuario=request.user,
                content_object=venta,
                ip_address=get_client_ip(request),
                nivel_importancia='ALTA'
            )
        """
        return cls.objects.create(
            accion=accion,
            descripcion=descripcion,
            usuario=usuario,
            **kwargs
        )

    @classmethod
    def registrar_login(cls, usuario, ip_address=None, user_agent=None, exito=True):
        """Registra un intento de login"""
        accion = cls.TipoAccion.LOGIN if exito else cls.TipoAccion.INTENTO_LOGIN_FALLIDO
        nivel = cls.NivelImportancia.MEDIA if exito else cls.NivelImportancia.ALTA

        return cls.registrar(
            accion=accion,
            descripcion=f"{'Login exitoso' if exito else 'Login fallido'} - {usuario.username if usuario else 'Usuario desconocido'}",
            usuario=usuario if exito else None,
            ip_address=ip_address,
            user_agent=user_agent,
            nivel_importancia=nivel,
            exito=exito
        )

    @classmethod
    def registrar_logout(cls, usuario, ip_address=None):
        """Registra un logout"""
        return cls.registrar(
            accion=cls.TipoAccion.LOGOUT,
            descripcion=f"Cierre de sesión - {usuario.username}",
            usuario=usuario,
            ip_address=ip_address,
            nivel_importancia=cls.NivelImportancia.BAJA
        )

    @classmethod
    def registrar_venta(cls, venta, usuario, ip_address=None):
        """Registra la creación de una venta"""
        return cls.registrar(
            accion=cls.TipoAccion.VENTA_CREADA,
            descripcion=f"Venta #{venta.numero_venta} - Total: ${venta.total} - Items: {venta.detalles.count()}",
            usuario=usuario,
            content_object=venta,
            datos_nuevos={
                'numero_venta': venta.numero_venta,
                'total': str(venta.total),
                'subtotal': str(venta.subtotal),
                'descuento_total': str(venta.descuento_total),
                'cantidad_items': venta.detalles.count(),
            },
            ip_address=ip_address,
            sucursal=cls.derivar_sucursal(venta),
            nivel_importancia=cls.NivelImportancia.MEDIA
        )

    @classmethod
    def registrar_anulacion_venta(cls, venta, usuario, motivo, ip_address=None):
        """Registra la anulación de una venta"""
        return cls.registrar(
            accion=cls.TipoAccion.VENTA_ANULADA,
            descripcion=f"Venta #{venta.numero_venta} anulada - Motivo: {motivo}",
            usuario=usuario,
            content_object=venta,
            datos_anteriores={
                'estado': venta.estado,
                'total': str(venta.total),
            },
            metadata={'motivo': motivo},
            ip_address=ip_address,
            sucursal=cls.derivar_sucursal(venta),
            nivel_importancia=cls.NivelImportancia.CRITICA
        )

    @classmethod
    def registrar_reimpresion_ticket(cls, venta, usuario, ip_address=None):
        """Registra la reimpresión de un ticket"""
        return cls.registrar(
            accion=cls.TipoAccion.TICKET_REIMPRESO,
            descripcion=f"Ticket reimpreso - Venta #{venta.numero_venta}",
            usuario=usuario,
            content_object=venta,
            ip_address=ip_address,
            sucursal=cls.derivar_sucursal(venta),
            nivel_importancia=cls.NivelImportancia.MEDIA
        )

    @classmethod
    def registrar_ajuste_inventario(cls, ajuste, usuario, ip_address=None):
        """Registra un ajuste de inventario"""
        return cls.registrar(
            accion=cls.TipoAccion.AJUSTE_INVENTARIO,
            descripcion=f"Ajuste de inventario - {ajuste.get_tipo_display()} - Producto: {ajuste.lote.producto.nombre}",
            usuario=usuario,
            content_object=ajuste,
            datos_nuevos={
                'tipo': ajuste.tipo,
                'cantidad': ajuste.cantidad,
                'lote': str(ajuste.lote),
                'motivo': ajuste.motivo,
            },
            ip_address=ip_address,
            sucursal=cls.derivar_sucursal(ajuste),
            nivel_importancia=cls.NivelImportancia.ALTA
        )

    @classmethod
    def registrar_compra(cls, compra, usuario, ip_address=None):
        """Registra una compra y la creación de lotes"""
        return cls.registrar(
            accion=cls.TipoAccion.COMPRA_REGISTRADA,
            descripcion=f"Compra registrada - {compra.detalles.count()} productos - Total: ${compra.total}",
            usuario=usuario,
            content_object=compra,
            datos_nuevos={
                'proveedor': compra.proveedor,
                'total': str(compra.total),
                'cantidad_productos': compra.detalles.count(),
            },
            ip_address=ip_address,
            nivel_importancia=cls.NivelImportancia.MEDIA
        )

    @classmethod
    def registrar_cambio_precio(cls, producto, precio_anterior, precio_nuevo, usuario, ip_address=None):
        """Registra un cambio de precio de producto"""
        return cls.registrar(
            accion=cls.TipoAccion.PRECIO_MODIFICADO,
            descripcion=f"Precio modificado - {producto.nombre}: ${precio_anterior} → ${precio_nuevo}",
            usuario=usuario,
            content_object=producto,
            datos_anteriores={'precio_venta': str(precio_anterior)},
            datos_nuevos={'precio_venta': str(precio_nuevo)},
            ip_address=ip_address,
            nivel_importancia=cls.NivelImportancia.ALTA
        )

    @classmethod
    def registrar_error(cls, descripcion, usuario=None, detalle_error=None, nivel_importancia='ALTA'):
        """Registra un error del sistema"""
        return cls.registrar(
            accion=cls.TipoAccion.ERROR_SISTEMA,
            descripcion=descripcion,
            usuario=usuario,
            exito=False,
            mensaje_error=detalle_error or '',
            nivel_importancia=nivel_importancia
        )

    # === MÉTODOS DE CONSULTA ===

    @classmethod
    def obtener_acciones_usuario(cls, usuario, limite=50):
        """Obtiene las últimas acciones de un usuario"""
        return cls.objects.filter(usuario=usuario).order_by('-fecha_hora')[:limite]

    @classmethod
    def obtener_acciones_objeto(cls, obj):
        """Obtiene todas las acciones relacionadas con un objeto"""
        content_type = ContentType.objects.get_for_model(obj)
        return cls.objects.filter(
            content_type=content_type,
            object_id=obj.pk
        ).order_by('-fecha_hora')

    @classmethod
    def obtener_acciones_criticas(cls, dias=7):
        """Obtiene acciones críticas de los últimos N días"""
        from django.utils import timezone
        from datetime import timedelta

        fecha_inicio = timezone.now() - timedelta(days=dias)
        return cls.objects.filter(
            nivel_importancia=cls.NivelImportancia.CRITICA,
            fecha_hora__gte=fecha_inicio
        ).order_by('-fecha_hora')

    @classmethod
    def obtener_intentos_login_fallidos(cls, dias=1):
        """Obtiene intentos de login fallidos"""
        from django.utils import timezone
        from datetime import timedelta

        fecha_inicio = timezone.now() - timedelta(days=dias)
        return cls.objects.filter(
            accion=cls.TipoAccion.INTENTO_LOGIN_FALLIDO,
            fecha_hora__gte=fecha_inicio
        ).order_by('-fecha_hora')

    @classmethod
    def obtener_estadisticas_periodo(cls, fecha_inicio, fecha_fin):
        """Obtiene estadísticas de auditoría para un período"""
        from django.db.models import Count

        registros = cls.objects.filter(
            fecha_hora__range=[fecha_inicio, fecha_fin]
        )

        return {
            'total_acciones': registros.count(),
            'acciones_por_tipo': registros.values('accion').annotate(
                count=Count('id')
            ).order_by('-count'),
            'acciones_por_usuario': registros.values('usuario__username').annotate(
                count=Count('id')
            ).order_by('-count'),
            'acciones_criticas': registros.filter(
                nivel_importancia=cls.NivelImportancia.CRITICA
            ).count(),
            'errores': registros.filter(exito=False).count(),
        }


# === FUNCIONES HELPER ===

def get_client_ip(request):
    """
    Direccion IP del cliente, atribuida solo a lo que se puede comprobar.

    `X-Forwarded-For` lo puede enviar cualquiera. La version anterior lo
    prefería siempre que estuviera presente, asi que la IP registrada en la
    auditoria era, literalmente, la que el cliente decidiera declarar: un
    atacante escribia la IP de otra persona en su propio rastro (AUD-011).

    Ahora solo se lee detras de un proxy DECLARADO, mediante
    `AUDITORIA_CONFIAR_EN_PROXY = True` en settings. Ese flag es una afirmacion
    de despliegue: "hay un proxy delante que reescribe la cabecera y descarta la
    del cliente". Sin el, se usa `REMOTE_ADDR`, que el cliente no controla.

    Cuando el flag esta activo se toma la ULTIMA entrada de la cadena, no la
    primera: las anteriores las pudo haber puesto el cliente; la ultima la
    agrego el proxy de confianza.
    """
    from django.conf import settings

    if getattr(settings, 'AUDITORIA_CONFIAR_EN_PROXY', False):
        cadena = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if cadena:
            partes = [p.strip() for p in cadena.split(',') if p.strip()]
            if partes:
                return partes[-1]

    return request.META.get('REMOTE_ADDR')


def get_user_agent(request):
    """
    Obtiene el User Agent del cliente desde el request.

    Args:
        request: HttpRequest

    Returns:
        str: User Agent
    """
    return request.META.get('HTTP_USER_AGENT', '')