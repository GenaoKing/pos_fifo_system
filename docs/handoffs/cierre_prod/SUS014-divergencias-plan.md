# Handoff — SUS-014 (postcondición de plan divergente)

Estado: **prevención y postcondición integradas localmente**. `PLAN_DRIFT` se
reporta desde `verificar_identidad_tenant`, sin reconciliar ni escribir en ninguna
base. Ítem `SUS-014` de `INVENTARIO.md`. Fecha de integración: **2026-09-18**.
Origen: Claude; cableado: Codex.

## SHA base / resultado

- **Base**: `integration/cierre-prod-A05-C03@b335223` (ya incluye la mitad 1
  de SUS-014, `b318681`+`c003af8`). Rama `claude/cierre-prod-SUS014-postcondicion`,
  worktree propio (`pos_fifo_system_sus014`), venv externo Django 5.2.17
  reusado de `pos_fifo_system_cierre_codex/.venv` (solo lectura, sin
  reinstalar nada), DB de test aislada `pos_cierre_claude_sus014`.
- Sin migración. El candidato se integró localmente y el consumidor de tenancy
  se agregó como bloque Codex. **NO publicado ni desplegado.**

## Contexto — por qué esto NO es "SUS-014 sigue abierto"

El hallazgo original tenía dos recomendaciones separadas: *"validar el slug
antes de tocar ambas bases"* **y** *"verificar postcondición cruzada antes de
reactivar/publicar el tenant"*. Son dos mecanismos distintos:

1. **Prevención** (evita que se ESCRIBA una divergencia nueva). Ya resuelta:
   `seed.validar_plan_slug` (mío, `b318681`, 2026-09-11) + el cableado en
   `bootstrap_tenant` (Codex, `c003af8`, 2026-09-15) — reordena el comando
   para migrar el alias tenant, validar el slug ahí, sembrar la suscripción, y
   solo DESPUÉS publicar `Tenant.plan_slug` en el control plane
   (`persistir_plan_slug_validado`). Un slug inválido no toca ninguna base y
   deja el checkpoint `FAILED` reanudable. Confirmado de nuevo hoy en un
   worktree/DB propios, sin depender del handoff previo:
   ```
   apps.tenancy.tests.test_bootstrap_tenant_plan_slug
   apps.suscripciones.tests.test_sus014_validar_plan_slug
   Ran 9 tests in 0.442s — OK
   ```
   Este mecanismo ya vive en `integration/cierre-prod-A05-C03`, sin mergear a
   `develop` todavía — es trabajo de integración de Codex, no algo que falte
   escribir.
2. **Detección** (si YA hay una fila divergente —por una edición manual, un
   bootstrap corrido antes del fix del punto 1, o cualquier vía que no pase
   por `bootstrap_tenant`— nada lo nota). Esto seguía sin existir. Es lo que
   agrega este handoff.

`apps/tenancy/services.py` ya tiene el mecanismo genérico para esto:
`divergencias_identidad(tenant, negocio, configuraciones)`, consumido por
`manage.py verificar_identidad_tenant` — compara `slug`/`nombre`/`rnc`/`activo`
y `ConfiguracionNegocio`, de solo lectura, sin intentar reconciliar. Nunca
comparó plan.

## Qué se agregó

`apps/suscripciones/engine.py::divergencias_plan_operativo(tenant_plan_slug, negocio)`:

- Puro, de solo lectura, sin importar nada de `apps.tenancy` (evita acoplar
  `suscripciones` a `tenancy` en el sentido inverso al que ya existe).
  Recibe el `plan_slug` del control plane como string simple — no el objeto
  `Tenant` — y el `negocio` ya resuelto (el mismo que `verificar_identidad_tenant`
  ya obtiene con `Negocio.self_row()` bajo `tenant_context`).
- Devuelve `[]` si coincide, o `[{'code': 'PLAN_DRIFT', 'field': 'plan_slug',
  'expected': <control plane>, 'actual': <operativo>}]` — mismo formato de
  fila que `divergencias_identidad`, pensado para concatenar sin transformar.
- `negocio=None` devuelve `[]` a propósito: `divergencias_identidad` ya
  reporta `NEGOCIO_MISSING`; no lo duplica.
- `''` (control plane) contra sin-plan-operativo (custom, `plan=None` o sin
  fila de `SuscripcionNegocio`) NO es divergencia: es "sin plan explícito" en
  ambos lados, el mismo caso que ya trata `validar_plan_slug` como válido.
- Cubre los dos sentidos: el control plane anuncia un plan que el operativo no
  tiene, y el caso inverso (operativo tiene plan, control plane quedó en
  blanco) — puede pasar si alguien edita `SuscripcionNegocio` a mano en la BD
  tenant sin tocar `Tenant.plan_slug`.

Tests nuevos, `apps/suscripciones/tests/test_sus014_divergencias_plan.py` (8
casos: `negocio=None`, sin plan en ningún lado, coincide, diverge en cada
sentido, custom vs. plan anunciado, sin fila de suscripción, y `activa=False`
no cambia la comparación).

## Integración Codex aplicada

Se agregó la lista al resultado de `divergencias_identidad`, dentro del mismo
`tenant_context` que usa `verificar_identidad_tenant`:

```python
# apps/tenancy/services.py, al final de divergencias_identidad, antes del return:
from apps.suscripciones.engine import divergencias_plan_operativo
diferencias.extend(divergencias_plan_operativo(tenant.plan_slug, negocio))
```

`tenant` y `negocio` ya están disponibles en ese scope. No hace falta una nueva
consulta explícita a la BD tenant: la relación one-to-one `negocio.suscripcion`
se resuelve en el contexto actual si aún no estaba cacheada.

## Pruebas — comando y resultado

```bash
cd pos_fifo_system_sus014
TENANT_TEST_DB_NAMESPACE=claude_sus014_verify \
  <venv>/Scripts/python.exe manage.py test \
  apps.tenancy.tests.test_bootstrap_tenant_plan_slug \
  apps.suscripciones.tests.test_sus014_validar_plan_slug \
  --settings=config.settings_development --noinput
# Ran 9 tests in 0.442s — OK  (verificación independiente de la mitad 1, sin cambios)

<venv>/Scripts/python.exe manage.py test apps.suscripciones.tests.test_sus014_divergencias_plan \
  --settings=config.settings_development --noinput
# Ran 8 tests in 0.211s — OK  (mitad 2, nueva)

<venv>/Scripts/python.exe manage.py test apps.suscripciones \
  --settings=config.settings_development --noinput
# Ran 84 tests in 5.972s — OK (skipped=2, esperado)  (regresión app completa)
```

## Rollback

`git revert` de la integración C05/SUS-014. La detección no escribe ni intenta
reconciliar, así que revertirla solo deja de informar `PLAN_DRIFT`.

## Documentación aplicada

`INVENTARIO.md`, `TODO_AUDITORIAS.md`, los mapas de tenancy/suscripciones y el
registro CT-03 se actualizaron junto con el cableado. La evidencia vigente debe
incluir la prueba de `PLAN_DRIFT` dentro de la matriz tenancy, no solo la prueba
unitaria de `divergencias_plan_operativo`.
