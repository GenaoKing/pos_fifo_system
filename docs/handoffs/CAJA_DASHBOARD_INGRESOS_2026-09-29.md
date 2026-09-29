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

## Actualización autorizada de staging y reparación de reportes

El usuario autorizó portal/API de staging y la instalación local de QA.
PR backend #37 y portal #10; producción y tiendas quedan fuera.

En QA se reprodujo HTTP 200 con errores JavaScript: un comentario `{# ... #}`
multilínea dejaba `<script>` literal y escondía el nodo `reportes-cajeros`
al parser HTML. Se cambió a `{% comment %}`; las 2 pruebas de serialización
segura pasan, incluyendo parseo real del nodo con un username hostil.
Al recuperar la pantalla apareció un segundo defecto: Alpine envolvía las
instancias Chart.js en proxies y el animador no se retiraba al destruirlos.
Las instancias ahora viven fuera del estado reactivo y se destruyen antes de
retirar el resultado. Chromium verificó consultas de ventas, top, inventario y
cajeros con HTTP 200/success, sin errores JavaScript.

Se respaldó `pos_stage_qa` y se copiaron únicamente los tres archivos de
runtime afectados, preservando configuración/datos/servicio sync y el patch
SYSADMIN anterior. Los originales coincidían con staging `8213ba5`.
Respaldo y manifiesto: `C:/Proyectos/pos_fifo_staging_qa/patch_20260929_caja_reportes/`.
Dump SHA-256: `7040942e776f819b02a8cdac1c16b838f03740ddef58fdcee1472087627618a3`;
índice `pg_restore --list` válido. El servicio web `POSFifoStagingQA` se reinició
mediante elevación Windows; el sync permanece Running. La instalación sigue
siendo el paquete `5f3e89b` con patches explícitos, no un paquete completo nuevo.

No requiere migraciones. Publicar backend antes del portal. Los cambios
preexistentes de productos, `.codex/` y análisis de costes quedan fuera.

## Despliegue verificado — 2026-09-29 16:01 UTC

- Backend: PR [#37](https://github.com/GenaoKing/pos_fifo_system/pull/37),
  staging `d24f24e897e06cbcfb850ebbd5c1dc7cf99a84ee`.
  [CI/CD 36592002904](https://github.com/GenaoKing/pos_fifo_system/actions/runs/36592002904)
  SUCCESS: gate físico 5 pruebas, suite Django 1666 y e-CF 72; health y ciclo
  manual de notificaciones aprobados. Las corridas duplicadas del PR se
  cancelaron; no se saltó el gate completo del workflow de despliegue.
- Imagen activa: `pos-fifo-backend@sha256:bc4cb4f7f1aa760e35d882aebeda0febaac22c007e2683af4a4f65eeca1e52a4`;
  revisión `posfifo-staging-api--0000021`. Imagen anterior para rollback:
  `sha256:83803cb6350c4b6d55fba3b7b7fd40919394f3a40067c010a383bb40dafa9ce1`.
  El job de notificaciones conserva trigger `Manual` y comparte la imagen nueva.
- Portal: PR [#10](https://github.com/GenaoKing/pos-cloud-dashboard/pull/10),
  staging `cbd04787d6bbc47815e9f963452102d296f8cbdc`.
  [deploy 36594308088](https://github.com/GenaoKing/pos-cloud-dashboard/actions/runs/36594308088)
  y [CI 36594308060](https://github.com/GenaoKing/pos-cloud-dashboard/actions/runs/36594308060)
  SUCCESS; suite frontend 146 pruebas.
- Chromium real contra el portal staging: login QA, cuatro tarjetas con
  importes de la API, recarga de `/dashboard` y cero errores JavaScript.
  Se reconciliaron `QA-PC-01` local/cloud: ventas y cobros de ventas `1140.00`,
  CxC `0.00`, ingresos `1140.00`. No se crearon ventas ni abonos para el smoke.
- QA local: 8 consultas/generaciones HTTP 200/success, cierre diario con PDF
  completo (no cierra un turno ni crea ventas), cambios rápidos entre gráficas,
  cero errores JavaScript y cero gráficas tras limpiar. `verificar_instalacion`:
  sana, sin migraciones pendientes. `verificar_sync --dias=7`: sin pérdida,
  ocho eventos confirmados. Web y sync Running.
- Evidencia protegida local: `patch_20260929_caja_reportes/smoke_local.json`,
  `smoke_cloud.json`, `smoke_cloud.png`, `local_totals.json` y `manifest.json`.
  El aviso del portal corresponde a la sucursal de laboratorio `01`, sin sync
  desde hace cuatro días; `QA-PC-01` aparece En línea.

Producción y tiendas no se modificaron. No se promovió `develop` ni se declara
cerrado el gate operativo de producción por estas pruebas de staging.
