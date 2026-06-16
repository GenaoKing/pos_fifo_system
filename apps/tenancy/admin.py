from django.contrib import admin

from .models import Domain, Identity, Membership, SyncToken, Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ('tenant_key', 'slug', 'nombre', 'db_name', 'activo')
    search_fields = ('tenant_key', 'slug', 'nombre', 'rnc')
    list_filter = ('activo',)


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
    list_display = ('domain', 'tenant', 'is_primary', 'activo')
    search_fields = ('domain', 'tenant__tenant_key')
    list_filter = ('activo', 'is_primary')


@admin.register(SyncToken)
class SyncTokenAdmin(admin.ModelAdmin):
    list_display = ('tenant', 'sucursal_codigo', 'activo', 'ultimo_uso')
    search_fields = ('tenant__tenant_key', 'sucursal_codigo')
    list_filter = ('activo',)
    readonly_fields = ('token_hash',)
