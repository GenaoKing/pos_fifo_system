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
- Resultado de las integraciones de código: `integration/cierre-prod-A06-C04-C05@dfb1dfc`;
  además de A06/C05, incorpora el cableado read-only de SUS-014 y el fixture/test
  rebasados de CT-03. Sigue siendo un candidato local, no `develop`.

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

### Smoke HTTP C04 ↔ A06 (evidencia de dos procesos)

Claude reportó el **2026-09-18** un smoke adicional de 23/23 casos contra el
backend vivo de esta rama en `a1ac443`, usando el mismo venv Django 5.2.17, una
BD desechable `pos_fifo_smoke_a06`, sesión Django + CSRF y un cliente HTTP
autenticado. Se comprobó después que `a1ac443` es ancestro de esta punta y que
las rutas `apps/api`, `apps/sync` y `apps/productos` ejercidas no cambiaron entre
ambas puntas. La BD se eliminó y el servidor se apagó.

- `GET /api/v1/maestros/conflictos/`: envelope `master.conflict-list.v1`, items
  `master.conflict.v1`, datos de entidad/sucursal y ambas acciones esperadas.
- Aislamiento entre dos tenants; cursor opaco página a página; `page_size=101`
  y cursor inválido devuelven 400.
- Resolución: schema inválido y motivo vacío devuelven 400; revisión CAS vieja,
  409; `CONSERVAR_CLOUD` y `APLICAR_LOCAL`, 200 y remoción del listado pendiente.
- Un usuario con solo `productos.ver` lista (200), no resuelve (403) y no altera
  el conflicto.

El smoke **no** minteó un JWT tenant-aware: en la BD single-DB desechable no se
sembró el control-plane requerido y el transporte validado fue sesión + CSRF.
Eso no reduce la cobertura del contrato que consume C04, pero conserva el JWT
tenant-aware como cobertura propia de tenancy/auth, no como evidencia de este
gate de conflictos.

## Actualización de integración — 2026-09-18

El reencuentro de los dos bloques se efectuó sin mover `develop`:

- `codex/cierre-prod-CT03-sync@9c6a698` se integró como
  `integration/cierre-prod-A06-C04-C05@6c74d163b41d32a3e3a94ec15c19b90260d6bcde`.
- `claude/cierre-prod-C04-p6-volumen@a241b7c` se integró como
  `claude/cierre-prod-C04@f0e6c2d0ad579b43ec5d445a3ff4f1e43da6ebd1`.
- La matriz combinada backend pasó **398 tests en 200.501 s**, y en la punta
  frontend integrada pasaron build, lint y **120 tests**. El detalle y límites
  de evidencia constan en `INTEGRACION-CT03-C04P6-2026-09-18.md`.

## Gates que siguen abiertos

- La aceptación técnica de ambas puntas locales fue dada el 2026-09-18. C04 p6
  tiene evidencia a volumen y CT-03 incorpora SUS-007/CFG-007; ese hecho no
  autoriza mover `develop`, publicar ni desplegar.
- Antes de cualquier entorno real, ejecutar el preflight read-only de
  `OPS-PRO-007` y revisar el conteo de productos activos bajo categorías
  inactivas. Este candidato no autoriza corregir datos ni aplicar migraciones
  fuera de una base desechable.
- Los productores CT-01 fuera del alcance p6 continúan explícitos en
  `C05-p6-auditoria-ct01.md`: edición de venta, edición de compra y la
  conversión de cotización por venta que aún emite `EDITAR` legacy. No fueron
  absorbidos por esta integración.

## Actualización C04 p5.2 backend — 2026-09-18

Se avanzó por fast-forward, sin conflicto y sin mover `develop`, desde el
checkpoint A07 `6d8b195` hasta
`integration/cierre-prod-A06-C04-C05@50fe6582b9a43598c6c8e19bf5b056741f543dd4`.
La fuente fue `codex/cierre-prod-C04-p5-backend-admin@50fe658` (código
`5790ec2`): rutas portal para usuarios, sucursales y configuración, servicios
auditados CT-01, RBAC global y baja de Membership en cloud.

En este worktree integrado pasaron `apps.api.tests.test_administracion_portal`
(**7 pruebas, OK; 1 skip físico esperado**), `manage.py check` (0 issues) y
`makemigrations --check --dry-run` (sin cambios; el aviso de la base aislada
inexistente no escribe nada). La prueba física ya acreditada por la fuente usó
dos PostgreSQL descartables y JWT tenant-aware: alta en tenant y revocación de
Membership en control plane.

El consumidor Claude/C04 p5.2 ya se fusionó sin conflicto como
`integration/cierre-prod-C04-admin@26e9bac`; conserva p5.2 y excluye la rama
solapada p5-admin. Se repitieron lint, build y 132 tests sobre ese SHA. El
smoke real de contrato de su handoff es sesión+CSRF y no una prueba de
navegador/JWT; Playwright queda ahora desbloqueado como bloque separado.
Esta integración sigue siendo local: no autoriza push, despliegue ni cambios de
datos operativos.

## Secuencia recomendada

1. Conservar los SHAs y límites de transporte/evidencia de las integraciones
   locales CT-03, C04 p6 y C04 p5.2 ya aceptadas.
2. C04 p5.2 ya está integrado; su siguiente bloque es C04 p5.3 E2E de
   navegador desde `integration/cierre-prod-C04-admin@26e9bac`, sin mocks.
3. A07 quedó revalidado. Codex puede iniciar A08.1 en un worktree aislado sin
   promover ni operar datos reales; C06 y los preflights siguen siendo gates
   separados.
4. Mantener los productores CT-01 fuera de p6 con su dueño actual hasta que se
   delimite un bloque independiente.

## Reversión

Esta rama local se descarta sin afectar `develop`. Si una futura integración
necesita revertirse, usar `git revert -m 1` sobre los merges `2db885e`,
`4125e64`, `6380c44` y `d965fc1` en orden inverso, tras decidir por separado
el manejo de las migraciones ya aplicadas en un entorno real.
