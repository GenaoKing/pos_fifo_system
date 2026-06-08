# RBAC — cutover del POS local (pendiente, requiere verificación manual)

Estado: la **infraestructura** para enforzar permisos granulares en el POS local
ya existe (PR4). Falta el **cutover** de los checks hardcoded a esa infraestructura,
y el **sync** de la config de roles desde el cloud. Ambos se dejan documentados aquí
porque tocan el POS en producción y conviene verificarlos manualmente.

## Lo que ya está (PR4)

- `apps/permisos/templatetags/permisos.py` — filtro `{% if request.user|puede:'codigo' %}`.
- `apps/permisos/decorators.py` — `@requiere_permiso_local('codigo')` para vistas Django.
- Tests: `apps/permisos/tests/test_templatetags.py`, `test_decorators.py`.
- El motor ya funciona standalone en el local: la migración `permisos/0002_seed_rbac`
  + `bootstrap_negocio` siembran roles default (Administrador / Cajero) que replican
  la conducta histórica. `es_acceso_total` mantiene ADMIN/SYSADMIN con acceso total.

## ⚠️ Discrepancia encontrada — resolver ANTES del cutover

El rol **Cajero** por defecto se sembró con el set `PERMISOS_CAJERO_DEFAULT`
(`apps/permisos/catalogo.py`): `ventas.crear`, `ventas.aplicar_descuento`,
**`ventas.anular`**, `ventas.reimprimir`. Ese set viene del **código muerto**
`permisos_cajera` del viejo `Usuario.tiene_permiso`.

Pero la conducta REAL hoy gatea las anulaciones a **ADMIN/SYSADMIN**:
- `apps/ventas/services/anulaciones_service.py:178` → `rol in ('ADMIN','SYSADMIN')`.
- `templates/base.html:100` → nav "Anulaciones" solo si `rol == 'ADMIN'/'SYSADMIN'`.

→ El set por defecto y la conducta real **se contradicen** en `ventas.anular`.
Decisión de producto necesaria: **¿el cajero debe poder anular?**
- Si NO (conducta actual): quitar `ventas.anular` de `PERMISOS_CAJERO_DEFAULT` y, al
  migrar el gate, usar `ventas.anular` (que el Cajero ya no tendría).
- Si SÍ: mantenerlo y aceptar el cambio de conducta.

Esta es exactamente la razón por la que el cutover NO se hizo a ciegas.

## Cutover propuesto (con verificación manual del POS)

Principio: como ADMIN/SYSADMIN tienen acceso total, reemplazar `if es_admin(user)` por
`if user.tiene_permiso('X')` es **neutral para admins**; el riesgo está en qué ve/puede
el **Cajero** según el set por defecto. Verificar cada flujo con un usuario CAJERA real
(skills `run` / `verify`).

| Sitio | Check actual | Permiso sugerido | ¿Cajero cambia? |
|---|---|---|---|
| `apps/caja/views.py` (`es_admin`) | `rol in (ADMIN,SYSADMIN)` | `caja.administrar` (nuevo) | no (no lo tiene) |
| `apps/ventas/services/anulaciones_service.py:178` | `rol in (ADMIN,SYSADMIN)` | `ventas.anular` | **sí — ver discrepancia** |
| `apps/reportes/views.py` (`es_admin`, `es_cajera`) | mezcla gate + display | `reportes.ver` / `reportes.consolidado.ver` | revisar: el cajero hoy ve dashboard |
| `apps/configuracion/decorators.py` (`requiere_sysadmin`) | `es_sysadmin` | dejar como sysadmin, o `configuracion.administrar` | no |
| `templates/base.html`, `caja/index.html`, `inventario/*` | `rol == 'ADMIN'` | `{% if request.user|puede:'...' %}` | según permiso elegido |

Permisos nuevos a agregar al catálogo si se adoptan: `caja.administrar`,
`configuracion.administrar` (y los que surjan). Tras agregarlos, `manage.py sync_permisos`.

## Sync de la config de roles (cloud → sucursal) — pendiente

Hoy el local usa los roles sembrados localmente. Para que lo que el admin configure en
el portal (PR2/PR3) llegue a las sucursales, extender el pull incremental existente
(patrón `SyncIncrementalMixin` en `apps/api/views/maestros.py`):
1. Exponer `Permiso`/`Rol`/`AsignacionRol` como recursos de lectura para el token de
   sucursal (como los maestros), con filtro `?desde=`.
2. En `apps/sync`, añadir el pull + `update_or_create` de esos recursos.
3. Considerar el scope: la sucursal solo debe recibir los roles/asignaciones de SU negocio.
