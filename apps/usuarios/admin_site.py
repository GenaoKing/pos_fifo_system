"""
apps/usuarios/admin_site.py

Gate de Django Admin local (USR-002).

En cloud coexistian dos puertas de autenticacion que no se hablaban:

- El **portal** autentica una `Identity` del control plane y exige una
  `Membership` sobre el tenant.
- **`/admin/`** autentica un `Usuario` de la base `default`, y no consulta
  identity, membership ni tenant.

Es decir: un `Usuario` con `is_staff` en `default` es una credencial
administrativa independiente del ciclo de vida cloud. Desactivar su identity o
revocar su membership no toca esa puerta, y `createsuperuser` o el instalador
pueden crear exactamente esa cuenta paralela sin que nadie la vea desde el
portal.

En cloud `/admin/` no se monta. En el POS local el gate reserva esa
herramienta de soporte a usuarios activos con `is_staff` y rol `SYSADMIN`.
Cuando tenancy esta activa, tambien exige una identidad **global** del
control plane como defensa adicional.

Lo que este gate NO cubre, y sigue siendo decision de despliegue: restringir
Admin por red, exigir MFA, y auditarlo como una frontera distinta.
"""
from django.contrib.admin import AdminSite
from django.contrib.admin.apps import AdminConfig


class PosAdminSite(AdminSite):
    """Admin local exclusivo de SYSADMIN; bajo tenancy exige identidad global."""

    def has_permission(self, request):
        if not super().has_permission(request):
            return False

        # `is_staff` por si solo no distingue al administrador del negocio
        # del responsable de la configuracion tecnica de esta instalacion.
        if getattr(request.user, 'rol', None) != 'SYSADMIN':
            return False

        from apps.tenancy.context import tenancy_enabled

        if not tenancy_enabled():
            # POS local: no hay control plane con el cual contrastar.
            # `super()` ya exigio `is_active` e `is_staff`.
            return True

        return _tiene_identidad_global(request.user)


def _tiene_identidad_global(user):
    """
    True si el usuario corresponde a un operador del control plane.

    Se aceptan tres formas de probarlo, en orden de preferencia:

    1. El principal ya ES una identidad global (`is_global_identity`), que es
       como llega un operador autenticado por el portal.
    2. Trae una `identity` marcada `is_global`.
    3. Su email coincide con una `Identity` global y activa del control plane.
       Es el caso del `Usuario` de sesion en Admin, que no pasa por el JWT.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return False

    if getattr(user, 'is_global_identity', False):
        return True

    identidad = getattr(user, 'identity', None)
    if getattr(identidad, 'is_global', False):
        return True

    email = (getattr(user, 'email', '') or '').strip()
    if not email:
        return False

    from apps.tenancy.models import Identity

    return Identity.objects.using('default').filter(
        email__iexact=email,
        is_global=True,
        activo=True,
    ).exists()


class PosAdminConfig(AdminConfig):
    """Reemplaza el AdminSite por defecto por el que aplica el gate."""

    default_site = 'apps.usuarios.admin_site.PosAdminSite'
