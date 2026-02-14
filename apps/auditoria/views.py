"""
Vistas para consultar auditoría
apps/auditoria/views.py

NOTA: Estas vistas son opcionales y se pueden implementar más adelante
cuando se necesite un dashboard de auditoría en el frontend.
"""
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from datetime import timedelta
from .models import Auditoria


@login_required
def dashboard_auditoria(request):
    """
    Dashboard principal de auditoría (solo para admin).
    TODO: Implementar cuando se necesite el frontend.
    """
    if not request.user.es_admin():
        return JsonResponse({'error': 'No autorizado'}, status=403)
    
    # Estadísticas del día
    hoy = timezone.now().date()
    
    context = {
        'total_acciones_hoy': Auditoria.objects.filter(
            fecha_hora__date=hoy
        ).count(),
        'acciones_criticas_hoy': Auditoria.objects.filter(
            fecha_hora__date=hoy,
            nivel_importancia='CRITICA'
        ).count(),
        'errores_hoy': Auditoria.objects.filter(
            fecha_hora__date=hoy,
            exito=False
        ).count(),
    }
    
    return render(request, 'auditoria/dashboard.html', context)


@login_required
def historial_usuario(request, usuario_id):
    """
    Muestra el historial de acciones de un usuario específico.
    TODO: Implementar cuando se necesite el frontend.
    """
    if not request.user.es_admin():
        return JsonResponse({'error': 'No autorizado'}, status=403)
    
    acciones = Auditoria.obtener_acciones_usuario(
        usuario_id=usuario_id,
        limite=100
    )
    
    return render(request, 'auditoria/historial_usuario.html', {
        'acciones': acciones
    })


@login_required
def api_ultimas_acciones(request):
    """
    API para obtener las últimas acciones (para uso con AJAX/fetch).
    Útil para actualizar dashboards en tiempo real.
    """
    if not request.user.es_admin():
        return JsonResponse({'error': 'No autorizado'}, status=403)
    
    limite = int(request.GET.get('limite', 20))
    
    acciones = Auditoria.objects.select_related('usuario').order_by('-fecha_hora')[:limite]
    
    data = [{
        'id': acc.id,
        'fecha_hora': acc.fecha_hora.isoformat(),
        'usuario': acc.usuario.username if acc.usuario else 'Sistema',
        'accion': acc.get_accion_display(),
        'descripcion': acc.descripcion,
        'nivel': acc.nivel_importancia,
        'exito': acc.exito,
    } for acc in acciones]
    
    return JsonResponse({'acciones': data})