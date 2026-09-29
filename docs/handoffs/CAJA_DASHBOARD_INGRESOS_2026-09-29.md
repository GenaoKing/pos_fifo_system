# Caja local y dashboard cloud — 2026-09-29

Implementación local en `codex/caja-dashboard-ingresos`, base `8213ba5`.
Portal en `C:/Proyectos/pos_cloud_dashboard_ingresos`, rama
`codex/dashboard-ingresos`, base `origin/staging@d66e8d9`.

## Comportamiento

- Apertura de caja tiene su propio formulario y campo numérico identificado,
  con autocompletado desactivado. Las credenciales de autorización tienen otro
  formulario y solo existen en el DOM mientras el modal está abierto (`x-if`).
- `GET /api/v1/reportes/ventas-hoy/` conserva los campos anteriores y agrega
  `cobros_ventas`, `cobros_cxc` e `ingresos_totales` a los totales; también
  devuelve los dos nuevos importes por sucursal (CxC ya existía allí).
- Cobros de ventas: efectivo, transferencia y tarjeta de ventas completadas
  del día local, según el contrato existente de reportes. Incluye la inicial;
  excluye el pago con método CREDITO. CxC: abonos APLICADOS con fecha de pago
  del día local, aunque la venta sea anterior. Ambos respetan las sucursales
  activas del negocio resuelto.
- Ingresos totales = cobros de ventas + cobros CxC. No representa utilidad ni
  saldo físico de caja; no incluye apertura ni reposiciones de menudo.
- Portal: cuatro tarjetas financieras (ingresos, cobros de ventas, cobros CxC,
  ventas facturadas), más transacciones y estado de sucursales. Los importes
  dependen de los datos sincronizados. Si la API anterior no entrega los
  campos nuevos, muestra «— / No disponible», nunca un cero inventado.

## Validación local

- Django/PostgreSQL: 63 pruebas aprobadas de `test_reportes_cloud`,
  `test_reportes_scope_negocio`, `test_reportes_permisos` y
  `apps.caja.tests.test_auditoria_caja`. BD descartable
  `test_pos_dashboard_ingresos_20260929`, destruida por el runner.
- Reejecución del test de alcance con la aserción nueva de ingresos: 1/1,
  BD `test_pos_dashboard_ingresos_scope_20260929`, destruida.
- Chromium/Playwright con el template Django renderizado y Alpine local:
  contraseña ausente al abrir caja; formularios independientes; navegación
  Enter usuario → contraseña → motivo; cancelar retira la contraseña del DOM
  y conserva el fondo digitado. Sin errores JavaScript. No usa un perfil con
  contraseñas guardadas: no acredita todas las extensiones de autocompletado.
- La evidencia de pruebas/build del portal queda en su handoff del mismo nombre.

No requiere migraciones. Publicar backend antes del portal. Sin push,
despliegue, reinicio de servicios ni cambios a datos operativos. Los cambios
preexistentes de productos, `.codex/` y análisis de costes quedan fuera.
