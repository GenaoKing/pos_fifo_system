"""
Utilidades para gestión de productos
Incluye generación de códigos de barra internos
"""

from apps.productos.models import Producto


def generar_codigo_barra_interno():
    """
    Genera un código de barras interno único formato RP-XXXXXX
    
    RP = Royal Plastic
    XXXXXX = Número secuencial de 6 dígitos
    
    Returns:
        str: Código generado (ej: "RP-000001")
    """
    
    # Obtener el último producto con código interno
    ultimo_producto = Producto.objects.filter(
        codigo_barras__startswith='RP-'
    ).order_by('-id').first()
    
    if not ultimo_producto:
        # Primer código interno
        numero = 1
    else:
        # Extraer número del último código y sumar 1
        try:
            ultimo_codigo = ultimo_producto.codigo_barras
            ultimo_numero = int(ultimo_codigo.replace('RP-', ''))
            numero = ultimo_numero + 1
        except:
            # Si hay error, buscar el máximo manualmente
            numero = 1
    
    # Formatear con 6 dígitos
    codigo = f"RP-{numero:06d}"
    
    # Verificar que no exista (por seguridad)
    while Producto.objects.filter(codigo_barras=codigo).exists():
        numero += 1
        codigo = f"RP-{numero:06d}"
    
    return codigo


def asignar_codigo_si_vacio(producto):
    """
    Asigna un código de barras interno si el producto no tiene uno
    
    Args:
        producto: Instancia del modelo Producto
    
    Returns:
        bool: True si se asignó código, False si ya tenía uno
    """
    
    if not producto.codigo_barras or producto.codigo_barras.strip() == '':
        producto.codigo_barras = generar_codigo_barra_interno()
        producto.save(update_fields=['codigo_barras'])
        return True
    
    return False