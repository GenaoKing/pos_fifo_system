"""
apps/auditoria/views.py
Dashboard de auditoria: pagina con filtros y API de busqueda paginada.

Cuatro hallazgos viven aca:

AUD-001  Ambas vistas comprobaban `tiene_permiso('auditoria.ver')` sin sucursal
         y despues consultaban `Auditoria.objects...` sin filtro alguno. Un
         supervisor con el permiso acotado a la sucursal A abria el dashboard y
         recibia registros, estadisticas y usuarios de B: motivos de anulacion,
         montos, nombres de clientes, usernames e IPs de otra tienda.

AUD-003  El actor se resolvia consultando la FK viva. Un usuario renombrado
         cambiaba como se presenta un hecho de hace meses, y una FK nula se
         mostraba como "Sistema", indistinguible de un job automatico.

AUD-014  Las fechas se formateaban con `strftime` sobre el datetime en UTC. En
         Santo Domingo (UTC-4) todo el historial se leia cuatro horas corrido.

AUD-015  `int(request.GET.get('pagina', 1))` con un valor no numerico levantaba
         `ValueError` y devolvia 500.
"""
import logging
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.usuarios.models import Usuario

from .models import Auditoria
from .scope import alcance_de

logger = logging.getLogger('auditoria')

POR_PAGINA_DEFECTO = 25
POR_PAGINA_MAX = 100


def _entero(valor, defecto, minimo=1, maximo=None):
    """Lee un entero del querystring sin convertir basura en un 500."""
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return defecto
    if numero < minimo:
        return defecto
    if maximo is not None:
        return min(numero, maximo)
    return numero


def _fecha(valor):
    """Fecha ISO del querystring, o None si no lo es."""
    if not valor:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


def _local(momento):
    """Formatea un instante en la zona del negocio, no en UTC."""
    if momento is None:
        return ''
    return timezone.localtime(momento).strftime('%d/%m/%Y %H:%M:%S')


@login_required
def dashboard_auditoria(request):
    """Dashboard de auditoria con filtros y tabla paginada."""
    alcance = alcance_de(request.user)
    if not alcance.permitido:
        messages.error(request, 'No tienes permisos para acceder a esta sección.')
        return redirect('pos:punto_venta')

    tipos_accion = [
        {'value': choice[0], 'label': choice[1]}
        for choice in Auditoria.TipoAccion.choices
    ]

    niveles = [
        {'value': choice[0], 'label': choice[1]}
        for choice in Auditoria.NivelImportancia.choices
    ]

    # Solo los usuarios del alcance: la lista completa de activos revelaba la
    # nomina de las otras sucursales.
    usuarios = list(
        alcance.filtrar_usuarios(Usuario.objects.filter(activo=True))
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

    # Las estadisticas tambien se acotan: un conteo agregado permite inferir la
    # actividad de otra tienda aunque despues se oculten las filas.
    base = alcance.filtrar(Auditoria.objects.all())
    hace_24h = timezone.now() - timedelta(hours=24)
    del_dia = base.filter(fecha_hora__gte=hace_24h)
    stats = {
        'total_24h': del_dia.count(),
        'criticas_24h': del_dia.filter(nivel_importancia='CRITICA').count(),
        'errores_24h': del_dia.filter(exito=False).count(),
        'usuarios_activos_24h': del_dia.filter(
            usuario__isnull=False
        ).values('usuario').distinct().count(),
    }

    context = {
        'init_data_json': {
            'tipos_accion': tipos_accion,
            'niveles': niveles,
            'usuarios': usuarios_data,
            'stats': stats,
            'alcance_global': alcance.es_global,
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
    alcance = alcance_de(request.user)
    if not alcance.permitido:
        return JsonResponse(
            {'success': False, 'error': 'Sin permisos', 'codigo': 'sin_permiso'},
            status=403,
        )

    pagina = _entero(request.GET.get('pagina'), 1)
    por_pagina = _entero(
        request.GET.get('por_pagina'), POR_PAGINA_DEFECTO, maximo=POR_PAGINA_MAX,
    )
    accion = request.GET.get('accion', '')
    nivel = request.GET.get('nivel', '')
    usuario_id = _entero(request.GET.get('usuario_id'), None)
    fecha_desde = _fecha(request.GET.get('fecha_desde'))
    fecha_hasta = _fecha(request.GET.get('fecha_hasta'))
    busqueda = request.GET.get('busqueda', '').strip()
    solo_errores = request.GET.get('solo_errores', '') == 'true'

    # El filtro de alcance se aplica ANTES que cualquier otro: ningun parametro
    # del cliente puede ampliarlo.
    qs = alcance.filtrar(
        Auditoria.objects.select_related('usuario', 'sucursal')
    ).order_by('-fecha_hora')

    if accion:
        qs = qs.filter(accion=accion)
    if nivel:
        qs = qs.filter(nivel_importancia=nivel)
    if usuario_id:
        qs = qs.filter(usuario_id=usuario_id)
    if fecha_desde:
        qs = qs.filter(fecha_hora__date__gte=fecha_desde)
    if fecha_hasta:
        qs = qs.filter(fecha_hora__date__lte=fecha_hasta)
    if busqueda:
        qs = qs.filter(descripcion__icontains=busqueda)
    if solo_errores:
        qs = qs.filter(exito=False)

    paginator = Paginator(qs, por_pagina)
    page = paginator.get_page(pagina)

    registros = [{
        'id': r.id,
        'fecha': _local(r.fecha_hora),
        # `actor_display` usa el snapshot congelado al momento del hecho, y
        # distingue una cuenta eliminada de un proceso automatico.
        'usuario': r.actor_display,
        'accion': r.accion,
        'accion_display': r.get_accion_display(),
        'descripcion': r.descripcion,
        'nivel': r.nivel_importancia,
        'nivel_display': r.get_nivel_importancia_display(),
        'exito': r.exito,
        'ip_address': r.ip_address or '',
        'sucursal': r.sucursal.nombre if r.sucursal_id else None,
    } for r in page.object_list]

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
