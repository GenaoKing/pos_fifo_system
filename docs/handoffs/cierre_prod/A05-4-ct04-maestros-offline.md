# Handoff A05.4 — publicación de CT-04 maestros offline

Estado: **LISTO PARA REVISIÓN E INTEGRACIÓN AISLADA**. Fecha: **2026-09-17**.
Propietario: Codex. No autoriza mover `develop`, hacer push, desplegar, tocar
cloud, ejecutar resoluciones contra un cliente ni incorporar A06/C04/SUS-014.

## Base y alcance

- Base exacta: `integration/cierre-prod-A05-C03@695b36cda73f67c5d4c448fc65fcb9c6762ae1d4`.
- Rama/worktree: `codex/cierre-prod-A05-4` en
  `C:\Proyectos\pos_fifo_system_a05_4`.
- A05.4 publica CT-04. Formaliza el transporte A05.3 ya implementado y fija
  schemas, permisos y fixture para C04/C05; no implementa el listado ni las
  decisiones humanas, que siguen siendo A06.
- SUS-014 postcondición (`claude/cierre-prod-SUS014-postcondicion@9c36dd9`) no
  se mezcló: permanece un candidato independiente.

## Contrato publicado

- Fuente única: `docs/handoffs/cierre_prod/CONTRATOS.md`, sección CT-04.
- Fixture canónico: `docs/handoffs/cierre_prod/fixtures/ct04_master_offline_v1.json`.
- Transporte implementado: `POST /api/v1/sync/mutaciones-maestro/` usa y ahora
  responde `schema_version: "master.mutation.v1"`. Un ACK sin esa versión o
  sin `detalle[]` válido vuelve a `PENDIENTE` para reintento; no se interpreta
  como confirmación.
- Listado reservado A06: `GET /api/v1/maestros/conflictos/` con
  `master.conflict-list.v1` / `master.conflict.v1`, cursor opaco, página máxima
  de 100 y filtros por estado, entidad y sucursal.
- Resolución reservada A06: `POST
  /api/v1/maestros/conflictos/{mutacion_id}/resolver/` con
  `master.conflict-resolution.v1`, acción, motivo obligatorio (1–500) y CAS.
  `CONSERVAR_CLOUD` y `APLICAR_LOCAL` requieren `productos.editar` o
  `categorias.editar`, según la entidad; la lectura requiere el permiso `ver`
  del mismo ámbito de sucursal.
- Semántica congelada: `PENDIENTE`/`ENVIANDO` siguen vendibles;
  `CONFLICTO` bloquea nuevas ventas (y categoría conflictiva bloquea sus
  productos); `RECHAZADA` queda durable, visible y sin reintento automático.
  No hay last-write-wins ni inferencia por el texto del error.

## Pruebas y gates

Entorno aislado: `.venv` propio, Python 3.11.14, settings
`config.settings_development`, ejecución serial Windows.

```powershell
$env:DB_NAME = 'pos_a054_20260917'
.\.venv\Scripts\python.exe manage.py test apps.sync.tests.test_ct04_contrato apps.api.tests.test_mutaciones_maestro_a053 apps.sync.tests.test_mutaciones_maestro_a053 apps.productos.tests.test_mutaciones_maestro_a052a apps.productos.tests.test_identidad_a05 apps.sync.tests.test_identidad_maestros_a05 apps.sync.tests.test_engine --settings=config.settings_development --noinput --verbosity 1
# 54 OK
```

- `python -m pip check`: sin requisitos rotos.
- `migrate` desde cero sobre `pos_a054_20260917`: todas las migraciones hasta
  `sync.0014` aplicaron correctamente.
- `check`: 0 issues; `makemigrations --check --dry-run`: sin cambios;
  `compileall -q apps/api apps/sync`, JSON del fixture y `git diff --check`:
  PASS.
- La BD de test y `pos_a054_20260917` se destruyeron al terminar; una consulta
  exacta posterior no devolvió ninguno de esos nombres.

Los warnings de HTTP 400/409, conflictos de adopción, imágenes `DummyResponse`
y configuración de sucursal corresponden a rutas negativas afirmadas por las
pruebas y no causaron fallos.

## Límites y siguiente gate

No hay migración ni cambio de datos. Revertir el código es seguro antes de que
un consumidor adopte CT-04; una vez integrado, conservar el fixture/versionado
y corregir hacia adelante. La siguiente etapa es revisión read-only del diff y
una integración local serial sobre un candidato nuevo; solo A06 puede crear los
endpoints reservados y solo C04 puede integrar React contra ellos. `develop`
permanece en `fffd02b`.
