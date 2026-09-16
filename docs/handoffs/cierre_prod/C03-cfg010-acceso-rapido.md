# Handoff C03 — CFG-010 (AccesoRapidoPOS: integridad + ámbito por sucursal)

Estado: **CERRADO en código** (pata 1 integridad de fila + pata 2 ámbito por
sucursal). Queda **un preflight operativo** antes de "endurecer": el
`CheckConstraint` de exclusividad y el backfill de las filas legacy (NULL). Ítem
`CFG-010` de `docs/handoffs/cierre_prod/INVENTARIO.md` (fila 212).
Fecha: **2026-09-16**. Agente: Claude.

> **Actualización 2026-09-16:** la pata 2 (ámbito) tenía una decisión de negocio
> pendiente; el usuario decidió **ámbito = por sucursal**, y se implementó (ver
> sección "Pata 2" abajo). El handoff original la dejaba ABIERTA.

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
# Ran 120 tests ... OK  (pata 1: 4 + pata 2: 2 nuevos + configuracion + accesos, 0 regresiones)

python manage.py makemigrations --check --dry-run   # No changes detected (0011 captura todo)
python manage.py check                              # 0 issues (related_name compartido OK)
```

## Pata 2 — ámbito por sucursal (IMPLEMENTADO, ámbito = por sucursal)

Decisión de negocio (usuario, 2026-09-16): **cada acceso pertenece a una
sucursal.** Implementado de forma **no destructiva** para no romper instalaciones
existentes en el `migrate`:

- **Modelo** (`apps/configuracion/models.py`): nuevo `AccesoRapidoPOS.sucursal`
  = FK a `sucursales.Sucursal`, `null=True, blank=True`, `on_delete=CASCADE`
  (los botones de una sucursal mueren con ella). **`null` deliberado**: las filas
  creadas antes de este campo quedan en NULL = **legacy global** (visibles en
  toda sucursal) hasta que un operador las reasigne.
- **Migración** `apps/configuracion/migrations/0011_accesorapidopos_sucursal.py`
  — solo `AddField` nullable, sin backfill: el `migrate` no toca datos.
- **Consumidor** (`apps/ventas/views.py`, endpoint `accesos-rapidos`): filtra
  `Q(sucursal=get_sucursal_actual()) | Q(sucursal__isnull=True)`. Cada sucursal
  ve los suyos + los legacy globales. En modo legacy (sin `SUCURSAL_CODIGO`,
  `get_sucursal_actual()` = None) solo se ven los NULL.
- **Admin** (`apps/configuracion/admin.py`): `sucursal` en el fieldset "Boton",
  `list_display` y `list_filter`, con nota de "vacío = legacy global".
- **Tests** (`apps/ventas/tests/test_accesos_rapidos_pos.py::
  AccesosRapidosPorSucursalTests`, 2): un acceso de la sucursal A NO aparece al
  consultar como B; cada sucursal ve los suyos + los legacy.

### Preflight operativo pendiente (NO ejecutar a ciegas)

1. **Backfill de las filas legacy (NULL)**: en una instalación de una sola
   sucursal, asignarlas a esa sucursal es directo; en multi-sucursal hay que
   decidir por fila. Mientras tanto siguen visibles en todas (comportamiento
   previo, sin regresión).
2. **`CheckConstraint` de exclusividad producto/categoría**: se deja para el
   mismo preflight (criterio CFG-006). Hoy la invariante está garantizada a
   nivel app por `full_clean()` en `save()` (pata 1); el constraint de base
   endurece contra escrituras que salteen el modelo, pero podría fallar el
   `migrate` si hubiera filas inválidas preexistentes.

## Rollback

`git revert` de los commits de esta rama. La migración `0011` solo agrega una
columna nullable; revertirla es un `RemoveField` sin pérdida de datos operativos
(los accesos siguen existiendo, sin ámbito).

## Migraciones / BDs

`0011_accesorapidopos_sucursal` (AddField nullable, no destructiva). **Al
integrar sobre `integration/cierre-prod-A05-C03`**: verificar que no colisione el
número `0011` con otra migración de `configuracion` que haya entrado allí; si
colisiona, `makemigrations --merge`.

## Deltas propuestos a documentos de seguimiento

- `INVENTARIO.md` fila 212 (`CFG-010`): `PENDIENTE`→`ACREDITADO` con los commits
  de esta rama, anotando que el `CheckConstraint` de exclusividad y el backfill
  de filas legacy quedan como **preflight operativo** (no bloquean el código).
- `TODO_AUDITORIAS.md`: quitar CFG-010 de abiertos; dejar la nota del preflight.
