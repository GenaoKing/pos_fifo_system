"""
Modelos para el sistema de auditoría
apps/auditoria/models.py
"""
from django.db import models
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericForeignKey
from django.conf import settings


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
    fecha_hora = models.DateTimeField(
        auto_now_add=True,
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
    
    def __str__(self):
        usuario_str = self.usuario.username if self.usuario else 'Sistema'
        return f"{self.get_accion_display()} - {usuario_str} - {self.fecha_hora.strftime('%d/%m/%Y %H:%M')}"
    
    # === MÉTODOS ESTÁTICOS PARA REGISTRAR ACCIONES ===
    
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
    Obtiene la dirección IP del cliente desde el request.
    
    Args:
        request: HttpRequest
    
    Returns:
        str: Dirección IP
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def get_user_agent(request):
    """
    Obtiene el User Agent del cliente desde el request.
    
    Args:
        request: HttpRequest
    
    Returns:
        str: User Agent
    """
    return request.META.get('HTTP_USER_AGENT', '')