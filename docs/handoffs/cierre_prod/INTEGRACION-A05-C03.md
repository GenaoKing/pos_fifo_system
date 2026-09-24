# Integración A05.1 + C03 parte 2 — gate final de fase 2

Fecha: **2026-09-15**
Estado: **INTEGRADA; BLOQUEADA PARA PUBLICACIÓN y sin merge a `develop`**.

Esta rama integra A05.1 y C03 parte 2. Las secciones iniciales preservan el
checkpoint de fase 1; la sección «Fase 2» documenta el SHA correctivo recibido,
su merge exacto y el resultado de los gates finales.

## Base e historia verificadas

- Worktree: `C:\Proyectos\pos_fifo_system_cierre_codex`.
- Base A05: `codex/cierre-prod-A05@fdd02b18fc478ca66a845dfffce94a7fabde54f1`.
- Base común: `develop@fffd02bd384d8078928fa24761c550df34b711b5`.
- Entrada Claude exacta: `29bd07f7fa5ef7d39272b19b9034c66a6b746782`.
- Merge explícito: `07433c531fcb104c2ccccb5d60265a03b59d3b1e`, con padres
  `fdd02b18fc478ca66a845dfffce94a7fabde54f1` y
  `29bd07f7fa5ef7d39272b19b9034c66a6b746782`.
- `develop` permanece en `fffd02bd`; no hubo push, staging, producción ni
  escrituras de datos operativos.

Commits de consumidores posteriores al merge:

1. `d3257bf3fd8b65db15238a0eacc7c82e4dedcc6f`
   `fix(sync): bootstrap configuration pull explicitly`.
2. `c003af8b9572a143ea54ff2a10ffa7f94318f999`
   `fix(tenancy): validate tenant plan before publishing`.
3. `9097bdd30467e5c9125667efe618f1c3e45193d1`
   `fix(login-catalog): handle missing configuration safely`.

## Consumidores cerrados en fase 1

- **Sync/API:** el primer pull local de configuración usa
  `ConfiguracionNegocio.bootstrap(sucursal=...)`; el endpoint cloud conserva
  `load()` como lectura y devuelve `[]` ante `ConfiguracionNoInicializada`.
  Hay cobertura de creación inicial, replay, actualización y colección cloud
  vacía.
- **Tenancy/SUS-014:** `bootstrap_tenant` prepara el checkpoint sin publicar el
  plan solicitado, crea/migra el alias tenant, llama
  `validar_plan_slug(plan_slug, using='tnt_<tenant_key>')` y solo entonces
  siembra la suscripción y publica `Tenant.plan_slug`. El slug inexistente no
  llama al seed tenant ni cambia el plan previo. La lectura posterior del plan
  es estricta, sin omitir una suscripción si desaparece entre validación y seed.
- **Login:** las ramas bloqueada e inactiva no importan ni llaman `get_config`;
  el context processor render-safe es la única fuente de ese contexto.
- **Productos:** los diez handlers genéricos registran la excepción y devuelven
  un mensaje JSON público fijo. Los fixtures no-CFG crean la configuración de
  forma explícita. El caso sin configuración sigue sin autocrear config ni SKU
  y no filtra el texto interno al cliente.

## Evidencia de validación

Todas las corridas usaron `C:\Proyectos\pos_fifo_system_cierre_codex\.venv`
con bases y namespaces propios, serialmente en Windows.

```powershell
$env:DB_NAME='pos_cierre_codex_a05c03_phase1_20260915_r3'
$env:TENANT_TEST_DB_NAMESPACE='a05c03_codex_20260915_phase1_r3'
.\.venv\Scripts\python.exe manage.py test `
  apps.sync.tests.test_configuracion_cfg012 `
  apps.api.tests.test_sync_extended `
  apps.tenancy.tests.test_bootstrap_tenant_plan_slug `
  apps.tenancy.tests.test_models_and_commands `
  apps.usuarios.tests.test_auditoria_usuarios `
  apps.productos.tests.test_auditoria_productos `
  apps.configuracion.tests.test_cfg012_leer_no_crea `
  apps.configuracion.tests.test_ct03_contrato `
  apps.suscripciones.tests.test_sus014_validar_plan_slug `
  --settings=config.settings_development --noinput --verbosity 0
```

Resultado: **131 pruebas OK en 30.980 s**. Las trazas de configuración ausente,
rate-limit y auditoría simulada corresponden a casos negativos esperados; la
respuesta HTTP de producto queda saneada y el detalle solo se registra en el
servidor.

También finalizaron correctamente:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe manage.py check --settings=config.settings_development
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.settings_development
.\.venv\Scripts\python.exe -m compileall -q apps config
git diff --check
```

Resultados: `pip check` sin requisitos rotos; `check` sin incidencias;
`makemigrations` sin cambios; `compileall` y `diff --check` correctos.

## Riesgos y gates aún pendientes

- La rama `claude/cierre-prod-C03-parte2-fixes` aún apunta a `29bd07f` y su
  worktree tiene correcciones sin commit en configuración y fixtures C05. No se
  tomó ningún cambio móvil o no confirmado.
- El caso legacy de `ConfiguracionNegocio.bootstrap()` pertenece a
  `apps/configuracion`/Claude y queda fuera de esta fase.
- No hay atomicidad distribuida entre la suscripción tenant y el control plane.
  El orden nuevo garantiza que un plan inválido no escribe ninguna proyección;
  si falla la publicación posterior a un seed válido, el checkpoint auditable
  permite reanudar el mismo comando y reconciliarlo.
- No se ejecutaron aún discovery Django completo, e-CF por pytest, TEN-016 ni
  el grafo de migraciones desde cero: son gates de la combinación final con el
  SHA correctivo de Claude.

**CT-03 permanece sin publicar. CT-04 permanece PENDIENTE/reservado.** No se
modificaron `CONTRATOS.md`, ledgers compartidos, C04 ni A05.2–A05.4.

## Fase 2 — corrección C03 e integración verificada

Esta sección reemplaza los gates pendientes del checkpoint de fase 1.

### Entrada y merge exactos

- SHA correctivo recibido y verificado: `9e3d8c7f75bbb09920ecb8e3da3c70fa566754ff`
  (`docs(cierre-C03): handoff de correcciones parte2 - bootstrap legacy + fixtures CFG-012`).
- Es descendiente de `29bd07f7fa5ef7d39272b19b9034c66a6b746782`; el ref
  `claude/cierre-prod-C03-parte2-fixes` y su worktree estaban limpios y
  apuntaban exactamente a ese objeto antes de integrarlo.
- Merge realizado sin conflictos: `a1064be90a92f97c69b0135042492427d66d4e34`,
  con padres `0104c652521083038b609a1a37fb07c0b09576cb` y
  `9e3d8c7f75bbb09920ecb8e3da3c70fa566754ff`.

La corrección de Claude aplica CFG-002 también a
`ConfiguracionNegocio.bootstrap(sucursal=None)`: cero filas crea una sin PK
forzado; una sola fila de cualquier PK se reutiliza; dos o más filas levantan
`ConfiguracionAmbigua`. También migra los fixtures explícitos de los cuatro
módulos C05 originalmente cubiertos y documenta el residuo conocido.

### Gates verdes

Todas las corridas usaron bases y namespaces aislados, serialmente en Windows.

| Gate | Resultado |
| --- | --- |
| Configuración, suscripciones y consumidores C03/C05 (`configuracion`, `suscripciones`, API de suscripciones, ventas, inventario, cuentas por cobrar y reportes) | **537 OK** en 192.065 s |
| Consumidores A05 (`sync`, producto/API, tenancy, usuarios) | **292 OK** en 57.621 s |
| TEN-016 de aislamiento físico | **2 OK** en 0.518 s |
| e-CF por pytest | **72 passed** en 18.50 s |
| `pip check`, `manage.py check`, `makemigrations --check --dry-run`, `compileall`, `git diff --check` | Correctos; sin requisitos rotos, incidencias ni cambios de migración |

### Bloqueos descubiertos

1. **Discovery Django global:** 131 módulos de prueba (excluido e-CF, que se
   ejecutó por pytest) hallaron 1,508 pruebas; finalizaron en 512.583 s con
   **48 errores**. Los 35 ya documentados se reproducen al correr
   `apps.caja apps.cotizaciones` (96 pruebas, 52.020 s). Los 13 restantes se
   aíslan en `apps.permisos` (89 pruebas, 32.713 s). Todos alcanzan
   `caja:api_validar_admin` sin una `ConfiguracionNegocio` creada por el
   fixture y terminan en `ConfiguracionNoInicializada`; no apareció otro
   mecanismo ni un fallo de A05. El residuo de permisos no estaba enumerado en
   el handoff correctivo de Claude, aunque comparte el mismo endpoint de caja.

2. **Grafo de migraciones cloud desde cero:** se creó la base desechable
   `pos_cierre_codex_a05c03_migrationgraph_20260915_r1`, previamente
   verificada inexistente, con `TENANCY_DB_PER_TENANT_ENABLED=true`. El
   `migrate` del control plane registró `sucursales.0001_initial` sin crear
   `sucursales_sucursal` y falló en `auditoria.0002_auditoria_sucursal_and_more`
   con `ProgrammingError: relation "sucursales_sucursal" does not exist`.
   Por ello el bootstrap no llegó a crear/migrar el alias tenant y no se puede
   declarar verde la cadena `sync`/`tenancy` aplicada desde cero. La base
   parcial se eliminó inmediatamente y se verificó que ni ella ni
   `tnt_miggraph_a05c03_r1` quedaran en PostgreSQL. El router y esas
   migraciones no cambiaron entre `fdd02b18`, `29bd07f` y este merge: es un
   bloqueo de baseline, no una regresión introducida por A05/C03.

No se corrigieron esos residuos: están fuera del alcance autorizado de esta
integración y requieren una corrección explícita de fixtures/contrato para
caja, cotizaciones y permisos, además de una decisión separada sobre el
router/grafo cloud.

**CT-03 no se publica. CT-04 sigue reservado.** No hubo push, merge a
`develop`, staging, producción ni escrituras sobre datos operativos.

## Fase 3 — grafo cloud/tenant corregido; fixture residual pendiente

- Entrada Codex verificada: `b6b71c383928100c43e6b6e507a2ca5e1734e451`,
  descendiente de `c95b9aff86fa6ef67d2bb90fe91d5a78b1b679b3`, con worktree
  `codex/cierre-prod-tenant-migration-graph` limpio.
- Merge exacto y sin conflictos: `39ed373589596b731df59d611a5c81a8878ccbe8`,
  con padres `c95b9aff86fa6ef67d2bb90fe91d5a78b1b679b3` y
  `b6b71c383928100c43e6b6e507a2ca5e1734e451`.
- `sucursales` ahora es dual-home, coherente con la FK de
  `auditoria.0002_auditoria_sucursal_and_more`. El estado heredado se detecta
  antes de migrar; `reparar_sucursales_dual_home` es dirigido, emite ledger y
  requiere `--apply`. No hay autoreparación al iniciar ni SQL manual sugerido.

Validación independiente sobre PostgreSQL temporal, ya eliminado:

- 104 pruebas de tenancy, OK; `pip check`, `check`, migraciones en seco y
  `compileall`, OK.
- Control plane cloud vacío: `migrate_cloud`, bootstrap de tenant, segundo
  `migrate_tenants`, `verificar_identidad_tenant`, `verificar_sync --json` y
  `tablas_faltantes == []`, todos correctos; existen
  `sucursales_sucursal` y `sync_eventosync` en el tenant.
- Estado heredado reproducido con el código anterior: el nuevo preflight lo
  bloqueó sin escribir; dry-run dejó ledger; `--apply` desregistró exactamente
  las tres migraciones `sucursales` presentes, reaplicó la app y permitió que
  `migrate_cloud` terminara correctamente.

El SHA presentado para fixtures, `3d29772b5d33bb8b426090e021726622d445c4fe`,
**no se integró**: no es descendiente de `c95b9af` (su merge-base es
`9e3d8c7`), y su diff contra esa base elimina cambios A05/C03 ya integrados.
Se requiere un SHA de reemplazo rebasado sobre la punta actual de esta rama,
limitado a los fixtures de caja, cotizaciones y permisos.

El único bloqueo conocido para el gate global es ahora ese SHA de fixtures:
al integrarlo habrá que repetir discovery Django completo, e-CF, los gates
focales A05/C03 y los checks estáticos. CT-03 sigue sin publicar.

## Rollback y siguiente paso

No hay efecto fuera de Git. Si hay que retirar solo el grafo cloud/tenant,
revertir `39ed373589596b731df59d611a5c81a8878ccbe8` con `git revert -m 1`.
Si hay que retirar también la fase 2, revertir después
`a1064be90a92f97c69b0135042492427d66d4e34` con `git revert -m 1`, conservando
la fase 1. Para retirar también la fase 1, revertir luego en orden inverso
`9097bdd`, `c003af8` y `d3257bf`; no resetear `develop` ni la rama de Claude.
El commit de este handoff se revierte separadamente si el historial debe volver
a describir un checkpoint anterior.

La base de código integrada es
`integration/cierre-prod-A05-C03@39ed373589596b731df59d611a5c81a8878ccbe8`.
Antes de cualquier publicación falta un SHA correctivo, descendiente de esta
línea, con alcance explícito para los fixtures residuales. Después se repiten
el discovery global, el bootstrap/migrate desde cero, TEN-016, los consumidores
A05/C03 y e-CF. No se debe publicar CT-03 mientras alguno de esos gates
permanezca rojo.

## Fase 4 — fixtures residuales CFG-012 r2 integrados y gate global verde

Fecha: **2026-09-15**. Esta sección reemplaza el siguiente paso pendiente de la
fase 3. No modifica la evidencia histórica anterior.

### Recepción, revisión y merge

- Worktree destino: `C:\Proyectos\pos_fifo_system_cierre_codex`, limpio en
  `integration/cierre-prod-A05-C03@355de7c45979f220b71fb01171abcf460a56e95d`
  antes de recibir el candidato.
- Worktree fuente: `C:\Proyectos\pos_fifo_system_cierre_claude_residual_r2`,
  limpio en `claude/cierre-prod-C03-residual-fixtures-r2@1092756366de9d75ae52a12968ddd7868346c178`.
- Se verificó el objeto y
  `git merge-base --is-ancestor 355de7c45979f220b71fb01171abcf460a56e95d 1092756366de9d75ae52a12968ddd7868346c178`: OK.
- El diff `355de7c..1092756` contiene exclusivamente cuatro fixtures de test
  (`apps/caja/tests/test_auditoria_caja.py`,
  `apps/cotizaciones/tests/test_auditoria_cotizaciones.py`,
  `apps/cotizaciones/tests/test_cotizacion_hardening.py`,
  `apps/permisos/tests/test_credencial_fisica.py`) y el handoff
  `C03-residual-fixtures-r2.md`. `git diff --check` fue limpio. No toca código
  productivo, A05, sync/API, settings, tenancy, migraciones ni datos
  operativos.
- Se revisaron los hunks contra los mapas de caja, cotizaciones y permisos. Cada
  fixture crea `ConfiguracionNegocio` explícitamente; el caso con
  `SUCURSAL_CODIGO='COT-A'` crea además la configuración de esa sucursal. No
  se reintroduce auto-bootstrap de lecturas.
- Merge exacto, sin conflictos:

  ```powershell
  git merge --no-ff 1092756366de9d75ae52a12968ddd7868346c178 `
    -m "merge(cierre): integrar fixtures residuales C03 CFG-012 r2"
  ```

  Resultado: `ca68ad62670674a9365d70030bfc597764fdc3d4`, padres `355de7c` y
  `1092756`.

### Validación propia serial y aislada

Se usó `C:\Proyectos\pos_fifo_system_cierre_codex\.venv\Scripts\python.exe`,
`config.settings_development`, Windows serial y nombres exclusivos
`pos_a05c03_integrator_r4*` / `a05c03_integrator_r4*`; ningún comando apuntó
a staging, producción ni una BD compartida.

| Gate | Comando / alcance | Resultado real |
| --- | --- | --- |
| Fixtures residuales | `manage.py test apps.caja apps.cotizaciones apps.permisos --settings=config.settings_development --noinput --verbosity 0` | **185 OK** en **87.385 s** |
| C03/C05 focal | Todos los módulos `test_*.py` bajo `apps/configuracion`, `suscripciones`, `api`, `ventas`, `inventario`, `cuentas_por_cobrar` y `reportes` | **781 OK** en **388.343 s** |
| A05 focal | Todos los módulos `test_*.py` bajo `apps/sync`, `productos`, `tenancy` y `usuarios` | **377 OK** en **61.958 s** |
| TEN-016 | `manage.py test apps.tenancy.tests.test_multidb_isolation --settings=config.settings_development --noinput --verbosity 0` | **2 OK** en **0.644 s** |
| Discovery Django | `manage.py test apps.api apps.auditoria apps.caja apps.clientes apps.common apps.configuracion apps.cotizaciones apps.cuentas_por_cobrar apps.inventario apps.negocios apps.notificaciones apps.permisos apps.productos apps.reportes apps.sucursales apps.suscripciones apps.sync apps.tenancy apps.usuarios apps.ventas --settings=config.settings_development --noinput --verbosity 0` | **1,516 OK** en **569.625 s**; 0 fallos, 0 errores, 0 skips |
| e-CF separado | `python -m pytest apps/facturacion_electronica/tests --ds=config.settings_development -q` | **72 passed** en **20.72 s** |

Los dos gates focales de la tabla se ejecutaron con estos comandos exactos
(cada uno con un `DB_NAME` y `TENANT_TEST_DB_NAMESPACE` exclusivos):

```powershell
$testModules = rg --files apps/configuracion/tests apps/suscripciones/tests apps/api/tests apps/ventas/tests apps/inventario/tests apps/cuentas_por_cobrar/tests apps/reportes/tests |
  Where-Object { $_ -match '(\\|/)test_.*\.py$' } |
  ForEach-Object { ($_ -replace '[\\/]', '.') -replace '\.py$', '' }
python manage.py test @testModules --settings=config.settings_development --noinput --verbosity 0

$testModules = rg --files apps/sync/tests apps/productos/tests apps/tenancy/tests apps/usuarios/tests |
  Where-Object { $_ -match '(\\|/)test_.*\.py$' } |
  ForEach-Object { ($_ -replace '[\\/]', '.') -replace '\.py$', '' }
python manage.py test @testModules --settings=config.settings_development --noinput --verbosity 0
```

Los warnings y trazas de HTTP 4xx/429, red, PDF, sync y configuración ausente
son casos negativos esperados de las suites. En particular, el discovery no
reprodujo `ConfiguracionNoInicializada` como error de fixture en caja,
cotizaciones o permisos.

### Migración cloud/tenant limpia

Con control plane temporal `pos_a05c03_integrator_r4mig`,
`TENANCY_DB_PER_TENANT_ENABLED=true` y tenant temporal `intgcfgr4`:

```powershell
python manage.py migrate_cloud --skip-tenants --noinput --verbosity 0
python manage.py bootstrap_tenant --tenant intgcfgr4 --nombre 'Integracion CFG R4' `
  --admin-password $adminPassword --identity-password $identityPassword `
  --plan empresarial --verbosity 0
python manage.py migrate_tenants --tenant intgcfgr4 --incluir-inactivos --noinput --verbosity 0
python manage.py verificar_identidad_tenant --tenant intgcfgr4
$env:SUCURSAL_CODIGO='SD-001'
python manage.py with_tenant --tenant intgcfgr4 verificar_sync -- --json
```

`$adminPassword` y `$identityPassword` se generaron distintos en memoria del
proceso y no se imprimieron. Resultado: control plane y tenant migrados; se
aplicó `sucursales.0001` antes de `auditoria.0002`; el segundo pase terminó
1/1; `verificar_identidad_tenant` informó identidad consistente;
`verificar_sync --json` quedó sin alertas; y la verificación directa devolvió
`tablas_faltantes('tnt_intgcfgr4') == []`.

El primer intento de `bootstrap_tenant` con `--noinput` fue rechazado por su
parser antes de escribir. El reintento documentado arriba, sin ese flag, fue
el que creó y validó el tenant.

### Higiene y limpieza

```powershell
python -m pip check
python manage.py check --settings=config.settings_development
python manage.py makemigrations --check --dry-run --settings=config.settings_development
python -m compileall -q apps config
git diff --check
```

Todos correctos: requisitos sin roturas, 0 incidencias, sin migraciones nuevas,
bytecode compilable y diff sin whitespace roto. Tras los gates se verificaron y
eliminaron exclusivamente las BDs desechables
`pos_a05c03_integrator_r4mig`, `tnt_intgcfgr4` y
`test_pos_a05c03_integrator_r4ecf`; la comprobación posterior devolvió lista
vacía. No hubo media temporal que remover.

### Estado, rollback y frontera

- **CT-03 queda verde localmente y puede considerarse cerrado como gate de
  integración.** No se publicó: no hubo push, merge a `develop`, staging,
  producción ni escrituras sobre datos operativos.
- CT-04, C04 y A05.2--A05.4 continúan fuera de alcance de este bloque.
- Rollback de esta recepción: `git revert -m 1
  ca68ad62670674a9365d70030bfc597764fdc3d4`. No hacer `reset`, no mover
  `develop` ni alterar las ramas/worktrees de Claude. El commit documental de
  esta fase se revierte separadamente sólo si también se revierte el merge.
