# apps/tenancy — mapa para agentes

<!-- Última revisión: 2026-09-10 -->

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
| Verificar identidad sin escribir | `manage.py verificar_identidad_tenant --tenant X`; compara control plane, `Negocio` y configuración |
| Checkpoint de provisioning | `services.marcar_estado_provisioning`; estados reanudables auditados en `default` |
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
- `tenant_key`, `slug`, `db_name` y `media_prefix` son identidad física
  inmutable. El provisioning confirma por checkpoints cada base por separado:
  no existe una transacción atómica distribuida control-plane/tenant.
- El JWT tenant-aware conserva un inicio de sesión absoluto y el servidor lo
  limita a 12 horas tanto en access como en refresh.
- TEN-016 usa dos bases PostgreSQL físicas con nombres bajo
  `TENANT_TEST_DB_NAMESPACE`; nunca reutiliza o elimina bases tenant compartidas.
  El gate también prueba que una misma identidad cloud de Producto se adopta
  por separado en cada alias, sin cruzar filas entre tenants.
- Runbooks: `docs/runbooks/INSTALACION_CLIENTE_NUEVO.md`,
  `MIGRAR_IMAGENES_A_BLOB.md`. Auditoría 2026-08-20
  (`docs/exploracion/AUDITORIA_CODIGO_APPS_TENANCY.md`) — **snapshot histórico**.
