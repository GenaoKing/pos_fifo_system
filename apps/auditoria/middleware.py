"""
Middleware para auditoría automática
apps/auditoria/middleware.py
"""
from django.utils.deprecation import MiddlewareMixin

from apps.tenancy.context import tenancy_enabled
from .models import Auditoria, get_client_ip, get_user_agent


class AuditoriaMiddleware(MiddlewareMixin):
    """
    Middleware para auditar automáticamente acciones críticas del sistema.
    
    Características:
    - Registra accesos a URLs críticas
    - Captura información del cliente (IP, User Agent)
    - Detecta patrones sospechosos
    - Maneja errores sin interrumpir el flujo
    """
    
    # URLs que requieren auditoría automática
    URLS_CRITICAS = [
        '/productos/eliminar/',
        '/productos/editar/',
        '/ventas/anular/',
        '/ventas/pos/',
        '/usuarios/crear/',
        '/usuarios/editar/',
        '/usuarios/toggle/',
        '/inventario/ajustar/',
        '/inventario/compras/crear/',
        '/reportes/',
    ]
    
    # URLs que NO deben auditarse (para evitar spam en logs)
    URLS_EXCLUIDAS = [
        '/static/',
        '/media/',
        '/admin/jsi18n/',
        '/admin/autocomplete/',
        '/__debug__/',
    ]
    
    # Métodos HTTP que disparan auditoría
    METODOS_AUDITABLES = ['POST', 'PUT', 'PATCH', 'DELETE']

    def _skip_api_tenancy(self, request):
        return tenancy_enabled() and request.path.startswith('/api/')
    
    def process_request(self, request):
        """
        Se ejecuta antes de que la vista procese el request.
        Guarda información relevante en el request para uso posterior.
        """
        # Guardar información del request para process_response
        if self._skip_api_tenancy(request):
            return None

        request.audit_info = {
            'path': request.path,
            'method': request.method,
            'ip_address': get_client_ip(request),
            'user_agent': get_user_agent(request),
        }
        
        return None
    
    def process_view(self, request, view_func, view_args, view_kwargs):
        """
        Se ejecuta justo antes de llamar a la vista.
        Aquí podemos auditar el acceso a vistas específicas.
        """
        # Solo auditar usuarios autenticados
        if self._skip_api_tenancy(request):
            return None

        if not request.user.is_authenticated:
            return None
        
        # Verificar si la URL debe ser auditada
        if self._debe_auditar_url(request.path):
            # Guardar que se debe auditar este acceso
            request.audit_info['debe_auditar'] = True
            request.audit_info['view_name'] = view_func.__name__
        
        return None
    
    def process_response(self, request, response):
        """
        Se ejecuta después de que la vista genera la respuesta.
        Aquí registramos la auditoría si es necesario.
        """
        if self._skip_api_tenancy(request):
            return response

        try:
            # Solo auditar usuarios autenticados
            if not request.user.is_authenticated:
                return response
            
            # Verificar si hay información de auditoría
            audit_info = getattr(request, 'audit_info', None)
            if not audit_info:
                return response
            
            # Verificar si se debe auditar
            if not audit_info.get('debe_auditar', False):
                return response
            
            # Verificar si el método HTTP requiere auditoría
            if audit_info['method'] not in self.METODOS_AUDITABLES:
                return response
            
            # Solo auditar respuestas exitosas (2xx y 3xx)
            if not (200 <= response.status_code < 400):
                return response
            
            # Registrar la auditoría
            self._registrar_acceso(request, audit_info)
            
        except Exception as e:
            # No interrumpir el flujo si hay error en auditoría
            # Solo loguear el error
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error en AuditoriaMiddleware: {str(e)}")
        
        return response
    
    def process_exception(self, request, exception):
        """
        Se ejecuta cuando una vista lanza una excepción.
        Registra el error en auditoría.
        """
        if self._skip_api_tenancy(request):
            return None

        try:
            if request.user.is_authenticated:
                audit_info = getattr(request, 'audit_info', {})
                
                Auditoria.registrar_error(
                    descripcion=f"Error en {audit_info.get('path', 'URL desconocida')}: {str(exception)}",
                    usuario=request.user,
                    detalle_error=str(exception),
                    nivel_importancia='ALTA'
                )
        except Exception as e:
            # Evitar cascada de errores
            pass
        
        return None
    
    # === MÉTODOS AUXILIARES ===
    
    def _debe_auditar_url(self, path):
        """
        Determina si una URL debe ser auditada.
        
        Args:
            path: str - Ruta del request
        
        Returns:
            bool: True si debe auditarse
        """
        # Verificar URLs excluidas primero
        for excluida in self.URLS_EXCLUIDAS:
            if path.startswith(excluida):
                return False
        
        # Verificar URLs críticas
        for critica in self.URLS_CRITICAS:
            if critica in path:
                return True
        
        return False
    
    def _registrar_acceso(self, request, audit_info):
        """
        Registra el acceso en la auditoría.
        
        Args:
            request: HttpRequest
            audit_info: dict con información del acceso
        """
        # Determinar tipo de acción basado en el método HTTP
        metodo = audit_info['method']
        path = audit_info['path']
        
        if metodo == 'POST':
            if 'crear' in path or 'nuevo' in path:
                accion = Auditoria.TipoAccion.CREAR
            elif 'login' in path:
                return  # El login ya tiene su propia auditoría
            else:
                accion = Auditoria.TipoAccion.CREAR
        
        elif metodo == 'PUT' or metodo == 'PATCH':
            accion = Auditoria.TipoAccion.EDITAR
        
        elif metodo == 'DELETE':
            accion = Auditoria.TipoAccion.ELIMINAR
        
        else:
            accion = Auditoria.TipoAccion.VER
        
        # Determinar nivel de importancia
        if '/anular/' in path or '/eliminar/' in path:
            nivel = Auditoria.NivelImportancia.CRITICA
        elif '/editar/' in path or '/ajustar/' in path:
            nivel = Auditoria.NivelImportancia.ALTA
        else:
            nivel = Auditoria.NivelImportancia.MEDIA
        
        # Registrar
        Auditoria.registrar(
            accion=accion,
            descripcion=f"Acceso a {path} ({metodo}) - Vista: {audit_info.get('view_name', 'desconocida')}",
            usuario=request.user,
            ip_address=audit_info['ip_address'],
            user_agent=audit_info['user_agent'],
            nivel_importancia=nivel
        )


class SesionAuditoriaMiddleware(MiddlewareMixin):
    """
    Middleware adicional para auditar sesiones de usuario.
    Detecta patrones sospechosos como múltiples IPs para un mismo usuario.
    """
    
    def process_request(self, request):
        """
        Verifica la sesión del usuario y detecta anomalías.
        """
        if tenancy_enabled() and request.path.startswith('/api/'):
            return None

        if not request.user.is_authenticated:
            return None
        
        try:
            ip_actual = get_client_ip(request)
            
            # Obtener IP de la sesión anterior
            ip_sesion = request.session.get('audit_ip')
            
            if ip_sesion and ip_sesion != ip_actual:
                # La IP cambió - posible cambio de red o ataque
                Auditoria.registrar(
                    accion=Auditoria.TipoAccion.ERROR_SISTEMA,
                    descripcion=f"Cambio de IP detectado: {ip_sesion} → {ip_actual}",
                    usuario=request.user,
                    ip_address=ip_actual,
                    metadata={
                        'ip_anterior': ip_sesion,
                        'ip_nueva': ip_actual,
                    },
                    nivel_importancia='ALTA'
                )
            
            # Actualizar IP en sesión
            request.session['audit_ip'] = ip_actual
            
        except Exception as e:
            pass
        
        return None
