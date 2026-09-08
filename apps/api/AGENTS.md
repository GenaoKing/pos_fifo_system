# apps/api — mapa para agentes

<!-- Última revisión: 2026-09-08 -->

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
| Endpoints de *pull* para la sucursal (roles, asignaciones, métodos de crédito, configuración, resumen) | `views/sync.py` → `*_para_sucursal`; `sync_status`, `heartbeat` |
| Login/refresh/impersonación del portal (JWT tenant-aware) | `auth_views.py` → `PortalTokenObtainPairView`, `TenantTokenRefreshView`, `impersonar_tenant`, `logout`, `perfil_actual` |
| Autenticar a una sucursal (token DRF) | `authentication.py` → `SucursalTokenAuthentication` |
| Permisos DRF | `permissions.py` → `EsAdminOSysadmin`, `EsSoloLectura`, `EsSucursalAutenticada`, `TienePermiso`/`requiere_permiso`, `PuedeLeerMaestro`, `RequiereModulo`/`requiere_modulo` |
| Reportes consolidados (JSON, sin PDF) | `views/reportes.py` + `services/reporting.py` → `build_*` |
| Cartera (read-only portal) | `views/cuentas_por_cobrar.py` → `CuentaPorCobrarViewSet` |
| Admin RBAC / suscripciones / notificaciones | `views/permisos.py`, `views/suscripciones.py`, `views/notificaciones.py` |
| Health | `views/health.py` → `health_check`, `health_live` |
| Tokens | `manage.py crear_tokens_api`, `manage.py vincular_sucursal_token` |

## Invariantes / trampas

- `views/sync.py` **valida al importarse** que todo tipo de evento tenga handler:
  si falta uno, Django no arranca. Idempotencia por hash: un reenvío responde
  `DUPLICADO` sin reprocesar.
- El alcance de tenant se resuelve **siempre** con
  `apps.negocios.utils.resolver_negocio(request)` (NEG-001); no leer
  `request.user.negocio` suelto.
- `?desde=` es cursor keyset — ver `apps/sync/AGENTS.md`. En el cliente,
  `encodeURIComponent()`.
- Throttling de login: `throttling.py`; paginación: `pagination.py`.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_API.md`) —
  **snapshot histórico**, verificar contra código.
