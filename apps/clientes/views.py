"""
Views para Gestion de Clientes
apps/clientes/views.py
"""

from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.db.models import Q, Sum, Count
import json

from apps.auditoria.models import Auditoria, get_client_ip
from apps.permisos.decorators import requiere_permiso_json

from .models import Cliente


def _parse_plazo_credito_dias(value):
    try:
        plazo = int(value or 30)
    except (TypeError, ValueError):
        raise ValueError('El plazo de credito debe ser un numero entero.')
    if plazo < 1 or plazo > 365:
        raise ValueError('El plazo de credito debe estar entre 1 y 365 dias.')
    return plazo


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
            'plazo_credito_dias': cliente.plazo_credito_dias,
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


def _limite_credito_autorizado(request, data, *, actual):
    """
    Devuelve el limite a persistir.

    Si el payload trae un limite distinto del actual y el usuario NO tiene
    `clientes.editar_limite_credito`, se conserva el actual en vez de fallar:
    el formulario de clientes envia el campo siempre, aunque el operador no lo
    haya tocado. Lo que no puede es CAMBIARLO sin el permiso.
    """
    if 'limite_credito' not in data:
        return actual

    solicitado = Decimal(str(data.get('limite_credito') or 0))
    if solicitado == Decimal(str(actual or 0)):
        return actual

    if not request.user.tiene_permiso(
        'clientes.editar_limite_credito',
        sucursal=getattr(request, 'sucursal', None),
    ):
        raise PermissionDenied(
            'No tienes permisos para cambiar el limite de credito.'
        )
    return solicitado


@login_required
@requiere_permiso_json('clientes.crear')
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
            limite_credito=_limite_credito_autorizado(request, data, actual=0),
            plazo_credito_dias=_parse_plazo_credito_dias(data.get('plazo_credito_dias', 30)),
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

    except PermissionDenied as exc:
        # 403, no 400: el `except Exception` de abajo lo devolvia como error de
        # datos y el cliente no podia distinguir "faltan permisos" de "payload
        # invalido".
        return JsonResponse({'success': False, 'message': str(exc)}, status=403)

    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)


@login_required
@requiere_permiso_json('clientes.editar')
@require_http_methods(["POST"])
def editar_cliente(request, cliente_id):
    """
    Editar cliente existente via AJAX.

    El limite de credito NO se toca con `clientes.editar`: requiere
    `clientes.editar_limite_credito`. Antes bastaba con estar autenticado para
    subirlo a lo que fuera, y con eso se evitaba por completo el flujo de
    override — primero se elevaba el limite y despues se vendia a credito sin
    dejar ninguna excepcion crediticia registrada.
    """

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
        plazo_anterior = cliente.plazo_credito_dias
        cliente.nombre = data['nombre'].strip()
        cliente.cedula_rnc = cedula_rnc
        cliente.telefono = data.get('telefono', '').strip() or None
        cliente.direccion = data.get('direccion', '').strip() or None
        limite_anterior = cliente.limite_credito
        cliente.limite_credito = _limite_credito_autorizado(
            request, data, actual=cliente.limite_credito,
        )
        cliente.plazo_credito_dias = _parse_plazo_credito_dias(data.get('plazo_credito_dias', cliente.plazo_credito_dias))
        cliente.condiciones_pago = data.get('condiciones_pago', '').strip() or None
        cliente.notas = data.get('notas', '').strip() or None
        cliente.activo = data.get('activo', True)

        cliente.save()

        if Decimal(str(limite_anterior)) != Decimal(str(cliente.limite_credito)):
            # Un cambio de limite es una decision financiera: queda auditada con
            # valor anterior, nuevo y quien lo hizo.
            Auditoria.registrar(
                accion=Auditoria.TipoAccion.EDITAR,
                descripcion=(
                    f'Limite de credito de {cliente.nombre}: '
                    f'{limite_anterior} -> {cliente.limite_credito}'
                ),
                usuario=request.user,
                content_object=cliente,
                datos_anteriores={'limite_credito': str(limite_anterior)},
                datos_nuevos={'limite_credito': str(cliente.limite_credito)},
                ip_address=get_client_ip(request),
                nivel_importancia=Auditoria.NivelImportancia.ALTA,
            )

        if int(plazo_anterior) != int(cliente.plazo_credito_dias):
            from apps.cuentas_por_cobrar.services import reprogramar_cxc_por_plazo_cliente

            reprogramar_cxc_por_plazo_cliente(
                cliente,
                usuario=request.user,
                origen='local_cliente_update',
                plazo_anterior=int(plazo_anterior),
            )

        return JsonResponse({
            'success': True,
            'message': f'Cliente "{cliente.nombre}" actualizado exitosamente'
        })

    except PermissionDenied as exc:
        # 403, no 400: el `except Exception` de abajo lo devolvia como error de
        # datos y el cliente no podia distinguir "faltan permisos" de "payload
        # invalido".
        return JsonResponse({'success': False, 'message': str(exc)}, status=403)

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
                'plazo_credito_dias': c.plazo_credito_dias,
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
