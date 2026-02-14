"""
Driver para impresora Zebra LP 2824 (EPL2)
Maneja impresión de etiquetas con código de barras CODE128

Especificaciones:
- Tamaño etiqueta: 37mm x 27mm
- Gap entre etiquetas: 5mm (2mm gap real medido)
- Formato: Código de barras + Nombre + Precio
- Conexión: USB
- Lenguaje: EPL2 (Eltron Programming Language 2)

IMPORTANTE: La LP 2824 usa EPL2, NO ZPL
"""

import win32print
import win32ui


class ZebraLabelPrinter:
    """
    Clase para manejar impresión de etiquetas en impresora Zebra LP 2824
    Usa comandos EPL2 (Eltron Programming Language 2)
    """
    
    def __init__(self, printer_name="ZDesigner LP 2824"):
        """
        Inicializa el driver de la impresora Zebra
        
        Args:
            printer_name: Nombre exacto de la impresora en Windows
        """
        self.printer_name = printer_name
        self.dpi = 203  # DPI de la LP 2824
        
        # Dimensiones en mm
        self.label_width_mm = 37
        self.label_height_mm = 27
        self.gap_mm = 2  # Gap real aproximado
        
        # Convertir a dots (puntos de impresora)
        self.label_width_dots = self.mm_to_dots(self.label_width_mm)
        self.label_height_dots = self.mm_to_dots(self.label_height_mm)
        self.gap_dots = self.mm_to_dots(self.gap_mm)
        
    def mm_to_dots(self, mm):
        """Convierte milímetros a dots según DPI (203 dpi = 8 dots por mm)"""
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
    
    def generar_epl2(self, codigo_barras, nombre_producto, precio, cantidad=1):
        """
        Genera comandos EPL2 para imprimir etiqueta
        
        EPL2 (Eltron Programming Language 2) es el lenguaje para LP 2824
        
        Args:
            codigo_barras: Código a imprimir (ej: "RP-000123")
            nombre_producto: Nombre del producto (máx 40 chars en 2 líneas)
            precio: Precio del producto
            cantidad: Número de etiquetas a imprimir
            
        Returns:
            String con comandos EPL2
        """
        
        # Dividir nombre en 2 líneas si es muy largo
        if len(nombre_producto) > 20:
            nombre_linea1 = nombre_producto[:20].strip()
            nombre_linea2 = nombre_producto[20:40].strip()
        else:
            nombre_linea1 = nombre_producto
            nombre_linea2 = ""
        
        # Formatear precio
        precio_str = f"RD$ {float(precio):,.2f}"
        
        # Ajustar posición del código según si hay 1 o 2 líneas de nombre
        barcode_y = 50 if nombre_linea2 else 40
        precio_y = 155 if nombre_linea2 else 145
        
        # Construir comandos EPL2 - SIN ESPACIOS AL INICIO
        epl2_commands = f"""N
q300
Q220,16
S2
D10
A10,8,0,3,1,1,N,"{nombre_linea1}"
"""
    
        # Agregar segunda línea del nombre si existe
        if nombre_linea2:
            epl2_commands += f'A10,24,0,3,1,1,N,"{nombre_linea2}"\n'
        
        # Código de barras con altura moderada (65) y mostrando código debajo
        # Tipo 1 = Code 39 (el que funcionaba)
        # 2,4 = ancho de barras (igual que funcionaba)
        # 65 = altura (más grande que 50, pero no tanto como 85)
        # B = mostrar código legible debajo
        epl2_commands += f"""B10,{barcode_y},0,1,2,4,65,B,"{codigo_barras}"
A10,{precio_y},0,4,1,2,N,"{precio_str}"
P{cantidad}
"""
    
        return epl2_commands
    
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
            
            # Generar EPL2
            epl2_commands = self.generar_epl2(
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
                win32print.WritePrinter(handle, epl2_commands.encode('utf-8'))
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