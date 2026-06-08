# RBAC — Sistema de permisos data-driven y multitenant

Referencia de arquitectura del sistema de permisos. Explica **qué se hizo**, **cómo
funciona**, **cómo extenderlo** y **qué decisiones quedan abiertas** para evolucionarlo.

- Plan de decisión original: `C:\Users\Santiago\.claude\plans\abstract-skipping-parnas.md`
- Cutover del POS local (pendiente): [RBAC_LOCAL_CUTOVER_PENDIENTE.md](RBAC_LOCAL_CUTOVER_PENDIENTE.md)

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
| `AsignacionRol` | `apps/permisos/models.py` | Une `usuario`→`rol`, opcional `sucursal` (null = todas las del negocio). |

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

### 6.2 POS local Django (`apps/permisos/`)
- **Template tag** `templatetags/permisos.py`: `{% load permisos %}` →
  `{% if request.user|puede:'compras.registrar' %}`.
- **Decorador** `decorators.py`: `@requiere_permiso_local('codigo')` (redirige si falta).

> El **cutover** de los gates hardcoded del POS local a estas herramientas está **pendiente**
> y requiere verificación manual (es producción). Ver
> [RBAC_LOCAL_CUTOVER_PENDIENTE.md](RBAC_LOCAL_CUTOVER_PENDIENTE.md).

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
  del negocio edita los permisos de cada rol por módulo. Consume los endpoints RBAC.

### Endpoints de administración RBAC (`apps/api/views/permisos.py`)
Gated por `permisos.administrar`, scoped por **`negocio_actual(request)`** (`apps/negocios/
utils.py`). Aislamiento cross-tenant: un admin del negocio A no ve/edita lo de B.
```
GET/POST/PATCH/DELETE  /api/v1/permisos/roles/
GET                    /api/v1/permisos/catalogo/
GET/POST/PATCH/DELETE  /api/v1/permisos/asignaciones/
```

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

Repo backend `GenaoKing/pos_fifo_system` (stacked sobre `features/cloud-dashboard`):

| Rama | Contenido |
|---|---|
| `features/rbac-permisos` | **Keystone**: apps `negocios`/`permisos`, motor, FKs, DRF, maestros, payload, seed/migración. |
| `features/rbac-pr1-api-adopcion` | reportes/CxC/sucursales → permisos granulares. |
| `features/rbac-pr2-admin-endpoints` | endpoints admin RBAC + `negocio_actual`. |
| `features/rbac-pr4-local-infra` | template tag `puede` + decorator `requiere_permiso_local`. |
| `features/rbac-audit-docs` | fixes de auditoría + esta documentación. |

Repo React `GenaoKing/pos-cloud-dashboard`: `feat/rbac-permisos-ui` (base `main`).

Orden de merge: keystone → PR1/PR2/PR4/audit → PR3.

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

---

## 11. Limitaciones conocidas / cómo seguir

1. **Cutover del POS local** (gates hardcoded → motor) — pendiente, con verificación manual.
   Resolver primero la decisión "¿el cajero puede anular?". Ver cutover doc.
2. **Sync de roles cloud→sucursal** — pendiente. Extender el pull incremental
   (`SyncIncrementalMixin`) para traer `Permiso`/`Rol`/`AsignacionRol` por negocio.
3. **UI de asignación usuario→rol** en el portal — el backend (`AsignacionRolViewSet`) ya
   existe; falta la pantalla (la de `/roles` hoy edita rol→permiso).
4. **Aislamiento de datos por tenant** en maestros — no implementado (lo resolverá
   django-tenants, o un scoping por `negocio` + `negocio_actual`).
5. **Rol Administrador = snapshot:** se fija al crear; si el catálogo crece, no se auto-actualiza.
   Los admins reales no se ven afectados (acceso total por `es_acceso_total`); solo importaría
   si se asigna ese rol a un no-admin. Opcional: que `sync_permisos` re-otorgue todos al rol
   Administrador de sistema.
6. **Unicidad de `AsignacionRol` con `sucursal` NULL:** Postgres trata NULLs como distintos, así
   que el `unique_together` no bloquea duplicados globales a nivel BD. Mitigado a nivel app
   (`get_or_create` en el seed; `UniqueTogetherValidator` de DRF). Endurecible con
   `UniqueConstraint(nulls_distinct=False)` (PG ≥15).

---

## 12. Verificación

```bash
# Motor, seed, aceptación y RBAC admin
python manage.py test apps.permisos apps.negocios apps.api.tests --settings=config.settings_development
```
Criterio de aceptación (el caso del usuario): mismo rol "Cajero" con permisos distintos por
negocio → en `/api/v1/maestros/clientes/`, el cajero con `clientes.crear` recibe **201** y el
otro **403** (ver `apps/api/tests/test_clientes_permisos_negocio.py`).

Portal React: `npm run test` + `npm run build` en `pos-cloud-dashboard`.
