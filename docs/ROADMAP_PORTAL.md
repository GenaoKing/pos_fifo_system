# Roadmap Portal Cloud (Fase 5)

Documento vivo. Estado al **12 junio 2026**.
Branch backend: `features/cloud-dashboard` / `develop` segun flujo vigente.
Repo frontend: `pos-cloud-dashboard` (sibling de `pos_fifo_system`).

---

## Que falta ahora

1. **Deploy frontend dev/staging**: crear o desbloquear Azure Static Web Apps
   u otra alternativa para publicar `pos-cloud-dashboard`. Hoy el portal local
   ya consume APIs remotas dev/staging correctamente. Runbook:
   `docs/runbooks/FRONTEND_DEPLOY_AZURE_STATIC_WEB_APPS.md`.
2. **Smoke cloud publicado**: login, dashboard, maestros, reportes, inventario
   y CxC ya fueron validados con Vite local contra API staging; falta repetirlo
   cuando el frontend viva en Azure y hacer smoke con sync de sucursal real.
3. **RBAC/modulos cutover**: el portal ya tiene `/roles` y `/suscripciones`,
   pero falta cerrar asignacion usuario->rol y enforcement local consistente.
4. **Hardening produccion**: logout con blacklist, rate limit login, HSTS,
   Sentry/observability y runbook staging/prod.

## Estado actual

| Capa | Sub-fase | Estado |
|------|----------|--------|
| Backend | 5.A | Done (B1-B5) |
| Frontend | 5.A | Done: F1-F4 (login/layout/dashboard real con polling 30s) |
| Backend + Frontend | 5.C | Done: CRUD productos + smoke E2E manual OK |
| Backend + Frontend | 5.D | Done en codigo: CRUD categorias/clientes + pull maestros probado; smoke frontend local -> API staging OK; falta sync real |
| Frontend | 5.G | Parcial: hardening + tests criticos (Vitest/RTL, 43 tests), README y code-splitting hechos; falta `/`-focus y observability |
| Backend + Frontend | 5.H | Done en codigo: CxC read-only `/cuentas` + endpoint backend; smoke frontend local -> API staging OK |
| Backend | 5.B / 5.E | Done para reportes JSON cloud: comparativo real + ventas por cajero + top productos + cierre consolidado |
| Frontend | 5.E | Done (frontend): `/inventario` (F10) + `/reportes` (F8) consumen B13/B14; smoke frontend local -> API staging OK |
| Frontend | 5.B | Done en codigo: `/comparativo` (F5) con recharts, gate `reportes.consolidado.ver`; smoke frontend local -> API staging OK |
| Backend | 5.F | Dev desplegado: Docker + ACR + Container Apps + Key Vault + remote state + CI/CD MVP |
| Frontend | 5.F | Prep frontend listo: `staticwebapp.config.json`, `.env.example`, README, CI (lint+test+build), config seam multi-tenant. Falta recurso/deploy frontend Azure |
| Backend + Frontend | 5.I | Parcial: RBAC/modulos backend + `/roles` + `/suscripciones`; falta asignacion usuario->rol y cutover local |

---

## Mapa de sub-fases

```
5.A  Dashboard MVP (KPIs + estado sucursales)          <- DONE
5.B  Comparativo entre sucursales con graficos          <- DONE + smoke local contra staging OK
5.C  CRUD de productos                                  <- DONE
5.D  CRUD de categorias y clientes                      <- DONE + smoke local contra staging OK; falta sync real
5.E  Reportes consolidados on-demand                    <- BACKEND JSON DONE; /inventario F10 + /reportes F8 hechos
5.F  Deploy a produccion (backend + frontend)           <- BACKEND DEV DONE; frontend Azure pendiente
5.G  Hardening + polish (cross-cutting)                 <- PARCIAL
5.H  Cartera / cuentas por cobrar (portal read-only)    <- DONE + smoke local contra staging OK
5.I  RBAC y modulos del portal                          <- PARCIAL; UI base lista
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

- [x] **F1** — Bootstrap Vite + React + TS + Tailwind v3
  - Nota: el bootstrap real quedó en **React 19** (no 18), Vite 8 y TypeScript 6. Sin impacto funcional.
- [x] **F2** — Auth foundation: `api.ts` (axios + interceptor refresh), `AuthContext`, `Login`, `ProtectedRoute`
  - Interceptor con promise singleton anti-refresh-concurrente y distinción 401 (refresh) vs 403 (permisos).
- [x] **F3** — Layout shell: sidebar colapsable + header con user/logout
- [x] **F4** — Dashboard real:
  - Card de KPIs por sucursal (ventas hoy, anulaciones, desglose de pagos)
  - Sección estado de sucursales con semáforo
  - Refetch automático cada 30s (TanStack Query `refetchInterval`)
  - Estado vacío + estado loading + estado error

### DoD 5.A

- Login con usuario admin de prueba configurado por ambiente -> redirige a `/dashboard`
- Dashboard muestra datos reales de SD-001 actualizándose solo
- Si el access token expira a los 30 min, refresh automático sin interrumpir UX
- Logout local (borrar tokens en memoria) + redirect a `/login`

---

## Sub-fase 5.B — Comparativo entre sucursales

### Backend

- [x] **B6** — Refactor real de `comparativo_sucursales/`:
  - Implementado en `apps/api/services/reporting.py` y expuesto por `GET /api/v1/reportes/comparativo/`.
  - Query params: `desde`, `hasta`, `agrupacion` (`dia` / `semana` / `mes`) y `sucursal`.
  - Output: serie temporal por sucursal con `cantidad_ventas`, `ventas_facturadas`, `credito_facturado`, `cobros_cxc` y `ticket_promedio`.
  - Reutiliza `_estado_sync`; la funcion se mantiene importable desde `apps.api.views.reportes` para no romper `sucursales/status/`.
  - Ventas `sucursal=NULL` no entran al consolidado multi-sucursal y se reportan en `metadata.legacy_ventas_omitidas`.
  - Decision: `apps/reportes` queda como modulo local/POS; el portal cloud usa servicios query-based sobre la BD cloud, no `ReporteManager`.
  - Cobertura: `apps/api/tests/test_reportes_cloud.py`.
  - **Contrato completado para F5**: la serie viene zero-filled (un punto por CADA periodo del rango, en cero si no hubo movimiento — `_period_keys`) y el `totales` global trae las 5 metricas. Limite `MAX_COMPARATIVO_PUNTOS = 400`: si el rango genera mas puntos responde 400 sugiriendo agrupacion mayor. Nota semana: el primer punto es el lunes ISO de `desde`, que puede caer antes del rango.

### Frontend

- [x] **F5** — Página `/comparativo`:
  - Date range con presets (hoy / ayer / 7d / 30d / mes actual; default mes actual) + agrupación día/semana/mes
  - Selector de métrica (una a la vez: ventas facturadas default, cantidad, ticket promedio, crédito, cobros CxC)
  - Gráfico de líneas con **recharts** (una línea por sucursal, paleta fija) + gráfico de barras: total por sucursal
  - Tabla agregada con totales por sucursal, fila Total general y **% de participación sobre el total** de la métrica seleccionada; export CSV
  - Gate por permiso `reportes.consolidado.ver` en Sidebar y ruta (`ProtectedRoute requiere=`), igual que el backend
  - Archivos: `src/pages/Comparativo.tsx`, `src/lib/comparativo.ts` (transforms puros: pivot, % participación, labels de período, presets), hook `useComparativo` en `src/hooks/useReports.ts`
  - recharts cae en el chunk lazy de la página (~110KB gzip); el bundle inicial no cambia
  - OJO timezone: las claves `YYYY-MM` de agrupación mes se parsean a mano en `formatPeriodoLabel` (no pasar por `new Date(string)` — UTC retrocede un mes en RD)
  - Tests: `src/lib/comparativo.test.ts`, `src/pages/Comparativo.test.tsx` (recharts mockeado), `fetchComparativo` en `src/lib/reports.test.ts`

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
- [x] **B11** — Propagación cloud → sucursal vía `pull_maestros`
  - [x] Categorías: `descripcion`, `activa`, `tipo_negocio`, `atributos_configurados` llegan a sucursal.
  - [x] Clientes: `tipo`, `nombre`, `cedula_rnc`, `telefono`, `direccion`, `limite_credito`, `condiciones_pago`, `notas`, `activo` llegan a sucursal.
  - [x] Igual que productos, no depende de eventos `CATEGORIA_ACTUALIZADA` / `CLIENTE_ACTUALIZADO`; la propagación queda cubierta por lectura incremental de maestros.
  - [x] Cobertura: `apps/sync/tests/test_engine.py::SyncEnginePullCategoriasTests` y `SyncEnginePullClientesTests`.
- [ ] **B11b** — Escrituras locales de maestros deben ir al cloud
  - Decisión: el **cloud es la fuente de verdad** de productos, categorías y clientes.
  - Crear/editar/desactivar maestros desde una pantalla local de sucursal **no debe** crear un registro local esperando que viaje por sync. En v1 no hay eventos `CLIENTE_*` ni `CATEGORIA_*`.
  - Si un ADMIN/SYSADMIN usa una pantalla local para editar maestros, esa vista debe:
    1. requerir conexión cloud (`@requiere_conexion_cloud` o equivalente),
    2. escribir directamente en `/api/v1/maestros/...` con credenciales admin/cloud válidas,
    3. refrescar la copia local con la respuesta cloud o disparar un pull inmediato.
  - Si no hay conexión cloud, bloquear la operación administrativa con mensaje claro.
  - Excepción futura no implementada: cliente temporal offline, sin crédito y sin cartera, con reconciliación explícita posterior.
  - **REVISIÓN 2026-08-19 (Fase 1 de `ROADMAP_SYNC_CONFIABLE.md`).** La excepción
    de arriba se implementó, forzada por BUG-C: los cajeros SÍ crean clientes
    localmente (el flujo local nunca se bloqueó), y como el cloud solo sabía
    resolverlos por `cedula_rnc` —campo opcional y casi siempre vacío— las ventas
    replicaban sin cliente y **ninguna cuenta por cobrar podía replicar**
    (RD$240,435 invisibles en Royal Plast).
    Ahora un cliente puede **nacer en la sucursal y promoverse al cloud**: los
    eventos de venta/CxC/cotización llevan la identidad `(sucursal, id_local)` y
    el cloud lo crea si no lo conoce, sellando su origen.
    Lo que **no** cambia: el cloud sigue siendo la autoridad para **editar**
    maestros. El upsert solo crea lo que no existe; nunca pisa una edición hecha
    en el portal (única excepción acotada: rellenar una cédula vacía).
  - **REVISIÓN 2026-08-24.** El mismo patrón se extendió de `Cliente` a
    `Producto`: un `sku` desconocido en el detalle de una venta ya no
    rechaza la venta completa (ver `BUG-H` en `docs/BUGS.md`, recurrencia
    detectada esta sesión del mismo síntoma de fondo). En vez de eso crea un
    stub sellado — `categoria = Categoria.get_sin_clasificar()`,
    `pendiente_revision=True`, `origen_sucursal` — y la venta se aplica
    completa referenciándolo. El stub queda **invisible para el pull de la
    sucursal que lo originó** hasta que alguien lo completa en el portal
    (PATCH con `categoria` real; cualquier otro campo, ej. solo `activo`,
    NO libera el stub).
    De paso: el portal se abrió a la cajera vía permisos granulares
    (`productos.ver` + `productos.fotografiar`, nuevo, ambos en
    `PERMISOS_CAJERO_DEFAULT`) y ahora permite subir/cambiar la foto de un
    producto desde el celular (compresión client-side, cámara vía
    `capture="environment"`); la foto baja al POS local en el siguiente pull
    con su miniatura regenerada.
    Estado: backend en `origin/develop` (commits `ee3c152`..`33f7a84`) y
    frontend commiteado en `main` de `pos-cloud-dashboard` (`c9116f5`).
    **Ninguno de los dos desplegado a prod todavía.**
  - Smoke esperado: crear cliente/categoría desde portal cloud → pull sucursal → aparece local. Crear cliente/categoría local con flujo legacy **no** debe considerarse propagación soportada.

### Frontend

- [x] **F7** — Páginas `/categorias` y `/clientes`
  - Reutilizar el patrón de `/productos`: lib API, hook React Query, ruta, nav, tabla responsive, filtros, modal create/edit, toggle activo.
  - `/categorias`: implementada en portal con búsqueda por nombre, filtro activa/inactiva, edición de `tipo_negocio` y `atributos_configurados`.
  - `/clientes`: implementada en portal con búsqueda por nombre/RNC/cédula, filtro activo/inactivo y tipo, edición de datos de contacto/crédito.
  - Regla de negocio UI: el cliente genérico `CONTADO` se muestra si viene del API, pero queda bloqueado para edición/desactivación/eliminación porque el backend lo gestiona internamente.
  - Regla frontend para query params: preferir `apiClient.get(url, { params })`; si se arma un query string manual, codificar fechas/cursors con `encodeURIComponent()`.
  - Verificación frontend: `npm run lint` y `npm run build` OK en `C:\Proyectos\pos-cloud-dashboard`.
  - Pendiente: smoke manual create/edit/deactivate contra API dev desplegada y validación de propagación a sucursal real.

### DoD 5.D

- [x] Catálogo completo (productos + categorías + clientes) gestionado desde portal en código.
- [x] Pull de categorías/clientes probado en backend.
- [ ] Smoke cloud final portal -> API dev -> pull sucursal real.

---

## Sub-fase 5.E — Reportes consolidados

### Backend

- [ ] **B12** — Refactor de `inventario_consolidado/` para multi-sucursal real
  - Decision tomada: no inferir inventario multi-sucursal desde ventas ni desde `Producto.stock_actual`.
  - El endpoint mantiene el contrato actual `stock_por_sucursal: {"LOCAL": n}` para no romper `/inventario`.
  - Pendiente real: agregar evento/snapshot `INVENTARIO_SNAPSHOT` por sucursal y expandir `stock_por_sucursal` con codigos reales.
  - En el contrato actual se agregaron campos opcionales no rompientes: `ultima_actualizacion_por_sucursal` y `sucursales_sin_datos`.
- [x] **B13** — Endpoints nuevos de reportes on-demand JSON:
  - `GET /api/v1/reportes/ventas-por-cajero/?desde=&hasta=&sucursal=`
  - `GET /api/v1/reportes/top-productos/?desde=&hasta=&sucursal=&limit=10`
  - `GET /api/v1/reportes/cierre-consolidado/?fecha=&sucursal=`
  - Los tres separan ventas facturadas, credito facturado y cobros CxC. Los cobros CxC no inflan ventas nuevas.
  - Todos interpretan fechas como dia local de negocio (`timezone.localdate()` para defaults y filtros date-only).
  - Permisos: `ADMIN`/`SYSADMIN`.
  - Pendiente fuera de B13 JSON: `inventario-valorizado` con `?format=pdf`.
- [x] **B14** — Servicio compartido de reporteria cloud
  - Implementado como `apps/api/services/reporting.py`.
  - Decision de arquitectura: no reutilizar `ReporteManager` local como motor del portal. `ReporteManager`, cierres/PDFs y dashboards Django quedan en `apps/reportes` para POS local.
  - Razon: algunos snapshots locales no son fuente cloud multi-sucursal y `CierreCaja.fecha` no modela cierre unico por sucursal.

### Frontend

- [x] **F10** — Página `/inventario` (read-only, inventario consolidado)
  - Consume el endpoint REAL existente `GET /api/v1/reportes/inventario-consolidado/?categoria=&bajo_stock=&activo=`.
  - KPIs: total productos, bajo stock, sin stock (del `resumen` del backend).
  - Filtros server: categoría, solo bajo stock, solo activos. Búsqueda (producto/SKU) y paginación **client-side** porque el endpoint no pagina ni expone `search`.
  - Tabla con stock coloreado (rojo sin stock / ámbar reposición) y badge de reposición; responsive desktop/móvil.
  - Archivos: `src/pages/Inventory.tsx`, `src/lib/inventory.ts`, `src/hooks/useInventory.ts`; ruta en `src/App.tsx`; nav en `src/components/layout/Sidebar.tsx`.
  - Verificación frontend: `npm run lint` y `npm run build` OK.
  - **Límite actual (backend):** `inventario_consolidado` aún es single-sucursal (`stock_por_sucursal = {"LOCAL": n}`, TODO Fase 2/B12). La página ya está lista para multi-sucursal sin cambios de forma cuando B12 cierre.
- [x] **F8** — Página `/reportes` (reportes on-demand consolidados):
  - Selector de tipo de reporte (dropdown): **Ventas por cajero**, **Top productos**, **Cierre consolidado**.
  - Formulario dinámico: rango `desde`/`hasta` (cajero, top-productos) o `fecha` única (cierre), selector de sucursal (opcional, desde `/sucursales/status/`) y `Top N` (10/25/50) para productos. Validación `desde <= hasta`.
  - Preview en pantalla: tabla por tipo de reporte con etiquetas separadas **Ventas facturadas / Ventas a crédito / Efectivo / Transferencia / Tarjeta / Cobros CxC**. Los cobros CxC NO se suman a ventas facturadas (columna y leyenda aparte).
  - **Exportar CSV** client-side (BOM UTF-8 para Excel). PDF queda fuera de este corte (depende de `inventario-valorizado ?format=pdf` backend, aún pendiente).
  - **Advertencia de sync**: banner si alguna sucursal involucrada está en `amarillo`/`rojo`/`sin_datos` (cruzado contra `/sucursales/status/`), porque sus cifras pueden estar atrasadas.
  - `src/lib/reports.ts` con tipos explícitos `ComparativoResponse` (listo para F5), `VentasPorCajeroResponse`, `TopProductosResponse`, `CierreConsolidadoResponse`; todo vía `apiClient.get(url, { params })`.
  - Archivos: `src/pages/Reports.tsx`, `src/lib/reports.ts`, `src/hooks/useReports.ts`; ruta en `src/App.tsx`; nav habilitado en `src/components/layout/Sidebar.tsx`.
  - Verificación frontend: `npm run lint` y `npm run build` OK.
  - Pendiente: smoke contra API dev desplegada. `/comparativo` (F5) ya habilitado — ver sub-fase 5.B.

### DoD 5.E

- [x] Owner puede generar consolidados desde el portal en codigo (/reportes).
- [ ] Smoke cloud contra API dev con datos reales de sucursal.
- [x] Owner consulta inventario consolidado (stock, bajo stock, reposición) desde el portal — vía `/inventario`

---

## Sub-fase 5.F — Deploy a producción

> Estado actualizado 2026-06-12: backend dev/staging ya corre en Azure Container
> Apps con Docker, ACR, Key Vault, remote state y CI/CD MVP. El portal local
> (`localhost:5173`) autentica y consume la API staging correctamente; dev y
> staging tienen CORS local validado para Vite. Este bloque queda como resumen;
> la fuente operativa es `docs/ROADMAP_DEPLOY_AZURE.md` y el inventario actual
> es `docs/runbooks/AZURE_DEV_RESOURCES.md`.

### Decision base

- [x] **D1** — Decision de arquitectura: **Docker + Azure Container Apps** para backend.
  - Azure App Service Linux sin Docker queda como plan B para demo rapida, no como arquitectura objetivo.
  - Razon: imagen Docker reproducible, Container Apps Jobs para migraciones/comandos, revisiones/rollback y mejor camino hacia workers/sync/multi-tenant.
  - Frontend se mantiene en Azure Static Web Apps.
  - DB cloud se mantiene en Azure PostgreSQL Flexible Server.

### Backend cloud

- [x] **D0** — Readiness previo a cloud:
  - secretos saneados en repo; rotacion externa documentada como pendiente si
    fueron expuestos,
  - reemplazar valores reales por env vars/placeholders,
  - health endpoint con DB/version/ambiente,
  - settings cloud con `DEBUG=False`.
- [x] **D2** — `Dockerfile` backend:
  - Python + dependencias,
  - `collectstatic`,
  - Gunicorn,
  - logs a stdout/stderr,
  - sin migraciones automaticas en startup.
- [x] **D3** — `config/settings_cloud.py` o endurecer `config/settings_production.py` para Azure:
  - `settings_azure_pg.py` queda como dev local contra Azure DB, no como production settings.
- [x] **D4** — Variables de entorno/secrets:
  - `SECRET_KEY` (Azure Key Vault o Container Apps secrets)
  - `ALLOWED_HOSTS=api.tudominio.com`
  - `DEBUG=False`
  - `CORS_ALLOWED_ORIGINS=https://portal.tudominio.com`
  - `JWT_ACCESS_MINUTES=30`, `JWT_REFRESH_DAYS=7`
- [x] **D5** — Terraform dev minimo:
  - Resource Group,
  - Azure Container Registry,
  - Container Apps Environment,
  - Container App `api`,
  - Container Apps Job `migrate`,
  - PostgreSQL Flexible Server o referencia al existente,
  - Log Analytics / Application Insights,
  - Key Vault o secrets de Container Apps.
- [ ] **D5b** — Floci/Terraform lab opcional:
  - `infra/floci-lab/` para aprender Terraform sin costo.
  - No sustituye staging real; no valida Container Apps/ACR/PostgreSQL/domains/RBAC.
- [x] **D6** — GitHub Actions backend:
  - PR: `manage.py check` + tests criticos.
  - Merge `develop`: build Docker image, tag con SHA, push a ACR, deploy a Container Apps dev.
  - Ejecutar migraciones con job/control explicito, no dentro del startup del contenedor.
  - Smoke: health, login, reportes, sucursales/status.
- [~] **D7** — Inicializacion operativa:
  - migracion inicial del schema en Azure DB,
  - crear SYSADMIN via management command,
  - documentar rollback por revision/imagen.
  - Dev ya tiene Container App Job `migrate`; staging/prod requieren runbook
    operativo completo.

### Frontend (Azure Static Web Apps)

- [ ] **D8** — Crear recurso ASWA conectado a `pos-cloud-dashboard` (requiere Azure; pendiente)
- [~] **D9** — CI/CD:
  - [x] `.github/workflows/ci.yml` (lint + tests + build en PR/push) — sin secretos, no rompe antes de existir el recurso.
  - [ ] Workflow de deploy con el deployment token de ASWA (lo añade ASWA al crear el recurso).
- [x] **D10** — Manejo de `VITE_API_URL` por ambiente:
  - Centralizado en `src/lib/config.ts` (único punto que resuelve el backend). `.env.example` documenta dev/staging/prod.
  - **Seam multi-tenant:** `config.ts` prioriza `window.__APP_CONFIG__.apiUrl` (runtime) sobre la env de build, para que un build inmutable sirva varios hosts de API (D8 SaaS) sin rebuild. Hoy se usa el modo build-time.
- [ ] **D11** — Custom domain `portal.tudominio.com` (requiere Azure; pendiente)
- [x] **D12** — `staticwebapp.config.json`: rewrites SPA a `/index.html` + headers de seguridad (CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options`, `Referrer-Policy`). CSP usa `connect-src 'self' https:` (multi-tenant friendly); endurecer al host de API exacto en prod.

Nota validada 2026-06-12:

- `.env.local` con `VITE_API_URL` apuntando a staging permite login y consumo
  del portal local contra backend cloud.
- Dev usa el mismo mecanismo cambiando `VITE_API_URL` a la URL dev y
  reiniciando Vite.
- Backend dev y staging permiten CORS para `http://localhost:5173` y
  `http://127.0.0.1:5173`.

Nota deploy 2026-06-13:

- Runbook ASWA preparado:
  `docs/runbooks/FRONTEND_DEPLOY_AZURE_STATIC_WEB_APPS.md`.
- ASWA dev creado: `https://agreeable-moss-051bc0010.7.azurestaticapps.net`.
- CORS dev aplicado para ese origen y preflight validado.
- Pendiente: subir workflow ASWA corregido con `VITE_API_URL` para que el
  bundle publicado apunte al backend dev; luego repetir smoke de login desde el
  frontend publicado.

### Pre-deploy checklist

- [x] `DEBUG=False`
- [x] `ALLOWED_HOSTS` correcto para dev cloud
- [x] `CORS_ALLOWED_ORIGINS` correcto para dev cloud
- [x] `SECRET_KEY` fuera del repo y referenciado via Key Vault en dev
- [x] DB credentials en env vars / Key Vault
- [x] `STATIC_ROOT` configurado y `collectstatic` ejecutado
- [x] Logging a stdout/stderr para captura por Azure
- [x] `CSRF_TRUSTED_ORIGINS` configurable si aplica
- [x] HTTPS externo por Azure Container Apps ingress
- [ ] `SECURE_HSTS_SECONDS` configurado

### DoD 5.F

- [x] Ambiente `dev` real en Azure creado por Terraform.
- [x] Backend Django corre en Azure Container Apps desde imagen Docker taggeada por SHA.
- [x] Migraciones corren por Container Apps Job/pipeline, no por startup.
- [ ] Portal dev carga desde Azure Static Web Apps y consume API dev.
- [~] Login, dashboard, reportes, CxC y maestros pasan smoke contra Azure como flujo completo.
  - [x] Frontend local Vite -> API staging validado.
  - [ ] Frontend publicado en Azure -> API dev/staging pendiente.
  - [ ] Sync desde sucursal real pendiente.
- [ ] Staging y produccion quedan definidos como ambientes separados antes de abrir a clientes reales.
- [x] Backend queda sin estado en disco local y listo para escalar horizontalmente.

---

## Sub-fase 5.G — Hardening + polish (cross-cutting)

Estas tareas pueden tocar varios pasos, pero conviene tenerlas listadas.

### UX

- [x] Loading skeletons en cada página (no spinners genéricos) — dashboard, productos, categorías, clientes, cuentas
- [x] Empty states ("Sin datos para mostrar" con CTA cuando aplique)
- [x] Error boundaries en React con fallback útil — `src/components/ErrorBoundary.tsx`, envuelve el `<Outlet/>` en `AppLayout` con `key={pathname}` (resetea al navegar). Punto de enganche para Sentry en `componentDidCatch`.
- [x] Toast global para errores de red (TanStack Query `onError`) — `src/lib/toast.ts` (bus pub/sub) + `src/components/Toaster.tsx`, cableado en `QueryCache.onError` (`main.tsx`). Solo notifica fallos de **refetch en segundo plano** (`query.state.data !== undefined`) y excluye 401; el primer load y las mutaciones siguen mostrando error inline (no se duplican mensajes).
- [x] Confirmación pre-acción destructiva — `window.confirm` en toggle/eliminar de productos/categorías/clientes
- [x] Atajos de teclado básicos — **Esc cierra modales** (`src/hooks/useEscapeKey.ts` en los 4 modales) y **`/` enfoca búsqueda** (`src/hooks/useSlashFocus.ts` en productos, categorías, clientes, cuentas e inventario; ignora la tecla si ya se escribe en un campo editable).
- [x] **Responsive — tablet/móvil** — tabla desktop + filas compactas móvil en todas las páginas

### Auth + Seguridad

- [ ] App `token_blacklist` instalada (instala app + correr migración + cambiar `BLACKLIST_AFTER_ROTATION=True`) — backend
- [ ] Endpoint `POST /api/v1/auth/logout/` que blacklista el refresh — backend; el frontend hoy hace logout local (limpia tokens en memoria)
- [x] Sesión expirada: redirect a `/login?expired=1` con mensaje — `AuthContext` distingue logout manual vs por expiración; `ProtectedRoute` redirige con `?expired=1`; `Login` muestra el aviso leyendo el query param
- [ ] Cambio de password desde `/perfil` — requiere endpoint backend
- [ ] Rate limiting en `/login/` (`django-ratelimit`) — backend

### Observability

- [ ] Sentry (o equivalente) para errores frontend
- [ ] Logs estructurados (JSON) en backend
- [~] Health check con info útil: API/DB/version/ambiente listo; falta sync status/uptime operacional
- [~] Métricas con Azure Application Insights: recurso existe; falta dashboard/alertas p95/error rate

### Performance

- [ ] Índices compuestos en `EventoSync(sucursal_id, estado)` y `(sucursal_id, confirmed_at)` — backend
- [ ] Pagination en endpoints de listas grandes — backend
- [x] Code-splitting por ruta (React.lazy + Suspense): login eager, páginas autenticadas en chunks aparte. Chunk inicial 442KB→324KB (gzip 122→104KB); cada página se baja on-demand. `<Suspense>` con `PageLoader` dentro del `ErrorBoundary` (captura también fallos de carga de chunk).
- [x] Versión del build visible en UI (Header → "Versión &lt;sha&gt;"), `VITE_APP_VERSION` inyectada por CI (SHA corto); seam en `src/lib/config.ts`. Alinea con el `version/commit` del health del backend (D0/D7).
- [ ] Lighthouse >90 en `/login` y `/dashboard` (medir post-deploy; code-splitting ya ayuda)
- [ ] Opcional: vendor chunk dedicado (`manualChunks`) para mejorar cache cross-deploy.

### DX / Docs

- [x] `README.md` en `pos-cloud-dashboard` con setup (stack, env, scripts, estructura, auth, deploy ASWA)
- [ ] `HANDOFF_FASE5.md` (este doc evoluciona y se vuelve handoff al cerrar)
- [x] Vitest + React Testing Library para los componentes críticos: `AuthContext`, `ProtectedRoute`, `Login`, `api.ts` interceptor
  - Toolchain: Vitest 4 + jsdom + RTL 16 (React 19) + `axios-mock-adapter`. Scripts: `npm test` (watch), `test:run`, `test:coverage`. Config en `vite.config.ts` (`test.env.VITE_API_URL`) + `src/test/setup.ts` (jest-dom + cleanup).
  - **43 tests / 8 archivos**. Cobertura del camino crítico: `api.ts` interceptor 100% líneas, `AuthContext` 96%, `Login` 95%, `errors` 100%, `ProtectedRoute` cubierto.
  - `api.ts` (riesgo #5 del roadmap): cubre refresh único ante 401, **refresh compartido entre requests concurrentes**, 403 NO refresca, fallo de refresh → limpia tokens + `onSessionExpired`, sin loop infinito ante 401 persistente, y sin refresh-token disponible.
  - Alineado SaaS multi-tenant: `AuthContext` verifica que `tenant_id` y `rol` se preservan en login; `reports.ts`/`cxc.ts` verifican que el filtro `sucursal` se envía vía `{ params }` (no concatenación manual) — base del aislamiento multi-sucursal.
  - Fuera de este corte: tests de páginas/tablas (Products/Categories/Clients/Cuentas/Inventory/Reports) — se priorizó la capa auth/datos.

---

## Sub-fase 5.H — Cartera / Cuentas por cobrar (portal read-only)

> A diferencia de los maestros (el portal **escribe** y la sucursal hace pull), CxC fluye **sucursal → cloud por eventos** (`CXC_CREADA`, `CXC_PAGO_REGISTRADO`, `CXC_ANULADA`). El alta de crédito, abonos y anulaciones nacen en el POS. **En el portal CxC es SOLO LECTURA / presentación.** Ver `ROADMAP_CLOUD.md` → "Decision record: Credito y cuentas por cobrar v1".

### Backend (lo que ya existe)

- [x] Modelos `MetodoPlazoCredito`, `CuentaPorCobrar`, `CuotaCxC`, `PagoCxC` (`apps/cuentas_por_cobrar/models.py`)
- [x] Servicios de crédito/abono/anulación + `resumen_credito_cliente()` (`apps/cuentas_por_cobrar/services.py`)
- [x] Sync sucursal→cloud: helpers de evento (`apps/sync/events.py`), tipos en `apps/sync/constants.py`, serializers y **handlers cloud que replican la cartera completa** (`apps/api/views/sync.py::_handler_cxc_*`)

### Backend (cerrado)

- [x] **B15** — Endpoint(s) de **lectura** para el portal (read-only DRF). Implementado siguiendo el patrón canónico de `ProductoViewSet` pero como `ReadOnlyModelViewSet` (solo `list`/`retrieve` + acción `resumen/`). Ruta servida en `/api/v1/cuentas-por-cobrar/` (coincide con `src/lib/cxc.ts → BASE_PATH`, no hace falta tocar la constante).
  - Archivos: `apps/api/views/cuentas_por_cobrar.py`, `apps/api/serializers/cuentas_por_cobrar.py`, ruta en `apps/api/urls.py`, propiedad `CuentaPorCobrar.esta_vencida` en `apps/cuentas_por_cobrar/models.py`. Tests: `apps/api/tests/test_cuentas_por_cobrar_viewset.py` (13 tests).
  - `GET /api/v1/cuentas-por-cobrar/?search=&estado=&vencidas=&page=&page_size=` → `PaginatedResponse<CuentaCxC>` (`StandardPagination`: page_size 50, max 200).
    - `CuentaCxC`: `id, numero_venta, cliente_id, cliente_nombre, cliente_cedula_rnc, sucursal_codigo, metodo_plazo_nombre, total, monto_inicial, saldo, estado(ABIERTA|PARCIAL|PAGADA|VENCIDA|ANULADA), fecha_emision, fecha_limite, esta_vencida`
  - `GET /api/v1/cuentas-por-cobrar/<id>/` → `CuentaCxCDetalle` (la cuenta + `cuotas[]` + `pagos[]`)
    - `CuotaCxC`: `id, numero, monto, saldo, fecha_vencimiento, estado(PENDIENTE|PARCIAL|PAGADA|VENCIDA|ANULADA), fecha_pago`
    - `PagoCxC`: `id, metodo, monto, referencia, fecha_pago, estado, registrado_por` (`registrado_por` = username)
  - `GET /api/v1/cuentas-por-cobrar/resumen/` → `CarteraResumen`: `cartera_total, saldo_vencido, cuentas_abiertas, cuentas_vencidas, clientes_con_saldo` (agregados de TODA la cartera en **una sola** query de agregación condicional; `cartera`/`vencido` solo cuentan estados con saldo vivo: ABIERTA/PARCIAL/VENCIDA).
  - Permisos: `IsAuthenticated + EsSoloLectura` (mismo patrón que maestros: leen sucursal + admin). Sin endpoints de escritura desde el portal en v1.
  - `search` cubre `venta.numero_venta`, `cliente.nombre` y `cliente.cedula_rnc`.
  - **`esta_vencida` se calcula por fecha, no por `estado`.** El campo `estado` solo se recalcula por eventos (abono/anulación), así que una cuenta ABIERTA/PARCIAL puede estar vencida de hecho. La propiedad y el filtro `?vencidas=true` usan `esta_abierta AND fecha_limite < hoy`. Por eso `?estado=VENCIDA` (valor almacenado) y `?vencidas=true` (cálculo por fecha) son controles distintos a propósito.
  - Eficiencia: `select_related(cliente, venta, metodo_plazo, sucursal)` siempre; `prefetch_related(cuotas, pagos_cxc__registrado_por)` solo en `retrieve`.

### Decisiones futuras (post-v1, NO bloquean el cierre de 5.H)

> Estas quedaron explícitamente fuera del alcance de B15. Anotarlas aquí para no re-descubrirlas en la auditoría.

1. **Scoping por sucursal en lecturas.** Hoy un token de sucursal puede leer la cartera de **todas** las sucursales (igual que maestros). Inocuo mientras el producto sea single-sucursal, pero al entrar multi-sucursal hay que decidir si el endpoint filtra por la sucursal del token o se restringe a admin/sysadmin. Punto de cambio: `CuentaPorCobrarViewSet.get_permissions()` / `get_queryset()`.
2. **Aging por buckets (0-30 / 31-60 / 61-90 / 90+).** El frontend lo difirió; el `resumen/` actual solo da total y vencido global. Cuando se pida, agregar al payload de `resumen/` (otra agregación condicional por rangos de `fecha_limite`).
3. **Alertas de vencimiento / próximos a vencer.** No expuestas en v1. `services.resumen_credito_cliente()` ya calcula `proximo_vencimiento` por cliente; evaluar si el portal lo consume vía un endpoint nuevo o se agrega al detalle.
4. **Escritura de abonos desde el portal.** Explícitamente NO en v1 (los abonos nacen en el POS y fluyen por eventos). Si alguna vez se quiere cobrar desde el portal, hay que diseñar el flujo inverso cloud→sucursal, que hoy no existe.
5. **Filtro `?desde=` (sync incremental) para CxC.** B15 no incluye el `SyncIncrementalMixin` porque el portal no hace pull incremental de cartera (la consume on-demand). Si en el futuro otra instalación quisiera sincronizar cartera por cursor, habría que sumarlo.

### Frontend

- [x] **F9** — Página `/cuentas` (read-only)
  - Tarjetas de resumen de cartera (cartera total, saldo vencido, cuentas abiertas/vencidas) desde `resumen/`; si el resumen falla, se ocultan sin romper la tabla (el toast global avisa).
  - Tabla paginada con búsqueda (venta/cliente/RNC), filtro por estado y toggle "solo vencidas".
  - Modal de detalle con encabezado + tabla de **cuotas** y tabla de **abonos**; cierra con Esc.
  - Sin modales de create/edit ni toggles: CxC no se edita desde el portal (origen en sucursal).
  - Fechas date-only formateadas en local para evitar shift por timezone (`formatDate` en `src/lib/format.ts`).
  - Archivos: `src/pages/Cuentas.tsx`, `src/lib/cxc.ts`, `src/hooks/useCxc.ts`; ruta en `src/App.tsx`; nav en `src/components/layout/Sidebar.tsx`.
  - Verificación frontend: `npm run lint` y `npm run build` OK.
  - **Conexión:** B15 ya expone el endpoint en `/api/v1/cuentas-por-cobrar/`, que coincide con `BASE_PATH` actual en `src/lib/cxc.ts` — no requiere cambios de ruta. Pendiente: smoke E2E del portal contra el backend desplegado (validar que las cifras cuadran con el POS de la sucursal).

### DoD 5.H

- Owner consulta la cartera consolidada (saldos, vencidos, cuotas y abonos por cuenta) desde el portal
- Las cifras cuadran contra la cartera del POS de la sucursal de origen

---

## Sub-fase 5.I — RBAC y módulos del portal

> Estado 2026-06-09: avance posterior al roadmap original. La base backend y
> las pantallas principales existen; queda cerrar cutover operativo y permisos
> finos antes de tratarlo como producto administrable completo.

### Backend

- [x] App `apps/permisos` con `Permiso`, `Rol`, `AsignacionRol`, seed y engine.
- [x] Endpoints `/api/v1/permisos/catalogo/`, `/api/v1/permisos/roles/` y `/api/v1/permisos/asignaciones/`.
- [x] App `apps/suscripciones` con catálogo de módulos, planes, overrides y engine.
- [x] Endpoints `/api/v1/suscripciones/modulos/`, `/planes/`, `/negocios/` y `/overrides/`.
- [x] Payload auth expone `permisos` y `modulos` para gating frontend.
- [~] Enforcement por permisos/módulos existe en piezas críticas; falta completar cortes legacy/locales documentados en `RBAC_LOCAL_CUTOVER_PENDIENTE.md`.

### Frontend

- [x] `AuthContext` expone `can()` y `hasModule()`.
- [x] `ProtectedRoute` soporta `requiere` y `requiereModulo`.
- [x] Sidebar oculta/filtra CxC, roles y suscripciones según módulo/permiso.
- [x] Pantalla `/roles` para editar rol → permisos.
- [x] Pantalla `/suscripciones` para ver/administrar módulos/planes.
- [ ] UI de asignación usuario → rol/sucursal pendiente.
- [ ] Smoke cloud RBAC completo pendiente: usuario sin permiso no ve ruta y backend devuelve 403.

### DoD 5.I

- Admin del negocio administra roles sin tocar Django admin.
- Operador/SYSADMIN administra módulos/planes del negocio.
- Frontend y backend niegan rutas/acciones de forma consistente.
- POS local y API cloud comparten el mismo contrato de permisos efectivo.

---

## Decisiones pendientes (NO bloquean hoy)

1. **Tokens en memoria vs `sessionStorage` para el refresh.** Roadmap original dice "memory only". Implicación: cada reload obliga a re-login. ¿Aceptable o usamos `sessionStorage` (se borra al cerrar la tab) como compromiso?
2. **App Service vs Container Apps.** Decidido: Docker + Azure Container Apps como arquitectura objetivo; App Service Linux sin Docker queda como plan B para demo rapida.
3. **WebSocket vs polling.** TanStack Query con `refetchInterval: 30000` cubre el 90% de los casos. WebSocket sería overkill.
4. **Tenancy cloud.** Decision actual: DB-per-tenant, no `django-tenants`. No bloquea Fase 5, pero SK Performance no debe entrar al cloud hasta que el control plane + una DB por tenant este implementado. Fuente viva: `docs/TENANCY_DB_PER_TENANT.md`.
5. **Mobile-responsive: prioridad.** Si los dueños usan móvil mucho → desde F4. Si solo PC en oficina → diferir a 5.G.
6. **i18n.** Probablemente no en esta fase (todos los clientes son DO, español).
7. **Comparativos multi-sucursal.** RESUELTO 2026-06-12: `/comparativo` habilitado en el portal (sub-fase 5.B / F5) con gate por permiso `reportes.consolidado.ver`. Queda el smoke contra API desplegada como el resto de pantallas.
8. **Escritura local de maestros.** Decisión tomada: cloud como fuente de verdad. Pendiente implementar proxy/local admin flow para que vistas locales de clientes/categorías/productos escriban en la API cloud y refresquen la copia local. No implementar sync bidireccional por eventos para maestros en v1.

---

## Riesgos identificados

1. **Performance del sync engine con N sucursales.** Cuando lleguemos a 10+ sucursales, la cola `EventoSync` puede crecer. Mitigación: índices (5.G) + monitoreo desde el mismo portal (ya en 5.A).
2. **Drift entre POS local y cloud cuando una sucursal está offline mucho.** El semáforo amarillo/rojo lo comunica al owner.
3. **Costos de Azure post free-tier.** Hoy todo está cubierto por cuenta de estudiante. Calcular plan de costos antes de que expire.
4. **Maintenance burden pre-tenancy.** Cada cliente no debe ser un deploy separado. La ruta aprobada es un backend compartido con control plane + DB por tenant. Hasta implementarlo, evitar subir nuevos clientes reales al cloud.
5. **Falla del refresh interceptor en F2.** Si el flujo de refresh tiene un bug sutil, la UX se vuelve horrible (logout cada 30 min sin razón). Cubrir con tests específicos en 5.G.

---

## Fuera de scope (Fase 6+)

- App móvil nativa (React Native u otra) — está en el roadmap del producto, no en Fase 5
- Gestión de cola e-CF desde el portal (monitoreo de errores DGII, reintentos)
- IA para escaneo de facturas de compra (modelo evaluado, no implementado)
- Implementación real de DB-per-tenant con control plane global
- Catálogo de vehículos / VIN decoder para SK Performance
- Gestión de financiación cooperativa desde portal (hoy solo se usa en sucursal)

---

## Próximos 3 hitos demoables

Si tuviéramos que cortar el trabajo en 3 hitos visibles para el owner de Royal Plast:

1. **Hito 1 — "Veo mi negocio en la nube"**: backend dev + portal local consumiendo API dev, con login/dashboard/reportes/CxC.
2. **Hito 2 — "Administro mis maestros"**: productos, categorías y clientes desde portal, con pull validado en sucursal real.
3. **Hito 3 — "Controlo accesos y módulos"**: roles/permisos y suscripciones funcionando con usuario de prueba restringido.

El comparativo 5.B (antes diferido) se habilitó el 2026-06-12; sirve para demos de la visión multi-sucursal aunque el cliente actual opere una sola sucursal.

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
