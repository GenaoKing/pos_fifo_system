# apps/permisos — mapa para agentes

<!-- Última revisión: 2026-09-10 -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

RBAC data-driven y multitenant (`models.py`): `Permiso` (catálogo declarado en
código), `Rol` (por negocio; de sistema: Administrador / Cajero),
`AsignacionRol` (global o acotada a una sucursal), `EstadoRBAC` (revisiones de
capacidades), `AutorizacionOverride`
(token de un solo uso: `credito.exceder_limite`, `caja.retiro`,
`ventas.descuento`) y `CredencialFisica` (carnet para autorizar sin teclear).

## Entrypoints

| Necesito… | Voy a… |
| --- | --- |
| **¿Puede el usuario X?** | `engine.tiene_permiso(usuario, codigo, sucursal=None)` (vía `Usuario.tiene_permiso`); `TODAS` como centinela de scope |
| Mutar roles/asignaciones | `services.py`: transacción, lock, tombstone, revisión y auditoría CT-01 |
| Resolver asignaciones para otro consumidor | `engine.asignaciones_efectivas`; no dupliques sus filtros de tenant/scope/estado |
| Gatear una vista del POS | `decorators.requiere_permiso_local` (redirige) · `requiere_permiso_json` (403). DRF: `apps/api/permissions.py` |
| Gatear en template | `{% load permisos %}` → `user|puede:'codigo'` |
| Alcance de **datos** (qué sucursales ve) | `alcance.alcance_de(usuario, PERM_PROPIO, PERM_CONSOLIDADO)` → `Alcance.filtrar(qs)` (lo usan reportes y auditoría) |
| Agregar un permiso | `catalogo.CATALOGO` (+ `PERMISOS_CAJERO_DEFAULT`, `PERMISOS_OPERADOR_SAAS`) → `manage.py sync_permisos`; no cambia roles salvo `--aplicar-presets-sistema` explícito |
| Bootstrap RBAC de una instalación | `manage.py bootstrap_negocio` · `seed.py`; con varios negocios exige `--negocio-id` |
| Preflight para retirar bypass ADMIN | `manage.py preflight_rbac_admin_cutover [--tenant <key>]` (solo lectura) |
| Fixtures en tests | `testing.habilitar_cajero`, `crear_negocio`, `crear_rol` |
| Freno de intentos de credenciales | `throttling.py` |

## Invariantes / trampas

- **Default deny.** Un código que no está en el catálogo deniega siempre,
  incluso a un admin. `suscripciones.administrar` solo la aprueba un principal
  global (SYSADMIN/superusuario).
- `sucursal=None` significa **solo asignaciones globales** (PER-003), no "en
  alguna sucursal". Una asignación global cubre cualquier sucursal.
- Cache con clave por tenant (PER-001); con `LocMemCache` **no** cachea entre
  requests, solo memo por request (`middleware.PermisosRequestCacheMiddleware`,
  PER-002). Las signals invalidan en `on_commit` (PER-010/011).
- Asignaciones cuyo rol es de otro negocio no cuentan (PER-004).
- `Rol.cloud_id`/`AsignacionRol.cloud_id` son inmutables. Mover una asignación
  crea/reactiva la identidad destino y deja tombstone de la anterior; reactivar
  un rol no reactiva sus asignaciones.
- Las mutaciones soportadas pasan por `services.py`; el Django Admin de
  Permiso/Rol/AsignacionRol es deliberadamente de solo lectura.
- `sembrar_catalogo` serializa upserts concurrentes con un advisory lock
  transaccional por BD PostgreSQL; no lo saques del bloque `atomic`.
- `RBAC_LEGACY_ADMIN_BYPASS` queda `True` hasta que el preflight dé cero
  bloqueadores en cada tenant. No apagarlo por inferencia ni por una prueba mock.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_PERMISOS.md`) —
  **snapshot histórico**; estado A03 actualizado en su tabla final.
