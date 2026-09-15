# Integración A05.1 + C03 parte 2 — checkpoint de fase 1

Fecha: **2026-09-15**
Estado: **CHECKPOINT LIMPIO; no publicable y sin merge a `develop`**.

Esta rama integra solamente A05.1 y el corte inicial de C03 parte 2. La fase 2
queda detenida hasta recibir por este chat un SHA concreto, descendiente de
`29bd07f`, de Claude.

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

## Rollback y siguiente paso

No hay efecto fuera de Git. Si hay que retirar la fase 1, revertir en orden
inverso los commits `9097bdd`, `c003af8` y `d3257bf`, conservando el merge de
revisión y sin resetear `develop` ni la rama de Claude.

La siguiente base recomendada es
`integration/cierre-prod-A05-C03@9097bdd30467e5c9125667efe618f1c3e45193d1`.
Cuando llegue el SHA exacto de Claude, verificar que sea descendiente de
`29bd07f`, que su worktree esté limpio y que el SHA exista; fusionar solo ese
SHA y repetir todos los gates combinados antes de considerar CT-03 o cualquier
publicación.
