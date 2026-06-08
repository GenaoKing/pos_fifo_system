# Bugs y hallazgos

## Resueltos

### Dashboard de reportes mostraba KPIs de hoy en cero durante la noche

- Fecha de hallazgo: 2026-05-16 23:10 America/Santo_Domingo.
- Sintoma: `Ultimas Ventas` mostraba ventas recientes, pero `Ventas Hoy`, `Efectivo`, `Transferencia` y `Cajeros Hoy` salian en cero.
- Causa raiz: `apps/reportes/views.py` usaba `timezone.now().date()`, que toma la fecha UTC. En Santo Domingo, a las 11:00 p. m. locales ya era el dia siguiente en UTC, asi que el dashboard consultaba 2026-05-17 aunque las ventas pertenecian al 2026-05-16 local.
- Correccion: cambiar calculos de "hoy" a `timezone.localdate()` y el reloj del servidor a `timezone.localtime()`.
- Evidencia local: a `2026-05-17 03:13 UTC`, la fecha local correcta era `2026-05-16`; habia 3 ventas completadas por `$49,200.00`, con `$15,800.00` en efectivo y `$33,400.00` en transferencia.

### Referencias antiguas a `Venta.cajero` tras refactor a `Venta.usuario`

- Sintoma: algunos reportes/API seguian usando `cajero`/`cajero_id`, aunque el modelo `Venta` ya no tiene ese campo.
- Impacto: la API de metricas para cajeras podia fallar con `FieldError`; el filtro por cajero en reportes on-demand no aplicaba contra el campo real; el template admin intentaba leer `venta.cajero`.
- Correccion: usar `usuario`, `usuario_id` y `venta.usuario` en reportes.

## Pendientes

- Ninguno confirmado en esta revision.
