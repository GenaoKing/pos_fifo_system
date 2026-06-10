# POS FIFO System — Roadmap Integral
## De sistema local a plataforma multi-sucursal con cloud

**Fecha:** Abril 2026  
**Estado actual:** Sistema POS local funcionando en producción (Royal Plast EIRL)

> Estado actualizado 2026-06-09: este roadmap queda como mapa integral de
> producto/plataforma. Para estado operativo actual usar `PROJECT_STATUS.md`.
> Para deploy Azure usar `ROADMAP_DEPLOY_AZURE.md`; para el portal cloud usar
> `ROADMAP_PORTAL.md`.

---

## Que falta ahora

1. **Staging cloud**: crear `infra/azure/environments/staging` con remote state
   propio, secretos separados, pipeline controlado y smoke tests.
2. **Frontend cloud deploy**: resolver Azure Static Web Apps o alternativa para
   portal dev/staging; hoy backend dev esta desplegado, frontend ASWA sigue
   pendiente/bloqueado.
3. **Sync operacional**: probar una segunda sucursal real, correr `sincronizar`
   como servicio y definir heartbeat/liveness separado de `ultima_sync`.
4. **Maestros local -> cloud**: cambiar vistas locales administrativas para
   escribir en API cloud y refrescar copia local; no crear maestros locales
   desconectados en v1.
5. **Inventario multi-sucursal real**: implementar snapshot/evento
   `INVENTARIO_SNAPSHOT`; el inventario cloud actual conserva contrato
   compatible, pero no reconstruye stock real por sucursal.
6. **Hardening antes de prod**: tests criticos, roles custom/RBAC minimo,
   backups/restore drill, alertas, HSTS/dominos y runbooks de rollback.

## Estado actual del proyecto

### Completado (✅)

**Core del sistema:**
- Modelos completos: Producto, Categoría, Lote, MovimientoLote, Venta, DetalleVenta, Pago, Compra, DetalleCompra, AjusteInventario
- Lógica FIFO completa en `fifo_logic.py`: consumo automático por fecha, valuación, stock disponible
- POS operativo: carrito dinámico, escaneo código de barras, descuentos por línea, pagos múltiples (efectivo/transferencia/mixto/tarjeta)
- Ventas a credito y cuentas por cobrar v1: venta a credito desde POS, cuenta CxC, cuotas, abonos, limite de credito bloqueante, override ADMIN/SYSADMIN, caja/reportes/e-CF/sync integrados; lectura de cartera expuesta al portal cloud read-only (B15: `/api/v1/cuentas-por-cobrar/` lista + detalle + resumen)
- Sistema de impresión: térmica 80mm (2Connect) + etiquetas Zebra LP 2824 (EPL2) + PrintManager singleton
- Cotizaciones: crear, listar, convertir a venta, PDF
- Clientes: CRUD + cliente contado + búsqueda en POS
- Anulaciones: con devolución a lotes originales + auditoría + límite configurable de días

**Infraestructura:**
- ConfiguracionNegocio: singleton con feature flags, cache invalidation, context processor, decoradores `@requiere_modulo` / `@requiere_sysadmin`
- Sistema de roles: **evolucionó** de hardcoded (SYSADMIN/ADMIN/CAJERA) a **RBAC data-driven y multitenant** (apps `permisos`/`negocios`): roles configurables por negocio, enforcement server-side en API + POS local, sync de roles cloud→sucursal. El enum `Usuario.rol` queda legacy/informativo. Ver `docs/RBAC_PERMISOS.md`.
- Auditoría: modelo completo, middleware auto-logging, registro de ventas/anulaciones/login
- Deploy v3: instalar.bat, scripts de servicio (NSSM), backup automático, verificar_sistema.py
- Presets de cliente: plasticos, accesorios_auto, retail_general via `crear_config_inicial`
- Logging: RotatingFileHandler con separación all/errors
- Settings production con WhiteNoise (CompressedStaticFilesStorage)

**Reportes:**
- Dashboard con métricas en tiempo real (Alpine.js polling)
- Dashboard cajera (vista filtrada)
- Reportes On-Demand backend completo: cierre de caja, ventas por período, top productos, inventario valorizado FIFO, ventas por cajero
- Reportes On-Demand frontend completo: formularios dinámicos, Chart.js, export PDF
- PDF Generator con ReportLab
- Reporteria cloud multi-sucursal v1: servicio query-based para portal (`apps/api/services/reporting.py`), comparativo real por sucursal, ventas por cajero, top productos, cierre consolidado, separacion de credito facturado y cobros CxC.

### Pendiente del sistema local / estado 2026-06-09 (🔲)

- [x] **Frontend anulaciones**: UI POS en `templates/pos/anulaciones.html` + `vista_anulaciones` + API `api_anular_venta`.
- [x] **UI ajustes de inventario**: `templates/inventario/ajustes.html` + `vista_ajustes` + API de ajuste.
- [x] **Dashboard auditoría frontend**: `templates/auditoria/dashboard.html` + `dashboard_auditoria` + API de búsqueda.
- [~] **Métodos de pago dinámicos en POS**: el POS hidrata `metodos_pago` desde `ConfiguracionNegocio`; queda como seguimiento limpiar usos legacy/hardcoded si aparecen.
- [ ] **Migración ConfiguracionNegocio Fase 3**: mover usos restantes de `settings.BUSINESS_INFO` y `settings.THERMAL_PRINTER` hardcodeados a `get_config()`.

---

## Decision record: Credito y cuentas por cobrar v1

**Estado 2026-06-09:** implementado localmente como base de producto y expuesto
al portal cloud en modo read-only. Pendiente: smoke E2E contra backend dev,
aging avanzado, alertas y permisos/operaciones de escritura futuras.

### Alcance implementado

- Nueva app `apps/cuentas_por_cobrar` para separar el dominio CxC del dominio de ventas.
- Modelos:
  - `MetodoPlazoCredito`: define vencimiento unico o cuotas, dias, frecuencia, inicial minima, activo y sucursal opcional.
  - `CuentaPorCobrar`: una cuenta por venta a credito, con cliente, total, saldo, estado, metodo, fecha limite y override admin auditado.
  - `CuotaCxC`: calendario de vencimientos por cuenta, con monto, saldo, fecha y estado.
  - `PagoCxC`: abonos posteriores, metodo, referencia, cajero, aplicaciones a cuotas y estado.
- `Venta` ahora tiene `condicion_pago = CONTADO|CREDITO`.
- `Pago` ahora acepta `CREDITO` para representar el saldo financiado al cerrar la venta.
- `procesar_venta_service` acepta payload `credito`, mantiene FIFO/e-CF/sync como hoy y crea la venta, pagos, cuenta y cuotas dentro del mismo `transaction.atomic`.
- El limite de credito es bloqueante: `limite_credito - saldo_pendiente_no_anulado`.
- El override de limite reutiliza el patron de soft-login admin de caja (`/caja/api/validar-admin/`) y queda registrado en auditoria.
- POS:
  - Selector de credito como metodo de pago.
  - Selector de metodo de plazo.
  - Inicial opcional.
  - Resumen de limite, saldo pendiente, disponible y vencido del cliente.
  - Autorizacion admin cuando el saldo nuevo excede limite.
- Clientes:
  - Lista muestra saldo pendiente, vencido y credito disponible.
  - Acceso al estado de cuenta por cliente.
- Cuentas por cobrar:
  - Vista global.
  - Vista por cliente.
  - Registro de abonos.
  - Filtros por estado y busqueda.
- Caja/reportes:
  - Cobros CxC en efectivo suman al efectivo esperado de caja.
  - Dashboard separa credito facturado y cobros CxC para no inflar ventas del dia.
  - Cierre de caja guarda `total_cobros_cxc`.
- e-CF/MSeller:
  - `venta_a_ecf_data` expone `metadata.tipo_pago` desde la venta.
  - `build_mseller_payload` prefiere el dato por venta sobre `Emisor.config_proveedor`.
  - Credito envia `TipoPago=2`.
  - `FechaLimitePago` sale de la CxC y solo aplica en v1 al flujo tipo 31.
- Sync/API:
  - Serializacion de venta incluye `condicion_pago` y resumen de CxC.
  - Eventos nuevos: `CXC_CREADA`, `CXC_PAGO_REGISTRADO`, `CXC_ANULADA`.
  - Handlers cloud **implementados** (`apps/api/views/sync.py::_handler_cxc_*`): replican cuenta, cuotas y pagos en los mismos modelos del cloud, con idempotencia por hash + chequeos secundarios. El assert de integridad en ese archivo obliga a tener handler por cada tipo declarado en `constants.py`.
  - APIs internas locales para metodos de plazo, resumen de credito por cliente y registro de abonos.
  - **[Estado may 2026] Endpoint de LECTURA para el portal cloud IMPLEMENTADO** (B15, read-only DRF). `apps/api/urls.py` ahora registra `cuentas-por-cobrar/` además de `maestros/*`, `sync/*` y `reportes/*`. `CuentaPorCobrarViewSet` (`ReadOnlyModelViewSet` + acción `resumen/`) sirve `GET /api/v1/cuentas-por-cobrar/` lista + `<id>/` detalle + `resumen/`. Contrato y detalles en `ROADMAP_PORTAL.md` → 5.H/B15. Decisiones futuras (scoping por sucursal, aging por buckets, alertas, escritura desde portal) listadas ahí mismo.
- Pruebas agregadas:
  - Venta a credito con vencimiento unico.
  - Venta a credito en cuotas.
  - Limite bloqueante con rollback.
  - Override ADMIN/SYSADMIN.
  - Abonos parciales contra cuotas antiguas primero.
  - Contrato fiscal contado vs credito.
  - Builder MSeller prefiriendo metadata de venta.

### Decisiones de arquitectura

1. **CxC vive fuera de `ventas`.**
   - `Venta` sigue siendo el documento comercial/fiscal que descuenta inventario FIFO y alimenta e-CF.
   - `CuentaPorCobrar` es el ledger operativo del saldo pendiente.
   - Esto evita que cada abono sea una nueva venta y protege los reportes de ventas reales.

2. **La venta a credito es una venta real desde el inicio.**
   - Se factura, descuenta inventario y puede entrar al flujo e-CF igual que contado.
   - La diferencia es la condicion de pago y el calendario CxC.

3. **`Pago(CREDITO)` representa financiamiento, no dinero recibido.**
   - Los pagos reales de inicial se registran como efectivo/transferencia/tarjeta.
   - El saldo queda como `Pago(CREDITO)` para cuadrar la venta y explicar el cierre.
   - Los abonos posteriores son `PagoCxC`, no `Pago` de venta.

4. **Los cobros CxC no crean ventas.**
   - Son flujo de caja y reduccion de saldo.
   - Caja los considera para efectivo esperado.
   - Dashboard/reportes los muestran separados para no inflar facturacion del dia.

5. **Atomicidad primero.**
   - Venta, detalles, FIFO, pagos, CxC y cuotas se crean en una sola transaccion.
   - Si el limite falla o el metodo de plazo es invalido, no queda venta parcial ni stock consumido.

6. **Override explicito y auditable.**
   - Solo ADMIN/SYSADMIN puede autorizar exceso de limite.
   - Se guarda usuario autorizador y motivo.
   - La auditoria queda como evento critico.

7. **Regla simple de credito disponible en v1.**
   - `credito_disponible = limite_credito - saldo_pendiente_no_anulado`.
   - No hay intereses, mora automatica, refinanciacion ni contabilidad de doble partida en v1.

8. **MSeller recibe condicion fiscal por venta.**
   - La configuracion del emisor mantiene defaults.
   - La venta puede sobrescribir `TipoPago` mediante metadata.
   - Esto evita que una config global fuerce contado/credito incorrectamente.

9. **`FinanciacionCooperativa` queda separada.**
   - No se migro ni mezclo con CxC.
   - Sigue siendo otro flujo de negocio para cooperativa/financiacion especial.

10. **Sync por eventos explicitos.**
    - Igual que ventas, CxC emite eventos despues del commit.
    - La nube puede reconstruir estado de cartera sin consultar la BD local directamente.

### Pendiente y futuro del modulo Credito/CxC

- Validar en operacion real el flujo POS completo: seleccion de cliente, autorizacion, cierre, impresion y posterior abono.
- Agregar permisos granulares para CxC: ver cartera, registrar abonos, anular abonos, autorizar exceso de limite.
- Implementar anulacion/reversa de `PagoCxC`; v1 registra abonos aplicados, pero no trae flujo completo de reversa.
- Mejorar aging:
  - buckets 0-30, 31-60, 61-90, 90+.
  - saldo vencido por cuota, no solo por fecha limite de cuenta.
  - proyeccion de vencimientos proximos.
- Definir politica de mora/interes:
  - mora automatica diaria o mensual.
  - cargos por atraso.
  - condonaciones autorizadas.
- Definir refinanciaciones:
  - reestructurar cuotas.
  - consolidar varias cuentas.
  - mantener trazabilidad del saldo original.
- Definir castigos/incobrables:
  - estado `CASTIGADA` o flujo separado.
  - auditoria y permisos de gerencia.
- Agregar recibos/impresion de abonos CxC.
- Agregar export PDF/Excel de estado de cuenta.
- Exponer CxC en el portal cloud:
  - cartera por cliente.
  - aging consolidado.
  - cobros por sucursal/cajero.
  - alertas de vencimiento.
  - **[Estado may 2026] Backend + frontend listos.** Frontend `/cuentas` (read-only) implementado en `pos-cloud-dashboard`: lista filtrable, resumen de cartera (total/vencido/abiertas/vencidas) y detalle con cuotas + abonos. Endpoint de lectura backend (B15) **implementado** (`/api/v1/cuentas-por-cobrar/`); falta solo el smoke E2E contra el backend desplegado. **Pendientes diferidos (decisiones futuras):** aging por buckets (0-30/31-60/61-90/90+), alertas de vencimiento, cobros por sucursal/cajero y scoping de lectura por sucursal — todos listados en `ROADMAP_PORTAL.md` → 5.H "Decisiones futuras".
- Endurecer endpoints API para cloud/portal con DRF, permisos y paginacion; los endpoints actuales son internos/locales.
- Definir si `MetodoPlazoCredito` sera global, por empresa, por sucursal o por cliente en modo SaaS.
- Agregar pruebas E2E de UI para POS credito y registro de abonos.
- Agregar validacion de contratos sync CxC contra cloud staging.
- Agregar contabilidad formal solo si el producto evoluciona hacia doble partida.

---

## Decision record: Reporteria cloud multi-sucursal

**Estado 2026-06-09:** backend JSON implementado para portal cloud. Frontend de
`/reportes` e `/inventario` reportado como listo en `ROADMAP_PORTAL.md`;
`/comparativo` queda pendiente/deshabilitado por decision de producto.

### Decision principal

- `apps/reportes` queda como modulo local/POS para dashboard Django, cierres locales, PDFs y reportes on-demand de una sucursal.
- `apps/api/views/reportes.py` queda como capa HTTP del portal cloud.
- `apps/api/services/reporting.py` es el motor query-based de reportería cloud sobre la BD cloud.
- No se estira `ReporteManager` local como motor del portal. Sus snapshots y cierres locales no son una fuente limpia para multi-sucursal; ademas `CierreCaja.fecha` no modela cierre unico por sucursal.

### Contrato implementado

```
GET /api/v1/reportes/ventas-hoy/
GET /api/v1/reportes/ventas-hoy/<sucursal>/
GET /api/v1/reportes/comparativo/?desde=&hasta=&agrupacion=dia|semana|mes&sucursal=
GET /api/v1/reportes/ventas-por-cajero/?desde=&hasta=&sucursal=
GET /api/v1/reportes/top-productos/?desde=&hasta=&sucursal=&limit=10
GET /api/v1/reportes/cierre-consolidado/?fecha=&sucursal=
GET /api/v1/reportes/inventario-consolidado/?categoria=&bajo_stock=&activo=
```

- Auth: `ADMIN`/`SYSADMIN`.
- Fechas: `YYYY-MM-DD`, interpretadas como dia local de negocio.
- Decimales: strings con 2 decimales.
- `sucursal` filtra por codigo; si no viene, se incluyen todas las sucursales activas.
- Ventas con `sucursal=NULL` se excluyen del consolidado multi-sucursal y se reportan en `metadata.legacy_ventas_omitidas`.
- Metricas estandar:
  - `ventas_facturadas`: ventas completadas.
  - `credito_facturado`: ventas completadas con `condicion_pago=CREDITO`.
  - `cobros_cxc`: `PagoCxC` aplicado, separado de ventas nuevas.
  - `ticket_promedio`: `ventas_facturadas / cantidad_ventas`.
- `ventas-hoy/` conserva el contrato previo (`total_ventas`, desglose de pagos, anulaciones) y agrega campos nuevos no rompientes (`ventas_facturadas`, `credito_facturado`, `cobros_cxc`, `metadata`).

### Inventario

- `inventario-consolidado/` mantiene `stock_por_sucursal: {"LOCAL": n}` por compatibilidad con `/inventario`.
- Multi-sucursal real de inventario queda pendiente hasta agregar un evento/snapshot por sucursal, recomendado como `INVENTARIO_SNAPSHOT`.
- No inferir stock cloud desde ventas: eso no reconstruye ajustes, compras, mermas, anulaciones ni lotes FIFO locales.

### Consideraciones frontend

- Crear `src/lib/reports.ts` con tipos de `ComparativoResponse`, `VentasPorCajeroResponse`, `TopProductosResponse` y `CierreConsolidadoResponse`.
- Usar `apiClient.get(url, { params })`; no concatenar fechas manualmente.
- Etiquetar siempre por separado: "Ventas facturadas", "Ventas a credito", "Cobros CxC" y "Flujo de caja".
- No sumar cobros CxC a ventas facturadas en graficas principales; pueden ir como serie separada.
- Mostrar advertencia visual cuando `estado_sync` sea amarillo/rojo/sin_datos.
- `/inventario` puede seguir leyendo el contrato actual; cuando llegue `INVENTARIO_SNAPSHOT`, la tabla puede generar columnas desde `Object.keys(stock_por_sucursal)`.

### Pruebas

- Cobertura agregada en `apps/api/tests/test_reportes_cloud.py`.
- Cubre permisos admin, fechas, filtro por sucursal, dos sucursales activas con una sin ventas, legacy omitido, comparativo, ventas por cajero, top productos, cierre consolidado, contrato de inventario y separacion CxC vs ventas.

### Pendiente futuro

- Implementar `INVENTARIO_SNAPSHOT` y expandir inventario real por sucursal.
- Agregar export CSV/PDF para reportes cloud si el portal lo requiere.
- Agregar `inventario-valorizado` cloud si se decide llevar valuacion FIFO al portal; debe venir de snapshots locales o de una fuente cloud formal, no de `Producto.stock_actual`.
- Optimizar queries con indices compuestos cuando existan volumenes reales.

---

## Roadmap por fases

### Estado resumido al 2026-06-09

| Fase | Estado | Qué existe hoy | Qué falta |
| --- | --- | --- | --- |
| Fase 0 - Sistema local | Mayormente cerrado | Anulaciones UI, ajustes UI, auditoría UI, CxC, reportes, e-CF y métodos de pago dinámicos en POS. | Remover usos legacy de `settings.BUSINESS_INFO` / `settings.THERMAL_PRINTER` y smoke operativo final. |
| Fase 1 - DB cloud | Cerrada para la ruta objetivo | Azure PostgreSQL dev existe y es la base elegida para cloud; `settings_azure_pg.py` y resultados de latencia quedaron documentados. | Azure SQL queda como exploración histórica, no como objetivo actual. |
| Fase 2 - Multi-sucursal | MVP implementado | App `sucursales`, `Sucursal`, `SUCURSAL_CODIGO`, middleware/context, `Venta.sucursal`, `ConfiguracionNegocio` por sucursal y FKs en caja/auditoría/inventario/CxC. | Hardening de migración de datos reales y validación con segunda sucursal física. |
| Fase 3 - API REST | MVP implementado | DRF, JWT/token sucursal, maestros, sync, reportes cloud, CxC read-only, health y tests API. | Más tests críticos, paginación/permisos en todos los bordes y smoke contra Azure dev. |
| Fase 4 - Sync engine | MVP implementado | `EventoSync`, `VersionMaestro`, `SyncEngine`, `sincronizar`, `push_eventos`, `pull_maestros`, handlers cloud de ventas/CxC/anulaciones. | Proxy de escrituras locales de maestros hacia cloud, heartbeat/liveness explícito, `INVENTARIO_SNAPSHOT` real y operación como servicio. |
| Fase 5 - Portal cloud | MVP parcial | Backend API listo; portal React reportado en `ROADMAP_PORTAL.md` con dashboard, maestros, reportes, inventario y CxC read-only. | Deploy frontend ASWA bloqueado/no aplicado, smoke E2E cloud, `/comparativo` y endurecimiento final. |
| Fase 6 - Producción multi-sucursal | Pendiente | Base técnica existe en dev. | Staging, piloto segunda sucursal, instalador multi-sucursal, jobs operativos, rollback y monitoreo. |
| Fase 7+ - SaaS/futuro | En progreso | **RBAC data-driven** (`docs/RBAC_PERMISOS.md`) y **módulos/suscripciones vendibles** (`docs/ARQUITECTURA_MODULOS.md`) **implementados** (motor + API + portal + POS local); deploy Docker/ACA en marcha. | Producción, **multi-tenant real con `django-tenants`** (hoy row-level por `negocio`), aislamiento de datos por tenant, dominios, **billing/pasarela de pago** (hoy entitlements manuales) y producto móvil/IA. |

### FASE 0 — Completar sistema local (prioridad inmediata)
> Estado 2026-06-09: mayormente cerrado. Queda limpieza legacy de settings y
> smoke operativo final en ambiente real.
> *Estabilizar lo que hay antes de agregar complejidad*

**0.1 Frontend anulaciones — [x]**
- [x] UI en el POS para buscar venta y ejecutar anulación.
- [x] Confirmación con motivo obligatorio.
- [x] Backend maneja lógica FIFO reversa.

**0.2 Ajustes de inventario UI — [x]**
- [x] Formulario para seleccionar producto → lote específico → tipo ajuste (merma/daño).
- [x] Integración con `MovimientoLote` existente.

**0.3 Métodos de pago dinámicos — [~]**
- [x] POS hidrata métodos activos desde `ConfiguracionNegocio`.
- [~] Seguimiento: limpiar cualquier uso legacy/hardcoded que aparezca fuera del POS principal.

**0.4 Dashboard auditoría — [x]**
- [x] Template `auditoria/dashboard.html`.
- [x] Filtros por fecha, usuario, tipo de acción, nivel de importancia.

---

### FASE 1 — Branches de base de datos cloud (exploración)
> Estado 2026-06-09: cerrada para la ruta objetivo. Azure PostgreSQL quedó como
> base cloud de dev; Azure SQL se conserva como exploración histórica.
> *Aprender infraestructura cloud sin afectar producción*

**Branch `feature/azure-postgres`**
- [ ] `config/settings_neon.py` — conexión a Neon PostgreSQL (no es ruta objetivo actual).
- [x] `config/settings_azure_pg.py` — conexión a Azure Database for PostgreSQL.
- [x] Variables locales ignoradas para Azure PG.
- [x] `sslmode=require` / pooling / health checks documentados en settings cloud/dev.
- [x] Migración y uso real de Azure PostgreSQL dev.
- [x] Resultados de latencia documentados en `docs/historico/latency_results_azure_pg_20260419_1941.json`.

**Branch `feature/azure-sql`**
- [x] `config/settings_azure_sql.py` — conexión a Azure SQL Database para exploración.
- [~] `mssql-django`/compatibilidad dual queda como investigación, no como target.
- [~] Migraciones/compatibilidades SQL Server no bloquean la ruta Azure PostgreSQL.
- [x] Decisión práctica actual: no mantener dualidad como prioridad.

**Entregables Fase 1:**
- [~] Ambos caminos explorados; Azure PostgreSQL es la ruta activa.
- [x] Documento de decisión operativo: deploy usa Azure PostgreSQL Flexible Server.
- [x] Métricas de latencia reales desde Santo Domingo.

---

### FASE 2 — Modelo multi-sucursal (fundación)
> Estado 2026-06-09: MVP implementado en modelos/middleware/configuración. Falta
> validación operacional con segunda sucursal real.
> *Preparar la base de datos para operar con múltiples puntos de venta*

**2.1 App `sucursales`**
```
apps/sucursales/
    models.py      # Sucursal (codigo, nombre, direccion, activa, api_key)
    admin.py
    migrations/
```
- [x] Modelo `Sucursal` con código único (ej: `SD-001`, `STI-001`).
- [x] Management command `crear_sucursal` para inicialización.

**2.2 Agregar `sucursal` a modelos existentes**
- [x] `Venta.sucursal` — ForeignKey nullable para migración gradual.
- [x] `numero_venta` con prefijo de sucursal.
- [x] `ConfiguracionNegocio` soporta config por sucursal.
  - [x] `get_config()` filtra por sucursal actual.
  - [x] Cache key por sucursal (`config_negocio_{codigo}`).
- [x] `CierreCaja`, `Auditoria` — `sucursal` FK.
- [x] Inventario/CxC tienen FKs de sucursal donde aplica.

**2.3 Identificar la sucursal actual**
- [x] `settings.SUCURSAL_CODIGO = 'SD-001'` en settings base/dev.
- [x] `get_sucursal_actual()` helper que retorna la instancia basada en el setting.
- [x] Middleware que inyecta `request.sucursal`.

**Decisión clave: datos que NO llevan sucursal_id**
- Lote, MovimientoLote — son locales por naturaleza (el stock físico es de la sucursal)
- Producto, Categoría — son globales (datos maestros)
- Usuario — global (un SYSADMIN opera en todas las sucursales)

**Decisión clave: fuente de verdad de datos maestros**
- El **cloud es la fuente de verdad** para datos maestros: productos, categorías y clientes.
- La propagación normal de maestros es **cloud → sucursal** mediante `pull_maestros()` y `?desde=<cursor>`.
- En v1 **no existen eventos sucursal → cloud** para maestros (`CLIENTE_CREADO`, `CLIENTE_ACTUALIZADO`, `CATEGORIA_CREADA`, `CATEGORIA_ACTUALIZADA`, etc.).
- Si un admin/SYSADMIN edita maestros desde una pantalla local de sucursal, esa pantalla debe requerir conexión cloud y escribir **directamente en la API cloud** (`/api/v1/maestros/...`), no crear primero el registro local esperando que el sync lo empuje.
- Después de una escritura cloud exitosa, la sucursal puede:
  - ejecutar un pull inmediato de la tabla afectada, o
  - actualizar su copia local desde la respuesta cloud y dejar que el cursor lo confirme en el siguiente ciclo.
- Si no hay conexión cloud, la operación administrativa se bloquea con mensaje claro. No se crea un maestro local "pendiente" en v1.
- Excepción operativa permitida a futuro: "cliente temporal" para venta offline, sin crédito y sin efecto en cartera, hasta que se diseñe un flujo formal de reconciliación.
- Razón: evita conflictos bidireccionales, duplicados por identificadores naturales, y estados divergentes entre sucursales.

---

### FASE 3 — API REST (capa de comunicación)
> Estado 2026-06-09: MVP implementado. Existen DRF, endpoints de maestros,
> sync, reportes, CxC read-only, health, autenticación y pruebas API.
> *Exponer endpoints para que las sucursales se comuniquen con la nube*

**3.1 Instalar Django REST Framework**
- [x] `djangorestframework` instalado/configurado.
- [x] Agregado a `INSTALLED_APPS`.
- [x] Autenticación JWT para usuarios y token de sucursal para sync/maestros.

**3.2 Serializers para datos maestros**
```
api/serializers.py
    ProductoSerializer
    CategoriaSerializer
    ClienteSerializer
    ConfiguracionSerializer (parcial, solo campos relevantes)
```

**3.2b Serializers de cartera / CxC — [x lectura]**
```
apps/api/serializers/cuentas_por_cobrar.py
    CuentaPorCobrarSerializer          (lista)
    CuentaPorCobrarDetalleSerializer   (detalle: + cuotas[] + pagos[])
    CuotaCxCSerializer
    PagoCxCSerializer
    CarteraResumenSerializer           (forma de resumen/)
```
- [x] Exponer saldo por cliente y movimientos para portal cloud.
- [~] Aging avanzado por buckets queda diferido.
- [ ] Escritura de abonos desde portal no existe en v1; abonos nacen en POS y viajan por eventos.
- [x] No mezclar abonos CxC con ventas nuevas.
- **[Estado may 2026] Implementado (B15).** Serializers de lectura creados en `apps/api/serializers/cuentas_por_cobrar.py` y servidos por `CuentaPorCobrarViewSet`. En v1 el portal es **solo lectura**: no expone escritura de abonos (los abonos nacen en el POS y fluyen por eventos sucursal→cloud). El **aging consolidado** descrito arriba quedó diferido — el `resumen/` v1 da total/vencido global, no buckets. Decisiones futuras en `ROADMAP_PORTAL.md` → 5.H.

**3.3 Endpoints de datos maestros (cloud → sucursal)**
```
GET  /api/v1/maestros/productos/?desde=<timestamp>
GET  /api/v1/maestros/categorias/?desde=<timestamp>
GET  /api/v1/maestros/clientes/?desde=<timestamp>
```
- [x] Filtro `?desde=` para sync incremental.
- [x] Respuesta paginada/serializada para productos, categorías y clientes.

**3.4 Endpoints de eventos (sucursal → cloud)**
```
POST /api/v1/sync/eventos/         # Enviar batch de eventos
GET  /api/v1/sync/status/          # Estado de sincronización de la sucursal
```

- [x] Eventos de cartera incluidos en el contrato: `CXC_CREADA`, `CXC_PAGO_REGISTRADO`, `CXC_ANULADA`.
- [x] La nube reconstruye cartera por cliente desde eventos confirmados.
- [x] Eventos CxC idempotentes igual que ventas/cierres.

**3.5 Endpoints de reportes (cloud → dashboard)**
```
GET  /api/v1/reportes/ventas-hoy/            # Todas las sucursales
GET  /api/v1/reportes/ventas-hoy/<sucursal>/
GET  /api/v1/reportes/comparativo/?desde=&hasta=&agrupacion=&sucursal=
GET  /api/v1/reportes/ventas-por-cajero/?desde=&hasta=&sucursal=
GET  /api/v1/reportes/top-productos/?desde=&hasta=&sucursal=&limit=10
GET  /api/v1/reportes/cierre-consolidado/?fecha=&sucursal=
GET  /api/v1/reportes/inventario-consolidado/
```
- [x] Separar siempre ventas facturadas, ventas a credito y cobros CxC.
- **[Estado may 2026] Implementado.** La capa cloud de reportes vive en `apps/api/services/reporting.py`; los endpoints HTTP quedan en `apps/api/views/reportes.py`. `comparativo/` ya no devuelve placeholder `LOCAL`, agrupa por `Venta.sucursal`, incluye `metadata.legacy_ventas_omitidas` y separa CxC de ventas.
- `inventario-consolidado/` conserva contrato backward-compatible con `stock_por_sucursal: {"LOCAL": n}`. Multi-sucursal real de inventario queda pendiente de `INVENTARIO_SNAPSHOT`.
- **[Estado may 2026] Decisión tomada.** La cartera se sirve como recurso propio bajo **`/api/v1/cuentas-por-cobrar/`** (la propuesta del frontend), NO bajo `/reportes/`. Razón: es un recurso navegable (lista + detalle + agregados), no un reporte calculado; encaja con el router DRF y el patrón de maestros. `/reportes/cuentas-por-cobrar/` queda libre por si más adelante se quiere un reporte analítico distinto (p.ej. aging consolidado multi-sucursal con corte por fecha). El frontend ya apunta a esta ruta vía `src/lib/cxc.ts → BASE_PATH`.

---

### FASE 4 — Sistema de sincronización (sync engine)
> Estado 2026-06-09: MVP implementado para push de eventos y pull de maestros.
> Quedan abiertos proxy de escritura local de maestros, heartbeat/liveness e
> inventario multi-sucursal real.
> *El mecanismo que mueve datos entre sucursales y la nube*

**4.1 App `sync`**
```
apps/sync/
    models.py       # EventoSync, VersionMaestro, LogSync
    serializers.py  # Serialización de ventas/cierres para el payload
    engine.py       # SyncEngine: push eventos, pull maestros
    management/
        commands/
            sincronizar.py    # Management command para correr sync
```

**4.2 Modelo EventoSync — [x]**
```python
class EventoSync(models.Model):
    sucursal = ForeignKey(Sucursal)
    tipo_evento = CharField  # VENTA_CREADA, VENTA_ANULADA, CIERRE_CAJA, CXC_CREADA, CXC_PAGO_REGISTRADO, CXC_ANULADA
    payload = JSONField      # Datos serializados completos
    estado = CharField       # PENDIENTE → ENVIADO → CONFIRMADO / ERROR
    created_at = DateTimeField(auto_now_add)
    sent_at = DateTimeField(null)
    confirmed_at = DateTimeField(null)
    intentos = IntegerField(default=0)
    ultimo_error = TextField(blank)
    hash_payload = CharField  # Para deduplicación / idempotencia
```

**4.3 Generación de eventos (signals o explícito)**
- Opción A: `post_save` signal en Venta → crea EventoSync automáticamente
- [x] Opción B (recomendada): llamada explícita después del commit
  - Más control, más predecible, más fácil de debuggear
  - El evento se crea DESPUÉS de que la transacción local sea exitosa

**4.4 SyncEngine**
```python
class SyncEngine:
    def push_eventos(self):
        """Envía eventos PENDIENTE a la API cloud"""
        eventos = EventoSync.objects.filter(estado='PENDIENTE')[:50]  # batch de 50
        for evento in eventos:
            try:
                response = requests.post(CLOUD_API_URL, json=evento.payload, headers=auth)
                if response.status_code == 200:
                    evento.estado = 'CONFIRMADO'
                    evento.confirmed_at = now()
                else:
                    evento.estado = 'ERROR'
                    evento.ultimo_error = response.text
                    evento.intentos += 1
            except ConnectionError:
                evento.ultimo_error = 'Sin conexión'
                evento.intentos += 1
            evento.save()

    def pull_maestros(self):
        """Descarga cambios en datos maestros desde la nube"""
        ultima_sync = VersionMaestro.objects.get(tabla='productos').version
        response = requests.get(f'{CLOUD_API_URL}/maestros/productos/?desde={ultima_sync}')
        for producto_data in response.json():
            Producto.objects.update_or_create(
                sku=producto_data['sku'],
                defaults=producto_data
            )

    def check_connection(self):
        """Ping a la API cloud — usado para bloquear edición de maestros offline"""
        try:
            r = requests.get(f'{CLOUD_API_URL}/ping/', timeout=3)
            return r.status_code == 200
        except:
            return False
```

**4.5 Management command `sincronizar` — [x]**
```bash
# Correr como scheduled task cada 60 segundos
python manage.py sincronizar --settings=config.settings_sucursal
```
- Loop: push_eventos → pull_maestros → sleep
- Configurable: intervalo, batch size, max retries
- Logging a `sync.log`

**4.6 Decorador `@requiere_conexion_cloud`**
- [x] Decorador existe en `apps/sync/decorators.py`.
- [x] Verifica conexión cloud antes de permitir la operación.
- [~] Bloquea con mensaje si no hay conexión.
- [ ] Las vistas locales todavía deben cambiar a POST/PATCH/DELETE contra la API cloud y refrescar copia local.
- **Pendiente de implementación:** el decorador por sí solo no debe considerarse suficiente. Las vistas locales de maestros deben cambiar de "guardar ORM local" a "POST/PATCH/DELETE contra API cloud + refrescar copia local". Hasta cerrar ese cambio, crear/editar cliente o categoría desde el POS local no se replica al portal cloud y debe tratarse como dato local/legacy.

---

### FASE 5 — Portal administrativo cloud (React dashboard)
> Estado 2026-06-09: MVP parcial. Backend API y deploy backend dev existen. El
> portal React está documentado en `ROADMAP_PORTAL.md`; Static Web Apps dev sigue
> bloqueado/no aplicado por restricciones de Azure for Students.
> *Interfaz web para el dueño del negocio*

**5.1 Proyecto React separado**
```
pos-cloud-dashboard/
    src/
        components/
            Dashboard.jsx        # Vista principal con KPIs
            VentasPorSucursal.jsx
            ComparativoChart.jsx
            ProductosEditor.jsx  # CRUD datos maestros
            SucursalesStatus.jsx # Estado de conexión de cada sucursal
        services/
            api.js              # Calls a la API REST Django
            auth.js             # Login/token
        App.jsx
    package.json
```

**5.2 Funcionalidades del portal**
- [x] Login con credenciales Django/JWT.
- [x] Dashboard: ventas del día por sucursal con indicador de última sincronización.
- [~] Comparativo entre sucursales: backend listo; frontend queda según `ROADMAP_PORTAL.md`.
- [x] Gestión de productos: crear, editar precio, activar/desactivar.
- [x] Gestión de categorías y clientes.
- [~] Cuentas por cobrar: cartera read-only lista; aging/alertas avanzadas quedan diferidas.
- [x] Estado de sucursales: última sincronización, eventos pendientes, alertas.
- [x] Reportes consolidados consumen capa cloud query-based (`apps/api/services/reporting.py`), no `ReporteManager` local.

**5.3 Deployment**
- [x] Decision base: **Docker + Azure Container Apps** para backend Django.
- [x] Azure App Service Linux sin Docker queda como plan B para demo rapida, no como arquitectura objetivo.
- [ ] Azure Static Web Apps para el portal React sigue pendiente/bloqueado en dev.
- [x] Azure PostgreSQL Flexible Server como DB cloud.
- [x] Azure Container Registry para imagenes Docker del backend.
- [x] Azure Container Apps Job para migraciones y comandos operativos.
- [x] Terraform como fuente de verdad de infraestructura (`infra/azure/`), con remote state dev.
- [x] Build backend: GitHub Actions → Docker image taggeada con SHA → ACR → Container Apps.
- [ ] Build/deploy frontend a Azure Static Web Apps pendiente.
- [x] Roadmap operativo detallado: `docs/ROADMAP_DEPLOY_AZURE.md`.

**5.4 Autenticación**
- [x] JWT tokens (`djangorestframework-simplejwt`).
- [x] El React guarda token en memory según contrato del portal.
- [x] Refresh token flow.

---

### FASE 6 — Producción multi-sucursal (integración completa)
> Estado 2026-06-09: pendiente. La base técnica existe en dev, pero falta
> staging, piloto operativo y empaquetado multi-sucursal.
> *Todo funcionando junto para un cliente real*

**6.1 Prueba piloto con Royal Plast**
- [x] Sucursal principal: la actual (ya funcionando).
- [ ] "Sucursal" de prueba: segunda PC en la misma red o en la casa del dueño.
- [ ] Validar: sync funciona, reportes consolidan, maestros se propagan.

**6.2 Paquete de instalación multi-sucursal**
- [ ] Actualizar `instalar.bat` para preguntar: ¿sucursal nueva o nodo cloud?
- [~] `crear_config_inicial` acepta código de sucursal.
- [ ] `registrar_servicio.bat` incluye sync como segundo servicio.

**6.3 Monitoreo**
- [x] Endpoint `/api/v1/health/` existe para salud de API/DB.
- [x] Dashboard cloud usa `ultima_sync`: verde (sync reciente), amarillo (>5 min), rojo (>30 min).
- [ ] Heartbeat/liveness explícito de sucursal.
- [ ] Alerta por email si una sucursal lleva >1 hora sin sincronizar.

---

### FASE 7+ — Horizonte futuro

> **Visión de producto / priorización de valor:** ver
> [VISION_PRODUCTO_2026.md](VISION_PRODUCTO_2026.md). Argumenta que la línea de
> mayor valor a futuro es *Cumplimiento & Contabilidad* (e-CF completo → captura
> de facturas de compra por foto+IA → libros DGII 606/607/608 → inteligencia de
> ITBIS), con un reloj legal: e-CF obligatorio para pequeños contribuyentes el
> 15-nov-2026.

**Facturación electrónica (e-CF / DGII)**
- Flag `modulo_ecf` ya existe en ConfiguracionNegocio
- Integración con Alanube o DGMax (PSFEs ya investigados)
- Cada sucursal con su propio RNC + certificado .p12

**SaaS multi-tenant**
- `django-tenants` con schema-per-tenant en PostgreSQL
- Cada "empresa" (Royal Plast, Auto Parts, etc.) es un tenant
- Portal de administración central
- Deployment: Docker → Azure Container Apps

**App móvil para el dueño**
- React Native o PWA del portal cloud
- Notificaciones push de ventas, alertas de stock, cierre de caja
- Alertas de credito: cuotas vencidas, clientes sobre limite, cobros del dia

---

## Branches de Git propuestos

> Estado 2026-06-09: referencia historica de secuenciacion. La consolidacion
> actual ya vive en `features/cloud-dashboard`/`develop` segun el flujo vigente;
> no crear estos branches antiguos si la funcionalidad ya esta integrada.

| Branch | Propósito | Base | Dependencias |
|--------|-----------|------|--------------|
| `main` | Producción local estable | — | — |
| `develop` | Desarrollo activo (Fase 0) | main | — |
| `feature/azure-postgres` | Fase 1: BD en Neon/Azure PG | develop | — |
| `feature/azure-sql` | Fase 1: BD en Azure SQL | develop | mssql-django |
| `feature/multi-sucursal` | Fase 2: Modelo sucursales | develop | — |
| `feature/api-rest` | Fase 3: DRF endpoints | multi-sucursal | djangorestframework |
| `feature/sync-engine` | Fase 4: Sistema sync | api-rest | requests |
| `feature/cloud-dashboard` | Fase 5: React portal | api-rest | React, Recharts |

---

## Principios de ejecución

1. **Cada fase es independiente y funcional.** El sistema sigue operando local después de cada fase. La nube es un add-on, no un requerimiento.

2. **Incremental siempre.** No reescribir — agregar. Los modelos existentes reciben campos nuevos con `null=True` para migración sin fricción.

3. **Un chat por fase/módulo.** Handoff documents al final de cada sesión para mantener contexto.

4. **Probar antes de avanzar.** Cada fase tiene criterios de aceptación claros antes de empezar la siguiente.

5. **Seguridad desde el inicio.** Contraseñas en variables de entorno, SSL obligatorio, tokens con expiración, API keys por sucursal.
