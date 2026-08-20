"""
Views para Gestion de Cotizaciones
apps/cotizaciones/views.py

Funcionalidades:
1. Crear cotizacion (sin afectar inventario)
2. Listar cotizaciones
3. Ver detalle
4. Convertir cotizacion a venta (carga carrito POS)
"""


from apps.configuracion.decorators import requiere_modulo
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods
from django.db import transaction
from decimal import Decimal
import json

from apps.cotizaciones.pdf_generator import generar_pdf_cotizacion

from .models import Cotizacion, DetalleCotizacion
from apps.clientes.models import Cliente
from apps.productos.models import Producto
from apps.sucursales.models import get_sucursal_actual
from apps.sync import events as sync_events


@login_required
@requiere_modulo('cotizaciones')
def lista_cotizaciones(request):
    """Lista de cotizaciones"""

    cotizaciones = Cotizacion.objects.select_related(
        'cliente', 'usuario', 'venta'
    ).all()

    estado = request.GET.get('estado')
    if estado:
        cotizaciones = cotizaciones.filter(estado=estado)

    context = {
        'cotizaciones': cotizaciones,
    }

    return render(request, 'cotizaciones/lista_cotizaciones.html', context)


@login_required
@requiere_modulo('cotizaciones')
def crear_cotizacion(request):
    """
    Formulario para crear cotizacion.
    Usa interfaz similar al POS pero sin procesar pago.
    """
    if request.method == 'GET':
        productos = Producto.objects.filter(activo=True).order_by('nombre')
        clientes = Cliente.objects.filter(activo=True).exclude(tipo='CONTADO')

        context = {
            'productos': productos,
            'clientes': clientes,
        }
        return render(request, 'cotizaciones/crear_cotizacion.html', context)


@login_required
@require_http_methods(["POST"])
@requiere_modulo('cotizaciones')
def guardar_cotizacion(request):
    """
    API para guardar cotizacion via AJAX.

    Datos esperados (JSON):
    {
        "cliente_id": 1,  (opcional, null = CONTADO)
        "notas": "...",
        "productos": [
            {
                "producto_id": 1,
                "cantidad": 5,
                "precio_unitario": 100.00,
                "descuento": 10.00
            }
        ]
    }
    """
    try:
        data = json.loads(request.body)

        productos_data = data.get('productos', [])
        if not productos_data:
            return JsonResponse({
                'success': False,
                'error': 'Debe agregar al menos un producto'
            }, status=400)

        with transaction.atomic():
            # Obtener cliente
            cliente_id = data.get('cliente_id')
            if cliente_id:
                cliente = Cliente.objects.get(id=cliente_id)
            else:
                cliente = Cliente.get_cliente_contado()

            # Crear cotizacion
            cotizacion = Cotizacion.objects.create(
                cliente=cliente,
                usuario=request.user,
                sucursal=getattr(request, 'sucursal', None) or get_sucursal_actual(),
                total=Decimal('0')
            )

            total_cotizacion = Decimal('0')
            subtotal_cotizacion = Decimal('0')
            descuento_cotizacion = Decimal('0')

            for item in productos_data:
                producto = Producto.objects.get(id=item['producto_id'])
                cantidad = int(item['cantidad'])
                precio_unitario = Decimal(str(item['precio_unitario']))
                descuento_monto = Decimal(str(item.get('descuento', 0)))

                detalle = DetalleCotizacion.objects.create(
                    cotizacion=cotizacion,
                    producto=producto,
                    cantidad=cantidad,
                    precio_unitario=precio_unitario,
                    descuento_monto=descuento_monto,
                    subtotal=Decimal('0'),
                    total_linea=Decimal('0'),
                )

                subtotal_cotizacion += detalle.subtotal
                descuento_cotizacion += detalle.descuento_monto
                total_cotizacion += detalle.total_linea

            cotizacion.subtotal = subtotal_cotizacion
            cotizacion.descuento_total = descuento_cotizacion
            cotizacion.total = total_cotizacion
            cotizacion.notas = data.get('notas', '').strip() or None
            cotizacion.save()

            sync_events.evento_cotizacion_creada(cotizacion)

            return JsonResponse({
                'success': True,
                'message': 'Cotizacion creada exitosamente',
                'cotizacion_id': cotizacion.id,
                'numero_cotizacion': cotizacion.numero_cotizacion,
                'total': float(total_cotizacion),
            })

    except Producto.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Uno de los productos no existe'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al crear cotizacion: {str(e)}'
        }, status=500)


@login_required
@requiere_modulo('cotizaciones')
def detalle_cotizacion(request, cotizacion_id):
    """Detalle de una cotizacion"""

    cotizacion = get_object_or_404(
        Cotizacion.objects.select_related('cliente', 'usuario', 'venta'),
        id=cotizacion_id
    )
    detalles = cotizacion.detalles.select_related('producto').all()

    context = {
        'cotizacion': cotizacion,
        'detalles': detalles,
    }

    return render(request, 'cotizaciones/detalle_cotizacion.html', context)


@login_required
@require_http_methods(["GET"])
@requiere_modulo('cotizaciones')
def obtener_datos_cotizacion(request, cotizacion_id):
    """
    API: Devuelve los datos de una cotizacion en formato JSON
    para cargar en el POS y convertir a venta.
    """
    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)

    if not cotizacion.puede_convertirse:
        return JsonResponse({
            'success': False,
            'error': 'Esta cotizacion ya fue convertida a venta'
        })

    detalles = cotizacion.detalles.select_related('producto').all()

    productos_carrito = []
    for detalle in detalles:
        p = detalle.producto
        productos_carrito.append({
            'id': p.id,
            'sku': p.sku,
            'nombre': p.nombre,
            'precio_venta': float(detalle.precio_unitario),
            'cantidad': detalle.cantidad,
            'descuento': float(detalle.descuento_monto),
            'stock_actual': p.stock_actual,
        })

    return JsonResponse({
        'success': True,
        'cotizacion_id': cotizacion.id,
        'numero_cotizacion': cotizacion.numero_cotizacion,
        'cliente': {
            'id': cotizacion.cliente.id,
            'nombre': cotizacion.cliente.nombre,
            'cedula_rnc': cotizacion.cliente.cedula_rnc or '',
        } if not cotizacion.cliente.es_contado else None,
        'productos': productos_carrito,
        'notas': cotizacion.notas or '',
    })


@login_required
@require_http_methods(["POST"])
@requiere_modulo('cotizaciones')
def marcar_convertida(request, cotizacion_id):
    """
    Marca una cotizacion como convertida y la vincula a la venta.

    OJO: el POS ya NO usa este endpoint. La conversion ocurre dentro de la
    transaccion de la venta (`procesar_venta_service` recibe `cotizacion_id`,
    bloquea la cotizacion y la marca en el mismo atomic). Antes era un segundo
    request desde el navegador: si se perdia, la cotizacion quedaba PENDIENTE y
    podia venderse otra vez, duplicando inventario consumido.

    Se conserva para clientes externos y para conversiones manuales; sigue
    validando `puede_convertirse`, asi que una cotizacion ya convertida no se
    puede re-vincular.
    """
    try:
        data = json.loads(request.body)
        venta_id = data.get('venta_id')

        cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)

        if not cotizacion.puede_convertirse:
            return JsonResponse({
                'success': False,
                'error': 'Cotizacion ya fue convertida'
            })

        cotizacion.estado = 'CONVERTIDA'
        if venta_id:
            from apps.ventas.models import Venta
            cotizacion.venta = Venta.objects.get(id=venta_id)
        cotizacion.save()

        sync_events.evento_cotizacion_convertida(cotizacion)

        return JsonResponse({
            'success': True,
            'message': 'Cotizacion marcada como convertida'
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)
    

@login_required
@requiere_modulo('cotizaciones')
def descargar_pdf_cotizacion(request, cotizacion_id):
    """
    Genera y descarga el PDF de una cotizacion.
    """
    try:
        cotizacion = get_object_or_404(
            Cotizacion.objects.select_related('cliente', 'usuario'),
            id=cotizacion_id
        )

        # Generar PDF
        pdf_buffer = generar_pdf_cotizacion(cotizacion)

        # Preparar respuesta
        response = HttpResponse(pdf_buffer, content_type='application/pdf')
        filename = f'cotizacion_{cotizacion.numero_cotizacion}.pdf'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        return response

    except Exception as e:
        messages.error(request, f'Error al generar PDF: {str(e)}')
        return redirect('cotizaciones:detalle', cotizacion_id=cotizacion_id)
