# Handoff — residual de fixtures CFG-012 r2 (caja, cotizaciones, permisos)

Estado: **REVISION.** Rehace el residual explícito de fixtures CFG-012
—que en la primera pasada
([C03-residual-fixtures.md](C03-residual-fixtures.md)) se hizo sobre
`claude/cierre-prod-C03-parte2-fixes`— pero ahora **directamente sobre la
integración A05-C03**, para que quien integre no tenga que rescatar hunks de
una rama con historia divergente. No amplía C03 con hallazgos CFG/SUS nuevos,
no toca A05 ni publica CT-03, no integra a `develop`. Fecha: **2026-09-15**.
Agente: Claude (Opus 4.8).

## Base / SHAs

- **Base obligatoria:** `integration/cierre-prod-A05-C03` @
  `355de7c45979f220b71fb01171abcf460a56e95d`
  (`docs(handoff): registrar reparacion del grafo tenant`). Verificado con
  `git rev-parse` antes de crear la rama; `355de7c` es ancestro de HEAD.
- **Worktree nuevo y aislado:**
  `C:/Proyectos/pos_fifo_system_cierre_claude_residual_r2`, creado con
  `git worktree add -b claude/cierre-prod-C03-residual-fixtures-r2 <ruta>
  355de7c...`. No comparte BD ni interfiere con
  `pos_fifo_system_cierre_codex` (integración A05-C03 en curso) ni con
  `pos_fifo_system_cierre_claude_residual` (r1, @ `3d29772`).
- **Rama nueva:** `claude/cierre-prod-C03-residual-fixtures-r2`.
- **La r1 (`3d29772`) se usó SOLO como referencia de hunks.** No se rebaseó
  ni fusionó su historia (parte de una base más vieja: su diff contra `355de7c`
  incluye borrados de A05, `apps/sync/engine.py`, migraciones tenant, etc., que
  NO pertenecen a este alcance). Los 4 hunks de test son idénticos a los de r1;
  el mecanismo es el mismo que el resto de la serie CFG-012.
- **SHA del fix (4 archivos de test):**
  `60f1c4f6187220f44e38543a930f3e58ab730572`.
- **SHA final de la entrega:** el HEAD de
  `claude/cierre-prod-C03-residual-fixtures-r2` tras el commit de este handoff
  (descendiente directo de `355de7c` y de `60f1c4f`) — se reporta en la entrega
  y se obtiene con `git rev-parse claude/cierre-prod-C03-residual-fixtures-r2`.
- **No publicado a `origin`, sin merge a `develop`/staging/prod, sin datos
  operativos tocados.** Working tree limpio al cierre.

### Entorno de pruebas

- Intérprete: venv aislado
  `C:/Proyectos/.venvs/pos_cierre_claude_c03_20260910/Scripts/python.exe`
  (Python 3.11 / Django 5.2.17). **No** el conda compartido `pos_fifo`
  (desactualizado desde A01, ver `requirements/README.md`).
- `--settings=config.settings_development`, **serial** (Windows; `--parallel`
  da ruido falso por el router de tenancy).
- Variables exclusivas para no colisionar con otros worktrees:
  `DB_NAME=pos_cfg012_r2` (BD de test `test_pos_cfg012_r2`),
  `TENANT_TEST_DB_NAMESPACE=cfg012r2`.

## 1. Qué se corrigió

Mismo mecanismo exacto que el resto de la serie CFG-012: un `setUp` que
dependía accidentalmente de que `get_config()` / `ConfiguracionNegocio.load()`
crearan la fila —comportamiento retirado por CFG-012 (parte 2 de C03)— ahora
crea su `ConfiguracionNegocio` explícita. **Ningún cambio de producción,
ningún fallback nuevo, ninguna restauración de auto-create.**

| Archivo | Clase(s) afectadas | Causa exacta | Corrección | +líneas |
|---|---|---|---|---|
| `apps/caja/tests/test_auditoria_caja.py` | `CajaTestCase` (base) → `PertenenciaAlTurnoTests` (4 errores) | `setUp` nunca creaba la config; solo `PertenenciaAlTurnoTests` cobra en efectivo de verdad (`procesar_venta_service` → `get_config()`) | `ConfiguracionNegocio.objects.create()` legacy en `CajaTestCase.setUp` | +5 |
| `apps/cotizaciones/tests/test_auditoria_cotizaciones.py` | `CotizacionesTestCase` (base) → `GatesDelModuloTests` (2), `PrecioServerSideTests` (5), `AlcanceDeLaConversionTests` (4), `EndpointLegacyTests` (4), `VigenciaTests` (1) | `setUp` crea `suc_a`/`suc_b` pero ninguna config | `ConfiguracionNegocio.objects.create()` legacy en el `setUp` base | +11 |
| ↳ caso aparte, mismo archivo | `AlcanceDeLaConversionTests.test_no_se_convierte_en_otra_sucursal` | Fuerza `SUCURSAL_CODIGO='COT-A'` a mitad de test: `get_config()` resuelve `suc_a` de verdad y ya no cae al legacy del `setUp` | `ConfiguracionNegocio.objects.create(sucursal=self.suc_a)` dentro del mismo `with self.settings(...)` | (incl. arriba) |
| `apps/cotizaciones/tests/test_cotizacion_hardening.py` | `CotizacionHardeningBase` (base) → `COT008ImportesTests`, `COT009ClienteActivoTests`, `COT010NumeracionTests`, `COT012AuditoriaTests`, `COT014PDFErrorTests`, `COT017PaginacionTests` (15 en total) | `setUp` no crea sucursal ni config (escenario legacy puro) | `ConfiguracionNegocio.objects.create()` legacy en el `setUp` base | +6 |
| `apps/permisos/tests/test_credencial_fisica.py` | `EndpointCredencialTests` (5) + `ThrottlingTests` (8, hereda de ella) = 13 | `_pedir()` postea a `caja:api_validar_admin`, que consulta `get_config()` para decidir la autorización por carnet; ninguna clase base creaba la config | `ConfiguracionNegocio.objects.create()` legacy en `EndpointCredencialTests.setUp` | +7 |

**Total: 4 archivos, +29 líneas, 0 borrados.** Distribución de los 48 errores
CFG-012 eliminados: 4 (caja) + 16 + 15 (cotizaciones) = 35, más 13 (permisos)
— exactamente lo pedido.

No se tocó `AlmacenamientoTests`, `ResolucionTests` ni
`MotivoPorOperacionTests` de `test_credencial_fisica.py` (no llegan a
`get_config()`, no estaban rotos). Los tests de caja que fuerzan
`SUCURSAL_CODIGO` a códigos que no existen (`MIA-001`, `PRO-001`, `DET-002`)
no fallaban en baseline (no alcanzan `get_config()`) y tras el fix resuelven
por la única config legacy — sin regresión.

## 2. Verificación

### Gate 0 — estado y base
```
git status                          # limpio antes de editar
git rev-parse HEAD                  # 355de7c... (= base)
git merge-base --is-ancestor 355de7c HEAD  # OK
manage.py check --settings=config.settings_development  # 0 issues
```

### Gate 1 (obligatorio) — apps.caja apps.cotizaciones apps.permisos
```
# ANTES (baseline, sobre 355de7c limpio):
python manage.py test apps.caja apps.cotizaciones apps.permisos \
  --settings=config.settings_development
# Ran 185 tests -- FAILED (errors=48), todos ConfiguracionNoInicializada
# (caja 4, cotizaciones 16+15, permisos 13)

# DESPUES (con el fix 60f1c4f):
python manage.py test apps.caja apps.cotizaciones apps.permisos \
  --settings=config.settings_development
# Ran 185 tests in 82.110s -- OK   (0 failures, 0 errors)
```
El conteo se mantiene en **185** (no se agregaron ni quitaron tests, solo
fixtures). El único `ERROR:cotizaciones:...` en el log es de aplicación
(test de PDF que ejercita el manejo de error), no un fallo de test.

### Gate 2 — discovery Django completo (excluye e-CF)
> `apps.facturacion_electronica` corre con pytest, no con `manage.py test`
> (TESTING.md). Comando: las 20 apps con TestCase Django, mismo entorno serial.

```
python manage.py test apps.api apps.auditoria apps.caja apps.clientes apps.common \
  apps.configuracion apps.cotizaciones apps.cuentas_por_cobrar apps.inventario \
  apps.negocios apps.notificaciones apps.permisos apps.productos apps.reportes \
  apps.sucursales apps.suscripciones apps.sync apps.tenancy apps.usuarios apps.ventas \
  --settings=config.settings_development
# Found 1516 test(s).
# Ran 1516 tests in 547.606s -- OK   (0 failures, 0 errors, 0 skips)
```
**Discovery Django completo en VERDE sobre esta base.** A diferencia de la r1
(que sobre `claude/cierre-prod-C03-parte2-fixes` dejaba 12 residuales en
`apps/productos` y `apps/usuarios`, propiedad de Codex), la base de integración
A05-C03 ya trae esos fixtures resueltos: los 48 CFG-012 de caja/cotizaciones/
permisos eran los únicos que quedaban. Con el fix, la suite entera (sin e-CF)
pasa. 0 skips porque `TENANT_TEST_DB_NAMESPACE` está definido y los gates
opt-in TEN-016 corren en vez de saltarse.

> e-CF (`apps/facturacion_electronica`) se corre aparte con pytest; no se tocó
> ningún archivo suyo y su fixture ya crea su config explícita.

### Gate 3 — higiene
```
git diff --check      # limpio (sin whitespace roto)
```

## 3. Riesgos y rollback

- **Sin riesgo de dato real.** Las correcciones son fixtures de test dentro de
  transacciones que se revierten; ninguna toca `crear_config_inicial` ni una BD
  fuera de la de test. No hay migraciones ni estado persistente.
- **No se restauró** CFG-012, ni la auto-creación en lecturas, ni se agregó
  fallback productivo. La política CFG-012 (leer no crea) queda intacta.
- **No se tocó** A05, `apps/sync/engine.py`, `apps/api/views/sync.py`,
  `config/settings.py`, migraciones tenant ni CT-03.
- **Rollback:** `git revert 60f1c4f` (fix) — o, dado que no está integrada,
  simplemente no mergear la rama y retirar el worktree con
  `git worktree remove C:/Proyectos/pos_fifo_system_cierre_claude_residual_r2`
  y `git branch -D claude/cierre-prod-C03-residual-fixtures-r2`.

## 4. Estado final

- `git status`: limpio.
- Worktree: `C:/Proyectos/pos_fifo_system_cierre_claude_residual_r2`.
- Rama: `claude/cierre-prod-C03-residual-fixtures-r2`.
- SHA del fix: `60f1c4f6187220f44e38543a930f3e58ab730572`.
- SHA final de la entrega: HEAD de la rama tras este handoff (reportado en la
  entrega). Sin push, sin merge, sin despliegue.
