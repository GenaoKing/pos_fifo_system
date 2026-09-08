# apps/tenancy — mapa para agentes

<!-- Última revisión: 2026-09-08 -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

Multitenancy cloud **DB-per-tenant**: un *control plane* en `default`
(`Tenant`, `Identity`, `Membership`, `Domain`, `SesionImpersonacion`,
`SyncToken` — `models.py`) y **una base Postgres por tenant** (alias
`tnt_<tenant_key>`). Dormida salvo `TENANCY_DB_PER_TENANT_ENABLED` (o
`force_tenancy`). En una instalación local no hace nada.

## Entrypoints

| Necesito… | Voy a… |
| --- | --- |
| **Ejecutar código dentro de un tenant** | `context.tenant_context(tenant_or_key)`; `force_tenancy(True)`; `get_current_tenant_key/alias`, `tenancy_enabled` |
| A qué base va un modelo | `router.TenantDatabaseRouter`; conjuntos `CONTROL_PLANE_APPS`, `DEFAULT_ONLY_APPS`, `DUAL_HOME_APPS` |
| Registrar la conexión de un tenant | `registry.configure_tenant_database`, `tenant_alias` |
| Autenticar el portal (JWT tenant-aware) | `authentication.TenantJWTAuthentication`, `IdentityPrincipal` |
| Media separada por tenant | `media.tenant_media_prefix`, `producto_image_upload_to`, `config_logo_upload_to` |
| Comandos por tenant | `management/base.TenantCommandMixin`; `manage.py with_tenant --tenant X -- <cmd>` |
| Crear / migrar / respaldar | `bootstrap_tenant`, `migrate_cloud`, `migrate_tenants`, `backup_tenant` (pg_dump real), `migrar_media_tenant`, `normalizar_import_tenant` |
| Checks de aislamiento | `checks.py` (corren en `manage.py check`) |

## Invariantes / trampas

- Un modelo de tenant consultado **sin contexto falla en voz alta**
  (`TenantContextError`); degradar a `default` es lo que mezclaba negocios.
  `ClearTenantContextMiddleware` limpia el contexto entre requests.
- Mover una app entre los tres conjuntos del router **es una migración de
  datos**, no configuración (BUG-F): hay que desregistrar sus migraciones en
  cada base afectada. `usuarios`, `negocios`, `auth`, `contenttypes` y
  `token_blacklist` son dual-home por sus FKs.
- Las rutas de templates del POS **no existen** en cloud (`config/urls.py`,
  BUG-E). `/admin/` en cloud exige identidad global
  (`apps/usuarios/admin_site.py`).
- `media_prefix` es único por tenant; `Lower(email)` único en `Identity`.
- Runbooks: `docs/runbooks/INSTALACION_CLIENTE_NUEVO.md`,
  `MIGRAR_IMAGENES_A_BLOB.md`. Auditoría 2026-08-20
  (`docs/exploracion/AUDITORIA_CODIGO_APPS_TENANCY.md`) — **snapshot histórico**.
