"""
Views para Dashboard de Auditoría
Agregar a: apps/auditoria/views.py

Nueva vista:
1. dashboard_auditoria - Página con filtros y tabla de registros
"""

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.utils import timezone
from datetime import timedelta
import json

from .models import Auditoria
from apps.usuarios.models import Usuario


@login_required
def dashboard_auditoria(request):
    """
    Dashboard de auditoría con filtros y tabla paginada.
    Solo accesible por ADMIN y SYSADMIN.
    """
    if not request.user.tiene_permiso('auditoria.ver'):
        messages.error(request, 'No tienes permisos para acceder a esta sección.')
        return redirect('pos:punto_venta')

    # ── Opciones de filtro para el frontend ──
    tipos_accion = [
        {'value': choice[0], 'label': choice[1]}
        for choice in Auditoria.TipoAccion.choices
    ]

    niveles = [
        {'value': choice[0], 'label': choice[1]}
        for choice in Auditoria.NivelImportancia.choices
    ]

    usuarios = list(
        Usuario.objects.filter(activo=True)
        .values('id', 'username', 'first_name', 'last_name')
        .order_by('first_name', 'username')
    )
    usuarios_data = [
        {
            'id': u['id'],
            'nombre': f"{u['first_name']} {u['last_name']}".strip() or u['username'],
        }
        for u in usuarios
    ]

    # ── Estadísticas rápidas (últimas 24h) ──
    hace_24h = timezone.now() - timedelta(hours=24)
    stats = {
        'total_24h': Auditoria.objects.filter(fecha_hora__gte=hace_24h).count(),
        'criticas_24h': Auditoria.objects.filter(
            fecha_hora__gte=hace_24h,
            nivel_importancia='CRITICA'
        ).count(),
        'errores_24h': Auditoria.objects.filter(
            fecha_hora__gte=hace_24h,
            exito=False
        ).count(),
        'usuarios_activos_24h': Auditoria.objects.filter(
            fecha_hora__gte=hace_24h,
            usuario__isnull=False
        ).values('usuario').distinct().count(),
    }

    context = {
        'init_data_json': {
            'tipos_accion': tipos_accion,
            'niveles': niveles,
            'usuarios': usuarios_data,
            'stats': stats,
        },
    }

    return render(request, 'auditoria/dashboard.html', context)


@login_required
def api_auditoria_buscar(request):
    """
    API para buscar registros de auditoría con filtros.

    GET params:
        pagina: int (default 1)
        por_pagina: int (default 25, max 100)
        accion: str (TipoAccion value)
        nivel: str (NivelImportancia value)
        usuario_id: int
        fecha_desde: str (YYYY-MM-DD)
        fecha_hasta: str (YYYY-MM-DD)
        busqueda: str (texto libre en descripcion)
        solo_errores: bool
    """
    if not request.user.tiene_permiso('auditoria.ver'):
        return JsonResponse({'error': 'Sin permisos'}, status=403)

    # Parámetros
    pagina = int(request.GET.get('pagina', 1))
    por_pagina = min(int(request.GET.get('por_pagina', 25)), 100)
    accion = request.GET.get('accion', '')
    nivel = request.GET.get('nivel', '')
    usuario_id = request.GET.get('usuario_id', '')
    fecha_desde = request.GET.get('fecha_desde', '')
    fecha_hasta = request.GET.get('fecha_hasta', '')
    busqueda = request.GET.get('busqueda', '').strip()
    solo_errores = request.GET.get('solo_errores', '') == 'true'

    # Query base
    qs = Auditoria.objects.select_related('usuario').order_by('-fecha_hora')

    # Aplicar filtros
    if accion:
        qs = qs.filter(accion=accion)
    if nivel:
        qs = qs.filter(nivel_importancia=nivel)
    if usuario_id:
        qs = qs.filter(usuario_id=int(usuario_id))
    if fecha_desde:
        qs = qs.filter(fecha_hora__date__gte=fecha_desde)
    if fecha_hasta:
        qs = qs.filter(fecha_hora__date__lte=fecha_hasta)
    if busqueda:
        qs = qs.filter(descripcion__icontains=busqueda)
    if solo_errores:
        qs = qs.filter(exito=False)

    # Paginar
    paginator = Paginator(qs, por_pagina)
    page = paginator.get_page(pagina)

    registros = []
    for r in page.object_list:
        registros.append({
            'id': r.id,
            'fecha': r.fecha_hora.strftime('%d/%m/%Y %H:%M:%S'),
            'usuario': (r.usuario.get_full_name() or r.usuario.username) if r.usuario else 'Sistema',
            'accion': r.accion,
            'accion_display': r.get_accion_display(),
            'descripcion': r.descripcion,
            'nivel': r.nivel_importancia,
            'nivel_display': r.get_nivel_importancia_display(),
            'exito': r.exito,
            'ip_address': r.ip_address or '',
        })

    return JsonResponse({
        'success': True,
        'registros': registros,
        'paginacion': {
            'pagina_actual': page.number,
            'total_paginas': paginator.num_pages,
            'total_registros': paginator.count,
            'tiene_anterior': page.has_previous(),
            'tiene_siguiente': page.has_next(),
        }
    })