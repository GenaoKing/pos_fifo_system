# Handoff — SUS-019 / CFG-021 (cobertura de fronteras del contrato)

Estado: **SUS-019 acreditado (regresiones de las vías faltantes del guard);
CFG-021 verificado como YA cubierto por C03, sin nuevos tests necesarios.**
Ítems `SUS-019` y `CFG-021` de `INVENTARIO.md`. Fecha: **2026-09-16**. Agente: Claude.

## SHA base / resultado

- **Base**: `develop@fffd02b`. Rama `claude/cierre-prod-SUS019-coverage`
  (branch en el worktree principal; `develop` se deja limpio). Venv externo
  Django 5.2.17, DB de test aislada `test_pos_fifo_dev_sus019`.
- Solo tests (apps/api/tests). Sin cambios de código de producción, sin
  migración, sin tocar a Codex. **NO publicado; NO fusionado.**

## SUS-019 — qué faltaba y qué se agregó

El guard de degradación (SUS-004/SUS-015, `GuardDegradacionMixin` en
`apps/api/views/suscripciones.py`) se unificó para aplicar por **cualquier**
canal (override, plan, `activa`): compara el set de módulos efectivo antes y
después y rechaza si un módulo retirado no puede irse. Pero la suite solo fijaba
como regresión el canal **override** (excluir con dependientes) y el cambio de
plan hacia **arriba**. Las dos rutas que el propio docstring dice que *antes
esquivaban el guard* —downgrade de plan y suspensión vía `activa`— no tenían
prueba.

`apps/api/tests/test_suscripciones_admin.py::SUS019CanalesDelGuardTests` (3):

1. `test_downgrade_de_plan_deja_un_evento_con_diff` — empresarial→basico por
   PATCH pasa por el guard, retira `ecf` y deja exactamente un evento CT-01 con
   el diff de plan (antes solo se probaba el upgrade).
2. `test_downgrade_bloqueado_por_datos_en_vuelo_no_escribe_ni_audita` — **la
   regresión clave**: con un hook de datos (`patch.dict(engine._HOOKS_DATOS,
   {'ecf': ...})`) que declara e-CF en vuelo, el downgrade que retira `ecf` se
   rechaza (400) y el rollback deja el plan intacto y **ningún** evento.
3. `test_suspender_via_activa_pasa_por_el_guard_y_se_audita` — PATCH
   `activa=False` sobre un `basico` (sin cxc/ecf, sin hooks que bloqueen)
   recomputa el set, se suspende y deja el evento con `activa: true→false`.

## CFG-021 — verificado como YA cubierto (sin tests nuevos)

La lista de fronteras de CFG-021 (caché tenant-aware, fallback estricto,
RBAC/Admin, validación cruzada, borrado, alcance de accesos rápidos) **ya está
cubierta** por los tests que C03 agregó en
`apps/configuracion/tests/test_auditoria_configuracion.py`
(`AislamientoDeCacheTests`, `ResolucionEstrictaTests`, `AdminGateadoPorRbacTests`,
`ValidacionCruzadaTests`, `BorradoProtegidoTests`) y por
`AccesoRapidoInvarianteTests` (rama `claude/cierre-prod-C03-cfg010`). El snapshot
de la auditoría es anterior a ese endurecimiento. Lo que resta de CFG-021 está
**bloqueado**, no sin escribir:

- **Múltiples workers**: necesita una matriz multiproceso real (infra tipo
  TEN-016), fuera de un test unitario.
- **Pull inválido**: es CFG-007, un contrato aún ABIERTO (C+A) — no hay contrato
  acordado que fijar como regresión todavía.
- **Alcance de accesos rápidos por sucursal**: es CFG-010 pata 2 (ámbito por
  sucursal, decidido pero no implementado); su regresión se escribe cuando exista
  el campo.

Recomendación: marcar CFG-021 en INVENTARIO como cubierto-salvo-bloqueados, con
puntero a los tests citados; no inventar tests redundantes.

## Pruebas — comando y resultado

```bash
DB_NAME=pos_fifo_dev_sus019 python manage.py test \
  apps.api.tests.test_suscripciones_admin \
  --settings=config.settings_development --noinput
# Ran 16 tests ... OK  (13 previos + 3 nuevos SUS-019, 0 regresiones)
```

## Rollback

`git revert` del commit. Solo tests; sin efecto en producción.

## Deltas propuestos a documentos de seguimiento

- `INVENTARIO.md` `SUS-019`: `PENDIENTE`→`ACREDITADO` con el commit de esta rama.
- `INVENTARIO.md` `CFG-021`: anotar "cubierto por tests de C03 (ver handoff);
  restan multiworker (infra) y pull inválido (CFG-007, abierto)".
- `TODO_AUDITORIAS.md`: idem.
