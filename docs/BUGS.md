# Bugs y hallazgos

## Resueltos

### Dashboard de reportes mostraba KPIs de hoy en cero durante la noche

- Fecha de hallazgo: 2026-05-16 23:10 America/Santo_Domingo.
- Sintoma: `Ultimas Ventas` mostraba ventas recientes, pero `Ventas Hoy`, `Efectivo`, `Transferencia` y `Cajeros Hoy` salian en cero.
- Causa raiz: `apps/reportes/views.py` usaba `timezone.now().date()`, que toma la fecha UTC. En Santo Domingo, a las 11:00 p. m. locales ya era el dia siguiente en UTC, asi que el dashboard consultaba 2026-05-17 aunque las ventas pertenecian al 2026-05-16 local.
- Correccion: cambiar calculos de "hoy" a `timezone.localdate()` y el reloj del servidor a `timezone.localtime()`.
- Evidencia local: a `2026-05-17 03:13 UTC`, la fecha local correcta era `2026-05-16`; habia 3 ventas completadas por `$49,200.00`, con `$15,800.00` en efectivo y `$33,400.00` en transferencia.

### Portal cloud: header del dashboard mostraba el dia anterior

- Fecha de hallazgo: 2026-06-12.
- Sintoma: el encabezado de `/dashboard` en el portal (`pos-cloud-dashboard`) mostraba la fecha del dia anterior ("jueves, 11 de junio" siendo viernes 12).
- Causa raiz: contraparte frontend del bug de timezone de arriba. `/api/v1/reportes/ventas-hoy/` devuelve `fecha` como date-only (`YYYY-MM-DD`) y `formatDateLong` en `src/lib/format.ts` la parseaba con `new Date(...)`, que interpreta date-only como medianoche UTC; en Santo Domingo (UTC-4) retrocede un dia. `formatDate` ya manejaba el caso pero `formatDateLong` no.
- Correccion: helper compartido `parseLocalDate` en `src/lib/format.ts` que construye fechas date-only en hora local; usado por `formatDate` y `formatDateLong`. Tests en `src/lib/format.test.ts`.

### Referencias antiguas a `Venta.cajero` tras refactor a `Venta.usuario`

- Sintoma: algunos reportes/API seguian usando `cajero`/`cajero_id`, aunque el modelo `Venta` ya no tiene ese campo.
- Impacto: la API de metricas para cajeras podia fallar con `FieldError`; el filtro por cajero en reportes on-demand no aplicaba contra el campo real; el template admin intentaba leer `venta.cajero`.
- Correccion: usar `usuario`, `usuario_id` y `venta.usuario` en reportes.

## Pendientes

- Ninguno confirmado en esta revision.
