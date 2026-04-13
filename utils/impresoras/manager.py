"""
Print Manager - Gestor Centralizado de Impresión
Sistema POS FIFO - Módulo de Impresión

Este módulo coordina toda la lógica de impresión del sistema,
incluyendo manejo de errores, auditoría y preparación de datos.

Autor: Sistema POS FIFO
Versión: 1.0
"""

import logging
from datetime import datetime
from django.conf import settings
from django.db import transaction
import pytz

# Importación condicional para evitar errores circulares
from apps.auditoria.models import Auditoria

logger = logging.getLogger(__name__)


class PrintManagerException(Exception):
    """Excepción para errores del Print Manager"""
    pass


class PrintManager:
    """
    Gestor centralizado de impresión
    
    Responsabilidades:
    - Preparación de datos de venta para impresión
    - Coordinación con el driver de impresora
    - Registro de auditoría de impresiones
    - Manejo de reimpresiones
    - Gestión de errores
    
    Uso:
        manager = PrintManager()
        manager.print_ticket_venta(venta, usuario)
    """
    
    def __init__(self):
        """Inicializa el Print Manager"""
        self.enabled = settings.THERMAL_PRINTER.get('ENABLED', False)
        
        # Importar el printer solo si está habilitado
        if self.enabled:
            try:
                from utils.impresoras.termica import ThermalPrinter2Connect
                self.printer_class = ThermalPrinter2Connect
            except ImportError as e:
                logger.error(f"No se pudo importar ThermalPrinter2Connect: {str(e)}")
                self.enabled = False
                self.printer_class = None
        else:
            self.printer_class = None
    
    def print_ticket_venta(self, venta, usuario, reimpresion=False):
        """
        Imprime ticket de una venta
        
        Args:
            venta: Instancia del modelo Venta
            usuario: Usuario que solicita la impresión
            reimpresion (bool): Indica si es una reimpresión
            
        Returns:
            dict: Resultado de la operación
                {
                    'success': bool,
                    'mensaje': str,
                    'error': str (opcional)
                }
        """
        # Verificar si el sistema está habilitado
        if not self.enabled:
            return {
                'success': False,
                'mensaje': 'Sistema de impresión deshabilitado',
                'error': 'DISABLED'
            }
        
        # Preparar datos para impresión
        try:
            venta_data = self._prepare_venta_data(venta)
        except Exception as e:
            error_msg = f"Error preparando datos de venta: {str(e)}"
            logger.error(error_msg)
            return {
                'success': False,
                'mensaje': error_msg,
                'error': 'DATA_PREPARATION_ERROR'
            }
        
        # Intentar imprimir
        try:
            printer = self.printer_class()
            printer.print_ticket(venta_data)
            
            # Registrar en auditoría
            self._registrar_auditoria_impresion(
                venta=venta,
                usuario=usuario,
                reimpresion=reimpresion,
                exitosa=True
            )
            
            mensaje = "Ticket reimpreso exitosamente" if reimpresion else "Ticket impreso exitosamente"
            logger.info(f"✓ {mensaje}: {venta.numero_venta}")
            
            return {
                'success': True,
                'mensaje': mensaje
            }
            
        except Exception as e:
            error_msg = f"Error imprimiendo ticket: {str(e)}"
            logger.error(error_msg)
            
            # Registrar error en auditoría
            self._registrar_auditoria_impresion(
                venta=venta,
                usuario=usuario,
                reimpresion=reimpresion,
                exitosa=False,
                error=str(e)
            )
            
            return {
                'success': False,
                'mensaje': 'No se pudo imprimir el ticket',
                'error': str(e)
            }
    
    def test_printer(self):
        """
        Ejecuta una prueba de impresión
        
        Returns:
            dict: Resultado de la prueba
        """
        if not self.enabled:
            return {
                'success': False,
                'mensaje': 'Sistema de impresión deshabilitado'
            }
        
        try:
            printer = self.printer_class()
            result = printer.print_test_page()
            
            if result:
                return {
                    'success': True,
                    'mensaje': 'Prueba de impresión exitosa'
                }
            else:
                return {
                    'success': False,
                    'mensaje': 'Prueba de impresión falló'
                }
        except Exception as e:
            return {
                'success': False,
                'mensaje': f'Error en prueba: {str(e)}'
            }
    
    def _prepare_venta_data(self, venta):
        """
        Prepara el diccionario de datos para impresión
        
        Args:
            venta: Instancia del modelo Venta
            
        Returns:
            dict: Datos formateados para el printer
        """
        # Obtener detalles de la venta
        detalles = venta.detalles.select_related('producto').all()
        
        # Preparar items
        items = []
        for detalle in detalles:
            items.append({
                'producto': detalle.producto.nombre,
                'cantidad': detalle.cantidad,
                'precio_unit': float(detalle.precio_unitario),
                'descuento': float(detalle.descuento_monto or 0),
                'subtotal': float(detalle.subtotal - (detalle.descuento_monto or 0))
            })
        
        # Obtener pagos
        pagos_lista = []
        tiene_efectivo = False
        
        pagos = venta.pagos.all()
        for pago in pagos:
            # Verificar si hay pago en efectivo
            if pago.metodo in ['EFECTIVO', 'MIXTO']:
                tiene_efectivo = True
            
            pagos_lista.append({
                'metodo': pago.get_metodo_display(),
                'monto': float(pago.monto)
            })
        
        # Calcular cambio si aplica
        if tiene_efectivo:
            total_pagado = sum(p['monto'] for p in pagos_lista)
            cambio = total_pagado - float(venta.total)
            
            if cambio > 0:
                pagos_lista.append({
                    'metodo': 'Cambio',
                    'monto': -cambio  # Negativo para que se muestre correctamente
                })
        
        # Construir diccionario completo
        santo_domingo_tz = pytz.timezone('America/Santo_Domingo')
        fecha_local = venta.fecha_venta.astimezone(santo_domingo_tz)
        venta_data = {
            'numero_venta': venta.numero_venta,
            'fecha': fecha_local.strftime('%d/%m/%Y %I:%M %p'),
            'cajero': venta.usuario.get_full_name() or venta.usuario.username,
            'items': items,
            'subtotal': float(venta.subtotal),
            'descuento_total': float(venta.descuento_total or 0),
            'total': float(venta.total),
            'pagos': pagos_lista,
            'tiene_efectivo': tiene_efectivo
        }
        
        # Agregar cliente si existe
        if hasattr(venta, 'cliente') and venta.cliente:
            venta_data['cliente'] = venta.cliente.nombre
        
        return venta_data
    
    @transaction.atomic
    def _registrar_auditoria_impresion(self, venta, usuario, reimpresion, exitosa, error=None):
        """
        Registra el evento de impresión en auditoría
        
        Args:
            venta: Instancia de Venta
            usuario: Usuario que imprime
            reimpresion (bool): Si es reimpresión
            exitosa (bool): Si la impresión fue exitosa
            error (str): Mensaje de error si aplica
        """
        try:
            accion = 'ERROR_IMPRESION' if not exitosa else 'IMPRESION_TICKET'
            
            detalles = {
                'numero_venta': venta.numero_venta,
                'reimpresion': reimpresion,
                'total': float(venta.total),
                'fecha_venta': venta.fecha_venta.isoformat(),
            }
            
            if error:
                detalles['error'] = error
            
            Auditoria.objects.create(
                usuario=usuario,
                accion=accion,
                detalles=detalles
            )
            
            logger.debug(f"Auditoría registrada: {accion} - {venta.numero_venta}")
            
        except Exception as e:
            # No queremos que un error de auditoría afecte la impresión
            logger.error(f"Error registrando auditoría: {str(e)}")
    
    def get_ultimas_impresiones(self, usuario=None, limit=50):
        """
        Obtiene las últimas impresiones del sistema
        
        Args:
            usuario: Filtrar por usuario específico (opcional)
            limit (int): Cantidad de registros
            
        Returns:
            QuerySet: Registros de auditoría
        """
        filtros = {
            'accion__in': ['IMPRESION_TICKET', 'ERROR_IMPRESION']
        }
        
        if usuario:
            filtros['usuario'] = usuario
        
        return Auditoria.objects.filter(**filtros).order_by('-fecha')[:limit]
    
    def get_estadisticas_impresion(self, fecha_desde=None, fecha_hasta=None):
        """
        Obtiene estadísticas de impresión
        
        Args:
            fecha_desde (datetime): Fecha inicio
            fecha_hasta (datetime): Fecha fin
            
        Returns:
            dict: Estadísticas de impresión
        """
        filtros = {
            'accion__in': ['IMPRESION_TICKET', 'ERROR_IMPRESION']
        }
        
        if fecha_desde:
            filtros['fecha__gte'] = fecha_desde
        if fecha_hasta:
            filtros['fecha__lte'] = fecha_hasta
        
        registros = Auditoria.objects.filter(**filtros)
        
        total = registros.count()
        exitosas = registros.filter(accion='IMPRESION_TICKET').count()
        errores = registros.filter(accion='ERROR_IMPRESION').count()
        reimpresiones = registros.filter(
            accion='IMPRESION_TICKET',
            detalles__reimpresion=True
        ).count()
        
        return {
            'total': total,
            'exitosas': exitosas,
            'errores': errores,
            'reimpresiones': reimpresiones,
            'tasa_exito': (exitosas / total * 100) if total > 0 else 0
        }


# ============================================================================
# INSTANCIA SINGLETON
# ============================================================================

# Crear instancia única del PrintManager para usar en toda la aplicación
print_manager = PrintManager()


# ============================================================================
# FUNCIONES DE CONVENIENCIA
# ============================================================================

def imprimir_ticket(venta, usuario, reimpresion=False):
    """
    Función de conveniencia para imprimir tickets
    
    Args:
        venta: Instancia de Venta
        usuario: Usuario que imprime
        reimpresion (bool): Si es reimpresión
        
    Returns:
        dict: Resultado de la impresión
    """
    return print_manager.print_ticket_venta(venta, usuario, reimpresion)


def test_impresora():
    """
    Función de conveniencia para probar impresora
    
    Returns:
        dict: Resultado de la prueba
    """
    return print_manager.test_printer()
