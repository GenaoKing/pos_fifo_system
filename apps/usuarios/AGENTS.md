# apps/usuarios — mapa para agentes

<!-- Última revisión: 2026-09-18 (C04 p5.2: ciclo de vida portal) -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

`Usuario` custom (`apps/usuarios/models.py`): `username`, `email`, `rol`
legacy (`SYSADMIN` / `ADMIN` / `CAJERA`), `negocio`, `activo`, `is_staff`.
Login/logout del POS local y el gate de Django Admin en cloud.

## Entrypoints

| Necesito… | Voy a… |
| --- | --- |
| **¿Tiene permiso?** | `Usuario.tiene_permiso(codigo, sucursal=None)` → delega en `apps.permisos.engine` |
| Rol legacy | `Usuario.es_admin` / `es_sysadmin` / `es_cajera`; `ROLES` |
| Login / logout del POS | `views.login_view` (`/login/`), `views.logout_view` (`/logout/`) |
| Freno de fuerza bruta | `throttling.LimiteLogin` (ráfaga + sostenida, clave IP+username, USR-006) |
| Django Admin | Solo local: `admin_site.PosAdminSite`; `/admin/` no se monta en cloud |
| Provisionar usuario operativo | `services.provisionar_usuario(...)` o `manage.py provisionar_usuario_tenant`; crea credencial local + RBAC + CT-01 en una transacción tenant |
| Actualizar/desactivar usuario | `services.actualizar_usuario(...)`; exige actor autorizado, motivo y CT-01 |
| Alta/baja desde portal cloud | `services.provisionar_usuario_portal(...)` / `actualizar_usuario_portal(...)`; con tenancy activa crean/revocan `Identity` + `Membership` además del usuario local |
| Crear principal humano en código | `UsuarioManager.create_human_user`; valida credencial y pertenencia |
| Crear cuenta de servicio | `UsuarioManager.create_service_user`; contraseña no utilizable |

## Invariantes / trampas

- El rol legacy solo da **acceso total** a `SYSADMIN`/`ADMIN`; todo lo granular
  es RBAC (`AsignacionRol`, `apps/permisos`). Un usuario sin negocio no tiene
  permisos de tenant.
- `activo` es la fuente de `is_active` (propiedad con setter).
- `next` se valida antes de redirigir (USR-008); el logout audita **después** de
  cerrar la sesión (USR-004).
- Los renders de error del login dependen del context processor seguro
  (`config_o_none()`); no llaman `get_config()` ni crean configuración al
  bloquear una cuenta o informar que está inactiva (CFG-012).
- `Usuario.negocio` es `PROTECT` (`usuarios.0004`). App **dual-home** en el
  router: vive en la base tenant, y `token_blacklist` la sigue por FK.
- En cloud, el portal autentica `Identity` + `Membership` (`apps/tenancy`), no
  este modelo directamente. La clave de `Identity` y la de `Usuario` son
  credenciales separadas; nunca se copian ni sincronizan en claro.
- Las sesiones local y JWT tienen un máximo absoluto de 12 horas, además de su
  expiración deslizante o por token. `last_login` y `ultimo_acceso` se actualizan
  juntos en los logins soportados.
- La creación directa desde Django Admin está cerrada: el alta soportada usa el
  servicio/comando anterior para no separar usuario, RBAC y auditoría.
- `Identity`/`Membership` viven en control plane y `Usuario`/CT-01 en la BD
  tenant: no hay transacción distribuida Django. El servicio portal prevalida y
  compensa un alta incompleta dejando el acceso revocado; no debe prometer
  atomicidad entre ambas BDs.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_USUARIOS.md`) —
  **snapshot histórico**, verificar contra código.
