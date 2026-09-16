# Handoff A05.2a — cola local durable de maestros

Fecha: **2026-09-15**
Estado: **LISTO PARA REVISIÓN; sin merge, push, despliegue ni datos reales.**

## Base y aislamiento

- Base verificada: `integration/cierre-prod-A05-C03@614d741693f373686dffac43e53eaae6f6c6f509`.
- Worktree: `C:\Proyectos\pos_fifo_system_a05_2a`.
- Rama: `codex/cierre-prod-A05-2a`.
- La base estaba limpia antes de crear el worktree. No se modificaron
  `develop`, `integration`, staging ni producción.

## Entregado

`sync.MutacionMaestro` es una outbox local distinta de `EventoSync` para
Producto y Categoria. Cada fila contiene UUID idempotente, entidad/PK,
operación, delta, revisión base y resultante, actor/username snapshot,
sucursal/código snapshot, tenant técnico, estado, intentos, error/conflicto,
timestamps y `auditoria_event_id` CT-01.

Las seis mutaciones de catálogo del POS (crear, editar y activar/desactivar
Producto/Categoria) pasan por `apps/productos/services.py`. El servicio exige
sucursal resuelta y reevalúa CT-02 con esa sucursal antes de
`transaction.atomic(using=...)`. Dentro del bloque persiste maestro,
`audit.event.v1` y cola. Un fallo de auditoría/cola revierte todo; un replay del
mismo UUID devuelve la propuesta ya creada sin duplicar maestro ni auditoría.
Las respuestas JSON incluyen UUID, estado y marca de replay.

Se preserva A05.1: SKU y `Producto.origen_cloud_id` no se modifican por el
servicio; siguen vigentes `MASTER_*` en pull. No hay referencias a `EventoSync`,
hechos financieros, receptor cloud ni HTTP externo.

Un pendiente continúa vendible. Un `CONFLICTO` de Producto o de su Categoria
se ve en el admin de sync (solo lectura) y queda excluido por
`productos_vendibles()` y `Producto.es_vendible`. El último gate de venta da
motivo explícito, sin cambiar ventas ni historial existentes.

## Migración

- Nueva y única: `sync.0012_mutacion_maestro_local`.
- Aditiva: crea `sync_mutacionmaestro` e índices por
  `(sucursal, estado, creado_at)` y `(entidad, entidad_id, estado)`.
- Sin backfill ni cambios a `EventoSync`, `DiferidoSync`, Producto, Categoria
  o datos operativos.

## Evidencia serial y aislada

Entorno: `C:\Proyectos\pos_fifo_system_cierre_codex\.venv\Scripts\python.exe`,
`config.settings_development`, DB temporal `pos_a052a_20260915` y namespace
`a052a_20260915`. No se usó Azure ni una instalación de cliente.

```powershell
$py = 'C:\Proyectos\pos_fifo_system_cierre_codex\.venv\Scripts\python.exe'
$env:DB_NAME = 'pos_a052a_20260915'
$env:TENANT_TEST_DB_NAMESPACE = 'a052a_20260915'
& $py manage.py migrate --settings=config.settings_development --noinput
```

Resultado: migración limpia completa, incluida `sync.0012_mutacion_maestro_local`.

```powershell
& $py manage.py test `
  apps.productos.tests.test_auditoria_productos `
  apps.productos.tests.test_identidad_a05 `
  apps.productos.tests.test_categoria_sin_clasificar `
  apps.productos.tests.test_miniaturas `
  apps.productos.tests.test_mutaciones_maestro_a052a `
  apps.sync.tests.test_engine `
  apps.sync.tests.test_identidad_maestros_a05 `
  apps.sync.tests.test_auditoria_sync `
  apps.sync.tests.test_outbox_transaccional `
  apps.auditoria.tests.test_ct01 `
  apps.ventas.tests.test_ventas_service `
  --settings=config.settings_development --keepdb --noinput --verbosity 0
```

Resultado final: **165 OK en 194.035 s**. Los **10** casos A05.2a cubren
UUID/replay, atomicidad inducida, actor/sucursal, CT-02, delta/revisión, SKU
A05.1, pendiente vendible, conflictos de Producto/Categoria, auditoría y la
ausencia de `EventoSync`.

```powershell
& $py manage.py test `
  apps.permisos.tests.test_auditoria_permisos `
  apps.permisos.tests.test_engine `
  apps.sync.tests.test_transporte_durable `
  --settings=config.settings_development --keepdb --noinput --verbosity 1
```

Resultado: **51 OK en 92.112 s**. El runner reutilizado emitió
`suscripciones.W001` porque su DB `--keepdb` quedó sin el seed de módulos tras
las suites; es un warning conocido del fixture reutilizable, no un cambio A05.
`manage.py check` sobre la DB migrada limpia terminó sin incidencias.

```powershell
& $py -m pip check
& $py manage.py check --settings=config.settings_development
& $py manage.py makemigrations --check --dry-run --settings=config.settings_development
& $py -m compileall -q apps config
git diff --check
```

Todos correctos: dependencias sin roturas, check sin incidencias, sin cambios
de modelo pendientes, compilación correcta y diff sin whitespace roto.

## Límites y bloqueos deliberados

- No hay receptor cloud, push/retry/ACK, pull protector, CAS remoto,
  revocación al reconectar ni resolución humana: pertenecen a A05.3/A06.
- No se publica CT-04, API externa, schema de frontend ni cambios C04/C05.
- Clientes y otros maestros no se incluyen. La carga/eliminación binaria de
  imágenes conserva su canal de media y no se serializa en esta cola.
- El admin de `MutacionMaestro` solo consulta trazabilidad; no permite añadir,
  editar, resolver ni borrar propuestas.

El siguiente bloqueo es A05.3: autenticación de sucursal, RBAC cloud vigente,
CAS de `revision_base`, ACK incierto y transición auditada. CT-04 sigue
reservado.

## Rollback

Antes de integrar, basta no consumir la rama. Después de una integración futura,
usar `git revert <SHA-de-este-handoff>`; no hacer reset ni mover `develop`.
La migración es aditiva y conserva evidencia durable. No bajar `sync.0012` ni
borrar filas de cola en una instalación: un rollback de schema/datos exige
backup verificado, ventana autorizada y una migración forward que preserve
trazabilidad.
