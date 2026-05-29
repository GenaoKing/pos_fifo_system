# Roadmap Portal Cloud (Fase 5)

Documento vivo. Estado al **25 mayo 2026**.
Branch backend: `features/cloud-dashboard`
Repo frontend: `pos-cloud-dashboard` (sibling de `pos_fifo_system`).

---

## Estado actual

| Capa | Sub-fase | Estado |
|------|----------|--------|
| Backend | 5.A | Done (B1-B5) |
| Frontend | 5.A | Base operativa: login/layout/dashboard |
| Backend + Frontend | 5.C | Done: CRUD productos + smoke E2E manual OK |
| Próximo foco | 5.D | CRUD categorías + clientes |
| Resto | 5.B, 5.E-5.G | Pendiente |

---

## Mapa de sub-fases

```
5.A  Dashboard MVP (KPIs + estado sucursales)
5.B  Comparativo entre sucursales con gráficos          <- DIFERIDO: clientes actuales single-sucursal
5.C  CRUD de productos                                  <- DONE
5.D  CRUD de categorías y clientes                      <- ACTUAL
5.E  Reportes consolidados on-demand
5.F  Deploy a producción (backend + frontend)
5.G  Hardening + polish (cross-cutting)
```

---

## Sub-fase 5.A — Dashboard MVP

### Backend ✅

- [x] **B1** — JWT + CORS en `settings_azure_pg.py`
- [x] **B2** — Auth endpoints (`login/`, `refresh/`, `verify/`, `me/`)
- [x] **B3** — `ventas-hoy/` multi-sucursal real (sin placeholders)
- [x] **B4** — `sucursales/status/` con semáforo y alertas
- [x] **B5** — E2E + CORS validados

### Frontend

- [ ] **F1** — Bootstrap Vite + React 18 + TS + Tailwind v3
- [ ] **F2** — Auth foundation: `api.ts` (axios + interceptor refresh), `AuthContext`, `Login`, `ProtectedRoute`
- [ ] **F3** — Layout shell: sidebar colapsable + header con user/logout, ruta `/dashboard` placeholder
- [ ] **F4** — Dashboard real:
  - Card de KPIs por sucursal (ventas hoy, anulaciones, desglose de pagos)
  - Sección estado de sucursales con semáforo
  - Refetch automático cada 30s (TanStack Query `refetchInterval`)
  - Estado vacío + estado loading + estado error

### DoD 5.A

- Login `Santiago/Prueba123` → redirige a `/dashboard`
- Dashboard muestra datos reales de SD-001 actualizándose solo
- Si el access token expira a los 30 min, refresh automático sin interrumpir UX
- Logout local (borrar tokens en memoria) + redirect a `/login`

---

## Sub-fase 5.B — Comparativo entre sucursales

### Backend

- [ ] **B6** — Refactor real de `comparativo_sucursales/`:
  - Query params: `desde`, `hasta`, `agrupacion` (`dia` / `semana` / `mes`)
  - Output: serie temporal por sucursal con métricas (ventas $, # transacciones, ticket promedio)
  - Reutilizar `_estado_sync` helper

### Frontend

- [ ] **F5** — Página `/comparativo`:
  - Date range picker con presets (hoy / ayer / 7d / 30d / mes actual)
  - Selector de métrica
  - Gráfico de líneas con Recharts (una línea por sucursal)
  - Gráfico de barras: total por sucursal en el período
  - Tabla agregada con totales y diferencias %

### DoD 5.B

- Owner compara dos o más sucursales en cualquier ventana temporal
- Las cifras cuadran contra el dashboard local del POS de cada sucursal

---

## Sub-fase 5.C — CRUD de productos

### Backend

- [x] **B7** — Validar `ProductoViewSet` (DRF router) permisos para JWT/admin
  - Lecturas (`list`, `retrieve`) requieren usuario autenticado y método seguro: sirven a sucursal con token DRF y a portal admin autenticado.
  - Escrituras (`create`, `update`, `partial_update`, `destroy`) requieren rol `ADMIN` o `SYSADMIN`.
  - Se corrigió `ProductoViewSet.get_permissions()` para no permitir `GET` anónimo por accidente al sobrescribir los permisos globales.
  - Cobertura agregada: `apps/api/test_producto_viewset.py::ProductoViewSetPermissionTests`.
- [x] **B8** — Verificar que UPDATE/CREATE en cloud se propaga a sucursales vía `pull_maestros`
  - El portal escribe `Producto` en cloud vía `ProductoViewSet`; `fecha_modificacion` se actualiza por `auto_now=True`.
  - La sucursal consume `/api/v1/maestros/productos/?desde=<cursor>` y aplica `update_or_create(sku=...)`.
  - No depende de un `EventoSync PRODUCTO_ACTUALIZADO`; la propagación cloud→sucursal ocurre por lectura incremental de maestros.
  - Campos sincronizados por el pull: `nombre`, `descripcion`, `precio_venta`, `codigo_barras`, `activo`, `categoria`, `estado`, `marca`, `stock_minimo`, `atributos`.
  - Las imágenes siguen fuera de B8: el endpoint expone `imagen_url`, pero la sucursal no descarga/copía archivos de `media`.
  - El `DELETE` físico del cloud queda fuera de esta garantía: si se elimina el registro ya no aparece en el pull incremental. Para desactivar productos desde portal, usar `PATCH {"activo": false}`; si se necesita borrar en sucursales, agregar tombstones/soft delete en una fase posterior.
  - Cobertura agregada: `apps/sync/test_engine.py::SyncEnginePullProductosTests::test_pull_productos_crea_y_actualiza_campos_editables_del_portal`.
- [x] **B9** — Auditar que el cache de `ConfiguracionNegocio` no interfiere con cambios de precio
  - `ConfiguracionNegocio` se cachea vía `get_config()`, pero ese cache solo afecta flags/métodos de pago/parámetros operativos, no el query de `Producto`.
  - El POS lee `Producto.precio_venta` directamente desde DB en `/pos/api/buscar/` y `/pos/api/producto/<codigo_barras>/`.
  - Después de `pull_maestros`, una búsqueda nueva o scanner nuevo ve el precio actualizado sin reiniciar el POS.
  - Límite UX: un carrito ya armado en el navegador conserva el precio que recibió antes del cambio; para aplicar precio nuevo hay que volver a buscar/scanear el producto o refrescar el carrito.
  - Cobertura agregada: `apps/ventas/test_producto_precio_cache.py::PrecioProductoCacheTests::test_pos_lee_precio_actualizado_despues_del_pull_con_config_cacheada`.

### Frontend

- [x] **F6** — Página `/productos`:
  - Tabla paginada (server-side pagination) con búsqueda y filtros (categoría, activo/inactivo)
  - Modal de creación con validaciones del backend y errores visibles
  - Modal de edición para `nombre`, `descripcion`, `precio_venta`, `codigo_barras`, `categoria`, `activo`, `estado`, `marca`, `stock_minimo`, `atributos`
  - Toggle activar/desactivar inline
  - Eliminación física disponible con confirmación, pero para sync seguro se prefiere desactivar (`activo=false`) hasta implementar tombstones/soft delete
  - Integración con TanStack Query: invalidación de lista tras create/update/delete
  - Confirmación antes de activar/desactivar y eliminar
  - UI responsive: tabla desktop y filas compactas en móvil
  - Archivos frontend: `src/pages/Products.tsx`, `src/lib/products.ts`, `src/hooks/useProducts.ts`; ruta en `src/App.tsx`; nav habilitado en `src/components/layout/Sidebar.tsx`
  - Verificación frontend: `npm run lint` y `npm run build` OK en `C:\Proyectos\pos-cloud-dashboard`
  - Fuera de este corte: carga/edición de imágenes y costo de compra; imágenes quedan fuera del sync JSON actual y el costo pertenece a inventario/lotes.

### DoD 5.C

- [x] Owner crea/edita/desactiva productos desde el portal
- [x] Backend propaga cambios de producto a sucursal vía `pull_maestros`
- [x] POS local refleja cambios de precio sin requerir restart
- [x] Smoke E2E manual final: portal real -> sync SD-001 -> búsqueda/scanner POS local (<60s)
  - Validado manualmente por Santiago: la pantalla `/productos` del portal edita producto, el pull de sucursal recibe el cambio y el POS local refleja el dato actualizado.

---

## Sub-fase 5.D — CRUD de categorías + clientes

> Prioridad actual: replicar el patrón ya probado en `/productos`. Hoy los clientes operan con una sola sucursal, así que completar catálogo maestro aporta más valor inmediato que construir comparativos multi-sucursal.

### Backend

- [x] **B10** — Convertir/validar `CategoriaViewSet` y `ClienteViewSet` para CRUD admin
  - Lecturas: sucursal con token DRF y portal admin autenticado.
  - Escrituras: solo `ADMIN`/`SYSADMIN`.
  - Serializers de escritura con validaciones mínimas y campos inmutables cuando aplique.
  - Tests equivalentes a `ProductoViewSetPermissionTests`.
  - Contrato disponible para frontend:
    - `GET /api/v1/maestros/categorias/?search=&activa=` para sucursal + admin.
    - `POST /api/v1/maestros/categorias/`, `PATCH /api/v1/maestros/categorias/<id>/`, `DELETE /api/v1/maestros/categorias/<id>/` para admin/sysadmin.
    - `GET /api/v1/maestros/clientes/?search=&tipo=&activo=` para sucursal + admin.
    - `POST /api/v1/maestros/clientes/`, `PATCH /api/v1/maestros/clientes/<id>/`, `DELETE /api/v1/maestros/clientes/<id>/` para admin/sysadmin.
  - **Nota de contrato (hallazgo frontend):** El proyecto tiene DOS espacios de URL para clientes. `apps/clientes/urls.py` expone `/clientes/<id>/` como vista de detalle del POS local (templates Django). El endpoint de **creación del portal** es `POST /api/v1/maestros/clientes/` (sin ID). El frontend React debe apuntar siempre a `/api/v1/maestros/clientes/` para el alta.
- [ ] **B11** — Propagación cloud → sucursal vía `pull_maestros`
  - Categorías: verificar que `descripcion`, `activa`, `tipo_negocio`, `atributos_configurados` llegan a sucursal.
  - Clientes: verificar que `tipo`, `nombre`, `cedula_rnc`, `telefono`, `email`, `direccion`, `limite_credito`, `condiciones_pago`, `notas`, `activo` llegan a sucursal.
  - Igual que productos, no depender de eventos `CATEGORIA_ACTUALIZADA` / `CLIENTE_ACTUALIZADO`; la propagación debe quedar cubierta por lectura incremental de maestros.
  - Hallazgo backend: timestamps ISO con UTC (`+00:00`) se corrompen si se concatenan manualmente en URLs porque `+` llega como espacio y el filtro `?desde=` puede no aplicar. Tests backend deben usar `urllib.parse.quote()` cuando construyan URLs manuales; frontend debe usar `axios` con `{ params }` o `encodeURIComponent()` si arma la URL a mano.

### Frontend

- [x] **F7** — Páginas `/categorias` y `/clientes`
  - Reutilizar el patrón de `/productos`: lib API, hook React Query, ruta, nav, tabla responsive, filtros, modal create/edit, toggle activo.
  - `/categorias`: implementada en portal con búsqueda por nombre, filtro activa/inactiva, edición de `tipo_negocio` y `atributos_configurados`.
  - `/clientes`: implementada en portal con búsqueda por nombre/RNC/cédula, filtro activo/inactivo y tipo, edición de datos de contacto/crédito.
  - Regla de negocio UI: el cliente genérico `CONTADO` se muestra si viene del API, pero queda bloqueado para edición/desactivación/eliminación porque el backend lo gestiona internamente.
  - Regla frontend para query params: preferir `apiClient.get(url, { params })`; si se arma un query string manual, codificar fechas/cursors con `encodeURIComponent()`.
  - Verificación frontend: `npm run lint` y `npm run build` OK en `C:\Proyectos\pos-cloud-dashboard`.
  - Pendiente: smoke manual create/edit/deactivate contra API real y validación de propagación a sucursal cuando B11 cierre.

### DoD 5.D

- Catálogo completo (productos + categorías + clientes) gestionado desde portal

---

## Sub-fase 5.E — Reportes consolidados

### Backend

- [ ] **B12** — Refactor de `inventario_consolidado/` para multi-sucursal real
- [ ] **B13** — Endpoints nuevos de reportes on-demand:
  - `GET /reportes/ventas-por-cajero/?desde=&hasta=&sucursal=`
  - `GET /reportes/top-productos/?desde=&hasta=&sucursal=&limit=10`
  - `GET /reportes/cierre-consolidado/?fecha=`
  - `GET /reportes/inventario-valorizado/?sucursal=` (con `?format=pdf`)
- [ ] **B14** — Reutilizar `ReporteManager` agregando un agregador multi-sucursal

### Frontend

- [ ] **F8** — Página `/reportes`:
  - Selector de tipo de reporte (dropdown)
  - Formulario dinámico según el tipo seleccionado
  - Preview en pantalla
  - Botón "Descargar PDF" / "Exportar CSV"

### DoD 5.E

- Owner genera consolidados desde el portal en lugar de pedir 4 PDFs por sucursal

---

## Sub-fase 5.F — Deploy a producción

> ⚠️ Hoy la "cloud" es solo la BD Azure PostgreSQL Flexible.
> Aquí se decide e implementa el deploy real del backend Django + frontend.

### Backend cloud

- [ ] **D1** — Decisión: **Azure App Service (Linux)** vs **Azure Container Apps con Docker**
  - App Service: setup más rápido, sin Docker; bueno para empezar
  - Container Apps: alinea con visión Docker futura; mejor pero más complejo
- [ ] **D2** — `Dockerfile` (si va por contenedor) — multi-stage con WhiteNoise para static
- [ ] **D3** — `config/settings_production.py` para Azure (vs `settings_azure_pg.py` que es dev contra Azure DB)
- [ ] **D4** — Variables de entorno producción:
  - `SECRET_KEY` (Azure Key Vault o App Service secrets)
  - `ALLOWED_HOSTS=api.tudominio.com`
  - `DEBUG=False`
  - `CORS_ALLOWED_ORIGINS=https://portal.tudominio.com`
  - `JWT_ACCESS_MINUTES=30`, `JWT_REFRESH_DAYS=7`
- [ ] **D5** — GitHub Actions backend:
  - Job lint (`ruff` o `flake8`)
  - Job tests (`pytest` si hay tests; agregar smoke tests mínimos)
  - Job build + deploy en merge a `main`
- [ ] **D6** — Migración inicial del schema en Azure DB (ya está hecha desde sync)
- [ ] **D7** — Crear usuario SYSADMIN del portal vía management command

### Frontend (Azure Static Web Apps)

- [ ] **D8** — Crear recurso ASWA conectado a `pos-cloud-dashboard`
- [ ] **D9** — GitHub Actions auto-generado por ASWA (build + deploy)
- [ ] **D10** — Env vars production: `VITE_API_URL=https://api.tudominio.com`
- [ ] **D11** — Custom domain `portal.tudominio.com`
- [ ] **D12** — `staticwebapp.config.json` con rewrites para SPA routing (todas las rutas → `/index.html`)

### Pre-deploy checklist

- [ ] `DEBUG=False`
- [ ] `ALLOWED_HOSTS` correcto
- [ ] `CORS_ALLOWED_ORIGINS` correcto
- [ ] `SECRET_KEY` rotado, NO commiteado
- [ ] DB credentials en env vars / Key Vault
- [ ] `STATIC_ROOT` configurado y `collectstatic` ejecutado
- [ ] Logging configurado (file + stdout para captura por Azure)
- [ ] `CSRF_TRUSTED_ORIGINS` si vamos a usar cookies (con JWT puro no es necesario)
- [ ] HTTPS forzado (`SECURE_SSL_REDIRECT=True`)
- [ ] `SECURE_HSTS_SECONDS` configurado

### DoD 5.F

- `portal.tudominio.com` carga desde ASWA
- Login y todas las pantallas funcionan contra `api.tudominio.com` con HTTPS
- `git push origin main` en cualquiera de los dos repos → deploy automático
- Backend escalable horizontalmente (sin estado en disco local)

---

## Sub-fase 5.G — Hardening + polish (cross-cutting)

Estas tareas pueden tocar varios pasos, pero conviene tenerlas listadas.

### UX

- [ ] Loading skeletons en cada página (no spinners genéricos)
- [ ] Empty states ("Sin datos para mostrar" con CTA cuando aplique)
- [ ] Error boundaries en React con fallback útil
- [ ] Toast global para errores de red (TanStack Query `onError`)
- [ ] Confirmación pre-acción destructiva
- [ ] Atajos de teclado básicos (Esc cierra modales, `/` enfoca búsqueda)
- [ ] **Responsive — tablet/móvil** (importante: los dueños usan móvil mucho)

### Auth + Seguridad

- [ ] App `token_blacklist` instalada (instala app + correr migración + cambiar `BLACKLIST_AFTER_ROTATION=True`)
- [ ] Endpoint `POST /api/v1/auth/logout/` que blacklista el refresh
- [ ] Sesión expirada: redirect a `/login?expired=1` con mensaje
- [ ] Cambio de password desde `/perfil`
- [ ] Rate limiting en `/login/` (`django-ratelimit`)

### Observability

- [ ] Sentry (o equivalente) para errores frontend
- [ ] Logs estructurados (JSON) en backend
- [ ] Health check con info útil (DB conn, sync status, version, uptime)
- [ ] Métricas con Azure Application Insights (requests/sec, error rate, p95)

### Performance

- [ ] Índices compuestos en `EventoSync(sucursal_id, estado)` y `(sucursal_id, confirmed_at)`
- [ ] Pagination en endpoints de listas grandes
- [ ] Lighthouse >90 en `/login` y `/dashboard`

### DX / Docs

- [ ] `README.md` en `pos-cloud-dashboard` con setup
- [ ] `HANDOFF_FASE5.md` (este doc evoluciona y se vuelve handoff al cerrar)
- [ ] Vitest + React Testing Library para los componentes críticos: `AuthContext`, `ProtectedRoute`, `Login`, `api.ts` interceptor

---

## Decisiones pendientes (NO bloquean hoy)

1. **Tokens en memoria vs `sessionStorage` para el refresh.** Roadmap original dice "memory only". Implicación: cada reload obliga a re-login. ¿Aceptable o usamos `sessionStorage` (se borra al cerrar la tab) como compromiso?
2. **App Service vs Container Apps.** Decidir antes de D1.
3. **WebSocket vs polling.** TanStack Query con `refetchInterval: 30000` cubre el 90% de los casos. WebSocket sería overkill.
4. **Cuándo introducir `django-tenants`.** No bloquea Fase 5 — los hooks `TENANCY` ya están dispuestos. Detonante natural: segundo cliente pagando.
5. **Mobile-responsive: prioridad.** Si los dueños usan móvil mucho → desde F4. Si solo PC en oficina → diferir a 5.G.
6. **i18n.** Probablemente no en esta fase (todos los clientes son DO, español).
7. **Comparativos multi-sucursal.** Diferir 5.B mientras todos los clientes estén en una sola sucursal; retomar cuando exista un cliente con 2+ sucursales o cuando el backend deje de ser placeholder/local.

---

## Riesgos identificados

1. **Performance del sync engine con N sucursales.** Cuando lleguemos a 10+ sucursales, la cola `EventoSync` puede crecer. Mitigación: índices (5.G) + monitoreo desde el mismo portal (ya en 5.A).
2. **Drift entre POS local y cloud cuando una sucursal está offline mucho.** El semáforo amarillo/rojo lo comunica al owner.
3. **Costos de Azure post free-tier.** Hoy todo está cubierto por cuenta de estudiante. Calcular plan de costos antes de que expire.
4. **Maintenance burden pre-tenancy.** Cada cliente = un deploy separado del cloud collector hasta que entre `django-tenants`. Soportable hasta 3–4 clientes; doloroso desde el 5°.
5. **Falla del refresh interceptor en F2.** Si el flujo de refresh tiene un bug sutil, la UX se vuelve horrible (logout cada 30 min sin razón). Cubrir con tests específicos en 5.G.

---

## Fuera de scope (Fase 6+)

- App móvil nativa (React Native u otra) — está en el roadmap del producto, no en Fase 5
- Gestión de cola e-CF desde el portal (monitoreo de errores DGII, reintentos)
- IA para escaneo de facturas de compra (modelo evaluado, no implementado)
- Implementación real de `django-tenants` con schema-per-cliente
- Catálogo de vehículos / VIN decoder para SK Performance
- Gestión de financiación cooperativa desde portal (hoy solo se usa en sucursal)

---

## Próximos 3 hitos demoables

Si tuviéramos que cortar el trabajo en 3 hitos visibles para el owner de Royal Plast:

1. **Hito 1 — "Veo cómo va mi negocio hoy"** (cerrar 5.A frontend): F2 + F3 + F4
2. **Hito 2 — "Comparo mi semana vs la anterior"** (cerrar 5.B): F5
3. **Hito 3 — "Subo precios sin venir a la sucursal"** (5.C): F6 implementado y smoke E2E manual validado

Con productos validado de punta a punta, el siguiente hito visible es completar catálogo maestro desde el portal: categorías + clientes (5.D). 5.B queda diferido mientras el producto siga siendo single-sucursal en clientes reales.

---

## Resumen ejecutivo de esfuerzo

Estimación gruesa (developer-días asumiendo trabajo focused):

| Sub-fase | Backend | Frontend | Total |
|----------|---------|----------|-------|
| 5.A      | ✅       | 3–4 d    | 3–4 d (frontend) |
| 5.B      | 1 d     | 2 d      | 3 d   |
| 5.C      | 1–2 d   | 3 d      | 4–5 d |
| 5.D      | 1 d     | 2 d      | 3 d   |
| 5.E      | 2–3 d   | 3 d      | 5–6 d |
| 5.F      | 2–3 d   | 1 d      | 3–4 d |
| 5.G      | 1–2 d   | 3–4 d    | 4–6 d |
| **Total** |         |          | **25–32 d** |

(Las estimaciones de 5.G son muy variables según cuánto polish quieras antes del demo.)

---

*Actualizar este doc al cerrar cada paso. Cuando se cierre 5.F, renombrar a `HANDOFF_FASE5.md`.*
