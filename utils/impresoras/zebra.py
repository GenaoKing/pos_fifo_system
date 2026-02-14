"""
Driver para impresora Zebra ZD220
Maneja impresión de etiquetas con código de barras CODE128

Especificaciones:
- Tamaño etiqueta: 37mm x 27mm
- Gap entre etiquetas: 5mm
- Formato: Código de barras + Nombre + Precio
- Conexión: USB
"""

import win32print
import win32ui
from PIL import Image, ImageDraw, ImageFont
import io
from barcode import Code128
from barcode.writer import ImageWriter
import os


class ZebraLabelPrinter:
    """
    Clase para manejar impresión de etiquetas en impresora Zebra
    """
    
    def __init__(self, printer_name="ZDesigner LP 2824"):
        """
        Inicializa el driver de la impresora Zebra
        
        Args:
            printer_name: Nombre exacto de la impresora en Windows
        """
        self.printer_name = printer_name
        self.dpi = 203  # DPI de la ZD220
        
        # Dimensiones en mm
        self.label_width_mm = 37
        self.label_height_mm = 27
        self.gap_mm = 5
        
        # Convertir a dots (puntos de impresora)
        self.label_width_dots = self.mm_to_dots(self.label_width_mm)
        self.label_height_dots = self.mm_to_dots(self.label_height_mm)
        
    def mm_to_dots(self, mm):
        """Convierte milímetros a dots según DPI"""
        return int((mm / 25.4) * self.dpi)
    
    def verificar_impresora(self):
        """
        Verifica que la impresora esté disponible y lista
        
        Returns:
            dict con status de la impresora
        """
        try:
            impresoras = [printer[2] for printer in win32print.EnumPrinters(2)]
            
            if self.printer_name not in impresoras:
                return {
                    'disponible': False,
                    'error': f'Impresora "{self.printer_name}" no encontrada',
                    'impresoras_disponibles': impresoras
                }
            
            # Verificar estado
            handle = win32print.OpenPrinter(self.printer_name)
            printer_info = win32print.GetPrinter(handle, 2)
            win32print.ClosePrinter(handle)
            
            # Status: 0 = Ready, otros valores indican errores
            status = printer_info['Status']
            
            return {
                'disponible': True,
                'status': status,
                'ready': status == 0,
                'mensaje': 'Impresora lista' if status == 0 else f'Impresora con error: {status}'
            }
            
        except Exception as e:
            return {
                'disponible': False,
                'error': str(e)
            }
    
    def generar_zpl(self, codigo_barras, nombre_producto, precio, cantidad=1):
        """
        Genera comandos ZPL para imprimir etiqueta
        
        ZPL (Zebra Programming Language) es el lenguaje nativo de las impresoras Zebra
        
        Args:
            codigo_barras: Código a imprimir (ej: "RP-000123")
            nombre_producto: Nombre del producto (máx 30 chars)
            precio: Precio del producto
            cantidad: Número de etiquetas a imprimir
            
        Returns:
            String con comandos ZPL
        """
        
        # Truncar nombre si es muy largo
        nombre = nombre_producto[:30] if len(nombre_producto) > 30 else nombre_producto
        
        # Formatear precio
        precio_str = f"RD$ {float(precio):,.2f}"
        
        # Comandos ZPL
        # ^XA = Inicio de formato
        # ^CF = Fuente por defecto
        # ^FO = Field Origin (posición x,y)
        # ^BC = Código de barras CODE128
        # ^FD = Field Data (datos a imprimir)
        # ^FS = Field Separator
        # ^XZ = Fin de formato
        # ^PQ = Print Quantity
        
        zpl = f"""
^XA
^LH0,0
^CF0,20
^PW{self.label_width_dots}
^LL{self.label_height_dots}

~SD15

^FO20,10^A0N,18,18^FD{nombre}^FS

^FO20,35^BCN,40,N,N,N^FD{codigo_barras}^FS

^FO20,85^A0N,22,22^FD{precio_str}^FS

^PQ{cantidad},0,1,Y
^XZ
"""
        
        return zpl
    
    def imprimir_etiqueta(self, codigo_barras, nombre_producto, precio, cantidad=1):
        """
        Imprime etiqueta(s) en la impresora Zebra
        
        Args:
            codigo_barras: Código de barras a imprimir
            nombre_producto: Nombre del producto
            precio: Precio del producto
            cantidad: Cantidad de etiquetas a imprimir
            
        Returns:
            dict con resultado de la impresión
        """
        
        try:
            # Verificar impresora
            status = self.verificar_impresora()
            if not status['disponible']:
                return {
                    'success': False,
                    'error': status.get('error', 'Impresora no disponible')
                }
            
            # Generar ZPL
            zpl_commands = self.generar_zpl(
                codigo_barras=codigo_barras,
                nombre_producto=nombre_producto,
                precio=precio,
                cantidad=cantidad
            )
            
            # Enviar a impresora
            handle = win32print.OpenPrinter(self.printer_name)
            
            try:
                job = win32print.StartDocPrinter(handle, 1, ("Etiqueta Producto", None, "RAW"))
                win32print.StartPagePrinter(handle)
                win32print.WritePrinter(handle, zpl_commands.encode('utf-8'))
                win32print.EndPagePrinter(handle)
                win32print.EndDocPrinter(handle)
                
                return {
                    'success': True,
                    'mensaje': f'{cantidad} etiqueta(s) enviada(s) a impresión',
                    'codigo_barras': codigo_barras,
                    'cantidad': cantidad
                }
                
            finally:
                win32print.ClosePrinter(handle)
                
        except Exception as e:
            return {
                'success': False,
                'error': f'Error al imprimir: {str(e)}'
            }
    
    def imprimir_etiquetas_multiples(self, productos_list):
        """
        Imprime múltiples etiquetas de diferentes productos
        
        Args:
            productos_list: Lista de dicts con formato:
                [
                    {
                        'codigo_barras': 'RP-000123',
                        'nombre': 'Producto A',
                        'precio': 150.00,
                        'cantidad': 3
                    },
                    ...
                ]
        
        Returns:
            dict con resultado
        """
        
        try:
            total_etiquetas = 0
            errores = []
            
            for producto in productos_list:
                resultado = self.imprimir_etiqueta(
                    codigo_barras=producto['codigo_barras'],
                    nombre_producto=producto['nombre'],
                    precio=producto['precio'],
                    cantidad=producto.get('cantidad', 1)
                )
                
                if resultado['success']:
                    total_etiquetas += producto.get('cantidad', 1)
                else:
                    errores.append({
                        'producto': producto['nombre'],
                        'error': resultado['error']
                    })
            
            if errores:
                return {
                    'success': False,
                    'total_exitosas': total_etiquetas,
                    'errores': errores
                }
            
            return {
                'success': True,
                'total_etiquetas': total_etiquetas,
                'mensaje': f'{total_etiquetas} etiquetas impresas exitosamente'
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }


def imprimir_etiqueta_producto(producto, cantidad=1):
    """
    Función helper global para imprimir etiqueta de un producto
    
    Args:
        producto: Instancia del modelo Producto
        cantidad: Cantidad de etiquetas a imprimir
    
    Returns:
        dict con resultado
    """
    printer = ZebraLabelPrinter()
    
    return printer.imprimir_etiqueta(
        codigo_barras=producto.codigo_barras,
        nombre_producto=producto.nombre,
        precio=producto.precio_venta,
        cantidad=cantidad
    )


def imprimir_etiquetas_compra(compra):
    """
    Función helper para imprimir etiquetas de productos de una compra
    Solo imprime productos con códigos internos (RP-)
    
    Args:
        compra: Instancia del modelo Compra
    
    Returns:
        dict con resultado
    """
    from apps.inventario.models import DetalleCompra
    
    # Obtener detalles de la compra con productos que tienen código interno
    detalles = DetalleCompra.objects.filter(
        compra=compra,
        producto__codigo_barras__startswith='RP-'
    ).select_related('producto')
    
    if not detalles.exists():
        return {
            'success': False,
            'error': 'No hay productos con códigos internos en esta compra'
        }
    
    # Preparar lista de productos
    productos_list = []
    for detalle in detalles:
        productos_list.append({
            'codigo_barras': detalle.producto.codigo_barras,
            'nombre': detalle.producto.nombre,
            'precio': detalle.producto.precio_venta,
            'cantidad': detalle.cantidad
        })
    
    # Imprimir
    printer = ZebraLabelPrinter()
    return printer.imprimir_etiquetas_multiples(productos_list)