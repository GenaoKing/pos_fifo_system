# Integración A06 + C04 + C05 — conflictos, maestros y venta auditada

Estado: **CANDIDATO LOCAL VALIDADO; SIN PUBLICAR**. Fecha: **2026-09-18**.

Esta rama reúne los candidatos backend de A06 y C05 sobre una base ya
integrada de A05/CT-04, y deja enlazado el consumidor C04 del repositorio
frontend. No mueve `develop`, no publica, no despliega y no consulta ni cambia
datos operativos.

## Topología exacta

- Base: `integration/cierre-prod-A05-C03@609c98f44efd595e39bb9df128d09e57781c3dfc`.
- A06: `codex/cierre-prod-A06@37a48de972e8ea995cca62e986a965ef22d58098`,
  integrado como `d965fc1`.
- C05 selectores CT-04: `claude/cierre-prod-C05-ct04-selectores@0208a56c245ff6af0d8b5444795d76c07785fffb`,
  integrado como `6380c44`.
- C05 p6 CT-01: el corte inicial `508a66ed4bfbc269cab09c8e786c5d261b7f2eb2`
  se integró como `4125e64`; el cierre completo
  `claude/cierre-prod-C05-p6-auditoria-ct01@bbb524658dd94931e751e363f676ceeac13c3436`
  se integró como `2db885e`.
- Handoff C04 actualizado con `7817c0a` y `0c7a070`, aplicados como
  `de927dc` y `6ac4a3e`.
- Frontend separado, no fusionado por Git con este repositorio:
  `pos-cloud-dashboard`, `claude/cierre-prod-C04@e319058`. Es descendiente de
  `928964c`, el correctivo CT-04 de runtime/schema.
- Resultado de las integraciones de código: `integration/cierre-prod-A06-C04-C05@2db885e`;
  este handoff y sus ajustes son descendientes documentales de esa punta.

## Alcance integrado

1. A06 publica listado y resolución CAS de conflictos de maestro, ledger de
   resolución, auditoría CT-01, pull de decisiones al POS y estado operativo
   efectivo de Producto/Categoría. Las migraciones aditivas son
   `productos.0014` y `sync.0015`/`0016`.
2. C05 acredita COT-009: producto/categoría inactivos o en `CONFLICTO` no son
   seleccionables en venta/cotización; `PENDIENTE` sigue vendible. Corrige la
   asimetría de accesos rápidos de categoría.
3. C05 p6 migra doce acciones de dominio al contrato `audit.event.v1` dentro de
   sus transacciones: venta creada/anulada, descuento autorizado, ajuste de
   inventario, seis acciones CxC y cotización creada/convertida. El evento de
   dominio `VENTA_CREADA` sigue siendo un riel distinto. Las claves de
   idempotencia opacas se guardan en `idempotencia_key`, nunca en
   `correlacion_id` (`UUIDField`).
4. C04 muestra conflictos y resuelve contra las rutas A06 con schemas
   validados en runtime. También hace visible el caso de producto activo bajo
   categoría inactiva; es distinto del eje `MutacionMaestro.CONFLICTO`.

## Gates realizados

En `C:/Proyectos/pos_fifo_system_integracion_a06_c04_c05`, con
`C:/Proyectos/pos_fifo_system_a06/.venv/Scripts/python.exe` (Django 5.2.17) y
una base de prueba efímera `test_pos_fifo_a06_c04_c05_p6`:

```powershell
& 'C:\Proyectos\pos_fifo_system_a06\.venv\Scripts\python.exe' manage.py test `
  apps.api.tests.test_maestros_conflictos_a06 `
  apps.api.tests.test_sync_resoluciones_a06 `
  apps.api.tests.test_categoria_viewset `
  apps.api.tests.test_producto_stub_anti_clobber `
  apps.api.tests.test_producto_viewset `
  apps.api.tests.test_sync_auditoria `
  apps.sync.tests.test_resoluciones_conflicto_a06 `
  apps.sync.tests.test_ct04_contrato `
  apps.sync.tests.test_mutaciones_maestro_a053 `
  apps.productos.tests.test_mutaciones_maestro_a052a `
  apps.productos.tests.test_identidad_a05 `
  apps.productos.tests.test_a06_estado_operativo `
  apps.sync.tests.test_identidad_maestros_a05 `
  apps.sync.tests.test_engine `
  apps.ventas.tests.test_accesos_rapidos_pos `
  apps.cotizaciones.tests.test_cotizacion_hardening `
  apps.ventas.tests.test_ventas_service `
  apps.ventas.tests.test_idempotencia_venta `
  apps.ventas.tests.test_descuento_autorizacion `
  apps.cotizaciones.tests.test_auditoria_cotizaciones `
  apps.cuentas_por_cobrar.tests.test_ct01_productores `
  apps.cuentas_por_cobrar.tests.test_auditoria_cxc `
  apps.inventario.tests.test_ct01_ajuste `
  apps.inventario.tests.test_auditoria_inventario `
  apps.auditoria `
  apps.sync.tests.test_outbox_transaccional `
  --settings=config.settings_development --noinput
# Ran 363 tests in 151.057s — OK; la base de prueba fue destruida.

& 'C:\Proyectos\pos_fifo_system_a06\.venv\Scripts\python.exe' manage.py check --settings=config.settings_development
# System check: 0 issues.

& 'C:\Proyectos\pos_fifo_system_a06\.venv\Scripts\python.exe' manage.py makemigrations --check --dry-run --settings=config.settings_development
# No changes detected.
```

`makemigrations` emitió solamente el aviso esperado de que la base aislada
nombrada para la comprobación no existe; no hizo ninguna escritura. El runner
de pruebas sí creó y destruyó su propia base, aplicando las migraciones. También
pasaron `compileall` de las apps afectadas y `git diff --check`.

En `C:/Proyectos/pos_cloud_dashboard_cierre_claude@e319058`:

```powershell
npm run build
npm run lint
npm run test:run
# 16 archivos, 109 tests — OK.
```

## Gates que siguen abiertos

- Hacer un smoke HTTP autenticado del portal C04 contra este backend: GET de
  conflictos con permisos de lectura y POST de resolución con permiso de
  edición, comprobando el schema real, cursor, 403 por permiso y 409 por CAS.
  Los tests de ambos repos prueban el contrato, pero todavía no son esa prueba
  de dos procesos contra la ruta viva.
- Antes de cualquier entorno real, ejecutar el preflight read-only de
  `OPS-PRO-007` y revisar el conteo de productos activos bajo categorías
  inactivas. Este candidato no autoriza corregir datos ni aplicar migraciones
  fuera de una base desechable.
- Los productores CT-01 fuera del alcance p6 continúan explícitos en
  `C05-p6-auditoria-ct01.md`: edición de venta, edición de compra y la
  conversión de cotización por venta que aún emite `EDITAR` legacy. No fueron
  absorbidos por esta integración.

## Secuencia recomendada

1. Cerrar el smoke HTTP C04/A06 y revisión read-only de este candidato.
2. Si ambos pasan, usar A07 para reconciliar el inventario vivo de hallazgos y
   evidencias, sin iniciar A08 ni promover esta rama.
3. Mantener los productores CT-01 fuera de p6 con su dueño actual hasta que se
   delimite el próximo bloque; el siguiente no debe inferirse de esta rama.

## Reversión

Esta rama local se descarta sin afectar `develop`. Si una futura integración
necesita revertirse, usar `git revert -m 1` sobre los merges `2db885e`,
`4125e64`, `6380c44` y `d965fc1` en orden inverso, tras decidir por separado
el manejo de las migraciones ya aplicadas en un entorno real.
