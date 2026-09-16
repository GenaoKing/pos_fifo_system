# Handoff C03 — CFG-010 (invariante de AccesoRapidoPOS), PARCIAL

Estado: **PARCIAL — cerrada la mitad self-contained (integridad de fila);
la mitad de ámbito por sucursal queda ABIERTA por requerir decisión de
negocio.** Ítem `CFG-010` de `docs/handoffs/cierre_prod/INVENTARIO.md` (fila 212).
Fecha: **2026-09-16**. Agente: Claude.

## SHA base / resultado

- **Base**: `develop@fffd02b`. Rama `claude/cierre-prod-C03-cfg010` (branch en el
  worktree principal, `develop` se deja limpio al terminar). Venv externo
  `.venvs/pos_cierre_claude_c03_20260910` (Django 5.2.17), DB de test aislada
  `test_pos_fifo_dev_cfg010`.
- `AccesoRapidoPOS` no lo tocaron C03 parte-2 ni A05, así que integra limpio
  sobre `integration/cierre-prod-A05-C03` cuando corresponda.
- **NO publicado; NO fusionado.**

## Hallazgo (CFG-010, dos patas)

1. **Integridad de fila (CERRADA aquí):** `clean()` exige exactamente producto
   XOR categoría según `tipo`, pero `save()` no lo invocaba →
   `AccesoRapidoPOS.objects.create(tipo='producto')` (sin producto), un `save()`
   directo o un import persistían una fila inválida que el POS después no sabe
   resolver.
2. **Ámbito multi-sucursal (ABIERTA, necesita decisión):** el modelo no tiene
   `sucursal`/`negocio`, y el endpoint POS (`apps/ventas/views.py`) lista todos
   los accesos activos → un acceso creado en la sucursal A aparece en la B.

## Qué cubre esta entrega (solo pata 1)

`apps/configuracion/models.py` — `AccesoRapidoPOS.save()` ahora llama
`self.full_clean()` antes de `super().save()`. Cierra la vía ORM/import a nivel
aplicación, **mismo criterio que CFG-006**: el `CheckConstraint` de base se
deja para un preflight coordinado, porque una instalación existente podría tener
filas inválidas y una migración con constraint fallaría en el `migrate`.

- **Sin migración** (`makemigrations --check` → "No changes detected"): es solo
  un override de `save()`, no cambia campos ni `Meta`.
- **Sin romper a nadie**: en producción solo el Admin crea `AccesoRapidoPOS` (ya
  valida vía ModelForm); no hay creación programática en dos pasos. Los tests
  existentes crean filas válidas.

### Pruebas nuevas

`apps/configuracion/tests/test_auditoria_configuracion.py::AccesoRapidoInvarianteTests`
(4 tests): create sin producto para `tipo=producto` se rechaza (la reproducción
exacta); create con producto **y** categoría se rechaza; `save()` directo de una
instancia inválida se rechaza; un acceso válido persiste. Cada uno verifica
además que la fila inválida **no** quedó en la tabla.

## Pruebas — comandos y resultados

```bash
DB_NAME=pos_fifo_dev_cfg010 python manage.py test \
  apps.configuracion apps.ventas.tests.test_accesos_rapidos_pos \
  --settings=config.settings_development --noinput
# Ran 118 tests ... OK  (4 nuevos + configuracion completo + accesos de ventas, 0 regresiones)

python manage.py makemigrations configuracion --check --dry-run   # No changes detected
python manage.py check                                            # 0 issues
```

## Lo que queda ABIERTO en CFG-010 (pata 2 — DECISIÓN PENDIENTE)

El ámbito por sucursal **no** se implementó porque requiere una decisión de
negocio + backfill de datos reales, fuera de un cambio self-contained:

1. **Decidir el ámbito**: ¿los accesos rápidos son por **negocio** o por
   **sucursal**? La arquitectura (config por sucursal, POS corre como un
   `SUCURSAL_CODIGO`, catálogos por tienda) apunta a **sucursal**, pero es una
   decisión explícita.
2. **Migración + backfill**: agregar `FK sucursal` obliga a asignar las filas
   existentes a una sucursal. En una instalación de una sola sucursal es
   directo; en multi-sucursal es ambiguo y es dato real → decisión operativa.
3. **Consumidor**: filtrar `apps/ventas/views.py` (endpoint `accesos-rapidos`)
   por la sucursal actual. Es superficie POS local (C), no toca a Codex/A05.

Recomendación: tratar la pata 2 como su propio mini-bloque una vez decidido el
ámbito; el `CheckConstraint` de exclusividad producto/categoría puede sumarse en
esa misma migración, detrás del preflight.

## Rollback

`git revert` del commit de esta rama. Sin migración, sin estado persistente.

## Deltas propuestos a documentos de seguimiento

- `INVENTARIO.md` fila 212 (`CFG-010`): mantener `PENDIENTE` pero anotar
  "integridad de fila acreditada (`c64255f`); ámbito por sucursal abierto por
  decisión". No pasar a `ACREDITADO` hasta cerrar la pata 2.
- `TODO_AUDITORIAS.md`: idem, dejar CFG-010 con la nota de decisión pendiente.
