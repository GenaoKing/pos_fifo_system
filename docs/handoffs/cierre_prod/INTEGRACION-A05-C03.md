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

## Rollback y siguiente paso

No hay efecto fuera de Git. Si hay que retirar solo la fase 2, revertir el
merge `a1064be90a92f97c69b0135042492427d66d4e34` con `git revert -m 1`,
conservando la fase 1. Si hay que retirar también la fase 1, revertir luego en
orden inverso `9097bdd`, `c003af8` y `d3257bf`; no resetear `develop` ni la
rama de Claude. El commit de este handoff se revierte separadamente si el
historial debe volver a describir el checkpoint anterior.

La base de código integrada es
`integration/cierre-prod-A05-C03@a1064be90a92f97c69b0135042492427d66d4e34`.
Antes de cualquier publicación hace falta un SHA correctivo con alcance
explícito para los fixtures residuales y una resolución independiente del
grafo cloud; después deben repetirse el discovery global, el bootstrap/migrate
desde cero, TEN-016, los consumidores A05/C03 y e-CF. No se debe publicar
CT-03 mientras alguno de esos gates permanezca rojo.
