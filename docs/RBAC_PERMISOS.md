# RBAC — Sistema de permisos data-driven y multitenant

Referencia de arquitectura del sistema de permisos. Explica **qué se hizo**, **cómo
funciona**, **cómo extenderlo** y **qué decisiones quedan abiertas** para evolucionarlo.

- Plan de decisión original: `C:\Users\Santiago\.claude\plans\abstract-skipping-parnas.md`
- Cutover del POS local (hecho + verificado): [RBAC_LOCAL_CUTOVER_PENDIENTE.md](RBAC_LOCAL_CUTOVER_PENDIENTE.md)

---

## 1. Problema y objetivo

El control de acceso era **hardcoded**: `Usuario.rol ∈ {SYSADMIN, ADMIN, CAJERA}` y mapas
de permisos escritos en Python. Eso impedía lo que el negocio necesita: que **el mismo rol
tenga permisos distintos según el negocio** (el "Cajero" de Royal Plast puede registrar
compras; el de SK Performance, no). Además el control era **solo de UI** (se ocultaban
menús, pero los endpoints seguían alcanzables por URL).

**Solución:** un motor de permisos **data-driven** (configurable en runtime), **scoped por
negocio (tenant)**, con **enforcement server-side y default-deny**. Los frontends solo
reflejan lo que el backend concede.

---

## 2. Modelo de datos

```
Negocio (tenant)  1 ─── N  Sucursal
   │                          
   │ 1                        
   └── N  Rol ──M2M── Permiso (catálogo GLOBAL)
            │
            └── N  AsignacionRol ── Usuario   (opcionalmente acotada a una Sucursal)

Usuario.negocio  (FK, null = global p.ej. SYSADMIN)
Usuario.rol      (enum legacy, informativo — ver §10)
```

| Modelo | Archivo | Rol |
|---|---|---|
| `Negocio` | `apps/negocios/models.py` | Tenant lógico actual. Agrupa N sucursales. En DB-per-tenant evoluciona hacia registro del control plane con `tenant_key` estable. |
| `Permiso` | `apps/permisos/models.py` | Catálogo **global** de acciones (`codigo`, ej. `clientes.crear`). Lo que *se puede* controlar. |
| `Rol` | `apps/permisos/models.py` | Rol **por negocio**; identidad `cloud_id` inmutable, `revision` monotónica, `deleted_at`, propiedad `origen_cloud` y M2M `permisos`. |
| `AsignacionRol` | `apps/permisos/models.py` | Une `usuario`→`rol`, opcional `sucursal`; misma identidad/revisión/tombstone. La terna no se edita: moverla revoca la anterior y crea/reactiva otra. |
| `EstadoRBAC` | `apps/permisos/models.py` | Revisiones del catálogo y de asignaciones que viajan en `rbac.capabilities.v1`. |

`Sucursal.negocio` y `Usuario.negocio` son FKs añadidas (migraciones `sucursales/0003`,
`usuarios/0003`). `Permiso` es global (no tiene FK a negocio): lo que varía por tenant es
**qué rol tiene cuál permiso**, no el catálogo.

---

## 3. El motor (`apps/permisos/engine.py`)

API pública:

- `permisos_de_usuario(usuario, sucursal=None) -> set[str]` — set de códigos efectivos.
  Para acceso total devuelve **todos** los códigos del catálogo (para el payload/`can()`).
- `tiene_permiso(usuario, codigo, sucursal=None) -> bool` — chequeo. **Corto-circuita** el
  acceso total solo después de validar que el código exista y no sea capacidad
  exclusiva del operador SaaS.
- `asignaciones_efectivas(...)` — queryset canónico compartido por el motor y
  notificaciones para no divergir en tenant/scope/estados.
- `invalidar_cache()` — la llaman las signals.

`Usuario.tiene_permiso(codigo, sucursal=None)` delega aquí.

**Acceso total (`es_acceso_total`):** superusuario de Django y `SYSADMIN`.
`ADMIN` conserva temporalmente el bypass solo mientras
`RBAC_LEGACY_ADMIN_BYPASS=True`; el preflight para retirarlo está en §10.

**Resolución (no acceso total):** unión de permisos de las `AsignacionRol` activas (rol
activo). Si se pasa `sucursal`, aplican las globales (sucursal NULL) + las de esa sucursal.

**Caché:** por `(usuario, sucursal)` con una **versión global**. Cualquier cambio en
`Rol`/`Rol.permisos`/`AsignacionRol`/`Permiso` bumpea la versión vía signals
(`apps/permisos/signals.py`), invalidando todo de forma portable. Con
`LocMemCache` no cachea entre requests; con Redis/memcached compartido recupera
ese cache de forma segura entre workers.

---

## 4. Catálogo de permisos (`apps/permisos/catalogo.py`)

Lista declarativa `CATALOGO` (fuente de verdad del dev). No se duplica aquí el
inventario completo: la lista vigente vive en `apps/permisos/catalogo.py` y las
migraciones históricas conservan snapshots propios, no importan ese módulo vivo.

**Agregar un permiso:** añadir una línea a `CATALOGO` y correr `manage.py sync_permisos`
(idempotente). Luego aplicarlo en la vista/endpoint correspondiente (§6).

---

## 5. Seed y bootstrap

- **`manage.py sync_permisos [--tenant <key>]`** — upsert del catálogo. No
  modifica roles existentes salvo que se pida explícitamente
  `--aplicar-presets-sistema`.
- **`manage.py bootstrap_negocio [--nombre "Royal Plast"] [--negocio-id N]`** — para una instalación
  existente: crea un Negocio (toma el nombre de `ConfiguracionNegocio` si no se pasa),
  enlaza sucursales/usuarios huérfanos, crea los roles de sistema y asigna rol según el
  `rol` legacy de cada usuario.
- **Data migration `permisos/0002_seed_rbac`** — siempre siembra el catálogo; si ya hay
  usuarios/sucursales, hace el bootstrap. En BD fresca/de tests solo siembra el catálogo.

**Roles de sistema por defecto** (`apps/permisos/seed.py:crear_roles_default`):
- **Administrador**: todos los permisos (plantilla inicial).
- **Cajero**: `ventas.crear`, `ventas.aplicar_descuento`, `ventas.reimprimir`.

Los permisos se fijan **solo al crear** el rol → re-ejecutar bootstrap **no pisa**
personalizaciones del admin ni reactiva asignaciones revocadas. Todo el bootstrap
es transaccional; con varios negocios se niega a elegir el primero y exige
`--negocio-id`. Bajo DB-per-tenant ambos comandos exigen `--tenant`.

### Rol Cajero por defecto — por qué NO incluye `ventas.anular`
El Cajero default no recibe `ventas.anular`. El contrato ya define la capacidad,
pero el servicio de anulación todavía usa el rol legacy; C02/C05 debe cambiar el
consumidor para reevaluar el permiso granular contra la sucursal de la venta.
Hasta entonces no se acredita que un rol custom pueda completar esa acción.

> Para una base con varios negocios, el bootstrap nunca adopta huérfanos ni
> elige un tenant por orden de PK. Resolver su pertenencia explícitamente.

---

## 6. Enforcement

### 6.1 API DRF (`apps/api/permissions.py`)
- `TienePermiso` + **`requiere_permiso('codigo')`** (factory) → usar en `permission_classes`.
- `PuedeLeerMaestro` — lectura de maestros: permite **token de servicio de sucursal** (sync)
  o `'<permiso_base>.ver'`.
- `MaestroPermisoMixin` — `get_permissions` por acción para ViewSets de maestros: lecturas →
  `PuedeLeerMaestro`; escrituras → `'<permiso_base>.{crear|editar|eliminar}'`. El viewset
  declara `permiso_base`.
- `EsSucursalAutenticada` — se conserva para el sync (tokens de servicio).

Endpoints adoptados:
- Maestros productos/categorías/clientes (`apps/api/views/maestros.py`) — `permiso_base`.
- Reportes (`reportes.ver` / `reportes.consolidado.ver`), CxC (`cuentas_por_cobrar.ver` vía
  `PuedeLeerMaestro`), sucursales (`sucursales.ver`).

**Gatear un endpoint nuevo:**
```python
@permission_classes([IsAuthenticated, requiere_permiso('mi_modulo.accion')])
def mi_vista(request): ...
# o en un ViewSet de maestros: permiso_base = 'mi_modulo' + MaestroPermisoMixin
```

### 6.2 POS local Django (`apps/permisos/`) — ✅ cutover HECHO
- **Template tag** `templatetags/permisos.py`: `{% load permisos %}` →
  `{% if request.user|puede:'compras.registrar' %}`.
- **Decorador** `decorators.py`: `@requiere_permiso_local('codigo')` (redirige si falta).
- **Gates migrados a `tiene_permiso`/`|puede:`** (server-side, cierra el acceso por URL):
  - `caja/views.py` (`es_admin` → `caja.administrar`), `reportes/views.py`
    (`es_admin` → `reportes.consolidado.ver`), `inventario/views.py`
    (compra → `compras.registrar`, ajustes → `inventario.ajustar`),
    `ventas/views.py` (anulaciones → `ventas.anular`), `auditoria/views.py` (`auditoria.ver`).
  - Plantillas `base.html`, `caja/index.html`, `inventario/*` → `|puede:`.
- **Verificado en la app corriendo** (cajera bloqueada 302 en URLs admin, admin 200). Ver
  [RBAC_LOCAL_CUTOVER_PENDIENTE.md](RBAC_LOCAL_CUTOVER_PENDIENTE.md) (rama `features/rbac-cutover-local`).

> **Se conservó a propósito (NO es la vulnerabilidad):** el *scoping de datos* por rol
> (`es_cajera` → la cajera ve solo sus ventas/cobros), `requiere_sysadmin` y el link a
> `/admin` (SYSADMIN = operador global), y los decoradores genéricos
> `requiere_admin_o_sysadmin`. Un decorador genérico no se mapea a un único permiso.

---

## 7. Sesión y portal React (`C:\Proyectos\pos-cloud-dashboard`)

`apps/api/auth_views.py`: `/auth/login/` y `/auth/me/` conservan `permisos: string[]`,
`modulos` y `negocio {id,slug,nombre}`, y añaden el envelope versionado
`rbac.capabilities.v1`; el JWT lleva el tenant técnico cuando aplica.

Portal:
- `src/lib/auth.ts` — `User` con `permisos` y `negocio`.
- `AuthContext` — `can(codigo)`.
- `ProtectedRoute` — prop `requiere` (gatea rutas; el backend igualmente responde 403).
- `Sidebar` — oculta ítems por permiso.
- Pantalla **`/roles`** (`lib/roles.ts` + `hooks/useRoles.ts` + `pages/Roles.tsx`): el admin
  del negocio edita los permisos de cada rol por módulo (qué *puede hacer* cada rol).
- Pantalla **`/asignaciones`** (`lib/asignaciones.ts` + `hooks/useAsignaciones.ts` +
  `pages/Asignaciones.tsx`): master-detail usuario↔rol — *qué rol tiene cada persona*, con
  scope opcional de sucursal. Los usuarios ADMIN/SYSADMIN se marcan con un banner de "acceso
  total" (para ADMIN solo mientras `RBAC_LEGACY_ADMIN_BYPASS=True`). Filtra `activo` en cliente para
  no mostrar las asignaciones soft-deleted. Ambas pantallas gated por `permisos.administrar`.

### 7.1 Endpoints de administración RBAC (`apps/api/views/permisos.py`)

Gated por `permisos.administrar`, scoped por **`negocio_actual(request)`** (`apps/negocios/
utils.py`). Aislamiento cross-tenant: un admin del negocio A no ve/edita lo de B.
```
GET/POST/PATCH/DELETE  /api/v1/permisos/roles/
GET                    /api/v1/permisos/catalogo/
GET/POST/PATCH/DELETE  /api/v1/permisos/asignaciones/
GET                    /api/v1/permisos/usuarios/      ← selector de la UI (read-only)
GET                    /api/v1/permisos/sucursales/    ← scope opcional (read-only)
```
Los dos `GET` read-only enumeran los usuarios y sucursales **del negocio** para poblar los
selectores de la pantalla de asignación (la gestión de usuarios vive fuera de RBAC).

**Ciclo de vida versionado (load-bearing):**

- Los ViewSets delegan toda mutación a `apps/permisos/services.py`: transacción,
  bloqueo, validación tenant/sucursal y exactamente un evento CT-01 por cambio.
- Rol y asignación tienen `cloud_id` inmutable, `revision` monotónica,
  `deleted_at` y `origen_cloud`. `DELETE` es baja lógica versionada.
- Mover usuario/rol/sucursal revoca la asignación anterior y crea/reactiva otra
  con identidad nueva en la misma transacción. Reactivar un rol no reactiva sus
  asignaciones revocadas.
- El cliente puede enviar `X-RBAC-Revision`; una revisión obsoleta responde
  `409 rbac_revision_conflict` y no escribe.

---

## 8. Multitenancy y DB-per-tenant

Hoy es **row-level** (FK `negocio`). La decision cloud actual es
**DB-per-tenant**: control plane global + una base PostgreSQL por tenant. Fuente
viva: `docs/TENANCY_DB_PER_TENANT.md`.

Esto cambia el objetivo futuro:
- **`negocio_actual(request)` es el único punto de resolución de tenant.** Úsalo siempre.
  Con DB-per-tenant pasa a significar "el tenant/BD activa" y debe fallar rapido
  si se consulta data operativa sin tenant activo.
- `Permiso`, catalogo de modulos, planes y memberships viven en el control plane.
- `Rol`/`AsignacionRol` y usuarios operativos viven en la BD del tenant.
- `Negocio.slug` deja de ser `schema_name`; el identificador tecnico estable es
  `tenant_key` y la BD se nombra `tnt_<tenant_key>`.

**Importante:** el RBAC controla **qué acciones** puede hacer un usuario, **no** el
**aislamiento de datos** entre tenants. Hoy los maestros (productos/clientes) **no** están
scoped por negocio; el cloud es de-facto single-tenant hasta DB-per-tenant. La excepción
son los endpoints admin RBAC (§7), que **sí** scopean por negocio por corrección.

---

## 9. Mapa de entrega (ramas)

**Consolidado:** todo el RBAC (keystone + adopción API + endpoints admin + infra local +
auditoría/docs) se mergeó (fast-forward) a **`features/cloud-dashboard`** (backend) y a
**`main`** (React `pos-cloud-dashboard`). El **cutover del POS local + sync de roles** vive en
`features/rbac-cutover-local` (off cloud-dashboard, pendiente de merge tras verificación — ya
verificado en la app corriendo).

Ramas originales (intactas, por si se revisan por separado): `features/rbac-permisos` (keystone),
`features/rbac-pr1-api-adopcion`, `features/rbac-pr2-admin-endpoints`, `features/rbac-pr4-local-infra`,
`features/rbac-audit-docs`; React `feat/rbac-permisos-ui`. (El sistema de **módulos/suscripciones**
es trabajo separado — ver `docs/ARQUITECTURA_MODULOS.md`.)

---

## 10. Decisiones y su porqué

- **Motor propio (no librería):** ninguna lib (guardian/rules/role-permissions) hace
  configuración rol→permiso **por tenant en runtime** de forma limpia.
- **ADMIN con acceso total — transición controlada:** `SYSADMIN` y superusuario
  siempre son principales globales. `ADMIN` solo conserva el bypass mientras
  `RBAC_LEGACY_ADMIN_BYPASS=True`. Antes de desactivarlo en cada tenant se debe
  ejecutar `manage.py preflight_rbac_admin_cutover --tenant <tenant_key>`; el
  comando falla si algún ADMIN activo carece de una asignación explícita con
  `permisos.administrar`.
- **`Negocio` separado de `ConfiguracionNegocio`:** distinta cardinalidad —
  `ConfiguracionNegocio` es **OneToOne con Sucursal** (config por sucursal); `Negocio` es el
  tenant (1→N sucursales). Deuda menor futura: mover los campos de identidad
  (`nombre_negocio`, `rnc`, `logo`…) de `ConfiguracionNegocio` a `Negocio`.
- **Caché versionada por tenant:** las mutaciones de rol, asignación y M2M
  incrementan revisión e invalidan la decisión efectiva después del commit.
- **Cutover local (decisiones del cutover):**
  - 3 permisos nuevos: `caja.administrar`, `auditoria.ver`, `configuracion.administrar`.
  - **`sync_permisos` separa catálogo de política:** por defecto solo hace upsert
    del catálogo. `--aplicar-presets-sistema` es una decisión explícita y no
    revive asignaciones revocadas ni pisa roles personalizados.
  - **El sync cloud→local propaga DEFINICIONES de rol Y asignaciones usuario→rol.**
    - Definiciones (`Rol`→permisos): `GET /api/v1/sync/roles/` + `SyncEngine._pull_roles()`.
    - Asignaciones (`AsignacionRol`): `GET /api/v1/sync/asignaciones/` + `SyncEngine._pull_asignaciones()`
      (corre después de `_pull_roles` en `pull_maestros`). El POS solicita
      `X-RBAC-Schema: rbac.sync.v2` y `snapshot=full`; clientes sin header siguen
      recibiendo la lista legacy.
    - **Identidad cross-DB v2 = `cloud_id` inmutable + `revision`:** las claves
      naturales (`usuario_username`, `rol_slug`, `sucursal_codigo`) se conservan
      por compatibilidad. El POS valida el `tenant_key` técnico activo y, para
      asignaciones, `scope.branch_code` antes de aplicar o reconciliar ausencias.
    - **El pull NO crea usuarios:** si el `username` no existe localmente, omite la asignación
      (evita provisionar credenciales por sync). Por eso el alta de usuarios sigue siendo local
      (`bootstrap_negocio` por el `rol` legacy); el sync solo sincroniza *qué rol* tiene un usuario
      que **ya existe** en ambos lados.
    - `snapshot_complete=true` permite revocar ausentes solo para filas de
      propiedad cloud; una respuesta parcial, fallida o con scope incorrecto no revoca.
  - **Consumidores pendientes por ownership:** el contrato publica
    `ventas.anular` y `ventas.reimprimir`, pero C02/C05 debe integrar el gate
    final y el scope de la venta en `apps/ventas`, `utils/impresoras` y la
    navegación global. A03 no escribe esas superficies.

---

## 11. Estado / cómo seguir

**Candidato A03/CT-02:** motor + DRF + endpoints admin + payload versionado +
revocación cloud→local por identidad estable. El productor queda listo para
revisión; los gates finales de anulación/reimpresión pertenecen al handoff C y
siguen pendientes. Desplegar y retirar el bypass ADMIN son gates operacionales
separados, no ejecutados por esta tarea.

### Mini-handoff — qué desarrollar a futuro (ordenado por valor/esfuerzo)

Esta sección es el punto de entrada para quien retome el RBAC.

1. **Aislamiento de datos por tenant** en maestros *(el más importante para escalar a multi-cliente
   en una sola BD cloud)* — el RBAC controla *acciones*, **no** *qué datos* ve cada tenant; hoy los
   maestros (productos/clientes) no están scoped por negocio, así que el cloud es de-facto
   single-tenant. Lo resolverá DB-per-tenant con control plane global. Ver §8 y
   `docs/TENANCY_DB_PER_TENANT.md`. **Empezar aquí** antes de vender a un 3.º cliente
   en la BD compartida.
2. **Cutover de ADMIN por tenant** — ejecutar el preflight, corregir únicamente
   los ADMIN reportados y después configurar `RBAC_LEGACY_ADMIN_BYPASS=False`.
   No cambiar la bandera antes de obtener un preflight verde por tenant.
3. **Provisión de usuarios cross-DB** — hoy el sync de asignaciones **omite** usuarios que no existen
   localmente (no crea credenciales por sync, a propósito). Si se quiere dar de alta una cajera desde
   el portal y que aparezca en la sucursal, falta un flujo de provisión de usuarios (con política de
   password/credenciales) — es la pieza que cierra "administrar el personal de la sucursal 100% desde
   el cloud". Ver §10 y `SyncEngine._pull_asignaciones`.
4. **Deuda menor:** mover identidad de negocio (`nombre_negocio`/`rnc`/`logo`) de
   `ConfiguracionNegocio` (por sucursal) a `Negocio` (tenant).

### Mantenimiento — cómo agregar un gate nuevo
1. Agregar el código al catálogo (`apps/permisos/catalogo.py`) y `manage.py sync_permisos`.
2. API: `@permission_classes([IsAuthenticated, requiere_permiso('x.y')])` o `MaestroPermisoMixin`.
3. POS local: vista → `if not request.user.tiene_permiso('x.y'): redirect/403`; plantilla →
   `{% if request.user|puede:'x.y' %}` (con `{% load permisos %}`).
4. Asignar el permiso al rol correspondiente (portal `/roles`, o seed si es default).
> **Git:** nunca `git add -A` en este repo — el working tree suele tener WIP de Terraform
> (`infra/azure/**`) que no debe entrar en commits de app. Stagear archivos explícitos.

---

## 12. Verificación

```bash
# Suite completa (especificar módulos; el discovery por app-label falla con el runner)
python manage.py test <módulos> --settings=config.settings_development
```
- **Candidato A03:** 109 pruebas focales (incluidas 3 de concurrencia);
  suite Django completa 1244 OK; e-CF separada 72 passed. Ver el handoff A03
  para comandos, entorno y tiempos exactos.
- **Aceptación (el caso del usuario):** mismo rol "Cajero" con permisos distintos por negocio →
  en `/api/v1/maestros/clientes/`, el cajero con `clientes.crear` recibe **201** y el otro **403**
  (`apps/api/tests/test_clientes_permisos_negocio.py`).
- **Cutover local:** `apps/permisos/tests/test_cutover_local.py` (cajera bloqueada en cada gate).
  Verificado además en la **app corriendo**: cajera `cajero_test` → 302 en `/pos/anulaciones/`,
  `/auditoria/`, `/caja/historial/`, `/inventario/ajustes/`; admin `Santiago` → 200. Receta
  reutilizable en el skill local `.claude/skills/run-pos-local/`.
- **Endpoints admin RBAC + asignaciones:** `apps/api/tests/test_rbac_admin.py` — gating,
  scoping por negocio, conflicto de revisión, movimiento versionado y auditoría CT-01.
- **Sync de roles y asignaciones:** `apps/api/tests/test_sync_roles.py` (endpoints scoped por
  negocio) + `apps/sync/tests/test_pull_roles.py` (V2, tombstones, revisión,
  snapshots completos/parciales, identidad técnica y compatibilidad legacy).
- **Portal React:** `npm run test` + `npm run build` en `pos-cloud-dashboard` (incl. `/asignaciones`).

Despliegue local: backup/preflight del runbook → `migrate` →
`sync_permisos --tenant <tenant_key>` → verificación. Aplicar presets o ejecutar
`bootstrap_negocio` solo cuando el procedimiento lo pida explícitamente. El
sync de roles corre con `manage.py sincronizar`.
