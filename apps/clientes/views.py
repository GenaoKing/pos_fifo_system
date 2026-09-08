"""
Views para Gestion de Clientes
apps/clientes/views.py
"""

import json
import logging
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.auditoria.models import Auditoria, get_client_ip
from apps.permisos.decorators import (
    requiere_permiso_json,
    requiere_permiso_local,
    sucursal_del_request as _sucursal_actual,
)

from .models import Cliente

logger = logging.getLogger('clientes')


def _es_del_cloud(cliente):
    """
    True si el maestro lo gobierna el cloud y el pull lo va a sobrescribir.

    Solo aplica con `SYNC_ENABLED`: una instalacion standalone es su propia
    fuente de verdad. Y solo a los clientes ADOPTADOS por el cloud
    (`origen_cloud_id`): los que nacieron en la sucursal siguen siendo suyos
    hasta que el cloud los adopte.
    """
    from django.conf import settings

    if not getattr(settings, 'SYNC_ENABLED', False):
        return False
    return getattr(cliente, 'origen_cloud_id', None) is not None


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
@requiere_permiso_local('clientes.ver')
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
      with transaction.atomic():
        cliente = get_object_or_404(
            Cliente.objects.select_for_update(), id=cliente_id,
        )

        if cliente.es_contado:
            return JsonResponse({
                'success': False,
                'message': 'No se puede editar el cliente CONTADO'
            })

        if _es_del_cloud(cliente):
            # CLI-004: la arquitectura declara al cloud fuente de verdad para
            # los maestros, y `_pull_clientes` reemplaza nombre, tipo,
            # identificacion, contacto, limite, plazo, condiciones, notas y
            # estado. Editar aca confirmaba una decision que desaparecia en el
            # siguiente pull —incluido el limite de credito— y podia disparar
            # otra reprogramacion de cartera.
            #
            # El proxy de la escritura local hacia la API cloud es la solucion
            # de fondo y esta pendiente. Mientras tanto, lo que NO se puede
            # hacer es confirmarle al operador un cambio sin ruta de
            # convergencia: se rechaza y se le dice donde editarlo.
            return JsonResponse({
                'success': False,
                'message': (
                    'Este cliente se administra desde el portal cloud. '
                    'Editalo alli: un cambio local se perderia en la proxima '
                    'sincronizacion.'
                ),
            }, status=409)

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

        # CLI-005: guardar, auditar y reprogramar cartera eran tres pasos
        # sueltos. Forzando un fallo en la auditoria, la respuesta era 400 pero
        # el limite nuevo ya estaba en base sin evidencia; forzando un fallo en
        # la reprogramacion, el plazo quedaba confirmado con las cuotas viejas.
        # El operador veia un error sobre una decision financiera que si se
        # habia aplicado.
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

    except (json.JSONDecodeError, KeyError, InvalidOperation, ValueError) as exc:
        return JsonResponse(
            {'success': False, 'message': f'Datos invalidos: {exc}'}, status=400,
        )
    except Exception:
        logger.exception('Error editando un cliente')
        return JsonResponse(
            {'success': False, 'message': 'No se pudo actualizar el cliente.'},
            status=400,
        )


@login_required
@requiere_permiso_json('clientes.eliminar')
@require_http_methods(["POST"])
def toggle_estado_cliente(request, cliente_id):
    """
    Activar/desactivar cliente.

    Exigia solo `@login_required` (CLI-002): cualquier usuario autenticado, sin
    un solo permiso, podia bloquear a un cliente para credito y cotizaciones —o
    reactivar a uno dado de baja por riesgo— y no quedaba ni autor ni fecha.

    Se gatea con `clientes.eliminar` porque desactivar ES la baja: el modelo no
    borra, inactiva. Y la transicion se audita dentro de la misma transaccion
    que la escribe, para que no exista un cambio de estado sin evidencia.
    """
    try:
        with transaction.atomic():
            cliente = get_object_or_404(
                Cliente.objects.select_for_update(), id=cliente_id,
            )

            if cliente.es_contado:
                return JsonResponse({
                    'success': False,
                    'message': 'No se puede desactivar el cliente CONTADO'
                })

            anterior = cliente.activo
            cliente.activo = not anterior
            cliente.save(update_fields=['activo', 'fecha_modificacion'])

            estado = "activado" if cliente.activo else "desactivado"
            Auditoria.registrar(
                accion=Auditoria.TipoAccion.EDITAR,
                descripcion=f'Cliente "{cliente.nombre}" {estado}',
                usuario=request.user,
                content_object=cliente,
                datos_anteriores={'activo': anterior},
                datos_nuevos={'activo': cliente.activo},
                ip_address=get_client_ip(request),
                sucursal=_sucursal_actual(request),
                nivel_importancia=Auditoria.NivelImportancia.ALTA,
            )

        return JsonResponse({
            'success': True,
            'activo': cliente.activo,
            'message': f'Cliente "{cliente.nombre}" {estado}'
        })

    except Http404:
        raise
    except Exception:
        # CLI-014: el texto de la excepcion iba literal al navegador.
        logger.exception('Error cambiando el estado de un cliente')
        return JsonResponse({
            'success': False,
            'message': 'No se pudo cambiar el estado del cliente.'
        }, status=400)


@login_required
@requiere_permiso_json('clientes.ver')
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
@requiere_permiso_local('clientes.ver')
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
