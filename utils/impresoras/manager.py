"""
Print Manager - Gestor Centralizado de Impresión
Sistema POS FIFO - Módulo de Impresión

Coordina la lógica de impresión del sistema,
incluyendo manejo de errores, auditoría y preparación de datos.

Versión: 2.0 - Migrado a ConfiguracionNegocio
"""

import logging
import json
from datetime import datetime
from django.conf import settings
from django.db import transaction
import pytz

from apps.auditoria.models import Auditoria

logger = logging.getLogger(__name__)


class PrintManagerException(Exception):
    """Excepción para errores del Print Manager"""
    pass


def _is_printing_enabled():
    """
    Verifica si la impresión está habilitada desde ConfiguracionNegocio.
    Se llama en cada operación (no al importar) para que sea siempre fresco.
    Fallback a settings.THERMAL_PRINTER si la BD no está disponible.
    """
    try:
        from apps.configuracion.utils import modulo_activo
        return modulo_activo('impresion_termica')
    except Exception:
        # BD no disponible (primera migración, etc.)
        return settings.THERMAL_PRINTER.get('ENABLED', False)


def _obtener_ecf_para_ticket(venta):
    """
    Recupera el ECF original (tipo 31 o 32) mas reciente de la venta.
    Retorna None si el modulo e-CF no esta activo o si la venta aun no
    tiene ECF asociado.
    """
    try:
        from apps.configuracion.utils import get_config
        from apps.facturacion_electronica.models import ECF
    except ImportError:
        return None

    config = get_config()
    if not config.modulo_ecf:
        return None

    return (
        ECF.objects
        .filter(venta=venta, tipo__in=('31', '32'))
        .order_by('-creado_en')
        .first()
    )


def _extraer_qr_url_para_ticket(ecf):
    """
    Extrae el qr_url persistido en ecf.xml_respuesta. Retorna None si no
    existe o si el JSON no se puede parsear.
    """
    if not ecf or not ecf.xml_respuesta:
        return None

    try:
        raw = json.loads(ecf.xml_respuesta)
    except (ValueError, TypeError):
        return None

    return raw.get('qr_url') or raw.get('qrUrl') or None


class PrintManager:
    """
    Gestor centralizado de impresión
    """
    
    def __init__(self):
        """Inicializa el Print Manager"""
        self.printer_class = None
        
        try:
            from utils.impresoras.termica import ThermalPrinter2Connect
            self.printer_class = ThermalPrinter2Connect
        except ImportError as e:
            logger.error(f"No se pudo importar ThermalPrinter2Connect: {str(e)}")

    @property
    def enabled(self):
        """Verifica en cada llamada si la impresión está habilitada"""
        return self.printer_class is not None and _is_printing_enabled()
    
    def print_ticket_venta(self, venta, usuario, reimpresion=False):
        """
        Imprime ticket de una venta
        """
        if not self.enabled:
            return {
                'success': False,
                'mensaje': 'Sistema de impresión deshabilitado',
                'error': 'DISABLED'
            }

        if reimpresion:
            venta.refresh_from_db()
        
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
    
    def print_recibo_cxc(self, pago, usuario, reimpresion=False):
        """
        Imprime el recibo de un abono CxC (best-effort: el abono ya esta
        registrado; un fallo de impresora no debe revertirlo).
        """
        if not self.enabled:
            return {
                'success': False,
                'mensaje': 'Sistema de impresión deshabilitado',
                'error': 'DISABLED'
            }

        if reimpresion:
            pago.refresh_from_db()

        try:
            recibo_data = self._prepare_recibo_cxc_data(pago, reimpresion=reimpresion)
        except Exception as e:
            error_msg = f"Error preparando datos de recibo CxC: {str(e)}"
            logger.error(error_msg)
            return {
                'success': False,
                'mensaje': error_msg,
                'error': 'DATA_PREPARATION_ERROR'
            }

        try:
            printer = self.printer_class()
            printer.print_recibo_cxc(recibo_data)

            self._registrar_auditoria_recibo_cxc(
                pago=pago,
                usuario=usuario,
                reimpresion=reimpresion,
                exitosa=True,
            )

            mensaje = "Recibo reimpreso exitosamente" if reimpresion else "Recibo impreso exitosamente"
            logger.info(f"✓ {mensaje}: abono {pago.pk} de {pago.cuenta.venta.numero_venta}")
            return {'success': True, 'mensaje': mensaje}

        except Exception as e:
            error_msg = f"Error imprimiendo recibo CxC: {str(e)}"
            logger.error(error_msg)
            self._registrar_auditoria_recibo_cxc(
                pago=pago,
                usuario=usuario,
                reimpresion=reimpresion,
                exitosa=False,
                error=str(e),
            )
            return {
                'success': False,
                'mensaje': 'No se pudo imprimir el recibo',
                'error': str(e)
            }

    def _prepare_recibo_cxc_data(self, pago, reimpresion=False):
        """Prepara el diccionario de datos para el recibo de abono CxC"""
        cuenta = pago.cuenta
        santo_domingo_tz = pytz.timezone('America/Santo_Domingo')
        fecha_local = pago.fecha_pago.astimezone(santo_domingo_tz)

        return {
            'numero_venta': cuenta.venta.numero_venta,
            'fecha': fecha_local.strftime('%d/%m/%Y %I:%M %p'),
            'cajero': pago.registrado_por.get_full_name() or pago.registrado_por.username,
            'cliente': cuenta.cliente.nombre,
            'metodo': pago.get_metodo_display(),
            'referencia': pago.referencia or '',
            'monto': float(pago.monto),
            'cuotas': [
                {
                    'numero': aplicacion.get('numero'),
                    'monto': float(aplicacion.get('monto', 0)),
                }
                for aplicacion in (pago.aplicaciones or [])
            ],
            'saldo_restante': float(cuenta.saldo),
            'reimpresion': reimpresion,
        }

    @transaction.atomic
    def _registrar_auditoria_recibo_cxc(self, pago, usuario, reimpresion, exitosa, error=None):
        """Registra la impresión del recibo CxC en auditoría"""
        try:
            from apps.sucursales.models import get_sucursal_actual
            numero_venta = pago.cuenta.venta.numero_venta
            if exitosa:
                accion = Auditoria.TipoAccion.RECIBO_CXC_IMPRESO
                descripcion = (
                    f"Recibo CxC {'reimpreso' if reimpresion else 'impreso'}: "
                    f"abono de venta {numero_venta}"
                )
            else:
                accion = Auditoria.TipoAccion.ERROR_SISTEMA
                descripcion = f"Error imprimiendo recibo CxC de venta {numero_venta}"

            metadata = {
                'origen': 'impresion',
                'tipo': 'recibo_cxc',
                'pago_cxc_id': pago.pk,
                'numero_venta': numero_venta,
                'reimpresion': reimpresion,
                'monto': float(pago.monto),
            }
            if error:
                metadata['error'] = error

            Auditoria.registrar(
                accion=accion,
                descripcion=descripcion,
                usuario=usuario,
                metadata=metadata,
                exito=exitosa,
                mensaje_error=error or '',
                sucursal=get_sucursal_actual(),
            )
        except Exception as e:
            logger.error(f"Error registrando auditoría: {str(e)}")

    def test_printer(self):
        """Ejecuta una prueba de impresión"""
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
        """Prepara el diccionario de datos para impresión"""
        detalles = venta.detalles.select_related('producto').all()
        
        items = []
        for detalle in detalles:
            items.append({
                'producto': detalle.producto.nombre,
                'cantidad': detalle.cantidad,
                'precio_unit': float(detalle.precio_unitario),
                'descuento': float(detalle.descuento_monto or 0),
                'subtotal': float(detalle.subtotal - (detalle.descuento_monto or 0))
            })
        
        pagos_lista = []
        tiene_efectivo = False
        
        pagos = venta.pagos.all()
        for pago in pagos:
            if pago.metodo in ['EFECTIVO', 'MIXTO']:
                tiene_efectivo = True
            
            pagos_lista.append({
                'metodo': pago.get_metodo_display(),
                'monto': float(pago.monto)
            })
        
        if tiene_efectivo:
            total_pagado = sum(p['monto'] for p in pagos_lista)
            cambio = total_pagado - float(venta.total)
            
            if cambio > 0:
                pagos_lista.append({
                    'metodo': 'Cambio',
                    'monto': -cambio
                })
        
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
        
        if hasattr(venta, 'cliente') and venta.cliente:
            venta_data['cliente'] = venta.cliente.nombre

        ecf = _obtener_ecf_para_ticket(venta)
        if ecf is not None:
            venta_data['ecf'] = {
                'estado': ecf.estado,
                'encf': ecf.encf or '',
                'codigo_seguridad': ecf.codigo_seguridad or '',
                'fecha_firma': (
                    ecf.fecha_emision.strftime('%d-%m-%Y %H:%M:%S')
                    if ecf.fecha_emision else ''
                ),
                'qr_url': _extraer_qr_url_para_ticket(ecf),
                'venta_numero': venta.numero_venta,
            }
        
        return venta_data
    
    @transaction.atomic
    def _registrar_auditoria_impresion(self, venta, usuario, reimpresion, exitosa, error=None):
        """Registra el evento de impresión en auditoría"""
        try:
            from apps.sucursales.models import get_sucursal_actual
            if exitosa:
                accion = (Auditoria.TipoAccion.TICKET_REIMPRESO if reimpresion
                          else Auditoria.TipoAccion.TICKET_IMPRESO)
                descripcion = (
                    f"Ticket {'reimpreso' if reimpresion else 'impreso'}: "
                    f"venta {venta.numero_venta}"
                )
            else:
                accion = Auditoria.TipoAccion.ERROR_SISTEMA
                descripcion = f"Error imprimiendo ticket de venta {venta.numero_venta}"

            metadata = {
                'origen': 'impresion',
                'tipo': 'ticket',
                'numero_venta': venta.numero_venta,
                'reimpresion': reimpresion,
                'total': float(venta.total),
                'fecha_venta': venta.fecha_venta.isoformat(),
            }
            if error:
                metadata['error'] = error

            Auditoria.registrar(
                accion=accion,
                descripcion=descripcion,
                usuario=usuario,
                content_object=venta,
                metadata=metadata,
                exito=exitosa,
                mensaje_error=error or '',
                sucursal=get_sucursal_actual(),
            )
            logger.debug("Auditoria de impresion registrada: %s - %s", accion, venta.numero_venta)

        except Exception as e:
            logger.error(f"Error registrando auditoría: {str(e)}")

    def get_ultimas_impresiones(self, usuario=None, limit=50):
        """Obtiene las últimas impresiones del sistema"""
        qs = Auditoria.objects.filter(metadata__origen='impresion')
        if usuario:
            qs = qs.filter(usuario=usuario)
        return qs.select_related('usuario').order_by('-fecha_hora')[:limit]

    def get_estadisticas_impresion(self, fecha_desde=None, fecha_hasta=None):
        """Obtiene estadísticas de impresión"""
        qs = Auditoria.objects.filter(metadata__origen='impresion')
        if fecha_desde:
            qs = qs.filter(fecha_hora__gte=fecha_desde)
        if fecha_hasta:
            qs = qs.filter(fecha_hora__lte=fecha_hasta)

        total = qs.count()
        exitosas = qs.filter(exito=True).count()
        errores = qs.filter(exito=False).count()
        reimpresiones = qs.filter(exito=True, metadata__reimpresion=True).count()

        return {
            'total': total,
            'exitosas': exitosas,
            'errores': errores,
            'reimpresiones': reimpresiones,
            'tasa_exito': (exitosas / total * 100) if total > 0 else 0
        }


# Instancia singleton
print_manager = PrintManager()


def imprimir_ticket(venta, usuario, reimpresion=False):
    """Función de conveniencia para imprimir tickets"""
    return print_manager.print_ticket_venta(venta, usuario, reimpresion)


def test_impresora():
    """Función de conveniencia para probar impresora"""
    return print_manager.test_printer()
