"""
Views para gestión de Productos y Categorías
"""
import logging

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
from apps.sync.decorators import requiere_conexion_cloud
from utils.imagenes import ImagenInvalida, nombre_seguro, validar_imagen_subida
from apps.permisos.decorators import (
    requiere_permiso_json,
    requiere_permiso_local,
    sucursal_del_request,
)
from .services import (
    CodigoBarrasDuplicado,
    IdempotenciaMutacionMaestroEnConflicto,
    IdempotenciaMutacionMaestroInvalida,
    NombreCategoriaDuplicado,
    cambiar_estado_categoria_local,
    cambiar_estado_producto_local,
    crear_categoria_local,
    crear_producto_local,
    editar_categoria_local,
    editar_producto_local,
)


logger = logging.getLogger(__name__)
MENSAJE_ERROR_GENERICO = 'No se pudo procesar la solicitud.'


def _respuesta_error_generico():
    """No expone detalles internos de errores inesperados al cliente."""
    logger.exception('Error inesperado en una operación de productos')
    return JsonResponse({
        'success': False,
        'message': MENSAJE_ERROR_GENERICO,
    }, status=400)


def _mutacion_id(request, data=None):
    """UUID obligatorio en HTTP: un reintento debe conservar su intencion."""
    identificador = (
        request.headers.get('X-Master-Mutation-ID')
        or (data or {}).get('mutation_id')
    )
    if not identificador:
        raise IdempotenciaMutacionMaestroInvalida(
            'Se requiere X-Master-Mutation-ID para mutar el catalogo local.'
        )
    return identificador


def _respuesta_error_idempotencia(exc):
    if isinstance(exc, IdempotenciaMutacionMaestroEnConflicto):
        return JsonResponse({
            'success': False,
            'code': 'master_mutation_id_conflict',
            'message': str(exc),
        }, status=409)
    return JsonResponse({
        'success': False,
        'code': 'master_mutation_id_invalid',
        'message': str(exc),
    }, status=400)


def _estado_deseado(request, campo):
    """El estado objetivo hace distinguible un retry de otro cambio distinto."""
    try:
        valor = json.loads(request.body or '{}').get(campo)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise IdempotenciaMutacionMaestroInvalida(
            'El estado objetivo debe enviarse como JSON valido.'
        ) from exc
    if not isinstance(valor, bool):
        raise IdempotenciaMutacionMaestroInvalida(
            f'El campo {campo} debe ser booleano.'
        )
    return valor


# ==========================================
# PRODUCTOS
# ==========================================

@login_required
@requiere_permiso_local('productos.ver')
def lista_productos(request):
    """Lista de productos con filtros"""
 
    productos = Producto.objects.select_related('categoria').all()
 
    # Filtro por categoría
    categoria_id = request.GET.get('categoria')
    if categoria_id:
        productos = productos.filter(categoria_id=categoria_id)
 
    # Preparar datos para el template
    productos_data = []
    marcas_set = set()
 
    for producto in productos:
        if producto.marca:
            marcas_set.add(producto.marca)
 
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
            'stock_actual': producto.stock_actual,
            'activo': producto.activo,
            # Miniatura: la grilla la pinta de 40x40 y el original sale del
            # celular del cliente. Cae al original si aun no tiene miniatura.
            'imagen': producto.imagen_preview.url if producto.imagen_preview else None,
            'imagen_original': producto.imagen.url if producto.imagen else None,
            'atributos': producto.atributos or {},
            # ─── CAMPOS NUEVOS ───
            'estado': producto.estado,
            'estado_display': producto.get_estado_display(),
            'marca': producto.marca or '',
        })
 
    # Marcas únicas ordenadas para el filtro
    marcas_lista = sorted(marcas_set)
 
    categorias = Categoria.objects.filter(activa=True).order_by('nombre')
 
    context = {
        # Objetos crudos: los serializa `json_script` en la plantilla (PRO-005).
        # `json.dumps` aca ademas doblaba la codificacion de `marcas_json`, asi
        # que el filtro de marcas recibia un STRING en vez de una lista y
        # `x-for` iteraba sus caracteres.
        'productos_json': productos_data,
        'categorias': categorias,
        'marcas_json': marcas_lista,
    }
 
    return render(request, 'productos/lista_productos.html', context)
 

@requiere_conexion_cloud(redirect_url='productos:lista')
@login_required
@requiere_permiso_json('productos.crear')
@require_http_methods(["POST"])
def crear_producto(request):
    """Crear nuevo producto vía AJAX"""
    
    try:
        data = json.loads(request.body)
        
        resultado = crear_producto_local(
            actor=request.user,
            sucursal=sucursal_del_request(request),
            datos=data,
            mutacion_id=_mutacion_id(request, data),
        )
        producto = resultado.entidad
        
        messages.success(request, f'Producto "{producto.nombre}" creado exitosamente')
        

        return JsonResponse({
            'success': True,
            'message': 'Producto creado exitosamente',
            'producto_id': producto.id,
            'sku': producto.sku,
            'codigo_barras': producto.codigo_barras,
            'master_mutation_id': str(resultado.mutacion.mutacion_id),
            'master_mutation_state': resultado.mutacion.estado,
            'idempotent_replay': resultado.repetida,
        })
        
    except (IdempotenciaMutacionMaestroInvalida, IdempotenciaMutacionMaestroEnConflicto) as exc:
        return _respuesta_error_idempotencia(exc)
    except Exception:
        return _respuesta_error_generico()


@requiere_conexion_cloud(redirect_url='productos:lista')
@login_required
@requiere_permiso_json('productos.editar')
@require_http_methods(["POST"])
def editar_producto(request, producto_id):
    """Editar producto existente vía AJAX"""
    
    try:
        producto = get_object_or_404(Producto, id=producto_id)
        data = json.loads(request.body)
        
        resultado = editar_producto_local(
            actor=request.user,
            sucursal=sucursal_del_request(request),
            producto_id=producto.id,
            datos=data,
            mutacion_id=_mutacion_id(request, data),
        )
        producto = resultado.entidad
        
        messages.success(request, f'Producto "{producto.nombre}" actualizado exitosamente')
        
        return JsonResponse({
            'success': True,
            'message': 'Producto actualizado exitosamente',
            'master_mutation_id': str(resultado.mutacion.mutacion_id),
            'master_mutation_state': resultado.mutacion.estado,
            'idempotent_replay': resultado.repetida,
        })
        
    except (IdempotenciaMutacionMaestroInvalida, IdempotenciaMutacionMaestroEnConflicto) as exc:
        return _respuesta_error_idempotencia(exc)
    except CodigoBarrasDuplicado:
        return JsonResponse({
            'success': False,
            'message': 'Ya existe otro producto con ese código de barras',
        })
    except Exception:
        return _respuesta_error_generico()


@login_required
@requiere_permiso_json('productos.eliminar')
@require_http_methods(["POST"])
def toggle_estado_producto(request, producto_id):
    """Activar/desactivar producto"""
    
    try:
        producto = get_object_or_404(Producto, id=producto_id)
        resultado = cambiar_estado_producto_local(
            actor=request.user,
            sucursal=sucursal_del_request(request),
            producto_id=producto.id,
            activo=_estado_deseado(request, 'activo'),
            mutacion_id=_mutacion_id(request),
        )
        producto = resultado.entidad
        
        estado = "activado" if producto.activo else "desactivado"
        messages.success(request, f'Producto "{producto.nombre}" {estado} exitosamente')
        
        return JsonResponse({
            'success': True,
            'activo': producto.activo,
            'master_mutation_id': str(resultado.mutacion.mutacion_id),
            'master_mutation_state': resultado.mutacion.estado,
            'idempotent_replay': resultado.repetida,
        })
        
    except (IdempotenciaMutacionMaestroInvalida, IdempotenciaMutacionMaestroEnConflicto) as exc:
        return _respuesta_error_idempotencia(exc)
    except Exception:
        return _respuesta_error_generico()


# ==========================================
# CATEGORÍAS
# ==========================================

@login_required
@requiere_permiso_local('categorias.ver')
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
        'categorias_json': categorias_data,
    }
    
    return render(request, 'productos/lista_categorias.html', context)


@login_required
@requiere_permiso_json('categorias.crear')
@require_http_methods(["POST"])
def crear_categoria(request):
    """Crear nueva categoría vía AJAX"""
    
    try:
        data = json.loads(request.body)
        
        resultado = crear_categoria_local(
            actor=request.user,
            sucursal=sucursal_del_request(request),
            datos=data,
            mutacion_id=_mutacion_id(request, data),
        )
        categoria = resultado.entidad
        
        messages.success(request, f'Categoría "{categoria.nombre}" creada exitosamente')
        
        return JsonResponse({
            'success': True,
            'message': 'Categoría creada exitosamente',
            'categoria_id': categoria.id,
            'master_mutation_id': str(resultado.mutacion.mutacion_id),
            'master_mutation_state': resultado.mutacion.estado,
            'idempotent_replay': resultado.repetida,
        })
        
    except (IdempotenciaMutacionMaestroInvalida, IdempotenciaMutacionMaestroEnConflicto) as exc:
        return _respuesta_error_idempotencia(exc)
    except NombreCategoriaDuplicado:
        return JsonResponse({
            'success': False,
            'message': 'Ya existe una categoría con ese nombre',
        })
    except Exception:
        return _respuesta_error_generico()


@login_required
@requiere_permiso_json('categorias.editar')
@require_http_methods(["POST"])
def editar_categoria(request, categoria_id):
    """Editar categoría existente vía AJAX"""
    
    try:
        categoria = get_object_or_404(Categoria, id=categoria_id)
        data = json.loads(request.body)
        
        resultado = editar_categoria_local(
            actor=request.user,
            sucursal=sucursal_del_request(request),
            categoria_id=categoria.id,
            datos=data,
            mutacion_id=_mutacion_id(request, data),
        )
        categoria = resultado.entidad
        
        messages.success(request, f'Categoría "{categoria.nombre}" actualizada exitosamente')
        
        return JsonResponse({
            'success': True,
            'message': 'Categoría actualizada exitosamente',
            'master_mutation_id': str(resultado.mutacion.mutacion_id),
            'master_mutation_state': resultado.mutacion.estado,
            'idempotent_replay': resultado.repetida,
        })
        
    except (IdempotenciaMutacionMaestroInvalida, IdempotenciaMutacionMaestroEnConflicto) as exc:
        return _respuesta_error_idempotencia(exc)
    except NombreCategoriaDuplicado:
        return JsonResponse({
            'success': False,
            'message': 'Ya existe otra categoría con ese nombre',
        })
    except Exception:
        return _respuesta_error_generico()



@login_required
@requiere_permiso_json('productos.ver')
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
        
    except Exception:
        return _respuesta_error_generico()



@login_required
@requiere_permiso_json('categorias.eliminar')
@require_http_methods(["POST"])
def toggle_estado_categoria(request, categoria_id):
    """Activar/desactivar categoría"""
    
    try:
        categoria = get_object_or_404(Categoria, id=categoria_id)
        resultado = cambiar_estado_categoria_local(
            actor=request.user,
            sucursal=sucursal_del_request(request),
            categoria_id=categoria.id,
            activa=_estado_deseado(request, 'activa'),
            mutacion_id=_mutacion_id(request),
        )
        categoria = resultado.entidad
        
        estado = "activada" if categoria.activa else "desactivada"
        messages.success(request, f'Categoría "{categoria.nombre}" {estado} exitosamente')
        
        return JsonResponse({
            'success': True,
            'activa': categoria.activa,
            'master_mutation_id': str(resultado.mutacion.mutacion_id),
            'master_mutation_state': resultado.mutacion.estado,
            'idempotent_replay': resultado.repetida,
        })
        
    except (IdempotenciaMutacionMaestroInvalida, IdempotenciaMutacionMaestroEnConflicto) as exc:
        return _respuesta_error_idempotencia(exc)
    except Exception:
        return _respuesta_error_generico()

@login_required
@requiere_permiso_json('productos.fotografiar')
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

        # PRO-006: el archivo se asignaba directo al campo y se guardaba, sin
        # comprobar que fuera una imagen, ni su tipo, ni su tamano. Se subieron
        # bytes HTML con `Content-Type: text/plain` y quedaron publicados desde
        # media. `ImageField` no valida solo cuando se asigna por codigo: esa
        # validacion vive en el formulario, y aca no hay formulario.
        archivo = request.FILES['imagen']
        try:
            formato = validar_imagen_subida(archivo)
        except ImagenInvalida as exc:
            return JsonResponse(
                {'success': False, 'message': str(exc)}, status=400,
            )

        # El nombre lo pone el servidor: el del cliente puede traer rutas,
        # caracteres de control o una extension que no corresponde al contenido.
        archivo.name = nombre_seguro(formato, prefijo=f'producto-{producto.id}')

        producto.imagen = archivo
        producto.save()
        
        messages.success(request, f'Imagen actualizada para "{producto.nombre}"')
        
        return JsonResponse({
            'success': True,
            'imagen_url': producto.imagen.url if producto.imagen else None,
            'imagen': producto.imagen_preview.url if producto.imagen_preview else None
        })
        
    except Exception:
        return _respuesta_error_generico()


@login_required
@requiere_permiso_json('productos.fotografiar')
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
        
    except Exception:
        return _respuesta_error_generico()
    

@login_required
@requiere_permiso_json('categorias.ver')
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
    except Exception:
        return _respuesta_error_generico()
