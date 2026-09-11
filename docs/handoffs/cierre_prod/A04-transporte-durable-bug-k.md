# Handoff A04 — transporte durable y reparación de BUG-K

- Estado: **LISTO PARA REVISIÓN / NO INTEGRADO**
- Fecha: **2026-09-11**
- Propietario: **A / Codex**
- Base exacta: `develop@0b4fb7c9b716177a83550f258014dedeed0e27b6`
- Rama aislada: `codex/cierre-prod-A04`
- Commit de implementación: `be15ea0`
- Contratos: extensión A04 del batch de eventos +
  `sync.reconciliation.v1` + `sync.bug_k.repair_plan.v1`

No hubo merge a `develop`, push, deploy, lectura de tenants/clientes, reset de
cursores ni reparación de datos. Todas las escrituras ejecutadas fueron sobre
las BDs PostgreSQL locales desechables de desarrollo/tests.

## Resultado entregado

1. `EventoSync` tiene `event_id` estable y unicidad de UUID/hash por sucursal.
   El claim persiste `EN_VUELO`, lease y vencimiento antes del HTTP; solo el
   dueño puede aplicar el ACK y otro proceso recupera un lease vencido.
2. Los pulls guardan fallos/dependencias en `DiferidoSync`. El cursor avanza
   únicamente si el elemento aplicó o quedó durablemente almacenado; un fallo
   de almacenamiento congela la frontera. Aplicación + resolución comparten
   transacción y un pendiente vuelve el ciclo `PARCIAL`.
3. El receptor deduplica dentro de la sucursal autenticada y exige equivalencia
   de identidad, tipo, hash y contenido. Una identidad ocupada con otro payload,
   un payload de otra sucursal o un `IntegrityError` real queda `ERROR`, nunca
   `DUPLICADO` terminal. Los lookups de venta/CxC se acotan por sucursal.
4. El health check usa timeout, reintentos y backoff configurables. El decorador
   legacy ya no exige un ping para una edición local; conserva su firma hasta
   que A05 conecte la outbox de maestros.
5. La sonda autenticada `POST /api/v1/sync/reconciliacion-eventos/` es read-only
   y distingue duplicado real, divergencia de contenido, hecho sin ledger,
   falso ACK, no verificable y ámbito inválido.
6. `reparar_bug_k` produce por defecto un plan dry-run exacto y scopeado. Solo
   reencola `FALSO_ACK`/`HECHO_SIN_EVENTO_CLOUD`; ejecutar exige archivo,
   digest, revalidación inmediata y precondiciones locales, y registra evidencia
   antes/después. Repetir el mismo plan se rechaza.

La migración aditiva es `sync.0011_transporte_durable_bug_k`. El contrato
completo y la compatibilidad legacy están en `CONTRATOS.md`.

## Hallazgo y superficie entregada

- Hallazgo cubierto: `BUG-K`. El bloque entrega detección y plan de reparación;
  no afirma que la historia operativa ya esté reparada.
- Transporte y persistencia: `apps/sync/models.py`, `apps/sync/engine.py`,
  `apps/sync/decorators.py`, `apps/sync/admin.py` y
  `apps/sync/migrations/0011_transporte_durable_bug_k.py`.
- Reconciliación/reparación: `apps/sync/reconciliation.py` y
  `apps/sync/management/commands/reparar_bug_k.py`.
- Receptor/contrato HTTP: `apps/api/serializers/sync.py`,
  `apps/api/views/sync.py` y `apps/api/views/sync_urls.py`.
- Configuración: `config/settings.py`.
- Cobertura nueva: `apps/sync/tests/test_transporte_durable.py`,
  `apps/sync/tests/test_reparar_bug_k.py`,
  `apps/api/tests/test_sync_reconciliacion_bug_k.py` y casos adicionales en
  `apps/sync/tests/test_pull_keyset.py`.

## Aislamiento usado

- Worktree: `C:\Proyectos\pos_fifo_system_cierre_codex`.
- Venv: `C:\Proyectos\.venvs\pos_cierre_codex_a01_20260910`.
- Runtime: Windows, CPython 3.11.14, Django 5.2.17.
- BD local: `pos_cierre_codex_a04`; test default:
  `test_pos_cierre_codex_a04`.
- Suite completa: namespace tenant efímero `a04final`; no reutiliza las BDs
  de Claude, staging ni clientes.

## Comandos reproducibles

Ejecutar en el worktree A04 y en serie:

```powershell
$a04Python = 'C:\Proyectos\.venvs\pos_cierre_codex_a01_20260910\Scripts\python.exe'
$env:DB_NAME = 'pos_cierre_codex_a04'

& $a04Python manage.py check --settings=config.settings_development
& $a04Python manage.py makemigrations --check --dry-run --settings=config.settings_development
& $a04Python -m pip check
& $a04Python -m compileall -q apps/sync apps/api config

$env:TENANT_TEST_DB_NAMESPACE = 'a04matrix'
& $a04Python manage.py test apps.sync apps.api.tests.test_sync_auditoria apps.api.tests.test_sync_extended apps.api.tests.test_sync_resumen apps.api.tests.test_sync_roles apps.api.tests.test_sync_venta_sin_usuario apps.api.tests.test_maestros_keyset apps.api.tests.test_sync_reconciliacion_bug_k --settings=config.settings_development --noinput

$env:TENANT_TEST_DB_NAMESPACE = 'a04final'
& $a04Python manage.py test --settings=config.settings_development --noinput
```

## Evidencia automatizada

```text
manage.py check --settings=config.settings_development
System check identified no issues (0 silenced).

manage.py makemigrations --check --dry-run --settings=config.settings_development
No changes detected

python -m pip check
No broken requirements found.

python -m compileall -q apps/sync apps/api config
OK

migrate desde una pos_cierre_codex_a04 vacía
sync.0001 ... sync.0011_transporte_durable_bug_k: OK

matriz focal A04 + regresión sync/API
Found 215 test(s); Ran 215 tests in 51.391s; OK

suite Django completa, serial, con default + tres BDs tenant nuevas
Found 1364 test(s); Ran 1364 tests in 469.692s; OK
Las cuatro BDs de test fueron destruidas al finalizar.

git diff --check
OK; solo avisos LF/CRLF del checkout Windows
```

La matriz A04 incluye dos procesos concurrentes, crash/restart, lease vencido,
ACK perdido después de commit remoto, replay duplicado, hash/UUID cross-branch,
error real, cursor y fallo al persistir, rollback entre aplicar/resolver, cola
pendiente, health cold-start, sonda BUG-K y plan aplicado/repetido.

No se repitieron pytest e-CF, la imagen Linux/cloud, frontend ni HTTP entre
procesos reales: A04 no modifica esas superficies. La compatibilidad HTTP de
versiones y el smoke del artefacto exacto pertenecen a la matriz A09/CI; el
acceso a una instalación real exige autorización separada.

## Rollback

Antes de integrar no hay rollback operativo: basta con no consumir la rama A04;
`develop` permanece en la base declarada. Si la revisión exige correcciones, se
hacen en esta rama y se repite la matriz.

Después de una integración futura, revertir el commit/merge de código mediante
un commit compensatorio. No bajar `sync.0011` ni borrar `EventoSync`,
`DiferidoSync` o `LogSync` en una instalación: la migración agrega identidad y
evidencia durable. Cualquier rollback de BD en A09 requiere backup verificado,
ventana autorizada y una migración forward que preserve historial.

## Revisión solicitada a Claude

Revisar el diff exacto `0b4fb7c..be15ea0`, con énfasis en:

- ownership/compatibilidad de `apps/api/views/sync.py` con sus consumidores;
- orden cloud nuevo/POS viejo y POS nuevo/cloud viejo;
- migración de constraints scopeados y recuperación de leases;
- que la clasificación BUG-K nunca proponga reenvío para divergencias;
- que no se haya adelantado CT-04 ni escrito superficies C.

Si la revisión encuentra un bloqueador, devolver archivo/línea, reproducción y
test esperado. Integrar requiere un merge explícito posterior y repetir la
matriz combinada; este handoff no autoriza un merge automático.

## Riesgos y siguiente gate

- A05 aún no existe. Quitar el ping evita el falso guard online, pero la edición
  durable de maestros no está lista; **no desplegar este estado intermedio**.
- `_pull_legacy` se conserva para la flota vieja. La matriz HTTP real con más de
  200 productos y compatibilidad de versiones queda en A09 sobre el RC exacto.
- No se ejecutó ni siquiera el dry-run contra clientes: necesita acceso y
  autorización operativa. A09 debe generar/revisar la lista por tienda antes de
  cualquier `--ejecutar`.
- CT-03 sigue pendiente de integración final; A04 usa defaults seguros y
  variables de proceso, sin editar archivos propiedad de C.

Siguiente acción: Claude revisa `be15ea0`; luego el integrador decide el merge y
repite pruebas combinadas. A05 no comenzó y no debe integrarse como efecto
secundario de esta entrega.
