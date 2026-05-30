"""
Views para Gestion de Clientes
apps/clientes/views.py
"""

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.db.models import Q, Sum, Count
import json

from .models import Cliente


def _resumen_credito(cliente):
    from apps.cuentas_por_cobrar.services import resumen_credito_cliente

    return resumen_credito_cliente(cliente)


@login_required
def lista_clientes(request):
    """Lista de clientes con filtros"""

    clientes = Cliente.objects.exclude(tipo='CONTADO').all()

    tipo = request.GET.get('tipo')
    if tipo:
        clientes = clientes.filter(tipo=tipo)

    clientes_data = []
    for cliente in clientes:
        credito = _resumen_credito(cliente)
        clientes_data.append({
            'id': cliente.id,
            'tipo': cliente.tipo,
            'nombre': cliente.nombre,
            'cedula_rnc': cliente.cedula_rnc or '',
            'telefono': cliente.telefono or '',
            'direccion': cliente.direccion or '',
            'limite_credito': str(cliente.limite_credito),
            'condiciones_pago': cliente.condiciones_pago or '',
            'notas': cliente.notas or '',
            'activo': cliente.activo,
            'total_compras': cliente.total_compras,
            'monto_total_compras': str(cliente.monto_total_compras),
            'saldo_pendiente': str(credito['saldo_pendiente']),
            'credito_disponible': str(credito['credito_disponible']),
            'monto_vencido': str(credito['monto_vencido']),
        })

    context = {
        'clientes_json': json.dumps(clientes_data),
    }

    return render(request, 'clientes/lista_clientes.html', context)


@login_required
@require_http_methods(["POST"])
def crear_cliente(request):
    """Crear nuevo cliente via AJAX"""

    try:
        data = json.loads(request.body)

        cedula_rnc = data.get('cedula_rnc', '').strip() or None

        if cedula_rnc and Cliente.objects.filter(cedula_rnc=cedula_rnc).exists():
            return JsonResponse({
                'success': False,
                'message': 'Ya existe un cliente con esa cedula/RNC'
            })

        cliente = Cliente.objects.create(
            tipo=data.get('tipo', 'PERSONAL'),
            nombre=data['nombre'].strip(),
            cedula_rnc=cedula_rnc,
            telefono=data.get('telefono', '').strip() or None,
            direccion=data.get('direccion', '').strip() or None,
            limite_credito=data.get('limite_credito', 0),
            condiciones_pago=data.get('condiciones_pago', '').strip() or None,
            notas=data.get('notas', '').strip() or None,
            activo=True
        )

        return JsonResponse({
            'success': True,
            'message': f'Cliente "{cliente.nombre}" creado exitosamente',
            'cliente_id': cliente.id,
            'cliente': {
                'id': cliente.id,
                'tipo': cliente.tipo,
                'nombre': cliente.nombre,
                'cedula_rnc': cliente.cedula_rnc or '',
                'telefono': cliente.telefono or '',
            }
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)


@login_required
@require_http_methods(["POST"])
def editar_cliente(request, cliente_id):
    """Editar cliente existente via AJAX"""

    try:
        cliente = get_object_or_404(Cliente, id=cliente_id)

        if cliente.es_contado:
            return JsonResponse({
                'success': False,
                'message': 'No se puede editar el cliente CONTADO'
            })

        data = json.loads(request.body)

        cedula_rnc = data.get('cedula_rnc', '').strip() or None
        if cedula_rnc and Cliente.objects.filter(cedula_rnc=cedula_rnc).exclude(id=cliente_id).exists():
            return JsonResponse({
                'success': False,
                'message': 'Ya existe otro cliente con esa cedula/RNC'
            })

        cliente.tipo = data.get('tipo', cliente.tipo)
        cliente.nombre = data['nombre'].strip()
        cliente.cedula_rnc = cedula_rnc
        cliente.telefono = data.get('telefono', '').strip() or None
        cliente.direccion = data.get('direccion', '').strip() or None
        cliente.limite_credito = data.get('limite_credito', 0)
        cliente.condiciones_pago = data.get('condiciones_pago', '').strip() or None
        cliente.notas = data.get('notas', '').strip() or None
        cliente.activo = data.get('activo', True)

        cliente.save()

        return JsonResponse({
            'success': True,
            'message': f'Cliente "{cliente.nombre}" actualizado exitosamente'
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)


@login_required
@require_http_methods(["POST"])
def toggle_estado_cliente(request, cliente_id):
    """Activar/desactivar cliente"""

    try:
        cliente = get_object_or_404(Cliente, id=cliente_id)

        if cliente.es_contado:
            return JsonResponse({
                'success': False,
                'message': 'No se puede desactivar el cliente CONTADO'
            })

        cliente.activo = not cliente.activo
        cliente.save()

        estado = "activado" if cliente.activo else "desactivado"
        return JsonResponse({
            'success': True,
            'activo': cliente.activo,
            'message': f'Cliente "{cliente.nombre}" {estado}'
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)


@login_required
@require_http_methods(["GET"])
def buscar_clientes(request):
    """
    API para buscar clientes por nombre o cedula/RNC.
    Usado en autocompletado del POS y cotizaciones.
    """
    query = request.GET.get('q', '').strip()

    if len(query) < 2:
        return JsonResponse({'clientes': []})

    clientes = Cliente.objects.filter(
        activo=True
    ).filter(
        Q(nombre__icontains=query) |
        Q(cedula_rnc__icontains=query) |
        Q(telefono__icontains=query)
    )[:10]

    data = {
        'clientes': [
            {
                'id': c.id,
                'tipo': c.tipo,
                'nombre': c.nombre,
                'cedula_rnc': c.cedula_rnc or '',
                'telefono': c.telefono or '',
                'direccion': c.direccion or '',
                'limite_credito': str(c.limite_credito),
                'saldo_pendiente': str(_resumen_credito(c)['saldo_pendiente']),
                'credito_disponible': str(_resumen_credito(c)['credito_disponible']),
            }
            for c in clientes
        ]
    }

    return JsonResponse(data)


@login_required
def detalle_cliente(request, cliente_id):
    """Detalle de cliente con historial de compras"""

    cliente = get_object_or_404(Cliente, id=cliente_id)

    ventas = cliente.ventas.all().order_by('-fecha_venta')[:20]
    cotizaciones = cliente.cotizaciones.all().order_by('-fecha_creacion')[:20]

    context = {
        'cliente': cliente,
        'ventas': ventas,
        'cotizaciones': cotizaciones,
    }

    return render(request, 'clientes/detalle_cliente.html', context)
