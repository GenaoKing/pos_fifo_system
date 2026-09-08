# apps/usuarios — mapa para agentes

<!-- Última revisión: 2026-09-08 -->

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
| Django Admin | `admin_site.PosAdminSite` — bajo tenancy exige identidad **global** del control plane (USR-002) |
| Crear usuarios | `UsuarioManager.create_user` / `create_superuser`; admin inicial desde `INITIAL_SYSADMIN_*` |

## Invariantes / trampas

- El rol legacy solo da **acceso total** a `SYSADMIN`/`ADMIN`; todo lo granular
  es RBAC (`AsignacionRol`, `apps/permisos`). Un usuario sin negocio no tiene
  permisos de tenant.
- `activo` es la fuente de `is_active` (propiedad con setter).
- `next` se valida antes de redirigir (USR-008); el logout audita **después** de
  cerrar la sesión (USR-004).
- `Usuario.negocio` es `PROTECT` (`usuarios.0004`). App **dual-home** en el
  router: vive en la base tenant, y `token_blacklist` la sigue por FK.
- En cloud, el portal autentica `Identity` + `Membership` (`apps/tenancy`), no
  este modelo directamente.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_USUARIOS.md`) —
  **snapshot histórico**, verificar contra código.
