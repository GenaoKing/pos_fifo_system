"""
apps/caja/views.py
Vistas del modulo de Arqueo y Gestion de Caja
"""

import json
from decimal import Decimal

from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate
from django.utils import timezone
from django.db.models import Sum, Count, Q

from .models import Caja, TurnoCaja, MovimientoCaja


def es_admin(user):
    return user.is_authenticated and user.rol in ['ADMIN', 'SYSADMIN']


# ============================================================================
# SOFT-LOGIN: Validar credenciales de admin sin cambiar sesion
# ============================================================================

@login_required
def api_validar_admin(request):
    """
    POST: Valida credenciales de un admin sin cambiar la sesion activa.
    Usado para autorizar operaciones sensibles (retiros).

    Body: { "username": "admin", "password": "..." }
    Returns: { "valido": true, "admin_id": 1, "admin_nombre": "Admin" }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        username = data.get('username', '')
        password = data.get('password', '')

        if not username or not password:
            return JsonResponse({'valido': False, 'error': 'Credenciales requeridas'})

        # Autenticar sin tocar la sesion
        user = authenticate(request, username=username, password=password)

        if user is None:
            return JsonResponse({'valido': False, 'error': 'Credenciales incorrectas'})

        if not user.is_active:
            return JsonResponse({'valido': False, 'error': 'Usuario inactivo'})

        if user.rol not in ['ADMIN','SYSADMIN']:
            return JsonResponse({'valido': False, 'error': 'El usuario no tiene rol de administrador'})

        return JsonResponse({
            'valido': True,
            'admin_id': user.id,
            'admin_nombre': user.get_short_name() or user.username,
        })

    except Exception as e:
        return JsonResponse({'valido': False, 'error': str(e)})


# ============================================================================
# PAGINA PRINCIPAL DE CAJA
# ============================================================================

@login_required
def caja_index(request):
    """
    Pagina principal del modulo de caja.
    Muestra el turno activo del usuario o la opcion de abrir uno.
    """
    turno_activo = TurnoCaja.objects.filter(
        usuario=request.user,
        estado='ABIERTO'
    ).select_related('caja').first()

    # Cajas disponibles (sin turno abierto)
    cajas_disponibles = Caja.objects.filter(
        activa=True
    ).exclude(
        turnos__estado='ABIERTO'
    )

    # Historial de turnos del usuario (ultimos 10)
    historial = TurnoCaja.objects.filter(
        usuario=request.user,
        estado='CERRADO'
    ).select_related('caja')[:10]

    # Si es admin, mostrar todos los turnos abiertos
    turnos_abiertos_otros = None
    if es_admin(request.user):
        turnos_abiertos_otros = TurnoCaja.objects.filter(
            estado='ABIERTO'
        ).exclude(
            usuario=request.user
        ).select_related('caja', 'usuario')


    init_data = {
        'cajas_disponibles': list(cajas_disponibles.values('id', 'nombre')),
    }

    context = {
        'turno_activo': turno_activo,
        'cajas_disponibles': cajas_disponibles,
        'historial': historial,
        'turnos_abiertos_otros': turnos_abiertos_otros,
    }
    
    # Si hay turno activo, agregar desglose
    if turno_activo:
        init_data['turno'] = {
            'id': turno_activo.id,
            'caja': turno_activo.caja.nombre,
            'apertura': turno_activo.fecha_apertura.strftime('%d/%m/%Y %H:%M'),
            'fondo_apertura': str(turno_activo.fondo_apertura),
        }
        init_data['desglose'] = {k: str(v) for k, v in context['desglose'].items()}
        init_data['movimientos'] = [{
            'id': m.id,
            'tipo': m.tipo,
            'tipo_display': m.get_tipo_display(),
            'monto': str(m.monto),
            'descripcion': m.descripcion,
            'fecha': m.fecha.strftime('%d/%m/%Y %H:%M'),
            'registrado_por': m.registrado_por.get_short_name() or m.registrado_por.username,
            'autorizado_por': (m.autorizado_por.get_short_name() or m.autorizado_por.username) if m.autorizado_por else None,
        } for m in context['movimientos']]

    context['init_data_json'] = json.dumps(init_data)

    return render(request, 'caja/index.html', context)


# ============================================================================
# ABRIR TURNO
# ============================================================================

@login_required
def api_abrir_turno(request):
    """
    POST: Abre un nuevo turno de caja.
    Body: { "caja_id": 1, "fondo_apertura": 2000.00, "notas": "..." }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        caja_id = data.get('caja_id')
        fondo = Decimal(str(data.get('fondo_apertura', 0)))
        notas = data.get('notas', '')

        # Validar que no tenga turno abierto
        turno_existente = TurnoCaja.objects.filter(
            usuario=request.user,
            estado='ABIERTO'
        ).first()

        if turno_existente:
            return JsonResponse({
                'success': False,
                'error': f'Ya tienes un turno abierto en {turno_existente.caja.nombre}'
            }, status=400)

        # Validar caja
        caja = get_object_or_404(Caja, id=caja_id, activa=True)

        # Validar que la caja no tenga turno abierto
        if caja.turno_activo():
            return JsonResponse({
                'success': False,
                'error': f'{caja.nombre} ya tiene un turno abierto'
            }, status=400)

        # Crear turno
        turno = TurnoCaja.objects.create(
            caja=caja,
            usuario=request.user,
            fondo_apertura=fondo,
            notas_apertura=notas,
        )

        return JsonResponse({
            'success': True,
            'turno': {
                'id': turno.id,
                'caja': caja.nombre,
                'fondo': str(turno.fondo_apertura),
                'fecha': turno.fecha_apertura.strftime('%d/%m/%Y %H:%M'),
            }
        })

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ============================================================================
# CERRAR TURNO
# ============================================================================

@login_required
def api_cerrar_turno(request):
    """
    POST: Cierra el turno activo.
    Body: { "monto_contado": 15000.00, "notas": "..." }

    La cajera cierra su propio turno.
    Un admin puede cerrar cualquier turno (con turno_id).
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        monto_contado = Decimal(str(data.get('monto_contado', 0)))
        notas = data.get('notas', '')
        turno_id = data.get('turno_id')  # Opcional, para admin cerrando turno de otro

        # Determinar cual turno cerrar
        if turno_id and es_admin(request.user):
            turno = get_object_or_404(TurnoCaja, id=turno_id, estado='ABIERTO')
        else:
            turno = TurnoCaja.objects.filter(
                usuario=request.user,
                estado='ABIERTO'
            ).first()

        if not turno:
            return JsonResponse({
                'success': False,
                'error': 'No hay turno abierto para cerrar'
            }, status=400)

        # Cerrar turno
        calculo = turno.cerrar(
            monto_contado=monto_contado,
            cerrado_por=request.user,
            notas=notas
        )

        return JsonResponse({
            'success': True,
            'cierre': {
                'turno_id': turno.id,
                'caja': turno.caja.nombre,
                'cajero': turno.usuario.get_short_name() or turno.usuario.username,
                'apertura': turno.fecha_apertura.strftime('%d/%m/%Y %H:%M'),
                'cierre': turno.fecha_cierre.strftime('%d/%m/%Y %H:%M'),
                'fondo_apertura': str(calculo['fondo_apertura']),
                'efectivo_ventas': str(calculo['efectivo_ventas']),
                'retiros': str(calculo['retiros']),
                'gastos': str(calculo['gastos']),
                'ingresos': str(calculo['ingresos']),
                'esperado': str(calculo['esperado']),
                'contado': str(turno.monto_contado),
                'diferencia': str(turno.diferencia),
            }
        })

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ============================================================================
# REGISTRAR MOVIMIENTO DE CAJA
# ============================================================================

@login_required
def api_registrar_movimiento(request):
    """
    POST: Registra un movimiento de caja (retiro, gasto, ingreso).
    Body: {
        "tipo": "RETIRO",
        "monto": 5000.00,
        "descripcion": "Retiro para deposito bancario",
        "admin_id": 1  // Requerido para RETIRO (viene del soft-login)
    }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        tipo = data.get('tipo', '').upper()
        monto = Decimal(str(data.get('monto', 0)))
        descripcion = data.get('descripcion', '').strip()
        admin_id = data.get('admin_id')

        # Validaciones
        if tipo not in ('RETIRO', 'GASTO', 'INGRESO'):
            return JsonResponse({'success': False, 'error': 'Tipo invalido'}, status=400)

        if monto <= 0:
            return JsonResponse({'success': False, 'error': 'Monto debe ser mayor a 0'}, status=400)

        if not descripcion:
            return JsonResponse({'success': False, 'error': 'Descripcion requerida'}, status=400)

        # Obtener turno activo
        turno = TurnoCaja.objects.filter(
            usuario=request.user,
            estado='ABIERTO'
        ).first()

        # Si es admin, puede registrar en su turno o indicar turno_id
        if not turno and es_admin(request.user):
            turno_id = data.get('turno_id')
            if turno_id:
                turno = get_object_or_404(TurnoCaja, id=turno_id, estado='ABIERTO')

        if not turno:
            return JsonResponse({
                'success': False,
                'error': 'No hay turno abierto'
            }, status=400)

        # RETIRO e INGRESO requieren autorizacion admin
        autorizado_por = None
        if tipo in ('RETIRO', 'INGRESO'):
            if not admin_id:
                # Si el usuario actual es admin, se auto-autoriza
                if es_admin(request.user):
                    autorizado_por = request.user
                else:
                    return JsonResponse({
                        'success': False,
                        'error': 'Se requiere autorizacion de un administrador'
                    }, status=403)
            else:
                from apps.usuarios.models import Usuario
                try:
                    admin = Usuario.objects.get(id=admin_id, rol__in=['ADMIN', 'SYSADMIN'], activo=True)
                    autorizado_por = admin
                except Usuario.DoesNotExist:
                    return JsonResponse({
                        'success': False,
                        'error': 'Admin no encontrado o inactivo'
                    }, status=400)

        # Crear movimiento
        movimiento = MovimientoCaja.objects.create(
            turno=turno,
            tipo=tipo,
            monto=monto,
            descripcion=descripcion,
            registrado_por=request.user,
            autorizado_por=autorizado_por,
        )

        # Recalcular esperado
        desglose = turno.calcular_esperado()

        return JsonResponse({
            'success': True,
            'movimiento': {
                'id': movimiento.id,
                'tipo': movimiento.get_tipo_display(),
                'monto': str(movimiento.monto),
                'descripcion': movimiento.descripcion,
                'fecha': movimiento.fecha.strftime('%d/%m/%Y %H:%M'),
                'autorizado_por': autorizado_por.get_short_name() if autorizado_por else None,
            },
            'desglose': {
                'fondo_apertura': str(desglose['fondo_apertura']),
                'efectivo_ventas': str(desglose['efectivo_ventas']),
                'retiros': str(desglose['retiros']),
                'gastos': str(desglose['gastos']),
                'ingresos': str(desglose['ingresos']),
                'esperado': str(desglose['esperado']),
            }
        })

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ============================================================================
# ESTADO ACTUAL DEL TURNO (para polling/refresh)
# ============================================================================

@login_required
def api_estado_turno(request):
    """
    GET: Retorna el estado actual del turno abierto del usuario.
    Usado para refrescar la UI sin recargar pagina.
    """
    turno = TurnoCaja.objects.filter(
        usuario=request.user,
        estado='ABIERTO'
    ).select_related('caja').first()

    if not turno:
        return JsonResponse({'tiene_turno': False})

    desglose = turno.calcular_esperado()
    movimientos = turno.movimientos.select_related(
        'registrado_por', 'autorizado_por'
    ).order_by('-fecha')[:20]

    return JsonResponse({
        'tiene_turno': True,
        'turno': {
            'id': turno.id,
            'caja': turno.caja.nombre,
            'apertura': turno.fecha_apertura.strftime('%d/%m/%Y %H:%M'),
            'fondo_apertura': str(turno.fondo_apertura),
        },
        'desglose': {
            'fondo_apertura': str(desglose['fondo_apertura']),
            'efectivo_ventas': str(desglose['efectivo_ventas']),
            'retiros': str(desglose['retiros']),
            'gastos': str(desglose['gastos']),
            'ingresos': str(desglose['ingresos']),
            'esperado': str(desglose['esperado']),
        },
        'movimientos': [{
            'id': m.id,
            'tipo': m.tipo,
            'tipo_display': m.get_tipo_display(),
            'monto': str(m.monto),
            'descripcion': m.descripcion,
            'fecha': m.fecha.strftime('%d/%m/%Y %H:%M'),
            'registrado_por': m.registrado_por.get_short_name() or m.registrado_por.username,
            'autorizado_por': (m.autorizado_por.get_short_name() or m.autorizado_por.username) if m.autorizado_por else None,
        } for m in movimientos],
    })


# ============================================================================
# HISTORIAL DE TURNOS (Admin)
# ============================================================================

@login_required
def historial_turnos(request):
    """
    Pagina de historial de turnos (solo admin).
    """
    if not es_admin(request.user):
        from django.shortcuts import redirect
        return redirect('caja:index')

    turnos = TurnoCaja.objects.filter(
        estado='CERRADO'
    ).select_related('caja', 'usuario', 'cerrado_por').order_by('-fecha_cierre')[:50]

    context = {
        'turnos': turnos,
    }

    return render(request, 'caja/historial.html', context)


# ============================================================================
# DETALLE DE TURNO CERRADO
# ============================================================================

@login_required
def api_detalle_turno(request, turno_id):
    """
    GET: Detalle completo de un turno cerrado.
    """
    turno = get_object_or_404(TurnoCaja, id=turno_id)

    # Solo admin o el propio usuario puede ver el detalle
    if not es_admin(request.user) and turno.usuario != request.user:
        return JsonResponse({'error': 'Sin permisos'}, status=403)

    movimientos = turno.movimientos.select_related(
        'registrado_por', 'autorizado_por'
    ).order_by('fecha')

    return JsonResponse({
        'turno': {
            'id': turno.id,
            'caja': turno.caja.nombre,
            'cajero': turno.usuario.get_short_name() or turno.usuario.username,
            'apertura': turno.fecha_apertura.strftime('%d/%m/%Y %H:%M'),
            'cierre': turno.fecha_cierre.strftime('%d/%m/%Y %H:%M') if turno.fecha_cierre else None,
            'fondo_apertura': str(turno.fondo_apertura),
            'esperado': str(turno.monto_esperado) if turno.monto_esperado else None,
            'contado': str(turno.monto_contado) if turno.monto_contado else None,
            'diferencia': str(turno.diferencia) if turno.diferencia is not None else None,
            'estado': turno.estado,
            'cerrado_por': (turno.cerrado_por.get_short_name() or turno.cerrado_por.username) if turno.cerrado_por else None,
            'notas_apertura': turno.notas_apertura,
            'notas_cierre': turno.notas_cierre,
        },
        'movimientos': [{
            'tipo': m.tipo,
            'tipo_display': m.get_tipo_display(),
            'monto': str(m.monto),
            'descripcion': m.descripcion,
            'fecha': m.fecha.strftime('%d/%m/%Y %H:%M'),
            'registrado_por': m.registrado_por.get_short_name() or m.registrado_por.username,
            'autorizado_por': (m.autorizado_por.get_short_name() or m.autorizado_por.username) if m.autorizado_por else None,
        } for m in movimientos],
    })