"""
Views de Impresión para Sistema POS
Sistema POS FIFO - Módulo de Impresión

Vistas para manejar impresión de tickets desde el POS,
reimpresión de tickets históricos y pruebas de impresora.

Autor: Sistema POS FIFO
Versión: 1.0
"""

import logging
from django.views import View
from django.views.generic import TemplateView, ListView
from django.http import JsonResponse
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.shortcuts import get_object_or_404
from django.db.models import Q
from datetime import datetime, timedelta

from apps.ventas.models import Venta
from apps.auditoria.models import Auditoria
from utils.impresoras.manager import print_manager

logger = logging.getLogger(__name__)


# ============================================================================
# VISTAS DE IMPRESIÓN
# ============================================================================

class ImprimirTicketAutomaticoView(LoginRequiredMixin, View):
    """
    Vista para impresión automática de ticket tras completar venta
    
    Esta vista es llamada automáticamente desde el POS al finalizar una venta.
    Imprime el ticket y retorna el resultado en JSON.
    
    URL: POST /ventas/imprimir-ticket/<venta_id>/
    """
    
    def post(self, request, venta_id):
        """
        Imprime ticket de venta recién completada
        
        Args:
            venta_id: ID de la venta
            
        Returns:
            JsonResponse: Resultado de la impresión
        """
        try:
            venta = get_object_or_404(Venta, id=venta_id)
            
            # Verificar que la venta no esté anulada
            if venta.estado == 'ANULADA':
                return JsonResponse({
                    'success': False,
                    'mensaje': 'No se puede imprimir una venta anulada'
                }, status=400)
            
            # Imprimir ticket
            resultado = print_manager.print_ticket_venta(
                venta=venta,
                usuario=request.user,
                reimpresion=False
            )
            
            return JsonResponse(resultado)
            
        except Exception as e:
            logger.error(f"Error en impresión automática: {str(e)}")
            return JsonResponse({
                'success': False,
                'mensaje': 'Error al imprimir ticket',
                'error': str(e)
            }, status=500)


class ReimprimirTicketView(LoginRequiredMixin, View):
    """
    Vista para reimpresión de tickets históricos
    
    Requiere permiso especial para evitar reimpresiones no autorizadas.
    Registra la reimpresión en auditoría.
    
    URL: POST /ventas/reimprimir-ticket/<venta_id>/
    Permiso requerido: ventas.reimprimir_ticket
    """
    
    
    def post(self, request, venta_id):
        """
        Reimprime un ticket de venta anterior
        
        Args:
            venta_id: ID de la venta a reimprimir
            
        Returns:
            JsonResponse: Resultado de la reimpresión
        """
        try:
            venta = get_object_or_404(Venta, id=venta_id)
            
            # Imprimir con flag de reimpresión
            resultado = print_manager.print_ticket_venta(
                venta=venta,
                usuario=request.user,
                reimpresion=True
            )
            
            return JsonResponse(resultado)
            
        except Exception as e:
            logger.error(f"Error en reimpresión: {str(e)}")
            return JsonResponse({
                'success': False,
                'mensaje': 'Error al reimprimir ticket',
                'error': str(e)
            }, status=500)


class TestImpresoraView(LoginRequiredMixin, View):
    """
    Vista para probar la impresora
    
    Imprime una página de prueba para verificar que la impresora
    está conectada y funcionando correctamente.
    
    URL: POST /admin/test-impresora/
    """
    
    def post(self, request):
        """
        Ejecuta prueba de impresión
        
        Returns:
            JsonResponse: Resultado de la prueba
        """
        try:
            resultado = print_manager.test_printer()
            
            # Registrar en auditoría
            Auditoria.objects.create(
                usuario=request.user,
                accion='TEST_IMPRESORA',
                detalles={
                    'resultado': resultado['success'],
                    'mensaje': resultado['mensaje']
                }
            )
            
            return JsonResponse(resultado)
            
        except Exception as e:
            logger.error(f"Error en test de impresora: {str(e)}")
            return JsonResponse({
                'success': False,
                'mensaje': f'Error ejecutando test: {str(e)}'
            }, status=500)


# ============================================================================
# VISTAS DE HISTORIAL Y AUDITORÍA
# ============================================================================

class HistorialImpresionesView(LoginRequiredMixin, TemplateView):
    """
    Vista para ver historial de impresiones
    
    Muestra todas las impresiones realizadas con posibilidad de filtrar
    por fecha, usuario, tipo de impresión, etc.
    
    URL: GET /reportes/historial-impresiones/
    Template: reportes/historial_impresiones.html
    """
    
    template_name = 'reportes/historial_impresiones.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Obtener parámetros de filtro
        fecha_desde = self.request.GET.get('fecha_desde')
        fecha_hasta = self.request.GET.get('fecha_hasta')
        usuario_id = self.request.GET.get('usuario')
        solo_errores = self.request.GET.get('solo_errores') == '1'
        solo_reimpresiones = self.request.GET.get('solo_reimpresiones') == '1'
        
        # Construir query
        filtros = Q(accion__in=['IMPRESION_TICKET', 'ERROR_IMPRESION'])
        
        if fecha_desde:
            try:
                fecha_desde_dt = datetime.strptime(fecha_desde, '%Y-%m-%d')
                filtros &= Q(fecha__gte=fecha_desde_dt)
            except ValueError:
                pass
        
        if fecha_hasta:
            try:
                fecha_hasta_dt = datetime.strptime(fecha_hasta, '%Y-%m-%d')
                fecha_hasta_dt = fecha_hasta_dt.replace(hour=23, minute=59, second=59)
                filtros &= Q(fecha__lte=fecha_hasta_dt)
            except ValueError:
                pass
        
        if usuario_id:
            filtros &= Q(usuario_id=usuario_id)
        
        if solo_errores:
            filtros &= Q(accion='ERROR_IMPRESION')
        
        if solo_reimpresiones:
            filtros &= Q(accion='IMPRESION_TICKET', detalles__reimpresion=True)
        
        # Obtener registros
        impresiones = Auditoria.objects.filter(filtros).select_related(
            'usuario'
        ).order_by('-fecha')[:200]
        
        context['impresiones'] = impresiones
        
        # Estadísticas
        if fecha_desde or fecha_hasta:
            fecha_desde_stat = datetime.strptime(fecha_desde, '%Y-%m-%d') if fecha_desde else None
            fecha_hasta_stat = datetime.strptime(fecha_hasta, '%Y-%m-%d') if fecha_hasta else None
            
            stats = print_manager.get_estadisticas_impresion(
                fecha_desde=fecha_desde_stat,
                fecha_hasta=fecha_hasta_stat
            )
        else:
            # Últimos 7 días por defecto
            fecha_desde_stat = datetime.now() - timedelta(days=7)
            stats = print_manager.get_estadisticas_impresion(
                fecha_desde=fecha_desde_stat
            )
        
        context['estadisticas'] = stats
        
        # Filtros aplicados (para mantener en el form)
        context['filtros'] = {
            'fecha_desde': fecha_desde,
            'fecha_hasta': fecha_hasta,
            'usuario': usuario_id,
            'solo_errores': solo_errores,
            'solo_reimpresiones': solo_reimpresiones
        }
        
        return context


class ListaVentasReimprimirView(LoginRequiredMixin,  TemplateView):
    """
    Vista para listar ventas y permitir reimpresión
    
    Muestra las ventas recientes con opción de reimprimir el ticket.
    Útil para cuando el cliente pierde el ticket o la impresora falló.
    
    URL: GET /ventas/reimprimir/
    Template: ventas/lista_reimprimir.html
    Permiso requerido: ventas.reimprimir_ticket
    """
    
    template_name = 'pos/lista_reimprimir.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Obtener parámetros de búsqueda
        busqueda = self.request.GET.get('q', '')
        fecha_desde = self.request.GET.get('fecha_desde')
        fecha_hasta = self.request.GET.get('fecha_hasta')
        
        # Construir query
        ventas = Venta.objects.filter(
            estado='COMPLETADA'
        ).select_related('usuario')
        
        # Filtro por búsqueda (número de venta o cliente)
        if busqueda:
            ventas = ventas.filter(
                Q(numero_venta__icontains=busqueda)
            )
        
        # Filtro por fecha
        if fecha_desde:
            try:
                fecha_desde_dt = datetime.strptime(fecha_desde, '%Y-%m-%d')
                ventas = ventas.filter(fecha_venta__gte=fecha_desde_dt)
            except ValueError:
                pass
        
        if fecha_hasta:
            try:
                fecha_hasta_dt = datetime.strptime(fecha_hasta, '%Y-%m-%d')
                fecha_hasta_dt = fecha_hasta_dt.replace(hour=23, minute=59, second=59)
                ventas = ventas.filter(fecha_venta__lte=fecha_hasta_dt)
            except ValueError:
                pass
        
        # Si no hay filtros, mostrar últimas 50 ventas
        if not busqueda and not fecha_desde and not fecha_hasta:
            ventas = ventas.order_by('-fecha_venta')[:50]
        else:
            ventas = ventas.order_by('-fecha_venta')[:200]
        
        context['ventas'] = ventas
        context['busqueda'] = busqueda
        context['fecha_desde'] = fecha_desde
        context['fecha_hasta'] = fecha_hasta
        
        return context


# ============================================================================
# VISTAS API PARA AJAX
# ============================================================================

class EstadoImpresoraAPIView(LoginRequiredMixin, View):
    """
    API para verificar estado de la impresora
    
    URL: GET /api/impresora/estado/
    """
    
    def get(self, request):
        """
        Retorna el estado actual de la impresora
        
        Returns:
            JsonResponse: Estado de la impresora
        """
        enabled = print_manager.enabled
        
        return JsonResponse({
            'enabled': enabled,
            'mensaje': 'Sistema de impresión habilitado' if enabled else 'Sistema deshabilitado'
        })


class UltimasImpresionesAPIView(LoginRequiredMixin, View):
    """
    API para obtener últimas impresiones
    
    URL: GET /api/impresora/ultimas/
    """
    
    def get(self, request):
        """
        Retorna las últimas impresiones realizadas
        
        Query params:
            limit: Cantidad de registros (default: 10)
            
        Returns:
            JsonResponse: Lista de impresiones
        """
        limit = int(request.GET.get('limit', 10))
        limit = min(limit, 100)  # Máximo 100
        
        impresiones = print_manager.get_ultimas_impresiones(
            usuario=request.user if not request.user.is_superuser else None,
            limit=limit
        )
        
        data = []
        for imp in impresiones:
            data.append({
                'id': imp.id,
                'accion': imp.get_accion_display(),
                'fecha': imp.fecha.isoformat(),
                'usuario': imp.usuario.get_full_name() or imp.usuario.username,
                'numero_venta': imp.detalles.get('numero_venta', ''),
                'reimpresion': imp.detalles.get('reimpresion', False),
                'error': imp.detalles.get('error', None)
            })
        
        return JsonResponse({
            'success': True,
            'impresiones': data
        })
