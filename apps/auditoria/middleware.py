"""
Middleware para auditoría automática
apps/auditoria/middleware.py

Cinco hallazgos viven aca:

AUD-005  Bajo tenancy se descartaba TODO `/api/` antes de crear contexto. La
         API es justamente donde viven sync, el CRUD cloud y las operaciones
         administrativas: acciones hechas con credenciales globales, de servicio
         o por impersonacion podian no dejar ni actor ni canal.

AUD-006  La cobertura se decidia buscando substrings de URL
         (`'/productos/editar/' in path`). Las rutas reales son
         `/productos/<id>/editar/`, `/pos/api/anular-venta/` y
         `/inventario/api/ajustar/`: el matcher devolvia False para las tres.
         Cambios sensibles pudieron ejecutarse durante meses sin fila y sin
         error, porque la aplicacion no sabe que la cobertura desaparecio al
         renombrar una URL.

AUD-007  La accion se adivinaba del metodo HTTP: todo POST era `CREAR`, aunque
         la vista fuera una anulacion o un ajuste. El historial afirmaba cosas
         que no habian pasado.

AUD-011  La IP salia de `X-Forwarded-For` sin validar el proxy.

AUD-012  El detector de cambio de IP escribia la sesion en CADA request.
"""
import logging

from django.utils.deprecation import MiddlewareMixin

from apps.tenancy.context import get_current_tenant_key, tenancy_enabled

from .models import Auditoria, get_client_ip, get_user_agent

logger = logging.getLogger('auditoria')


# ---------------------------------------------------------------------
# Registro de cobertura (AUD-006 + AUD-007)
# ---------------------------------------------------------------------
#
# La clave es el `view_name` que resuelve Django (`app_name:url_name`), no un
# fragmento de path. Renombrar una URL ya no apaga la auditoria en silencio: el
# nombre de la vista es estable y, si desaparece, el test de cobertura falla.
#
# El valor dice QUE fue la accion y CUANTO importa, en vez de deducirlo del
# metodo HTTP.
VISTAS_AUDITADAS = {
    'pos:procesar_venta': ('CREATE', 'MEDIA', 'Venta procesada'),
    'pos:api_anular_venta': ('VENTA_CANCEL', 'CRITICA', 'Anulacion de venta'),
    'productos:crear': ('PROD_CREATE', 'MEDIA', 'Alta de producto'),
    'productos:editar': ('PROD_UPDATE', 'ALTA', 'Edicion de producto'),
    'productos:toggle_estado': ('PROD_UPDATE', 'ALTA', 'Cambio de estado de producto'),
    'productos:subir_imagen': ('PROD_UPDATE', 'MEDIA', 'Imagen de producto'),
    'inventario:api_ajustar': ('AJUSTE_INV', 'ALTA', 'Ajuste de inventario'),
    'inventario:compra_crear': ('COMPRA_CREATE', 'MEDIA', 'Registro de compra'),
    'inventario:compra_editar': ('COMPRA_CREATE', 'ALTA', 'Edicion de compra'),
    'caja:api_movimiento': ('UPDATE', 'ALTA', 'Movimiento de caja'),
    'caja:api_cerrar': ('UPDATE', 'ALTA', 'Cierre de turno'),
}

# Vistas que ya emiten su propio evento de dominio: auditarlas aca duplicaria
# el hecho. Se enumeran para que la ausencia sea deliberada y no un olvido.
VISTAS_CON_PRODUCTOR_PROPIO = {
    'usuarios:login',
    'usuarios:logout',
}


class AuditoriaMiddleware(MiddlewareMixin):
    """
    Middleware para auditar automáticamente acciones críticas del sistema.

    - Registra accesos a vistas declaradas en `VISTAS_AUDITADAS`
    - Captura información del cliente (IP, User Agent)
    - Maneja errores sin interrumpir el flujo
    """

    # URLs que NO deben auditarse (para evitar spam en logs)
    URLS_EXCLUIDAS = [
        '/static/',
        '/media/',
        '/admin/jsi18n/',
        '/admin/autocomplete/',
        '/__debug__/',
    ]

    METODOS_AUDITABLES = ['POST', 'PUT', 'PATCH', 'DELETE']

    def _sin_destino_de_escritura(self, request):
        """
        True si todavia no se puede escribir el evento.

        Reemplaza al viejo `_skip_api_tenancy`, que descartaba `/api/` entero.
        Lo unico que impide escribir es no tener tenant activo: con tenancy
        encendida, el router rechaza cualquier consulta sin contexto. Para
        cuando corre la fase de respuesta, la autenticacion ya lo fijo — asi que
        las mutaciones del portal SI quedan auditadas.
        """
        return tenancy_enabled() and not get_current_tenant_key()

    def process_request(self, request):
        """Guarda contexto del request. No toca la base."""
        request.audit_info = {
            'path': request.path,
            'method': request.method,
            'ip_address': get_client_ip(request),
            'user_agent': get_user_agent(request),
        }
        return None

    def process_view(self, request, view_func, view_args, view_kwargs):
        """Decide si esta vista se audita, por NOMBRE de vista."""
        if not getattr(request, 'user', None) or not request.user.is_authenticated:
            return None

        audit_info = getattr(request, 'audit_info', None)
        if audit_info is None:
            return None

        if any(request.path.startswith(x) for x in self.URLS_EXCLUIDAS):
            return None

        nombre = self._nombre_de_vista(request)
        entrada = VISTAS_AUDITADAS.get(nombre)
        if entrada is None:
            return None

        accion, nivel, etiqueta = entrada
        audit_info.update({
            'debe_auditar': True,
            'view_name': nombre,
            'accion': accion,
            'nivel': nivel,
            'etiqueta': etiqueta,
        })
        return None

    def _nombre_de_vista(self, request):
        match = getattr(request, 'resolver_match', None)
        return getattr(match, 'view_name', None) if match else None

    def process_response(self, request, response):
        """Registra la auditoría si corresponde."""
        try:
            if self._sin_destino_de_escritura(request):
                return response

            if not getattr(request, 'user', None) or not request.user.is_authenticated:
                return response

            audit_info = getattr(request, 'audit_info', None)
            if not audit_info or not audit_info.get('debe_auditar'):
                return response

            if audit_info['method'] not in self.METODOS_AUDITABLES:
                return response

            if not (200 <= response.status_code < 400):
                return response

            self._registrar_acceso(request, audit_info)

        except Exception:
            # No interrumpir el flujo si hay error en auditoría.
            logger.exception('Error en AuditoriaMiddleware')

        return response

    def process_exception(self, request, exception):
        """Registra el error en auditoría."""
        try:
            if self._sin_destino_de_escritura(request):
                return None

            if getattr(request, 'user', None) and request.user.is_authenticated:
                audit_info = getattr(request, 'audit_info', {})
                Auditoria.registrar_error(
                    descripcion=(
                        f"Error en {audit_info.get('path', 'URL desconocida')}"
                    ),
                    usuario=request.user,
                    detalle_error=str(exception),
                    nivel_importancia='ALTA',
                )
        except Exception:
            # Evitar cascada de errores: el middleware de auditoria no puede
            # convertirse en el segundo fallo de un request que ya fallo.
            logger.exception('Error auditando una excepcion')

        return None

    # === MÉTODOS AUXILIARES ===

    def _registrar_acceso(self, request, audit_info):
        """Registra el acceso con la accion DECLARADA para esa vista."""
        Auditoria.registrar(
            accion=audit_info['accion'],
            descripcion=(
                f"{audit_info['etiqueta']} - {audit_info['path']} "
                f"({audit_info['method']})"
            ),
            usuario=request.user,
            ip_address=audit_info['ip_address'],
            user_agent=audit_info['user_agent'],
            sucursal=getattr(request, 'sucursal', None),
            nivel_importancia=audit_info['nivel'],
            metadata={'view_name': audit_info.get('view_name')},
        )


class SesionAuditoriaMiddleware(MiddlewareMixin):
    """
    Middleware adicional para auditar sesiones de usuario.
    Detecta patrones sospechosos como múltiples IPs para un mismo usuario.
    """

    def process_request(self, request):
        """Verifica la sesión del usuario y detecta anomalías."""
        if tenancy_enabled() and not get_current_tenant_key():
            return None

        if not getattr(request, 'user', None) or not request.user.is_authenticated:
            return None

        try:
            ip_actual = get_client_ip(request)
            ip_sesion = request.session.get('audit_ip')

            if ip_sesion == ip_actual:
                # Sin cambio: no se toca la sesion. Escribirla en cada request
                # forzaba un UPDATE de la tabla de sesiones por cada pagina
                # (AUD-012).
                return None

            if ip_sesion:
                Auditoria.registrar(
                    accion=Auditoria.TipoAccion.ERROR_SISTEMA,
                    descripcion=f"Cambio de IP detectado: {ip_sesion} → {ip_actual}",
                    usuario=request.user,
                    ip_address=ip_actual,
                    sucursal=getattr(request, 'sucursal', None),
                    metadata={
                        'ip_anterior': ip_sesion,
                        'ip_nueva': ip_actual,
                    },
                    nivel_importancia='ALTA',
                )

            request.session['audit_ip'] = ip_actual

        except Exception:
            logger.exception('Error en SesionAuditoriaMiddleware')

        return None
