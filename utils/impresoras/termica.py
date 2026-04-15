"""
Driver de Impresora Térmica 2Connect
Sistema POS FIFO - Módulo de Impresión

Este módulo maneja la comunicación con la impresora térmica 2Connect 2C-POS80-01
e incluye funcionalidades de impresión de tickets, manejo de cajón de dinero,
códigos QR y procesamiento de logos.

Versión: 2.0 - Migrado a ConfiguracionNegocio
"""

import logging
import os
from pathlib import Path
from time import sleep
from PIL import Image
import qrcode
from io import BytesIO

try:
    from escpos.printer import Usb, Win32Raw as Win
    from escpos.exceptions import Error as EscposError
except ImportError:
    Usb = None
    Win = None
    EscposError = Exception

from django.conf import settings

logger = logging.getLogger(__name__)


class ThermalPrinterException(Exception):
    """Excepción personalizada para errores de impresión"""
    pass


def _get_negocio_config():
    """
    Obtiene datos del negocio desde ConfiguracionNegocio (BD).
    Se llama cada vez que se imprime, no al importar el módulo,
    para que siempre tenga datos frescos.
    """
    from apps.configuracion.utils import get_config
    config = get_config()
    return {
        'NAME': config.nombre_negocio,
        'RNC': config.rnc or '',
        'PHONE': config.telefono or '',
        'ADDRESS': config.direccion or '',
        'FOOTER_MESSAGE': config.texto_pie_ticket or 'Gracias por su compra',
        'FOOTER_LINE2': '',
        'WHATSAPP': config.telefono or '',
        'INSTAGRAM': '',
        'FACEBOOK': '',
        'LOGO_PATH': config.logo.path if config.logo else None,
        'LOGO_ENABLED': config.imprimir_logo_ticket,
    }


class ThermalPrinter2Connect:
    """
    Driver para impresora térmica 2Connect 2C-POS80-01
    """
    
    def __init__(self):
        """Inicializa la conexión con la impresora"""
        self.printer = None
        # Hardware config (constantes) — se queda en settings
        self.config = settings.THERMAL_PRINTER
        # Datos del negocio — se lee de BD cada vez
        self.business_info = _get_negocio_config()
        # QR config — técnico, se queda en settings
        self.qr_config = getattr(settings, 'QR_CONFIG', {'ENABLED': False})
        
        if Usb is None or Win is None:
            raise ImportError(
                "python-escpos no está instalado. "
                "Ejecutar: pip install python-escpos"
            )
        
        
    def connect(self):
        """
        Establece conexión con la impresora 2Connect
        
        Intenta conectar usando el driver genérico de Windows primero,
        luego intenta conexión USB directa si falla.
        """
        if not self.config['ENABLED']:
            raise ThermalPrinterException("Sistema de impresión deshabilitado")
        
        try:
            logger.info("Intentando conectar con impresora 2Connect via Windows...")
            self.printer = Win(self.config['PRINTER_NAME'])
            self.printer.profile.media['width'] = {'pixels': 576, 'mm': 80}
            
            try:
                self.printer._raw(b'\x1D(L\x06\x00\x30\x00\x00\x00\x00\x00')
                self.printer._raw(b'\x1D\x7C\x20')
            except:
                pass

            if hasattr(self.printer, 'charcode'):
                self.printer.charcode(self.config['CODE_PAGE'])

            if hasattr(self.printer, '_raw'):
                self.printer._raw(b'\x1d\x7c\x0f') 
            
            logger.info("✓ Impresora 2Connect conectada exitosamente (Windows driver)")
            return True
            
        except Exception as e:
            logger.warning(f"Conexión vía Windows falló: {str(e)}")
            
            try:
                logger.info("Intentando conexión USB directa...")
                
                if self.config['USB_VENDOR_ID'] and self.config['USB_PRODUCT_ID']:
                    self.printer = Usb(
                        self.config['USB_VENDOR_ID'],
                        self.config['USB_PRODUCT_ID']
                    )
                else:
                    self.printer = Usb()
                
                logger.info("✓ Impresora 2Connect conectada (USB directo)")
                return True
                
            except Exception as usb_error:
                error_msg = f"No se pudo conectar con impresora 2Connect: {str(usb_error)}"
                logger.error(error_msg)
                raise ThermalPrinterException(error_msg)
        
    def disconnect(self):
        """Cierra la conexión con la impresora de forma segura"""
        if self.printer:
            try:
                self.printer.set(align='left', bold=False, normal_textsize=True)
                self.printer.close()
                logger.debug("Conexión con impresora cerrada")
            except Exception as e:
                logger.warning(f"Error al cerrar impresora: {str(e)}")
            finally:
                self.printer = None

    
    def print_ticket(self, venta_data):
        """
        Imprime un ticket de venta completo
        
        Args:
            venta_data (dict): Diccionario con los datos de la venta
        """
        if not self.connect():
            raise ThermalPrinterException("No se pudo conectar con la impresora")
        
        try:
            # === HEADER CON LOGO ===
            self._print_header()
            
            # === INFORMACIÓN DE LA VENTA ===
            self._print_venta_info(venta_data)
            
            # === ITEMS DE LA VENTA ===
            self._print_items(venta_data['items'])
            
            # === TOTALES ===
            self._print_totales(venta_data)
            
            # === PAGOS ===
            self._print_pagos(venta_data['pagos'])
            
            # === CÓDIGO QR ===
            self._print_qr_code(venta_data['numero_venta'])
            
            # === FOOTER ===
            self._print_footer()
            
            # === CORTE DE PAPEL ===
            if self.config['AUTO_CUT']:
                self.printer.cut()
            else:
                self.printer.text("\n\n\n")
            
            # === ABRIR CAJÓN SI HAY EFECTIVO ===
            if self.config['CASH_DRAWER'] and venta_data.get('tiene_efectivo', False):
                self._open_cash_drawer()
            
            logger.info(f"✓ Ticket {venta_data['numero_venta']} impreso exitosamente")
            return True
            
        except Exception as e:
            error_msg = f"Error imprimiendo ticket: {str(e)}"
            logger.error(error_msg)
            raise ThermalPrinterException(error_msg)
        
        finally:
            self.disconnect()
    
    def _print_header(self):
        """Imprime el header del ticket con logo y datos del negocio"""
        
        # Logo centrado (si está habilitado en ConfiguracionNegocio)
        if self.business_info['LOGO_ENABLED'] and self.business_info['LOGO_PATH']:
            logo_path = Path(self.business_info['LOGO_PATH'])
            if logo_path.exists():
                try:
                    self._print_logo(logo_path)
                except Exception as e:
                    logger.warning(f"No se pudo imprimir logo: {str(e)}")
            else:
                logger.warning(f"Logo no encontrado: {logo_path}")
        
        # Nombre del negocio (grande y centrado)
        self.printer.set(align='center', bold=True)
        self.printer.text(f"{self.business_info['NAME']}\n")
        
        # RNC
        self.printer.set(align='center', bold=False)
        if self.business_info['RNC']:
            self.printer.text(f"RNC: {self.business_info['RNC']}\n")
        
        # Teléfono
        if self.business_info['PHONE']:
            self.printer.text(f"Tel: {self.business_info['PHONE']}\n")
        
        # Dirección
        if self.business_info['ADDRESS']:
            self.printer.text(f"{self.business_info['ADDRESS']}\n")
        
        # Línea separadora
        self.printer.text("=" * self.config['PAPER_WIDTH'] + "\n")
        self.printer.set(align='left', bold=False)
    
    def _print_logo(self, logo_path):
        """Procesa e imprime el logo de la empresa"""
        try:
            logo = Image.open(logo_path)
            logo = logo.convert('L')
            
            width = self.config['LOGO_WIDTH']
            if self.config['LOGO_HEIGHT']:
                height = self.config['LOGO_HEIGHT']
            else:
                aspect_ratio = logo.height / logo.width
                height = int(width * aspect_ratio)
            
            logo = logo.resize((width, height), Image.LANCZOS)
            self.printer.image(logo, center=True)
            
        except Exception as e:
            logger.error(f"Error procesando logo: {str(e)}")
            raise
    
    def _print_venta_info(self, venta_data):
        """Imprime información básica de la venta"""
        self.printer.set(align='left', bold=True)
        self.printer.text(f"Ticket: ")
        self.printer.set(bold=False)
        numero_limpio = venta_data['numero_venta'].replace('VENTA-', '')
        self.printer.text(f"{numero_limpio}\n")
        self.printer.set(bold=True)
        self.printer.text(f"Fecha: ")
        self.printer.set(bold=False)
        self.printer.text(f"{venta_data['fecha']}    ")
        self.printer.set(bold=True)
        self.printer.text(f"Cajero: ")
        self.printer.set(bold=False)
        self.printer.text(f"{venta_data['cajero']}\n")
        
        if venta_data.get('cliente'):
            self.printer.text(f"Cliente: {venta_data['cliente']}\n")
        
        self.printer.text("-" * self.config['PAPER_WIDTH'] + "\n")

    
    def _print_items(self, items):
        """Imprime los items con formato de tabla alineada (48 caracteres)"""
        
        self.printer.set(align='left', bold=True)
        header = f"{'CANT':>4}     {'PRECIO':>7}     {'DESC.':>6}     {'SUBTOTAL':>8}"
        self.printer.text(header + "\n")
        self.printer.text("-" * 48 + "\n")
        self.printer.set(bold=False)
        
        for item in items:
            self.printer.set(bold=True, double_height=True)
            nombre = item['producto'][:40]
            self.printer.text(f"{nombre}\n")
            
            self.printer.set(bold=False, double_height=False)
            
            cant_str = f"{item['cantidad']:>4}"
            precio_str = f"{item['precio_unit']:>7.2f}"
            desc_str = f"{item.get('descuento', 0):>6.2f}"
            subtotal_str = f"{item['subtotal']:>8.2f}"
            
            linea = f"{cant_str}     {precio_str}     {desc_str}     {subtotal_str}"
            self.printer.text(linea + "\n")
        
        self.printer.set(align='left', bold=False, double_height=False, double_width=False)
    
    def _print_totales(self, venta_data):
        """Imprime la sección de totales"""
        self.printer.text("-" * self.config['PAPER_WIDTH'] + "\n")
        
        self._print_total_line("SUBTOTAL:", venta_data['subtotal'])
        
        if venta_data.get('descuento_total', 0) > 0:
            self._print_total_line("DESCUENTO:", -venta_data['descuento_total'])
        
        self.printer.set(bold=True)
        self._print_total_line("TOTAL:", venta_data['total'])
        self.printer.set(bold=False)
        
        self.printer.text("=" * self.config['PAPER_WIDTH'] + "\n")
    
    def _print_pagos(self, pagos):
        """Imprime los métodos de pago utilizados"""
        for pago in pagos:
            metodo = pago['metodo'].upper()
            monto = pago['monto']
            
            if metodo == 'CAMBIO':
                monto = abs(monto)
            
            self._print_total_line(f"{metodo}:", monto)
        
        self.printer.text("=" * self.config['PAPER_WIDTH'] + "\n")
    
    def _print_qr_code(self, numero_venta):
        """Genera e imprime código QR para trazabilidad"""
        if not self.qr_config.get('ENABLED', False):
            return
        
        try:
            if self.qr_config.get('BASE_URL'):
                qr_data = f"{self.qr_config['BASE_URL']}{numero_venta}"
            else:
                qr_data = numero_venta
            
            qr = qrcode.QRCode(
                version=1,
                error_correction=getattr(
                    qrcode.constants,
                    f"ERROR_CORRECT_{self.qr_config.get('ERROR_CORRECTION', 'M')}"
                ),
                box_size=self.qr_config.get('SIZE', 4),
                border=1,
            )
            qr.add_data(qr_data)
            qr.make(fit=True)
            
            qr_image = qr.make_image(fill_color="black", back_color="white")
            qr_image = qr_image.convert('L')
            
            self.printer.set(normal_textsize=True)
            self.printer.image(qr_image, center=True)
            
            self.printer.set(align='center', bold=False)
            self.printer.text(f"{numero_venta}\n")
            
        except Exception as e:
            logger.warning(f"No se pudo imprimir código QR: {str(e)}")
    
    def _print_footer(self):
        """Imprime el footer del ticket con mensaje de despedida"""
        self.printer.set(align='center', bold=False)
        
        if self.business_info['FOOTER_MESSAGE']:
            self.printer.set(bold=True)
            self.printer.text(f"{self.business_info['FOOTER_MESSAGE']}\n")
            self.printer.set(bold=False)
        
        if self.business_info.get('FOOTER_LINE2'):
            self.printer.text(f"{self.business_info['FOOTER_LINE2']}\n")
        
        if self.business_info.get('WHATSAPP'):
            self.printer.text(f"WhatsApp: {self.business_info['WHATSAPP']}\n")
        
        if self.business_info.get('INSTAGRAM'):
            self.printer.text(f"Instagram: @{self.business_info['INSTAGRAM']}\n")
        
        if self.business_info.get('FACEBOOK'):
            self.printer.text(f"Facebook: {self.business_info['FACEBOOK']}\n")
        
        self.printer.set(align='left', bold=False)
    
    def _print_total_line(self, label, amount):
        """Helper para imprimir líneas de totales alineadas a la derecha"""
        amount_str = f"${abs(amount):.2f}"
        espacios_necesarios = self.config['PAPER_WIDTH'] - len(label) - len(amount_str)
        linea = label + " " * max(1, espacios_necesarios) + amount_str
        self.printer.text(linea + "\n")
    
    def _open_cash_drawer(self):
        """Envía pulso para abrir el cajón de dinero"""
        try:
            pin = self.config['CASH_DRAWER_PIN']
            self.printer.cashdraw(pin)
            logger.info("✓ Cajón de dinero abierto")
        except Exception as e:
            logger.warning(f"No se pudo abrir cajón de dinero: {str(e)}")
    
    def print_test_page(self):
        """Imprime una página de prueba para verificar funcionamiento"""
        if not self.connect():
            return False
        
        try:
            self.printer.set(align='center', bold=True, double_width=True, double_height=True)
            self.printer.text("PRUEBA\n")
            
            self.printer.set(align='center', bold=False, width=1, height=1)
            self.printer.text("\nImpresora 2Connect\n")
            self.printer.text("Sistema POS FIFO\n")
            self.printer.text(f"{self.business_info['NAME']}\n\n")
            
            self.printer.text("=" * self.config['PAPER_WIDTH'] + "\n\n")
            self.printer.text("Estado: CONECTADA\n")
            self.printer.text("Cortador: OK\n")
            self.printer.text("Encoding: CP850\n\n")
            
            self.printer.text("Caracteres especiales:\n")
            self.printer.text("n N a e i o u\n")
            self.printer.text("? ! $ \n\n")
            
            if self.config['AUTO_CUT']:
                self.printer.cut()
            
            logger.info("✓ Página de prueba impresa")
            return True
            
        except Exception as e:
            logger.error(f"Error en prueba: {str(e)}")
            return False
        
        finally:
            self.disconnect()


def get_printer_instance():
    """Factory function para obtener instancia del printer"""
    return ThermalPrinter2Connect()
