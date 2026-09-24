"""
apps/sync/decorators.py

Decorador @requiere_conexion_cloud.

Uso:
    from apps.sync.decorators import requiere_conexion_cloud

    @requiere_conexion_cloud(redirect_url='productos:lista')
    def editar_producto(request, producto_id):
        ...

Comportamiento desde A04:
- Nunca hace un ping como precondicion de una mutacion local.
- Conserva la firma para no romper imports mientras A05 conecta las vistas de
  maestros con su cola durable offline.

NOTA ARQUITECTONICA:
La conectividad no es autoridad ni durabilidad: un health 200 puede preceder a
un corte, y un arranque en frio puede dar un falso offline. A05 reemplazara este
adaptador por la cola/CAS visible; hasta entonces A04 solo elimina el requisito
de ping, sin afirmar que la propagacion de maestros ya esta implementada.
"""
def requiere_conexion_cloud(redirect_url='dashboard', mensaje=None):
    """
    Decorador que bloquea vistas de edicion de datos maestros cuando no hay
    conexion al cloud.

    Args:
        redirect_url: nombre de URL para redirect si offline (ej: 'dashboard')
        mensaje: texto custom a mostrar. Si None usa uno generico.
    """
    def decorator(view_func):
        # `redirect_url` y `mensaje` siguen aceptados por compatibilidad de API.
        return view_func
    return decorator
