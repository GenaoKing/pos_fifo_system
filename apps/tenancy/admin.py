from django.contrib import admin

from .models import Domain, Identity, Membership, SyncToken, Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    """
    Alta y datos comerciales del tenant. La identidad de routing NO se edita.

    `tenant_key`, `db_name` y `media_prefix` deciden a que base se conecta cada
    request y bajo que namespace se guardan los archivos. Cambiarlos desde un
    formulario deja el sistema partido: los workers que ya tocaron el alias
    siguen con la conexion vieja (el `DatabaseWrapper` esta cacheado), los
    tokens emitidos con el key anterior dejan de resolver, y los blobs quedan
    en el prefijo viejo. Ninguno de esos efectos es reversible desde el admin.

    Un cambio real de routing es una migracion de tenant: drenar conexiones,
    mover datos y archivos, y recien ahi reapuntar. Se hace por comando, no por
    formulario.
    """
    list_display = ('tenant_key', 'slug', 'nombre', 'db_name', 'media_prefix', 'activo')
    search_fields = ('tenant_key', 'slug', 'nombre', 'rnc')
    list_filter = ('activo',)

    def get_readonly_fields(self, request, obj=None):
        # Editables al CREAR (hay que poder definirlos), congelados despues.
        if obj is None:
            return ()
        return Tenant.CAMPOS_INMUTABLES

    def has_delete_permission(self, request, obj=None):
        """
        Borrar la fila no borra la base ni los blobs: deja una BD huerfana con
        datos de un negocio y ninguna forma de encontrarla desde la app. Para
        dar de baja un tenant: `activo=False`.
        """
        return False


@admin.register(Identity)
class IdentityAdmin(admin.ModelAdmin):
    list_display = ('email', 'nombre', 'activo', 'is_global', 'ultimo_acceso')
    search_fields = ('email', 'nombre')
    list_filter = ('activo', 'is_global')
    readonly_fields = ('password',)


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ('identity', 'tenant', 'username', 'rol', 'activo')
    search_fields = ('identity__email', 'tenant__tenant_key', 'username')
    list_filter = ('activo', 'rol')


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    """
    NO ACTIVO. `Domain` no resuelve ningun tenant todavia.

    Fuera del modelo, el admin y su migracion no hay ningun consumidor: nada
    normaliza el host, ni elige tenant por dominio. Tenerlo editable daba
    apariencia de routing configurado — un operador podia cargar dominios y
    creer que el sistema los respeta.

    Antes de activarlo hay que definir normalizacion IDNA/lower/puerto, un
    unico primary activo por tenant y proteccion contra hosts reservados. Los
    datos cargados hoy serian ambiguos bajo esas reglas, asi que el alta queda
    cerrada hasta entonces.
    """
    list_display = ('domain', 'tenant', 'is_primary', 'activo')
    search_fields = ('domain', 'tenant__tenant_key')
    list_filter = ('activo', 'is_primary')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SyncToken)
class SyncTokenAdmin(admin.ModelAdmin):
    list_display = ('tenant', 'sucursal_codigo', 'activo', 'ultimo_uso')
    search_fields = ('tenant__tenant_key', 'sucursal_codigo')
    list_filter = ('activo',)
    readonly_fields = ('token_hash',)
