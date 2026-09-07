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
        'FOOTER_MESSAGE': getattr(config, 'texto_pie_ticket', None) or 'Gracias por su compra',
        'FOOTER_LINE2': '',
        'WHATSAPP': config.telefono or '',
        'INSTAGRAM': '',
        'FACEBOOK': '',
        'LOGO_PATH': config.logo.path if config.logo else None,
        'LOGO_ENABLED': getattr(config, 'imprimir_logo_ticket', True),
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
            logger.info(
                "Intentando conectar con impresora 2Connect via Windows: %r",
                self.config['PRINTER_NAME'],
            )
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

            # === SECCION e-CF ===
            if venta_data.get('ecf'):
                self._print_ecf_section(venta_data['ecf'])
            
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

        if venta_data.get('etiqueta_copia'):
            self.printer.set(align='center', bold=True)
            self.printer.text(f"{venta_data['etiqueta_copia']}\n")
            self.printer.set(align='left', bold=False)
        
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

    def _print_ecf_section(self, ecf_data):
        """
        Imprime la seccion e-CF segun el estado del documento fiscal.

        Casos:
        - APROBADO / APROBADO_CONDICIONAL: RI valida con QR.
        - PENDIENTE / ENVIADO / EN_PROCESO con datos: RI con leyenda
          de ENVIO DIFERIDO.
        - PENDIENTE sin eNCF/codigo todavia: aviso de reimpresion.
        - RECHAZADO / ERROR: leyenda destacada de NO VALIDO.
        """
        estado = ecf_data.get('estado')

        self.printer.text("\n")
        self.printer.set(align='center', bold=False)
        self.printer.text("-" * 32 + "\n")
        self.printer.set(align='center', bold=True)
        self.printer.text("COMPROBANTE FISCAL ELECTRONICO\n")
        self.printer.set(align='center', bold=False)

        if estado in ('RECHAZADO', 'ERROR'):
            self._print_ecf_rechazado(ecf_data)
            return

        if ecf_data.get('encf') and ecf_data.get('codigo_seguridad'):
            self._print_ecf_with_data(ecf_data)
            return

        self.printer.text("\n")
        self.printer.text("e-CF en proceso de emision\n")
        self.printer.text("Reimprima este ticket en unos minutos\n")
        self.printer.text("para obtener el comprobante final.\n")
        self.printer.text(f"Venta: {ecf_data.get('venta_numero', '')}\n")
        self.printer.text("\n")

    def _print_ecf_with_data(self, ecf_data):
        """
        Imprime un ECF con eNCF y codigo de seguridad asignados.
        Si no esta aprobado aun, agrega la leyenda de ENVIO DIFERIDO.
        """
        estado = ecf_data.get('estado')

        self.printer.text("\n")
        self.printer.text(f"eNCF: {ecf_data.get('encf', '')}\n")
        self.printer.text(
            f"Cod. Seguridad: {ecf_data.get('codigo_seguridad', '')}\n"
        )

        if ecf_data.get('fecha_firma'):
            self.printer.text(f"Fecha Firma: {ecf_data['fecha_firma']}\n")

        qr_url = ecf_data.get('qr_url')
        if qr_url:
            self.printer.text("\n")
            try:
                self._print_qr_from_data(qr_url)
            except Exception:
                self.printer.set(align='center', bold=False)
                self.printer.text("Validar en:\n")
                self.printer.text(f"{qr_url}\n")

        if estado not in ('APROBADO', 'APROBADO_CONDICIONAL'):
            self.printer.text("\n")
            self.printer.set(align='center', bold=True)
            self.printer.text("e-CF emitido en modalidad\n")
            self.printer.text("ENVIO DIFERIDO\n")
            self.printer.set(align='center', bold=False)
            self.printer.text("Disponible para validacion\n")
            self.printer.text("en portal DGII en 24 horas.\n")
        else:
            self.printer.text("\n")
            self.printer.set(align='center', bold=False)
            self.printer.text("Documento valido fiscalmente\n")
            self.printer.text("y aceptado por DGII.\n")

        self.printer.set(align='left', bold=False)
        self.printer.text("\n")

    def _print_ecf_rechazado(self, ecf_data):
        """
        Imprime leyenda destacada para un e-CF rechazado por DGII.
        """
        self.printer.text("\n")
        self.printer.set(align='center', bold=True, double_height=True)
        self.printer.text("DOCUMENTO RECHAZADO\n")
        self.printer.text("POR DGII\n")
        self.printer.set(align='center', bold=True, double_height=False)
        self.printer.text("NO VALIDO COMO\n")
        self.printer.text("COMPROBANTE FISCAL\n")
        self.printer.set(align='center', bold=False)
        self.printer.text("\n")
        self.printer.text("Solicite una nueva factura\n")
        self.printer.text("al cajero.\n")
        self.printer.text("\n")
        self.printer.text(
            f"Ref. interna: {ecf_data.get('venta_numero', '')}\n"
        )
        self.printer.text("\n")
        self.printer.set(align='left', bold=False, double_height=False)

    def _print_qr_from_data(self, qr_data):
        """
        Genera e imprime un QR desde un string arbitrario.
        """
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

    def print_recibo_cxc(self, recibo_data):
        """
        Imprime un recibo de abono a cuenta por cobrar.

        Args:
            recibo_data (dict): numero_venta, fecha, cajero, cliente, metodo,
                referencia, monto, cuotas (lista de {numero, monto}),
                saldo_restante, reimpresion (bool).
        """
        if not self.connect():
            raise ThermalPrinterException("No se pudo conectar con la impresora")

        try:
            self._print_header()

            self.printer.set(align='center', bold=True)
            self.printer.text("RECIBO DE ABONO CxC\n")
            if recibo_data.get('reimpresion'):
                self.printer.text("(REIMPRESION)\n")
            self.printer.set(align='left', bold=False)
            self.printer.text("-" * self.config['PAPER_WIDTH'] + "\n")

            numero_limpio = recibo_data['numero_venta'].replace('VENTA-', '')
            self.printer.text(f"Venta: {numero_limpio}\n")
            self.printer.text(f"Fecha: {recibo_data['fecha']}\n")
            self.printer.text(f"Cajero: {recibo_data['cajero']}\n")
            self.printer.text(f"Cliente: {recibo_data['cliente']}\n")
            self.printer.text(f"Metodo: {recibo_data['metodo']}\n")
            if recibo_data.get('referencia'):
                self.printer.text(f"Referencia: {recibo_data['referencia']}\n")

            self.printer.text("-" * self.config['PAPER_WIDTH'] + "\n")
            for cuota in recibo_data.get('cuotas', []):
                self._print_total_line(f"Cuota {cuota['numero']}:", cuota['monto'])

            self.printer.text("=" * self.config['PAPER_WIDTH'] + "\n")
            self.printer.set(bold=True)
            self._print_total_line("ABONO:", recibo_data['monto'])
            self.printer.set(bold=False)
            self._print_total_line("SALDO PENDIENTE:", recibo_data['saldo_restante'])
            self.printer.text("=" * self.config['PAPER_WIDTH'] + "\n")

            self._print_footer()

            if self.config['AUTO_CUT']:
                self.printer.cut()
            else:
                self.printer.text("\n\n\n")

            logger.info(f"✓ Recibo CxC de {recibo_data['numero_venta']} impreso exitosamente")
            return True

        except Exception as e:
            error_msg = f"Error imprimiendo recibo CxC: {str(e)}"
            logger.error(error_msg)
            raise ThermalPrinterException(error_msg)

        finally:
            self.disconnect()

    def print_cuadre_caja(self, cuadre_data):
        """
        Imprime el ticket de cuadre/arqueo de un turno de caja.

        Args:
            cuadre_data (dict): caja, cajero, apertura, cierre, estado,
                ventas_lineas [(label, monto)], total_ventas, cantidad_ventas,
                cobros_lineas [(label, monto)], cobros_total, fondo_apertura,
                efectivo_ventas, efectivo_cxc, ingresos, retiros, gastos,
                esperado, contado, diferencia, reimpresion (bool).
        """
        if not self.connect():
            raise ThermalPrinterException("No se pudo conectar con la impresora")

        ancho = self.config['PAPER_WIDTH']
        try:
            self._print_header()

            self.printer.set(align='center', bold=True)
            self.printer.text("CUADRE DE CAJA\n")
            if cuadre_data.get('reimpresion'):
                self.printer.text("(REIMPRESION)\n")
            self.printer.set(align='left', bold=False)
            self.printer.text("-" * ancho + "\n")

            self.printer.text(f"Caja: {cuadre_data['caja']}\n")
            self.printer.text(f"Cajero: {cuadre_data['cajero']}\n")
            self.printer.text(f"Apertura: {cuadre_data['apertura']}\n")
            if cuadre_data.get('cierre'):
                self.printer.text(f"Cierre: {cuadre_data['cierre']}\n")
            else:
                self.printer.text("Turno ABIERTO (parcial)\n")

            # --- VENTAS ---
            self.printer.text("-" * ancho + "\n")
            self.printer.set(bold=True)
            self.printer.text(f"VENTAS ({cuadre_data.get('cantidad_ventas', 0)})\n")
            self.printer.set(bold=False)
            for label, monto in cuadre_data.get('ventas_lineas', []):
                self._print_total_line(f"  {label}:", monto)
            self._print_total_line("Total ventas:", cuadre_data.get('total_ventas', 0))

            # --- COBROS CxC ---
            if cuadre_data.get('cobros_lineas'):
                self.printer.text("-" * ancho + "\n")
                self.printer.set(bold=True)
                self.printer.text("COBROS CxC\n")
                self.printer.set(bold=False)
                for label, monto in cuadre_data['cobros_lineas']:
                    self._print_total_line(f"  {label}:", monto)
                self._print_total_line("Total cobros:", cuadre_data.get('cobros_total', 0))

            # --- ARQUEO DE EFECTIVO ---
            self.printer.text("=" * ancho + "\n")
            self.printer.set(bold=True)
            self.printer.text("ARQUEO DE EFECTIVO\n")
            self.printer.set(bold=False)
            self._print_total_line("Fondo apertura:", cuadre_data['fondo_apertura'])
            self._print_total_line("+ Ventas efectivo:", cuadre_data['efectivo_ventas'])
            self._print_total_line("+ Cobros efectivo:", cuadre_data['efectivo_cxc'])
            self._print_total_line("+ Ingresos:", cuadre_data['ingresos'])
            self._print_total_line("- Retiros:", cuadre_data['retiros'])
            self._print_total_line("- Gastos:", cuadre_data['gastos'])
            self.printer.text("-" * ancho + "\n")
            self.printer.set(bold=True)
            self._print_total_line("ESPERADO:", cuadre_data['esperado'])
            self._print_total_line("CONTADO:", cuadre_data['contado'])
            self.printer.set(bold=False)

            diferencia = cuadre_data['diferencia']
            etiqueta = "SOBRANTE:" if diferencia >= 0 else "FALTANTE:"
            self.printer.set(bold=True)
            self._print_total_line(etiqueta, abs(diferencia))
            self.printer.set(bold=False)
            self.printer.text("=" * ancho + "\n")

            # Pie neutro (no es una venta: no lleva "gracias por su compra").
            self.printer.set(align='center')
            self.printer.text("Documento interno - arqueo de caja\n")
            self.printer.set(align='left')

            if self.config['AUTO_CUT']:
                self.printer.cut()
            else:
                self.printer.text("\n\n\n")

            logger.info("✓ Cuadre de caja impreso (caja %s)", cuadre_data.get('caja'))
            return True

        except Exception as e:
            error_msg = f"Error imprimiendo cuadre de caja: {str(e)}"
            logger.error(error_msg)
            raise ThermalPrinterException(error_msg)

        finally:
            self.disconnect()

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
            logger.info("Enviando pulso de cajon de dinero: pin=%s", pin)
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
