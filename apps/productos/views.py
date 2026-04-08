"""
Views para gestión de Productos y Categorías
"""

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.db.models import Q, Sum, Count
import json
from .utils import generar_codigo_barra_interno, asignar_codigo_si_vacio
from utils.impresoras.zebra import imprimir_etiqueta_producto
from apps.configuracion.decorators import requiere_modulo

from .models import Producto, Categoria


# ==========================================
# PRODUCTOS
# ==========================================

@login_required
def lista_productos(request):
    """Lista de productos con filtros"""
    
    # Obtener todos los productos con información relacionada
    productos = Producto.objects.select_related('categoria').all()
    
    # Filtro por categoría (desde GET params)
    categoria_id = request.GET.get('categoria')
    if categoria_id:
        productos = productos.filter(categoria_id=categoria_id)
    
    # Preparar datos para el template
    productos_data = []
    for producto in productos:
        productos_data.append({
            'id': producto.id,
            'sku': producto.sku,
            'codigo_barras': producto.codigo_barras,
            'nombre': producto.nombre,
            'descripcion': producto.descripcion,
            'categoria_id': producto.categoria.id,
            'categoria_nombre': producto.categoria.nombre,
            'precio_venta': str(producto.precio_venta),
            'stock_minimo': producto.stock_minimo,
            'stock_actual': producto.stock_actual,  # property del modelo
            'activo': producto.activo,
            'imagen': producto.imagen.url if producto.imagen else None,
            'atributos': producto.atributos or {},
        })
    
    # Obtener todas las categorías activas para los filtros
    categorias = Categoria.objects.filter(activa=True).order_by('nombre')
    
    context = {
        'productos_json': json.dumps(productos_data),
        'categorias': categorias,
    }
    
    return render(request, 'productos/lista_productos.html', context)


@login_required
@require_http_methods(["POST"])
def crear_producto(request):
    """Crear nuevo producto vía AJAX"""
    
    try:
        data = json.loads(request.body)
        
        # Generar SKU automático
        sku = Producto.generar_sku()
        
        # Generar código de barras interno siempre
        codigo_barras = generar_codigo_barra_interno()
        
        # Crear el producto
        producto = Producto.objects.create(
            sku=sku,
            codigo_barras=codigo_barras,
            nombre=data['nombre'],
            descripcion=data.get('descripcion', ''),
            categoria_id=data['categoria_id'],
            precio_venta=data['precio_venta'],
            stock_minimo=data.get('stock_minimo', 5),
            activo=True,
            atributos=data.get('atributos', {})
        )
        
        messages.success(request, f'Producto "{producto.nombre}" creado exitosamente')
        
        return JsonResponse({
            'success': True,
            'message': 'Producto creado exitosamente',
            'producto_id': producto.id,
            'sku': producto.sku,
            'codigo_barras': producto.codigo_barras,
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)


@login_required
@require_http_methods(["POST"])
def editar_producto(request, producto_id):
    """Editar producto existente vía AJAX"""
    
    try:
        producto = get_object_or_404(Producto, id=producto_id)
        data = json.loads(request.body)
        
        # Validar unicidad de SKU y código de barras (excluyendo el producto actual)
        if Producto.objects.filter(sku=data['sku']).exclude(id=producto_id).exists():
            return JsonResponse({
                'success': False,
                'message': 'Ya existe otro producto con ese SKU'
            })
        
        if Producto.objects.filter(codigo_barras=data['codigo_barras']).exclude(id=producto_id).exists():
            return JsonResponse({
                'success': False,
                'message': 'Ya existe otro producto con ese código de barras'
            })
        
        # Actualizar campos
        producto.sku = data['sku']
        producto.codigo_barras = data['codigo_barras']
        producto.nombre = data['nombre']
        producto.descripcion = data.get('descripcion', '')
        producto.categoria_id = data['categoria_id']
        producto.precio_venta = data['precio_venta']
        producto.stock_minimo = data.get('stock_minimo', 5)
        producto.activo = data.get('activo', True)
        producto.atributos = data.get('atributos', {})
        
        producto.save()
        
        messages.success(request, f'Producto "{producto.nombre}" actualizado exitosamente')
        
        return JsonResponse({
            'success': True,
            'message': 'Producto actualizado exitosamente'
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)


@login_required
@require_http_methods(["POST"])
def toggle_estado_producto(request, producto_id):
    """Activar/desactivar producto"""
    
    try:
        producto = get_object_or_404(Producto, id=producto_id)
        producto.activo = not producto.activo
        producto.save()
        
        estado = "activado" if producto.activo else "desactivado"
        messages.success(request, f'Producto "{producto.nombre}" {estado} exitosamente')
        
        return JsonResponse({
            'success': True,
            'activo': producto.activo
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)


# ==========================================
# CATEGORÍAS
# ==========================================

@login_required
def lista_categorias(request):
    """Lista de categorías con sus productos"""
    
    # Obtener todas las categorías con conteo de productos
    categorias = Categoria.objects.annotate(
        productos_count=Count('productos', filter=Q(productos__activo=True))
    ).prefetch_related('productos').all()
    
    # Preparar datos para el template
    categorias_data = []
    for categoria in categorias:
        productos_list = []
        for producto in categoria.productos.filter(activo=True)[:5]:  # Solo primeros 5
            productos_list.append({
                'id': producto.id,
                'nombre': producto.nombre,
                'sku': producto.sku,
                'precio_venta': str(producto.precio_venta),
                'stock_actual': producto.stock_actual,
                'activo': producto.activo,
            })
        
        categorias_data.append({
            'id': categoria.id,
            'nombre': categoria.nombre,
            'descripcion': categoria.descripcion,
            'activa': categoria.activa,
            'total_productos': categoria.productos_count,
            'productos': productos_list,
        })
    
    context = {
        'categorias_json': json.dumps(categorias_data),
    }
    
    return render(request, 'productos/lista_categorias.html', context)


@login_required
@require_http_methods(["POST"])
def crear_categoria(request):
    """Crear nueva categoría vía AJAX"""
    
    try:
        data = json.loads(request.body)
        
        # Validar que el nombre sea único
        if Categoria.objects.filter(nombre=data['nombre']).exists():
            return JsonResponse({
                'success': False,
                'message': 'Ya existe una categoría con ese nombre'
            })
        
        # Crear la categoría
        categoria = Categoria.objects.create(
            nombre=data['nombre'],
            descripcion=data.get('descripcion', ''),
            activa=True
        )
        
        messages.success(request, f'Categoría "{categoria.nombre}" creada exitosamente')
        
        return JsonResponse({
            'success': True,
            'message': 'Categoría creada exitosamente',
            'categoria_id': categoria.id
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)


@login_required
@require_http_methods(["POST"])
def editar_categoria(request, categoria_id):
    """Editar categoría existente vía AJAX"""
    
    try:
        categoria = get_object_or_404(Categoria, id=categoria_id)
        data = json.loads(request.body)
        
        # Validar unicidad del nombre (excluyendo la categoría actual)
        if Categoria.objects.filter(nombre=data['nombre']).exclude(id=categoria_id).exists():
            return JsonResponse({
                'success': False,
                'message': 'Ya existe otra categoría con ese nombre'
            })
        
        # Actualizar campos
        categoria.nombre = data['nombre']
        categoria.descripcion = data.get('descripcion', '')
        categoria.activa = data.get('activa', True)
        
        categoria.save()
        
        messages.success(request, f'Categoría "{categoria.nombre}" actualizada exitosamente')
        
        return JsonResponse({
            'success': True,
            'message': 'Categoría actualizada exitosamente'
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)



@login_required
@require_http_methods(["POST"])
@requiere_modulo('etiquetas_zebra')
def imprimir_etiqueta(request, producto_id):
    """Imprimir etiqueta de un producto"""
    
    try:
        producto = get_object_or_404(Producto, id=producto_id)
        data = json.loads(request.body)
        cantidad = int(data.get('cantidad', 1))
        
        if cantidad < 1 or cantidad > 100:
            return JsonResponse({
                'success': False,
                'message': 'La cantidad debe estar entre 1 y 100'
            })
        
        # Imprimir
        resultado = producto.imprimir_etiqueta(cantidad)
        
        if resultado['success']:
            messages.success(request, f'Se imprimieron {cantidad} etiqueta(s) de "{producto.nombre}"')
        else:
            messages.error(request, f'Error al imprimir: {resultado.get("error", "Error desconocido")}')
        
        return JsonResponse(resultado)
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)



@login_required
@require_http_methods(["POST"])
def toggle_estado_categoria(request, categoria_id):
    """Activar/desactivar categoría"""
    
    try:
        categoria = get_object_or_404(Categoria, id=categoria_id)
        categoria.activa = not categoria.activa
        categoria.save()
        
        estado = "activada" if categoria.activa else "desactivada"
        messages.success(request, f'Categoría "{categoria.nombre}" {estado} exitosamente')
        
        return JsonResponse({
            'success': True,
            'activa': categoria.activa
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)

@login_required
@require_http_methods(["POST"])
def subir_imagen_producto(request, producto_id):
    """Subir o actualizar imagen de un producto"""
    
    try:
        producto = get_object_or_404(Producto, id=producto_id)
        
        if 'imagen' not in request.FILES:
            return JsonResponse({
                'success': False,
                'message': 'No se recibió ninguna imagen'
            }, status=400)
        
        # Guardar la imagen
        producto.imagen = request.FILES['imagen']
        producto.save()
        
        messages.success(request, f'Imagen actualizada para "{producto.nombre}"')
        
        return JsonResponse({
            'success': True,
            'imagen_url': producto.imagen.url if producto.imagen else None
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)


@login_required
@require_http_methods(["POST"])
def eliminar_imagen_producto(request, producto_id):
    """Eliminar imagen de un producto"""
    
    try:
        producto = get_object_or_404(Producto, id=producto_id)
        
        if producto.imagen:
            # Eliminar archivo físico
            producto.imagen.delete(save=False)
            producto.imagen = None
            producto.save()
            
            messages.success(request, f'Imagen eliminada de "{producto.nombre}"')
        
        return JsonResponse({
            'success': True
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)
    

@login_required
def obtener_config_atributos(request, categoria_id):
    """Devuelve la configuración de atributos de una categoría"""
    try:
        categoria = get_object_or_404(Categoria, id=categoria_id)
        
        return JsonResponse({
            'success': True,
            'categoria_id': categoria.id,
            'categoria_nombre': categoria.nombre,
            'tipo_negocio': categoria.tipo_negocio,
            'atributos_configurados': categoria.atributos_configurados or {}
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)