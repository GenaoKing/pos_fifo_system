"""
Driver de Impresora Térmica 2Connect
Sistema POS FIFO - Royal Plastic

Este módulo maneja la comunicación con la impresora térmica 2Connect 2C-POS80-01
e incluye funcionalidades de impresión de tickets, manejo de cajón de dinero,
códigos QR y procesamiento de logos.

Autor: Sistema POS FIFO
Versión: 1.0
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
    # Fallback si no está instalado aún
    Usb = None
    Win = None
    EscposError = Exception

from django.conf import settings

logger = logging.getLogger(__name__)


class ThermalPrinterException(Exception):
    """Excepción personalizada para errores de impresión"""
    pass


class ThermalPrinter2Connect:
    """
    Driver para impresora térmica 2Connect 2C-POS80-01
    
    Características:
    - Impresión de tickets de venta
    - Logos y códigos QR
    - Control de cajón de dinero
    - Corte automático de papel
    - Manejo robusto de errores
    
    Uso:
        printer = ThermalPrinter2Connect()
        printer.print_ticket(venta_data)
    """
    
    def __init__(self):
        """Inicializa la conexión con la impresora"""
        self.printer = None
        self.config = settings.THERMAL_PRINTER
        self.business_info = settings.BUSINESS_INFO
        self.qr_config = settings.QR_CONFIG
        
        # Verificar que las librerías estén instaladas
        if Usb is None or Win is None:
            raise ImportError(
                "python-escpos no está instalado. "
                "Ejecutar: pip install python-escpos --break-system-packages"
            )
        
        
    def connect(self):
        """
        Establece conexión con la impresora 2Connect
        
        Intenta conectar usando el driver genérico de Windows primero,
        luego intenta conexión USB directa si falla.
        
        Returns:
            bool: True si la conexión fue exitosa
            
        Raises:
            ThermalPrinterException: Si no se puede conectar
        """
        if not self.config['ENABLED']:
            raise ThermalPrinterException("Sistema de impresión deshabilitado")
        
        try:
            # Método 1: Usar driver de Windows (Generic / Text Only)
            # Este es el método recomendado para la 2Connect con driver genérico
            logger.info("Intentando conectar con impresora 2Connect via Windows...")
            self.printer = Win(self.config['PRINTER_NAME'])
            self.printer.profile.media['width'] = {'pixels': 576, 'mm': 80}  # 80mm a 203 DPI = 576 píxeles
            
            try:
    # ESC 7 n1 n2 n3 - Ajustar densidad
                self.printer._raw(b'\x1D(L\x06\x00\x30\x00\x00\x00\x00\x00')  # Comando inicialización gráficos
                self.printer._raw(b'\x1D\x7C\x20')  # Densidad máxima (32)
            except:
                print("No se pudo configurar densidad, continuando con valores por defecto")
                pass

            # Configurar encoding
            if hasattr(self.printer, 'charcode'):
                self.printer.charcode(self.config['CODE_PAGE'])

            if hasattr(self.printer, '_raw'):
                self.printer._raw(b'\x1d\x7c\x0f') 
            
            logger.info("✓ Impresora 2Connect conectada exitosamente (Windows driver)")
            return True
            
        except Exception as e:
            logger.warning(f"Conexión vía Windows falló: {str(e)}")
            
            # Método 2: Conexión USB directa (fallback)
            try:
                logger.info("Intentando conexión USB directa...")
                
                # Auto-detectar IDs si no están configurados
                if self.config['USB_VENDOR_ID'] and self.config['USB_PRODUCT_ID']:
                    self.printer = Usb(
                        self.config['USB_VENDOR_ID'],
                        self.config['USB_PRODUCT_ID']
                    )
                else:
                    # Dejar que python-escpos auto-detecte
                    self.printer = Usb()
                
                logger.info("✓ Impresora 2Connect conectada (USB directo)")
                return True
                
            except Exception as usb_error:
                error_msg = f"No se pudo conectar con impresora 2Connect: {str(usb_error)}"
                logger.error(error_msg)
                raise ThermalPrinterException(error_msg)
        
    '''    
    def connect(self):
        """Establece conexión con la impresora 2Connect"""
        if not self.config['ENABLED']:
            raise ThermalPrinterException("Sistema de impresión deshabilitado")
        
        try:
            # Usar USB directo en lugar de driver de Windows
            
            logger.info("Conectando via USB directo...")
            
            # IDs para 2Connect (verifica con: python -m escpos.cli ls)
            # Si no los tienes, ejecuta ese comando primero
            self.printer = Usb(idVendor=0x0FE6, idProduct=0x811E)  # IDs comunes de 2Connect

            if hasattr(self.printer, 'profile'):
                self.printer.profile.media['width']['pixels'] = 576

            logger.info("✓ Impresora conectada (USB directo)")
            return True
            
        except Exception as e:
            logger.error(f"Error conectando: {str(e)}")
            raise ThermalPrinterException(str(e))
       ''' 
        
    def disconnect(self):
        """Cierra la conexión con la impresora de forma segura"""
        if self.printer:
            try:
                self.printer.set(align='left', bold=False, normal_textsize=True)  # Reset para asegurar estado limpio
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
                {
                    'numero_venta': 'V-00001',
                    'fecha': '2026-02-01 10:30:00',
                    'cajero': 'María González',
                    'cliente': 'Juan Pérez',  # Opcional
                    'items': [
                        {
                            'producto': 'Producto XYZ',
                            'cantidad': 2,
                            'precio_unit': 150.00,
                            'descuento': 10.00,
                            'subtotal': 290.00
                        }
                    ],
                    'subtotal': 500.00,
                    'descuento_total': 50.00,
                    'total': 450.00,
                    'pagos': [
                        {'metodo': 'Efectivo', 'monto': 500.00},
                        {'metodo': 'Cambio', 'monto': -50.00}
                    ],
                    'tiene_efectivo': True  # Para abrir cajón
                }
        
        Returns:
            bool: True si la impresión fue exitosa
            
        Raises:
            ThermalPrinterException: Si hay error en la impresión
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
                # Espacio para corte manual
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
        
        # Logo centrado (si está habilitado)
        if self.config['LOGO_ENABLED']:
            logo_path = settings.BASE_DIR / self.config['LOGO_PATH']
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
        
        # Información del negocio (normal y centrado)
        self.printer.set(align='center', bold=False)
        
        if self.business_info['RNC'] != '[PENDIENTE - CONFIGURAR]':
            self.printer.text(f"RNC: {self.business_info['RNC']}\n")
        
        self.printer.text(f"Tel: {self.business_info['PHONE']}\n")
        
        if self.business_info['ADDRESS'] != '[PENDIENTE - CONFIGURAR]':
            self.printer.text(f"{self.business_info['ADDRESS']}\n")
        
        self.printer.text(f"{self.business_info['CITY']}\n")
        
        # Línea separadora
        self.printer.text("=" * self.config['PAPER_WIDTH'] + "\n")
        self.printer.set(align='left', bold=False)
    
    def _print_logo(self, logo_path):
        """
        Procesa e imprime el logo de la empresa
        
        Args:
            logo_path (Path): Ruta al archivo del logo
        """
        try:
            # Cargar imagen
            logo = Image.open(logo_path)
            
            # Convertir a escala de grises
            logo = logo.convert('L')
            
            # Redimensionar manteniendo aspect ratio
            width = self.config['LOGO_WIDTH']
            if self.config['LOGO_HEIGHT']:
                height = self.config['LOGO_HEIGHT']
            else:
                # Calcular altura proporcional
                aspect_ratio = logo.height / logo.width
                height = int(width * aspect_ratio)
            
            logo = logo.resize((width, height), Image.LANCZOS)
            
            # Imprimir logo centrado
            self.printer.image(logo, center=True)
            #self.printer.text("\n")
            
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
        
        # Cliente si está presente
        if venta_data.get('cliente'):
            self.printer.text(f"Cliente: {venta_data['cliente']}\n")
        
        # Línea separadora
        self.printer.text("-" * self.config['PAPER_WIDTH'] + "\n")

    
    def _print_items(self, items):
        """Imprime los items con formato de tabla alineada (48 caracteres)"""
        
        # Headers de tabla (negrita, tamaño normal)
        # CANT(4) + 5 espacios + PRECIO(7) + 5 espacios + DESC(6) + 5 espacios + SUBTOTAL(8) = 40 chars
        self.printer.set(align='left', bold=True)
        header = f"{'CANT':>4}     {'PRECIO':>7}     {'DESC.':>6}     {'SUBTOTAL':>8}"
        self.printer.text(header + "\n")
        self.printer.text("-" * 48 + "\n")
        self.printer.set(bold=False)
        
        # Items
        for item in items:
            # Línea 1: Nombre del producto (negrita, doble altura)
            self.printer.set(bold=True, double_height=True)
            nombre = item['producto'][:40]
            self.printer.text(f"{nombre}\n")
            
            # Reset inmediato
            self.printer.set(bold=False, double_height=False)
            
            # Línea 2: Valores con distribución exacta
            cant_str = f"{item['cantidad']:>4}"
            precio_str = f"{item['precio_unit']:>7.2f}"
            desc_str = f"{item.get('descuento', 0):>6.2f}"  # Siempre muestra, 0.0 si no hay
            subtotal_str = f"{item['subtotal']:>8.2f}"
            
            # CANT(4) + 5 espacios + PRECIO(7) + 5 espacios + DESC(6) + 5 espacios + SUBTOTAL(8)
            linea = f"{cant_str}     {precio_str}     {desc_str}     {subtotal_str}"
            
            self.printer.text(linea + "\n")
        
        # Reset final
        self.printer.set(align='left', bold=False, double_height=False, double_width=False)
    
    def _print_totales(self, venta_data):
        """Imprime la sección de totales"""
        # Línea separadora
        self.printer.text("-" * self.config['PAPER_WIDTH'] + "\n")
        
        # Subtotal
        self._print_total_line("SUBTOTAL:", venta_data['subtotal'])
        
        # Descuento total si aplica
        if venta_data.get('descuento_total', 0) > 0:
            self._print_total_line("DESCUENTO:", -venta_data['descuento_total'])
        
        # Total (en negrita)
        self.printer.set(bold=True)
        self._print_total_line("TOTAL:", venta_data['total'])
        self.printer.set(bold=False)
        
        # Línea separadora doble
        self.printer.text("=" * self.config['PAPER_WIDTH'] + "\n")
    
    def _print_pagos(self, pagos):
        """
        Imprime los métodos de pago utilizados
        
        Args:
            pagos (list): Lista de diccionarios con métodos y montos
        """
        for pago in pagos:
            metodo = pago['metodo'].upper()
            monto = pago['monto']
            
            # El cambio se muestra como positivo
            if metodo == 'CAMBIO':
                monto = abs(monto)
            
            self._print_total_line(f"{metodo}:", monto)
        
        # Línea separadora
        self.printer.text("=" * self.config['PAPER_WIDTH'] + "\n")
    
    def _print_qr_code(self, numero_venta):
        """
        Genera e imprime código QR para trazabilidad
        
        Args:
            numero_venta (str): Número de la venta
        """
        if not self.qr_config['ENABLED']:
            return
        
        try:
            # Determinar contenido del QR
            if self.qr_config['BASE_URL']:
                qr_data = f"{self.qr_config['BASE_URL']}{numero_venta}"
            else:
                qr_data = numero_venta
            
            # Generar código QR
            qr = qrcode.QRCode(
                version=1,
                error_correction=getattr(
                    qrcode.constants,
                    f"ERROR_CORRECT_{self.qr_config['ERROR_CORRECTION']}"
                ),
                box_size=self.qr_config['SIZE'],
                border=1,
            )
            qr.add_data(qr_data)
            qr.make(fit=True)
            
            # Crear imagen del QR
            qr_image = qr.make_image(fill_color="black", back_color="white")
            
            # Convertir a formato compatible
            qr_image = qr_image.convert('L')
            
            # Imprimir QR centrado
            self.printer.set(normal_textsize=True)  # Asegurar tamaño normal para QR
            self.printer.image(qr_image, center=True)
            
            # Texto del número de venta debajo del QR
            self.printer.set(align='center', bold=False)
            self.printer.text(f"{numero_venta}\n")
            
        except Exception as e:
            logger.warning(f"No se pudo imprimir código QR: {str(e)}")
    
    def _print_footer(self):
        """Imprime el footer del ticket con mensaje de despedida"""
        self.printer.set(align='center', bold=False)
        
        # Mensaje principal
        if self.business_info['FOOTER_MESSAGE']:
            self.printer.set(bold=True)
            self.printer.text(f"{self.business_info['FOOTER_MESSAGE']}\n")
            self.printer.set(bold=False)
        
        # Mensaje secundario
        if self.business_info['FOOTER_LINE2']:
            self.printer.text(f"{self.business_info['FOOTER_LINE2']}\n")
        
        # WhatsApp si está configurado
        if self.business_info['WHATSAPP']:
            self.printer.text(f"WhatsApp: {self.business_info['WHATSAPP']}\n")
        
        # Redes sociales
        if self.business_info['INSTAGRAM']:
            self.printer.text(f"Instagram: @{self.business_info['INSTAGRAM']}\n")
        
        if self.business_info['FACEBOOK']:
            self.printer.text(f"Facebook: {self.business_info['FACEBOOK']}\n")
        
        # Espacio final
        self.printer.set(align='left', bold=False)
        #self.printer.text("\n\n")
    
    def _print_total_line(self, label, amount):
        """
        Helper para imprimir líneas de totales alineadas a la derecha
        
        Args:
            label (str): Etiqueta (ej: "SUBTOTAL:")
            amount (float): Monto a mostrar
        """
        amount_str = f"${abs(amount):.2f}"
        espacios_necesarios = self.config['PAPER_WIDTH'] - len(label) - len(amount_str)
        linea = label + " " * max(1, espacios_necesarios) + amount_str
        self.printer.text(linea + "\n")
    
    def _open_cash_drawer(self):
        """
        Envía pulso para abrir el cajón de dinero
        
        La impresora 2Connect tiene salida RJ11/RJ12 para cajón.
        Envía el comando ESC p m t1 t2 según especificación ESC/POS.
        """
        try:
            # Comando estándar ESC/POS para abrir cajón
            # ESC p m t1 t2
            # m = pin (0 = pin 2, 1 = pin 5)
            # t1 = tiempo ON (ms)
            # t2 = tiempo OFF (ms)
            pin = self.config['CASH_DRAWER_PIN']
            self.printer.cashdraw(pin)
            
            logger.info("✓ Cajón de dinero abierto")
            
        except Exception as e:
            logger.warning(f"No se pudo abrir cajón de dinero: {str(e)}")
    
    def print_test_page(self):
        """
        Imprime una página de prueba para verificar funcionamiento
        
        Returns:
            bool: True si la prueba fue exitosa
        """
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
            self.printer.text("Estado: CONECTADA ✓\n")
            self.printer.text("Cortador: OK\n")
            self.printer.text("Encoding: CP850\n\n")
            
            self.printer.text("Caracteres especiales:\n")
            self.printer.text("ñ Ñ á é í ó ú\n")
            self.printer.text("¿ ? ¡ ! $ €\n\n")
            
            if self.config['AUTO_CUT']:
                self.printer.cut()
            
            logger.info("✓ Página de prueba impresa")
            return True
            
        except Exception as e:
            logger.error(f"Error en prueba: {str(e)}")
            return False
        
        finally:
            self.disconnect()


# ============================================================================
# FUNCIONES DE UTILIDAD
# ============================================================================

def get_printer_instance():
    """
    Factory function para obtener instancia del printer
    
    Returns:
        ThermalPrinter2Connect: Instancia configurada del printer
    """
    return ThermalPrinter2Connect()
