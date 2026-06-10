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
| `Negocio` | `apps/negocios/models.py` | Tenant. Agrupa N sucursales. `slug` único (futuro `schema_name` de django-tenants). |
| `Permiso` | `apps/permisos/models.py` | Catálogo **global** de acciones (`codigo`, ej. `clientes.crear`). Lo que *se puede* controlar. |
| `Rol` | `apps/permisos/models.py` | Rol **por negocio** (`unique_together (negocio, slug)`). `es_sistema` protege los default. M2M `permisos`. |
| `AsignacionRol` | `apps/permisos/models.py` | Une `usuario`→`rol`, opcional `sucursal` (null = todas las del negocio). `activo` permite **soft-delete** y `fecha_modificacion` (`auto_now`) es el cursor del sync de asignaciones (§7.1, §10). |

`Sucursal.negocio` y `Usuario.negocio` son FKs añadidas (migraciones `sucursales/0003`,
`usuarios/0003`). `Permiso` es global (no tiene FK a negocio): lo que varía por tenant es
**qué rol tiene cuál permiso**, no el catálogo.

---

## 3. El motor (`apps/permisos/engine.py`)

API pública:

- `permisos_de_usuario(usuario, sucursal=None) -> set[str]` — set de códigos efectivos.
  Para acceso total devuelve **todos** los códigos del catálogo (para el payload/`can()`).
- `tiene_permiso(usuario, codigo, sucursal=None) -> bool` — chequeo. **Corto-circuita** el
  acceso total (no depende del catálogo, así un admin nunca queda bloqueado).
- `invalidar_cache()` — la llaman las signals.

`Usuario.tiene_permiso(codigo, sucursal=None)` delega aquí.

**Acceso total (`es_acceso_total`):** superusuario de Django, o `rol` legacy `ADMIN`/`SYSADMIN`.
Es **transitorio** para ADMIN (ver §10).

**Resolución (no acceso total):** unión de permisos de las `AsignacionRol` activas (rol
activo). Si se pasa `sucursal`, aplican las globales (sucursal NULL) + las de esa sucursal.

**Caché:** por `(usuario, sucursal)` con una **versión global**. Cualquier cambio en
`Rol`/`Rol.permisos`/`AsignacionRol`/`Permiso` bumpea la versión vía signals
(`apps/permisos/signals.py`), invalidando todo de forma portable. **Asume cache compartido
de un solo worker** (LocMemCache en dev y Azure single-worker). Si se escala a múltiples
workers/réplicas, usar Redis/memcached compartido (ver comentario en `engine.py`).

---

## 4. Catálogo de permisos (`apps/permisos/catalogo.py`)

Lista declarativa `CATALOGO` (fuente de verdad del dev). Códigos actuales por módulo:

```
clientes.{ver,crear,editar,eliminar}      productos.{ver,crear,editar,eliminar}
categorias.{ver,crear,editar,eliminar}    compras.{ver,registrar}
inventario.{ver,ajustar}                  ventas.{crear,anular,aplicar_descuento,reimprimir}
cuentas_por_cobrar.ver                    reportes.{ver,consolidado.ver}
sucursales.ver                            permisos.administrar   (meta: administrar RBAC)
```

**Agregar un permiso:** añadir una línea a `CATALOGO` y correr `manage.py sync_permisos`
(idempotente). Luego aplicarlo en la vista/endpoint correspondiente (§6).

---

## 5. Seed y bootstrap

- **`manage.py sync_permisos`** — upsert del catálogo en la tabla `Permiso`.
- **`manage.py bootstrap_negocio [--nombre "Royal Plast"]`** — para una instalación
  existente: crea un Negocio (toma el nombre de `ConfiguracionNegocio` si no se pasa),
  enlaza sucursales/usuarios huérfanos, crea los roles de sistema y asigna rol según el
  `rol` legacy de cada usuario.
- **Data migration `permisos/0002_seed_rbac`** — siempre siembra el catálogo; si ya hay
  usuarios/sucursales, hace el bootstrap. En BD fresca/de tests solo siembra el catálogo.

**Roles de sistema por defecto** (`apps/permisos/seed.py:crear_roles_default`):
- **Administrador**: todos los permisos (plantilla inicial).
- **Cajero**: `ventas.crear`, `ventas.aplicar_descuento`, `ventas.reimprimir`.

Los permisos se fijan **solo al crear** el rol → re-ejecutar bootstrap **no pisa**
personalizaciones del admin.

### Rol Cajero por defecto — por qué NO incluye `ventas.anular`
El viejo `permisos_cajera` (código muerto) listaba `puede_anular_venta`, pero la regla
**real** gatea anulaciones a ADMIN/SYSADMIN (`apps/ventas/services/anulaciones_service.py:
_puede_anular`). El default se alineó con la conducta real. La finalización del set del
Cajero (p. ej. si se quiere `reportes.ver` para su dashboard) se decide en el cutover local.

> Para multi-tenant en el cloud: `bootstrap_negocio` crea **un** negocio y enlaza a todos
> los usuarios. Para varios negocios en una misma BD, crear los `Negocio` explícitamente y
> asignar usuarios/sucursales en vez de confiar en el auto-bootstrap.

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

`apps/api/auth_views.py`: `/auth/login/` y `/auth/me/` devuelven `permisos: string[]` y
`negocio {id,slug,nombre}`; el JWT lleva `tenant_id = negocio.slug`.

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
  total" (sus asignaciones son inertes por `es_acceso_total`). Filtra `activo` en cliente para
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

**Patrón soft-delete ↔ reactivate (load-bearing — mantener juntos):**

- `AsignacionRolViewSet.perform_destroy` hace **soft-delete** (`activo=False`, bump de
  `fecha_modificacion`), no borra la fila — así la baja se propaga por el sync incremental.
- `AsignacionRolViewSet.create` es **reactivate-or-create**: si ya existe la terna
  (`usuario`, `rol`, `sucursal`) la reactiva (200) en vez de fallar; si no, crea (201). El
  `AsignacionRolSerializer` tiene el `UniqueTogetherValidator` automático **desactivado**
  (`validators = []`) para permitirlo — sin esto, re-asignar un rol previamente quitado daría
  400 por el `unique_together` (la fila inactiva persiste). Si alguien revierte el soft-delete
  a un borrado físico, o reactiva el validador, este ciclo se rompe.

---

## 8. Multitenancy y futuro `django-tenants`

Hoy es **row-level** (FK `negocio`). Es el **puente** hacia schema-per-tenant:
- **`negocio_actual(request)` es el único punto de resolución de tenant.** Úsalo siempre.
  Con django-tenants pasa a significar "el schema actual" (lo fija un middleware) y los
  filtros `negocio=...` desaparecen en un solo lugar.
- `Permiso` → futuro `SHARED_APPS`; `Rol`/`AsignacionRol` → `TENANT_APPS`. La FK `Rol.negocio`
  es lo único que se vuelve redundante (trivial de quitar).
- `Negocio.slug` es el candidato natural a modelo tenant (slug → `schema_name`).

**Importante:** el RBAC controla **qué acciones** puede hacer un usuario, **no** el
**aislamiento de datos** entre tenants. Hoy los maestros (productos/clientes) **no** están
scoped por negocio; el cloud es de-facto single-tenant hasta django-tenants. La excepción
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
- **ADMIN/SYSADMIN con acceso total (`es_acceso_total`) — transitorio:** preserva la conducta
  histórica y evita lockouts. El control granular aplica a roles operativos (cajeros, roles
  custom). Cuando todos los admins estén en roles explícitos, quitar `'ADMIN'` de
  `es_acceso_total` para poder restringirlos también. El campo `Usuario.rol` queda como
  legacy/informativo; el enforcement vive en `AsignacionRol`.
- **`Negocio` separado de `ConfiguracionNegocio`:** distinta cardinalidad —
  `ConfiguracionNegocio` es **OneToOne con Sucursal** (config por sucursal); `Negocio` es el
  tenant (1→N sucursales). Deuda menor futura: mover los campos de identidad
  (`nombre_negocio`, `rnc`, `logo`…) de `ConfiguracionNegocio` a `Negocio`.
- **Caché por versión global:** simple y portable; correcto con un worker (config actual).
- **Cutover local (decisiones del cutover):**
  - 3 permisos nuevos: `caja.administrar`, `auditoria.ver`, `configuracion.administrar`.
  - **`sync_permisos` re-otorga todo el catálogo al rol de sistema `administrador`** → ese rol
    no queda desfasado al crecer el catálogo (resuelve la limitación "snapshot").
  - **El sync cloud→local propaga DEFINICIONES de rol Y asignaciones usuario→rol.**
    - Definiciones (`Rol`→permisos): `GET /api/v1/sync/roles/` + `SyncEngine._pull_roles()`.
    - Asignaciones (`AsignacionRol`): `GET /api/v1/sync/asignaciones/` + `SyncEngine._pull_asignaciones()`
      (corre después de `_pull_roles` en `pull_maestros`). Ambos: token de sucursal, scoped al
      negocio, filtro incremental `?desde=<fecha_modificacion>`.
    - **Identidad cross-DB v1 = claves naturales:** `usuario_username` + `rol_slug` +
      `sucursal_codigo` (las PKs no son comparables entre las dos BD independientes). El endpoint
      de asignaciones sirve las globales del negocio (`sucursal` NULL) + las de la sucursal del
      token; el pull resuelve esas claves a filas locales.
    - **El pull NO crea usuarios:** si el `username` no existe localmente, omite la asignación
      (evita provisionar credenciales por sync). Por eso el alta de usuarios sigue siendo local
      (`bootstrap_negocio` por el `rol` legacy); el sync solo sincroniza *qué rol* tiene un usuario
      que **ya existe** en ambos lados.
    - El soft-delete (`activo=False`) es lo que permite propagar **bajas** de asignación por el
      cursor incremental (un borrado físico no dejaría rastro que sincronizar).
  - **`anular` quedó admin-only correctamente:** el rol Cajero default **no** trae `ventas.anular`
    (la auditoría lo quitó del set), alineado con `anulaciones_service._puede_anular`.

---

## 11. Estado / cómo seguir

**Hecho (fase madura):** motor + DRF (maestros/reportes/CxC/sucursales) + endpoints admin +
payload + React (`can()`, `/roles`, **`/asignaciones`**) + **cutover del POS local** + **sync
cloud→local de definiciones de rol Y de asignaciones usuario→rol** (identidad natural v1). El
ciclo de vida completo —configurar rol→permisos, asignar rol→usuario, propagarlo a la sucursal y
enforzarlo server-side en ambos lados— está cerrado de punta a punta.

### Mini-handoff — qué desarrollar a futuro (ordenado por valor/esfuerzo)

Esta sección es el punto de entrada para quien retome el RBAC. Ninguno bloquea producción hoy.

1. **Aislamiento de datos por tenant** en maestros *(el más importante para escalar a multi-cliente
   en una sola BD cloud)* — el RBAC controla *acciones*, **no** *qué datos* ve cada tenant; hoy los
   maestros (productos/clientes) no están scoped por negocio, así que el cloud es de-facto
   single-tenant. Lo resolverá `django-tenants` (schema-per-tenant) o, como puente, un scoping por
   `negocio` apoyado en `negocio_actual`. Ver §8. **Empezar aquí** antes de vender a un 3.º cliente
   en la BD compartida.
2. **`es_acceso_total` incluye `ADMIN` (transitorio)** — migrar los admins reales a roles explícitos
   y luego quitar `'ADMIN'` de `es_acceso_total`, para poder restringir también al admin por negocio.
   Requiere antes asegurar que cada admin tenga un rol con los permisos que hoy obtiene gratis.
3. **Provisión de usuarios cross-DB** — hoy el sync de asignaciones **omite** usuarios que no existen
   localmente (no crea credenciales por sync, a propósito). Si se quiere dar de alta una cajera desde
   el portal y que aparezca en la sucursal, falta un flujo de provisión de usuarios (con política de
   password/credenciales) — es la pieza que cierra "administrar el personal de la sucursal 100% desde
   el cloud". Ver §10 y `SyncEngine._pull_asignaciones`.
4. **Endurecer unicidad de `AsignacionRol` con `sucursal` NULL:** Postgres trata NULLs como distintos
   → el `unique_together` no bloquea duplicados globales a nivel BD. Mitigado a nivel app
   (reactivate-or-create, §7.1). Endurecible con `UniqueConstraint(nulls_distinct=False)` (PG ≥15).
5. **Deuda menor:** mover identidad de negocio (`nombre_negocio`/`rnc`/`logo`) de
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
python manage.py test <módulos> --settings=config.settings_development   # 180/180 OK
```
- **Aceptación (el caso del usuario):** mismo rol "Cajero" con permisos distintos por negocio →
  en `/api/v1/maestros/clientes/`, el cajero con `clientes.crear` recibe **201** y el otro **403**
  (`apps/api/tests/test_clientes_permisos_negocio.py`).
- **Cutover local:** `apps/permisos/tests/test_cutover_local.py` (cajera bloqueada en cada gate).
  Verificado además en la **app corriendo**: cajera `cajero_test` → 302 en `/pos/anulaciones/`,
  `/auditoria/`, `/caja/historial/`, `/inventario/ajustes/`; admin `Santiago` → 200. Receta
  reutilizable en el skill local `.claude/skills/run-pos-local/`.
- **Endpoints admin RBAC + asignaciones:** `apps/api/tests/test_rbac_admin.py` — gating,
  scoping por negocio, selectores `usuarios`/`sucursales`, y el ciclo **soft-delete ↔
  reactivate** (borrar deja `activo=False`; re-asignar reactiva con 200, no duplica ni da 400).
- **Sync de roles y asignaciones:** `apps/api/tests/test_sync_roles.py` (endpoints scoped por
  negocio) + `apps/sync/tests/test_pull_roles.py` (el pull actualiza `Rol.permisos`/asignaciones,
  resuelve claves naturales, omite usuarios inexistentes e invalida cache).
- **Portal React:** `npm run test` + `npm run build` en `pos-cloud-dashboard` (incl. `/asignaciones`).

Despliegue local: `migrate` → `sync_permisos` → `bootstrap_negocio` (→ `bootstrap_suscripciones`
para módulos). El sync de roles corre con `manage.py sincronizar`.
