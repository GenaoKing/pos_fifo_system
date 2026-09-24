# Handoff A05.3 — receptor cloud de mutaciones offline de maestros

Estado: **LISTO PARA REVISIÓN E INTEGRACIÓN AISLADA**. Fecha: **2026-09-17**.
Propietario: Codex. No autoriza publicar CT-04, mover `develop`, hacer push,
desplegar ni ejecutar el flujo contra una instalación o tenant operativo.

## Base y alcance

- Base exacta: `integration/cierre-prod-A05-C03@9d33248c7379d5b957e3c678c71aa2859ea0485c`.
- Rama: `codex/cierre-prod-A05-3`, worktree aislado
  `C:\Proyectos\pos_fifo_system_a05_3`.
- A05.3 completa el receptor cloud y el emisor local de la cola duradera A05.2a
  para `Producto` y `Categoria`. `MutacionMaestro` sigue separada de
  `EventoSync`: no representa ni transporta hechos financieros.

## Contrato implementado

- Envelope `master.mutation.v1` por `POST /api/v1/sync/mutaciones-maestro/`,
  autenticado por token de sucursal. Cada propuesta preserva UUID, entidad local,
  operación, delta, actor, identidad cloud y revisión base.
- El cloud vuelve a resolver actor activo, negocio/sucursal y permiso CT-02
  vigente; aplica CAS contra la revisión autoritativa del cloud. Las respuestas
  terminales son `CONFIRMADA`, `DUPLICADA`, `CONFLICTO` y `RECHAZADA`.
- El ledger cloud usa el UUID único como garantía de idempotencia. Dos requests
  concurrentes con el mismo UUID producen una sola mutación/auditoría y el
  segundo recibe `DUPLICADA`; reutilizar el UUID con otro contenido recibe
  `MASTER_MUTATION_ID_CONFLICT`.
- Cada decisión cloud se registra mediante CT-01 con correlación igual al UUID:
  `sync.maestro.aplicado`, `.conflicto` o `.rechazado`.
- El emisor reclama una propuesta con lease persistido, conserva orden por
  entidad y dependencia categoría → producto, reintenta ACK/HTTP inciertos con
  el mismo UUID y no reintenta conflictos o rechazos terminales. Un ACK válido
  sella identidad/revisión cloud local y rebasa la siguiente propuesta de la
  misma entidad.
- `Producto.revision_cloud` y `Categoria.revision_cloud` mantienen la revisión
  remota separada de `fecha_modificacion` local, que se modifica al aplicar pull.

## Migraciones

- `productos.0013_categoria_producto_revision_cloud`: columnas aditivas
  `revision_cloud` para CAS remoto; sin backfill.
- `sync.0013_mutacion_maestro_recepcion_cloud`: ACK, lease e índice de envío.
- `sync.0014_mutacion_maestro_cloud_categoria`: identidad cloud de la categoría
  referida por un producto.
- Migración desde la BD aislada `pos_a053_20260916`: `sync.0014 ... OK`.
  No se tocaron BDs operativas ni tenants reales.

## Validación ejecutada

Entorno: settings `config.settings_development`, intérprete
`pos_fifo_system_cierre_codex\.venv\Scripts\python.exe`, DB/namespace
aislados `pos_a053_20260916` / `a053_20260916`, ejecución serial Windows.

```powershell
python manage.py test apps.api.tests.test_mutaciones_maestro_a053 \
  apps.sync.tests.test_mutaciones_maestro_a053 apps.sync.tests.test_engine \
  apps.productos.tests.test_mutaciones_maestro_a052a \
  --settings=config.settings_development --keepdb --noinput --verbosity 1
# 39 OK

python manage.py migrate --settings=config.settings_development --noinput
# sync.0014 ... OK
python manage.py makemigrations --check --dry-run --settings=config.settings_development
# No changes detected
python manage.py check --settings=config.settings_development
# 0 issues
python -m pip check
# No broken requirements found
python -m compileall -q apps/api apps/productos apps/sync
git diff --check
```

La ejecución con `--keepdb` informó `suscripciones.W001` porque la base de
prueba preservada se vació al terminar el `TransactionTestCase` concurrente; no
es un fallo de A05.3. El `manage.py check` ejecutado aparte quedó en 0 issues.
Los avisos de imagen `DummyResponse` y respuestas HTTP 400/409 provienen de
regresiones existentes que prueban rutas de error y no fallaron la suite.

## Archivos y límites

- Dominio/receptor: `apps/sync/master_mutations.py`, `apps/api/serializers/sync.py`,
  `apps/api/views/sync.py`, `apps/api/views/sync_urls.py`.
- Emisor/ledger: `apps/sync/engine.py`, `apps/sync/models.py`,
  `apps/sync/management/commands/sincronizar.py`.
- Catálogo: `apps/productos/models.py`, `apps/productos/services.py` y las
  migraciones indicadas; los mapas de `api`, `sync` y `productos` se actualizaron.
- A05.4 publica CT-04; A06 expone resolución/UI de conflictos; C04 y selectores
  comerciales dependientes siguen expresamente fuera de alcance. No se editó el
  registro central de contratos ni documentos compartidos mientras Claude
  mantenía su deuda documental.

## Rollback y siguiente paso

No revertir migraciones en una instalación con nuevas escrituras. Si la
integración local revelara un defecto, preferir corrección hacia adelante y
conservar los ledgers/auditorías; un revert de código solo es seguro antes de
usar el endpoint. El siguiente gate es revisión del diff, merge serial en la
rama de integración y regresión conjunta; CT-04 no se publica hasta que ese
candidato quede validado.
