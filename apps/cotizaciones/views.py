"""
Views para Gestion de Cotizaciones
apps/cotizaciones/views.py

Funcionalidades:
1. Crear cotizacion (sin afectar inventario)
2. Listar cotizaciones
3. Ver detalle
4. Convertir cotizacion a venta (carga carrito POS)
"""


import json
import logging
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.clientes.models import Cliente
from apps.configuracion.decorators import requiere_modulo
from apps.cotizaciones.pdf_generator import generar_pdf_cotizacion
from apps.permisos.decorators import (
    requiere_permiso_json,
    requiere_permiso_local,
)
from apps.productos.models import Producto, productos_vendibles
from apps.sucursales.models import get_sucursal_actual
from apps.sync import events as sync_events

from .models import Cotizacion, DetalleCotizacion

logger = logging.getLogger('cotizaciones')


@login_required
@requiere_modulo('cotizaciones')
@requiere_permiso_local('cotizaciones.ver')
def lista_cotizaciones(request):
    """Lista de cotizaciones"""

    cotizaciones = _cotizaciones_en_alcance(request)

    estado = request.GET.get('estado')
    if estado:
        cotizaciones = cotizaciones.filter(estado=estado)

    context = {
        'cotizaciones': cotizaciones,
    }

    return render(request, 'cotizaciones/lista_cotizaciones.html', context)


@login_required
@requiere_modulo('cotizaciones')
@requiere_permiso_local('cotizaciones.crear')
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


def _cotizaciones_en_alcance(request, *, para_bloquear=False):
    """
    Cotizaciones que este operador puede ver (COT-005).

    Los listados partian de `.all()` y las consultas por id no filtraban nada:
    un operador de una sucursal podia listar, abrir, descargar en PDF y cargar
    en el POS la cotizacion de otra — con su cliente, sus precios negociados y
    sus condiciones.

    Las cotizaciones sin sucursal (anteriores a la Fase 2) quedan visibles: darlas
    por ajenas volveria invisible la historia de una instalacion sin migrar.
    """
    from django.db.models import Q

    # `select_related('venta')` produce un LEFT JOIN —la FK es nullable— y
    # PostgreSQL no admite `FOR UPDATE` sobre el lado nullable de un outer
    # join. Para el camino que bloquea la fila se omite.
    base = Cotizacion.objects.all()
    if not para_bloquear:
        base = base.select_related('cliente', 'usuario', 'venta')

    sucursal = getattr(request, 'sucursal', None) or get_sucursal_actual()
    if sucursal is None:
        return base
    return base.filter(Q(sucursal=sucursal) | Q(sucursal__isnull=True))


def _precio_autorizado(producto, item, *, puede_negociar):
    """
    Precio de una linea de cotizacion, decidido en el servidor.

    El endpoint aceptaba `precio_unitario` del JSON y lo persistia sin
    compararlo con nada (COT-002). Y ese numero no se queda en el documento:
    `_validar_precios` de ventas lo trata como **fuente autorizada de precio**.
    O sea que la cotizacion funcionaba como un mecanismo de autorizacion creado
    por el mismo cliente no confiable que propone el valor.

    Se reprodujo: una cajera con `ventas.crear` y SIN
    `ventas.aplicar_descuento` guardo una cotizacion de una unidad a RD$0.01 y
    despues vendio cinco a ese precio. El descuento real quedaba disfrazado de
    "precio cotizado" y esquivaba por completo el permiso de descuentos.

    Reglas:
      - Sin precio en el payload, o igual al vigente -> el vigente.
      - Por ENCIMA del vigente -> se acepta: cotizar mas caro no es un descuento
        encubierto (recargos, condiciones especiales), y el gate de ventas lo
        cubre igual porque el precio queda autorizado explicitamente.
      - Por DEBAJO -> exige `cotizaciones.precio_negociado`.
    """
    from django.core.exceptions import PermissionDenied

    vigente = Decimal(str(producto.precio_venta)).quantize(Decimal('0.01'))

    crudo = item.get('precio_unitario')
    if crudo in (None, ''):
        return vigente

    try:
        pedido = Decimal(str(crudo)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'Precio invalido para "{producto.nombre}".')

    if pedido < Decimal('0'):
        raise ValueError(f'El precio de "{producto.nombre}" no puede ser negativo.')

    if pedido >= vigente or pedido == vigente:
        return pedido

    if not puede_negociar:
        raise PermissionDenied(
            f'Cotizar "{producto.nombre}" a ${pedido} (vigente ${vigente}) '
            f'requiere el permiso "cotizaciones.precio_negociado": una '
            f'cotizacion por debajo del precio se convierte en precio '
            f'autorizado de venta.'
        )
    return pedido


@login_required
@requiere_permiso_json('cotizaciones.crear')
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

            puede_negociar = request.user.tiene_permiso(
                'cotizaciones.precio_negociado',
                sucursal=cotizacion.sucursal,
            )

            for item in productos_data:
                producto = productos_vendibles(
                    Producto.objects.select_related('categoria')
                ).get(id=item['producto_id'])
                cantidad = int(item['cantidad'])
                precio_unitario = _precio_autorizado(
                    producto, item, puede_negociar=puede_negociar,
                )
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

    except PermissionDenied as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=403)
    except Producto.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Uno de los productos no existe o no esta disponible para la venta'
        }, status=400)
    except (json.JSONDecodeError, KeyError, InvalidOperation, ValueError) as exc:
        return JsonResponse(
            {'success': False, 'error': f'Datos invalidos: {exc}'}, status=400,
        )
    except Exception:
        # COT-014: el texto de la excepcion iba literal al navegador.
        logger.exception('Error creando una cotizacion')
        return JsonResponse(
            {'success': False, 'error': 'No se pudo crear la cotizacion.'},
            status=500,
        )


@login_required
@requiere_modulo('cotizaciones')
@requiere_permiso_local('cotizaciones.ver')
def detalle_cotizacion(request, cotizacion_id):
    """Detalle de una cotizacion"""

    cotizacion = get_object_or_404(
        _cotizaciones_en_alcance(request), id=cotizacion_id,
    )
    detalles = cotizacion.detalles.select_related('producto').all()

    context = {
        'cotizacion': cotizacion,
        'detalles': detalles,
    }

    return render(request, 'cotizaciones/detalle_cotizacion.html', context)


@login_required
@requiere_permiso_json('cotizaciones.ver')
@require_http_methods(["GET"])
@requiere_modulo('cotizaciones')
def obtener_datos_cotizacion(request, cotizacion_id):
    """
    API: Devuelve los datos de una cotizacion en formato JSON
    para cargar en el POS y convertir a venta.
    """
    # La superficie mas sensible de COT-005: esta es la que CARGA el carrito
    # del POS con los precios negociados de la cotizacion.
    cotizacion = get_object_or_404(
        _cotizaciones_en_alcance(request), id=cotizacion_id,
    )

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
@requiere_permiso_json('cotizaciones.crear')
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

    Se conserva para clientes externos y para conversiones manuales.

    COT-006: tres agujeros que tenia esta ruta, todos por confiar en el payload:

      - **No bloqueaba la fila.** Dos llamadas simultaneas veian ambas
        `PENDIENTE` y las dos convertian.
      - **Aceptaba marcar convertida SIN venta.** Una cotizacion podia quedar
        `CONVERTIDA` con `venta=NULL`: la oferta se cerraba sin que existiera la
        operacion que supuestamente la consumio.
      - **Aceptaba una venta AJENA.** `Venta.objects.get(id=venta_id)` no
        comprobaba ni el cliente, ni la sucursal, ni que esa venta no estuviera
        ya vinculada a otra cotizacion.
    """
    try:
        data = json.loads(request.body)
        venta_id = data.get('venta_id')

        if not venta_id:
            return JsonResponse({
                'success': False,
                'error': (
                    'Marcar una cotizacion como convertida requiere la venta '
                    'que la consumio.'
                ),
            }, status=400)

        from apps.ventas.models import Venta

        with transaction.atomic():
            cotizacion = get_object_or_404(
                _cotizaciones_en_alcance(request, para_bloquear=True)
                .select_for_update(),
                id=cotizacion_id,
            )

            if not cotizacion.puede_convertirse:
                return JsonResponse({
                    'success': False,
                    'error': 'Cotizacion ya fue convertida o esta vencida',
                }, status=409)

            venta = Venta.objects.filter(id=venta_id).first()
            if venta is None:
                return JsonResponse(
                    {'success': False, 'error': 'La venta indicada no existe.'},
                    status=400,
                )

            if venta.cliente_id != cotizacion.cliente_id:
                return JsonResponse({
                    'success': False,
                    'error': 'La venta indicada es de otro cliente.',
                }, status=409)

            if (
                cotizacion.sucursal_id is not None
                and venta.sucursal_id is not None
                and venta.sucursal_id != cotizacion.sucursal_id
            ):
                return JsonResponse({
                    'success': False,
                    'error': 'La venta indicada es de otra sucursal.',
                }, status=409)

            ya_vinculada = Cotizacion.objects.filter(
                venta_id=venta.id,
            ).exclude(pk=cotizacion.pk).exists()
            if ya_vinculada:
                return JsonResponse({
                    'success': False,
                    'error': 'Esa venta ya esta vinculada a otra cotizacion.',
                }, status=409)

            cotizacion.estado = 'CONVERTIDA'
            cotizacion.venta = venta
            cotizacion.save()

            sync_events.evento_cotizacion_convertida(cotizacion)

        return JsonResponse({
            'success': True,
            'message': 'Cotizacion marcada como convertida'
        })

    except Http404:
        raise
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return JsonResponse(
            {'success': False, 'error': f'Datos invalidos: {exc}'}, status=400,
        )
    except Exception:
        logger.exception('Error marcando una cotizacion como convertida')
        return JsonResponse(
            {'success': False, 'error': 'No se pudo marcar la cotizacion.'},
            status=400,
        )
    

@login_required
@requiere_modulo('cotizaciones')
def descargar_pdf_cotizacion(request, cotizacion_id):
    """
    Genera y descarga el PDF de una cotizacion.
    """
    try:
        cotizacion = get_object_or_404(
            _cotizaciones_en_alcance(request), id=cotizacion_id,
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
