# Handoff A05.1 — identidad y adopción de maestros

Estado: **ENTREGADO EN RAMA / PENDIENTE DE REVISIÓN E INTEGRACIÓN.** A05.1 está
implementado y validado en `codex/cierre-prod-A05`. No se implementaron A05.2,
A05.3 ni A05.4; CT-04 continúa reservado y sin publicar. Fecha de cierre:
**2026-09-14**.

## Base, rama y commits

- Repositorio/worktree: `C:\Proyectos\pos_fifo_system_cierre_codex`.
- Rama: `codex/cierre-prod-A05`.
- Base exacta verificada antes de modificar:
  `develop@fffd02bd384d8078928fa24761c550df34b711b5`.
- Commits de código:
  1. `cfbd507dc46895f395bc4bd34e50e01833d047d5` — identidad estable e
     inmutabilidad de Producto.
  2. `6bda67979173cb776c6ce9ab7f17a16ae5771e65` — adopción id-first,
     conflictos explícitos y pruebas.
  3. `03f90b593819bae54ad171df6a31b4476204133b` — rechazo de identidad
     cruzada en la categoría referida por Producto.
- El commit que contiene este documento es únicamente de handoff.
- No hubo push, despliegue, merge ni modificación de `develop`, staging,
  producción o datos operativos.

## Resultado e invariantes

### Identidad

- `Producto.origen_cloud_id` es un `PositiveIntegerField` nullable, único,
  indexado y no editable. Representa el `id` entero que ya traen los payloads
  actuales; no cambia el schema de transporte.
- La identidad es estable dentro de cada base tenant. La topología
  DB-per-tenant da el ámbito: dos tenants pueden adoptar el mismo ID sin
  compartir filas.
- `Categoria` y `Cliente` conservan sus campos `origen_cloud_id` existentes;
  no se agregó otra identidad ni lógica paralela.
- `origen_sucursal` sigue siendo procedencia histórica de BUG-H.
  `pendiente_revision` sigue siendo un gate de revisión. Ninguno se usa como
  identidad y ninguno se limpia durante la adopción.

### Adopción y replay

Orden único de resolución para Producto, Categoría y Cliente:

1. Buscar por `origen_cloud_id`.
2. Solo en la primera bajada, buscar por clave natural exacta.
3. Si la coincidencia natural es única y no está sellada, adoptar la fila
   existente y grabar el ID cloud.
4. Si no existe ninguna fila, crearla con el ID cloud.
5. Una colisión o ambigüedad se guarda en `DiferidoSync`; no se elige la
   primera fila ni se crea un duplicado silencioso.

Para Producto la clave natural de adopción es el SKU exacto. Después del alta
el SKU es inmutable. Un renombre o cambio posterior de cualquier otro atributo
cloud actualiza la misma fila por identidad. La categoría referida por el
producto también se resuelve ID-first; el nombre exacto solo puede adoptar una
categoría no sellada y nunca puede cruzar dos identidades.

La inmutabilidad se cubre en tres superficies:

- `Producto.save()` impide cambiar SKU y permite únicamente la transición
  `origen_cloud_id: NULL -> id`; después el ID también queda inmutable.
- `ProductoQuerySet.update()` y `bulk_update()` rechazan mutaciones masivas de
  ambos campos.
- Admin y las superficies de actualización existentes mantienen el SKU de la
  fila. El API puede recibir un SKU legacy durante PATCH, lo ignora y aplica
  los demás atributos.

### Conflictos explícitos

`DiferidoSync.ultimo_error` conserva códigos estables y el payload completo:

- `MASTER_CLOUD_ID_AMBIGUOUS`
- `MASTER_NATURAL_AMBIGUOUS`
- `MASTER_NATURAL_ID_CONFLICT`
- `MASTER_SKU_IMMUTABLE`
- `MASTER_SKU_INVALID`

El cursor solo avanza sobre el ítem si este fue aplicado o quedó persistido en
la cola durable de A04. El replay mantiene el diferido en `PENDIENTE` y aumenta
`intentos` mientras la causa siga presente.

## Archivos

- `apps/productos/models.py`
- `apps/productos/admin.py`
- `apps/productos/views.py`
- `apps/productos/migrations/0012_producto_origen_cloud_id.py`
- `apps/productos/tests/test_identidad_a05.py`
- `apps/productos/AGENTS.md`
- `apps/sync/engine.py`
- `apps/sync/tests/test_identidad_maestros_a05.py`
- `apps/sync/AGENTS.md`
- `apps/tenancy/tests/test_multidb_isolation.py`
- `apps/tenancy/AGENTS.md`
- `docs/handoffs/cierre_prod/A05-identidad-maestros.md`

No se modificaron `apps/api`, `apps/clientes`, C04, selectores comerciales ni
frontend React.

## Migración

`productos.0012_producto_origen_cloud_id` depende de
`productos.0011_producto_imagen_origen_url_producto_origen_sucursal_and_more` y
solo ejecuta `AddField`. Es aditiva y no hace backfill: todas las filas actuales
empiezan en `NULL` y el primer pull las adopta por SKU exacto.

Se verificó el grafo desde cero en una base PostgreSQL física aislada:

- alias: `tnt_a051_graph_20260914`;
- base temporal exacta: `test_a051_graph_20260914`;
- `TEST.MIGRATE=True`, tenancy forzado y contexto apuntando al mismo alias;
- creación mediante `connection.creation.create_test_db()`;
- aserciones: `productos.0012` figura en `MigrationRecorder` y la tabla
  `productos` contiene `origen_cloud_id`;
- resultado: `A05 tenant graph OK: productos.0012 aplicada y columna presente`;
- la base temporal se destruyó al terminar.

`makemigrations --check --dry-run` terminó con `No changes detected`.

## Pruebas ejecutadas

Entorno: `.venv` propio, CPython 3.11.14, dependencias instaladas desde
`requirements-dev.txt` con `--require-hashes`, PostgreSQL y namespaces A05
aislados. Todas las pruebas Django fueron seriales en Windows.

### Suite Django amplia

```powershell
$env:DB_NAME='pos_cierre_codex_a051_full2'
$djangoTests = @(rg --files apps -g 'test_*.py' |
  Where-Object { $_ -notlike 'apps\facturacion_electronica\*' } |
  ForEach-Object { ($_ -replace '\\','.') -replace '\.py$','' })
.\.venv\Scripts\python.exe manage.py test @djangoTests `
  --settings=config.settings_development --noinput --verbosity 1
```

Resultado sobre los dos commits principales, antes del endurecimiento final
acotado a categoría: **1461 tests, OK, skipped=4, 822.932 s**.

### Regresión final de superficies afectadas

```powershell
$env:DB_NAME='pos_cierre_codex_a051_finalsync'
.\.venv\Scripts\python.exe manage.py test `
  apps.sync `
  apps.productos.tests.test_identidad_a05 `
  apps.api.tests.test_producto_viewset `
  --settings=config.settings_development --noinput --verbosity 1
```

Resultado en `03f90b5`: **190 tests, OK, 47.370 s**. Incluye adopción inicial,
renombre, replay, colisión de SKU, identidad cruzada de Producto/Categoría,
ambigüedad natural, SKU inmutable y preservación de BUG-H.

### Dos bases tenant físicas

```powershell
$env:DB_NAME='pos_cierre_codex_a051_multidb2'
$env:TENANT_TEST_DB_NAMESPACE='a051_codex_20260914_final2'
.\.venv\Scripts\python.exe manage.py test `
  apps.tenancy.tests.test_multidb_isolation `
  --settings=config.settings_development --noinput --verbosity 1
```

Resultado: **2 tests, OK, 0.646 s**. Creó y destruyó dos bases físicas; el mismo
ID/SKU cloud se adoptó independientemente y los nombres no cruzaron tenants.

### e-CF separado

```powershell
$env:DB_NAME='pos_cierre_codex_a051_ecf'
.\.venv\Scripts\python.exe -m pytest `
  apps\facturacion_electronica\tests `
  --ds=config.settings_development -q
```

Resultado: **72 passed, 25.08 s**.

### Checks

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe manage.py check --settings=config.settings_development
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run `
  --settings=config.settings_development
.\.venv\Scripts\python.exe -m compileall -q apps\productos apps\sync apps\tenancy
git diff --check develop...HEAD
```

Resultado: sin dependencias rotas, sin problemas de system check, sin
migraciones faltantes, compilación limpia y sin errores de whitespace.

## Revisión de las dos ramas de agentes

Se revisaron tips, estado, diffs, pruebas y compatibilidad sin fusionar nada.

### Codex — `codex/cierre-prod-A05`

- Base común: `fffd02b`.
- Tres commits de código, sin archivos ajenos y con las pruebas anteriores
  verdes.
- Candidata para revisión e integración aislada de A05.1.

### Claude — `claude/cierre-prod-C03-parte2@29bd07f`

- Worktree limpio; cinco commits sobre la misma base.
- 13 archivos, limitados a `configuracion`, `suscripciones`, pruebas y handoff.
- Suite focal repetida durante esta revisión:

```powershell
$env:DB_NAME='pos_cierre_claude_review_20260914'
.\.venv\Scripts\python.exe manage.py test `
  apps.configuracion apps.suscripciones `
  apps.api.tests.test_suscripciones_admin `
  --settings=config.settings_development --noinput --verbosity 1
```

Resultado: **226 tests, OK, skipped=2, 31.439 s**. También pasaron
`manage.py check`, `makemigrations configuracion suscripciones --check` y
`git diff --check`.

El análisis de archivos arrojó **cero archivos solapados** con A05 y
`git merge-tree` no produjo marcadores de conflicto. Eso evita conflictos
textuales, pero no resuelve las dependencias semánticas siguientes:

1. La rama cambia `ConfiguracionNegocio.load()` de auto-bootstrap a lectura
   pura. `apps/sync/engine.py::_pull_configuracion()` todavía debe usar
   `bootstrap(sucursal=...)`; el endpoint cloud en `apps/api/views/sync.py`
   debe convertir ausencia de config en lista vacía. Sin esos consumidores, el
   primer pull puede fallar.
2. `apps/usuarios/views.py` conserva dos llamadas directas y redundantes a
   `get_config()` durante errores de login. Sin configuración inicial, pueden
   impedir que el context processor seguro llegue a renderizar.
3. SUS-014 entrega `validar_plan_slug()` pero no está cableado antes de las dos
   escrituras de `bootstrap_tenant --plan`.
4. La propia evidencia de Claude registra que su discovery completo queda en
   **1471 tests: 1 failure + 234 errors** porque fixtures de ventas,
   inventario, CxC, reportes y Producto dependían del auto-create. Una base de
   integración no puede declarar verde ese resultado.
5. Hallazgo adicional de esta revisión: `ConfiguracionNegocio.bootstrap()` sin
   sucursal hace `get_or_create(pk=1)`. Si sobrevive una única configuración
   legacy con PK distinto de 1, puede crear una segunda fila en vez de devolver
   la existente, contrario a la semántica legacy declarada. Conviene corregirlo
   y agregar el caso antes de integrar.

### Recomendación de merge

**No fusionar todavía `claude/cierre-prod-C03-parte2` ni usarla como nueva base.**
Sería una base más avanzada en cantidad de cambios, pero conocida como roja y
con consumidores productivos pendientes. No se realizó ningún merge.

Secuencia recomendada:

1. Revisar e integrar A05.1 de forma aislada si se acepta este handoff.
2. Corregir en una rama de integración los cinco puntos anteriores y repetir
   la suite completa combinada, multi-BD y e-CF.
3. Publicar CT-03 solo después de que código y consumidores coincidan con el
   fixture definitivo.
4. Recién entonces iniciar A05.2 sobre esa base integrada. A05.2/A05.3 siguen
   dependiendo del resolutor efectivo de CT-03; A05.4 publicará CT-04.

## Riesgos y rollback

- La identidad usa la PK entera actual del payload. Es estable dentro del
  tenant, no pretende ser un UUID global entre bases.
- `unique=True` impide dos productos con la misma identidad en una base. La
  lógica de aplicación impide cambiarla, pero SQL directo puede eludir las
  guardas de modelo/manager y requiere disciplina operativa.
- La carrera entre dos creadores sigue protegida por las restricciones únicas;
  cualquier `IntegrityError` termina como diferido genérico, no como alta
  duplicada.
- La cola A04 solo tiene `PENDIENTE/RESUELTO`; los códigos `MASTER_*` dan
  diagnóstico explícito, pero la UI y la resolución humana pertenecen a A06.
- La comparación natural es deliberadamente exacta y sensible a la semántica
  actual de la BD. Variantes de mayúsculas o espacios no se fusionan por
  aproximación.
- Rollback preferido antes de despliegue: revertir los tres commits de código.
  La migración es aditiva y puede dejarse aplicada durante un rollback de
  aplicación. Revertir schema solo es seguro después de comprobar que ningún
  binario depende de la columna; no hay backfill que deshacer.

## Deltas propuestos para ledgers compartidos — no aplicados

No se editaron `PROJECT_STATUS.md`, `ESTADO_AUDITORIAS.md`,
`TODO_AUDITORIAS.md`, `PLAN_CIERRE_PROD.md`, `CIERRE_PROD_CODEX.md` ni
`CONTRATOS.md` mientras Claude trabaja. Al integrar se propone:

- marcar A05.1 como implementado/validado con los tres SHAs de código y este
  handoff;
- mantener CT-04 en `PENDIENTE/reservado`, aclarando que A05.1 no publica su
  schema;
- mantener CT-03 sin publicar hasta corregir consumidores y obtener suite
  combinada verde;
- registrar el hallazgo de `bootstrap(pk=1)` y los consumidores pendientes en
  el handoff/cola de C03, no resolverlos silenciosamente dentro de A05.1.
