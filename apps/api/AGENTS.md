# apps/api — mapa para agentes

<!-- Última revisión: 2026-09-18 (C04 p5.2: administración tenant-scoped) -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

La API REST (DRF) del **cloud**, bajo `/api/v1/`: la consume el portal React
(repo aparte) y es el **receptor del sync** de las sucursales. Vive en la misma
base de código que el POS, pero solo tiene sentido en la instancia cloud. No
tiene modelos propios (`models.py` vacío).

## Entrypoints

| Necesito… | Voy a… |
| --- | --- |
| Ver todas las rutas | `apps/api/urls.py` (`router_v1` + `auth_urls.py`, `sucursales_urls.py`, `views/sync_urls.py`, `views/reportes_urls.py`) |
| Datos maestros (productos/categorías/clientes) | `views/maestros.py` → `ProductoViewSet` (**patrón canónico**), `CategoriaViewSet`, `ClienteViewSet`; mixins `SyncIncrementalMixin` (`?desde=`), `ReadAfterWriteMixin`, `MaestroPermisoMixin` |
| **Recibir eventos de una sucursal** | `views/sync.py` → `recibir_eventos` + `_handler_<tipo>` por tipo de evento |
| **Recibir propuestas offline de maestros** | `views/sync.py` → `recibir_mutaciones_maestro`; dominio CAS/RBAC en `apps/sync/master_mutations.py` |
| **Listar/resolver conflictos de maestros CT-04** | `views/maestros_conflictos.py` → `conflictos_maestros` / `resolver_conflicto_maestro`; CAS/auditoría en `apps/sync/master_conflicts.py` |
| Reconciliar ACK históricos BUG-K (read-only) | `views/sync.py` → `reconciliar_eventos` (`sync.reconciliation.v1`) |
| Endpoints de *pull* para la sucursal (roles, asignaciones, métodos de crédito, configuración, resumen) | `views/sync.py` → `*_para_sucursal`; `sync_status`, `heartbeat` |
| Login/refresh/impersonación del portal (JWT tenant-aware) | `auth_views.py` → `PortalTokenObtainPairView`, `TenantTokenRefreshView`, `impersonar_tenant`, `logout`, `perfil_actual` |
| Autenticar a una sucursal (token DRF) | `authentication.py` → `SucursalTokenAuthentication` |
| Permisos DRF | `permissions.py` → `EsAdminOSysadmin`, `EsSoloLectura`, `EsSucursalAutenticada`, `TienePermiso`/`requiere_permiso`, `PuedeLeerMaestro`, `RequiereModulo`/`requiere_modulo` |
| Reportes consolidados (JSON, sin PDF) | `views/reportes.py` + `services/reporting.py` → `build_*` |
| Cartera (read-only portal) | `views/cuentas_por_cobrar.py` → `CuentaPorCobrarViewSet` |
| Admin RBAC / suscripciones / notificaciones | `views/permisos.py`, `views/suscripciones.py`, `views/notificaciones.py` |
| Usuarios, sucursales y configuración para C04 p5.2 | `views/administracion.py` + `serializers/administracion.py`; rutas `/api/v1/administracion/` |
| Devolver resoluciones CT-04 al POS | `views/sync.py` → `resoluciones_mutaciones_maestro` (`master.conflict-resolution-sync.v1`, cursor keyset y token de la sucursal origen) |
| Health | `views/health.py` → `health_check`, `health_live` |
| Tokens | `manage.py crear_tokens_api`, `manage.py vincular_sucursal_token` |

## Invariantes / trampas

- `views/sync.py` **valida al importarse** que todo tipo de evento tenga handler:
  si falta uno, Django no arranca. Idempotencia por `event_id`/hash dentro del
  scope autenticado: solo contenido equivalente responde `DUPLICADO`; conflicto
  o `IntegrityError` real responde `ERROR`.
- El alcance de tenant se resuelve **siempre** con
  `apps.negocios.utils.resolver_negocio(request)` (NEG-001); no leer
  `request.user.negocio` suelto.
- `?desde=` es cursor keyset — ver `apps/sync/AGENTS.md`. En el cliente,
  `encodeURIComponent()`.
- El pull cloud de configuración es lectura pura: una sucursal sin
  `ConfiguracionNegocio` recibe `[]`; el cloud no crea una fila al servirla.
- El pull de configuración conserva los campos legacy `modulo_*`, pero para
  una sucursal con negocio los deriva de `apps.suscripciones.engine`. Su
  `fecha_modificacion` es la revisión efectiva: incluye CT-01 de los cambios
  oficiales de plan/override para que el cursor incremental reemita la fila.
- Throttling de login: `throttling.py`; paginación: `pagination.py`.
- Todo token portal lleva `session_started_at`/`session_expires_at`; access y
  refresh rechazan la sesión al superar el máximo absoluto de 12 horas.
- CT-02: login/perfil conservan `permisos`/`modulos` y agregan
  `rbac.capabilities.v1`. Los pulls de roles/asignaciones sirven legacy por
  defecto y `rbac.sync.v2` solo con `X-RBAC-Schema: rbac.sync.v2`.
- Las mutaciones RBAC llaman `apps.permisos.services`; `X-RBAC-Revision` es el
  precondition opt-in y un valor obsoleto responde `409 rbac_revision_conflict`.
- `administracion/usuarios` y `administracion/sucursales` requieren
  `permisos.administrar` **global**; configuración requiere
  `configuracion.administrar` global. No convertir una asignación acotada a una
  sucursal en facultad de alterar identidad, topología o flags transversales.
- `DELETE /administracion/usuarios/{id}/` y `DELETE
  /administracion/sucursales/{id}/` son bajas lógicas, nunca borrado físico.
  En cloud la baja de usuario también revoca `Membership`; username/email y
  código de sucursal son identidades inmutables en estas rutas.
- Los ViewSets maestros solo aceptan escritura en la instancia cloud. En un POS
  local responden `403`, para no saltar maestro + auditoría CT-01 +
  `MutacionMaestro`. El flag de compatibilidad de sus tests exige además una
  base `test_*`; no habilita ninguna instalación productiva local.
- `POST /api/v1/sync/mutaciones-maestro/` no es `EventoSync`: el token prueba
  la sucursal y el receptor vuelve a resolver al actor, CT-02 y la revisión CAS
  en cloud. Un UUID exacto responde el resultado durable sin duplicar; un UUID
  con contenido distinto, una revisión vieja o una colisión no se tratan como
  ACK exitoso. Su respuesta 200 declara `master.mutation.v1`; CT-04 publica el
  fixture de listado/acciones.
- A06 publica `GET /api/v1/maestros/conflictos/` y `POST
  /api/v1/maestros/conflictos/<uuid>/resolver/`. El primero usa cursor opaco,
  `resolver_negocio(request)` y el permiso `*.ver` de la entidad **en la
  sucursal de origen**; el segundo exige `*.editar`, schema
  `master.conflict-resolution.v1`, motivo y la revisión cloud observada. Una
  revisión nueva responde conflicto de nuevo: nunca last-write-wins.
- `GET /api/v1/sync/mutaciones-maestro/resoluciones/` no es el listado portal:
  usa solamente `EsSucursalAutenticada`, limita las filas a
  `mutacion.sucursal`, pagina por `(resuelto_at, id)` y siempre declara
  `master.conflict-resolution-sync.v1`. No devolver decisiones de otra
  sucursal ni aceptar un token humano como sustituto.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_API.md`) —
  **snapshot histórico**, verificar contra código.
