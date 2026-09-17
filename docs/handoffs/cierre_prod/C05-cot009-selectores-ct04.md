# Handoff C05 — COT-009, selectores comerciales usando CT-04

Estado: **ACREDITADO**. Fecha: **2026-09-17**. Agente: Claude.

## Hallazgo real antes de escribir código

El encargo (`docs/planes/CIERRE_PROD_CLAUDE.md`, C05 punto 5) pedía: *"Selección
y escaneo comerciales solo de maestros operativamente activos; revalidar al
confirmar venta/cotización... Usar CT-04 para pendiente/conflicto."*

Antes de diseñar nada se corrió `git log --all --grep` sobre `PRO-007` y se leyó
`apps/ventas/AGENTS.md` — el mecanismo **ya existía**, construido por Codex
dentro de A05.2a (no es una entrega mía nueva de cero). `apps/productos/models.py`
ya tiene:

- `productos_vendibles(queryset=None)` — filtra `activo=True`,
  `categoria__activa=True` y excluye por `Exists()` cualquier producto o
  categoría con una fila `MutacionMaestro` en `CONFLICTO`.
- `tiene_conflicto_maestro(producto)` y la propiedad `Producto.es_vendible`.

Y ya estaba consumido en **todos** los puntos de venta: búsqueda (`/pos/api/buscar/`),
escaneo por código, accesos rápidos de producto, verificación de stock, y —el
gate que de verdad importa— `_cargar_productos()` dentro de la transacción de
`procesar_venta_service`, que ya distingue los tres motivos ("está inactivo",
"pertenece a una categoría inactiva", "tiene un conflicto de maestro pendiente
de resolver") en el mensaje de `ProductoInexistenteError`. `guardar_cotizacion`
(`apps/cotizaciones/views.py`) también lo usaba desde antes.

**Conclusión:** COT-009 no estaba "bloqueado por CT-04 sin construir" — el
código ya cumplía la semántica congelada por CT-04 (`PENDIENTE`/`ENVIANDO`
vendibles, `CONFLICTO` bloquea producto+categoría, `RECHAZADA` no bloquea).
Bloqueado estaba, en realidad, solo un tramo mucho más chico: cobertura de
test del lado producto (solo existía para cliente inactivo) y una asimetría
menor en accesos rápidos. Ver `[[verificar-ledger-antes-de-status]]` — mismo
patrón de ledger atrasado que ya había pasado con SUS-014 el mismo día.

## Qué se cerró en esta rama

1. **Gap real — sin test:** `apps/cotizaciones/tests/test_cotizacion_hardening.py`
   no tenía ningún caso de producto/categoría inactivos o en `CONFLICTO` en una
   cotización (solo `COT009ClienteActivoTests`, del lado cliente). Se agregó
   `COT009ProductoVendibleTests` (5 casos): producto inactivo → 400; categoría
   inactiva → 400; producto en `CONFLICTO` → 400 y la fila no se pierde
   (`estado` sigue `CONFLICTO`, no se auto-resuelve); categoría en `CONFLICTO`
   bloquea sus productos; producto con mutación `PENDIENTE` sigue cotizable
   (control negativo: `PENDIENTE` no debe bloquear).
2. **Gap real — asimetría menor:** `accesos_rapidos_pos`
   (`apps/ventas/views.py`) filtraba el tipo PRODUCTO con `es_vendible`
   (incluye conflicto) pero el tipo CATEGORIA solo con `.activa` (sin
   conflicto). No era explotable — el botón solo dispara `buscar_productos`,
   que ya filtra bien — pero mostraba un botón "vivo" para algo que no vendía
   nada. Se agregó `_categoria_en_conflicto_maestro(categoria_id)` (consulta
   de solo lectura sobre `MutacionMaestro`, mismo patrón que
   `tiene_conflicto_maestro` en `apps/productos/models.py`, pero **sin tocar
   ese archivo** — es de Codex) y se sumó al filtro. Test nuevo:
   `AccesosRapidosConflictoMaestroTests` (2 casos).
3. **No se tocó** (fuera de alcance de este handoff, no relacionado con el
   punto 5): `crear_cotizacion` (GET) construye un `productos = Producto.objects.filter(activo=True)`
   que el template nunca usa (la búsqueda real va por `/pos/api/buscar/`,
   correctamente filtrada) — código muerto, no un gap de seguridad. Queda
   anotado para quien haga limpieza de `apps/cotizaciones`.

## Límite de propiedad respetado

`apps/productos/models.py`, `apps/sync/models.py` y `apps/sync/AGENTS.md` son
de Codex (A05/A06) — no se editó ninguno. El fix de accesos rápidos vive
enteramente en `apps/ventas/views.py` (mío) con un import local a
`apps.sync.models.MutacionMaestro`, mismo patrón de lectura ya sancionado por
`apps/productos/models.py`.

## Pruebas — comandos y resultado

Worktree `C:/Proyectos/pos_fifo_system_c05_ct04`, venv propio (copiado de
`pos_fifo_system_a06` por robocopy), DB aislada `pos_c05_ct04_20260917`.

```bash
DB_NAME=pos_c05_ct04_20260917 python manage.py test \
  apps.ventas.tests.test_accesos_rapidos_pos \
  apps.cotizaciones.tests.test_cotizacion_hardening \
  apps.ventas.tests.test_ventas_service \
  apps.productos.tests.test_mutaciones_maestro_a052a \
  --settings=config.settings_development --noinput
# Ran 78 tests — OK (7 nuevos, 0 regresiones)

DB_NAME=pos_c05_ct04_20260917 python manage.py test apps.ventas apps.cotizaciones apps.productos \
  --settings=config.settings_development --noinput
# Ran 258 tests — OK

python manage.py makemigrations --check --dry-run   # No changes detected
python manage.py check                              # 0 issues
```

Sin migración: no se tocó ningún modelo.

## Base y alcance

Base: `integration/cierre-prod-A05-C03@609c98f` (ya incluye A05.1-A05.4, CT-04
publicado, y el handoff C04). Rama `claude/cierre-prod-C05-ct04-selectores`.
**NO publicado, NO fusionado, NO deployado.** `develop` sigue en `fffd02b`
(no tiene A05.2a todavía, así que este selector no aplica ahí — vive donde
vive `MutacionMaestro`).

## Deltas propuestos a documentos de seguimiento

Aplicados en esta misma rama (no solo propuestos, dado el tamaño acotado):

- `INVENTARIO.md` `COT-009`: `PARCIAL_C05; bloqueado CT-04` → `ACREDITADO`.
- `TODO_AUDITORIAS.md`: dos menciones de COT-009 actualizadas para reflejar
  que el selector ya existía y solo faltaba test + el accesorio de accesos
  rápidos.

## Contraparte frontend (C04) — integrar en conjunto

El portal React que refleja esta semántica vive en el repo separado
`pos-cloud-dashboard`, rama `claude/cierre-prod-C04`, commit **`e319058`**.
Codex debería integrar esa rama frontend **junto con** esta rama backend: son
las dos mitades de la historia "maestros operativamente activos" de CT-04.

- **Resolución de CONFLICTO** — pantalla `/maestros/conflictos` (entrega
  original de C04, `d4b3b5d`/`928964c`): lista y resuelve las `MutacionMaestro`
  en `CONFLICTO`/`RECHAZADA` que este backend produce. Es la vía por la que un
  admin destraba lo que acá se bloquea en la venta/cotización.
- **Categoría inactiva (PRO-007)** — nuance de la vista Productos (`e319058`):
  la respuesta de `Producto` no dice si su categoría está activa, así que el
  front arma el mapa de categorías y (a) marca con badge "Categoría inactiva"
  todo producto `activo=true` cuya categoría no lo está, y (b) en el filtro
  "Inactivos" agrega una sección paginada por cada categoría inactiva con sus
  productos activos (`activo=true&categoria=<id>`, server-side, sin merge en
  cliente). Es el reflejo en el portal del mismo `categoria__activa` que
  `productos_vendibles()` exige acá.

**Alcance, para no confundir los ejes:** el nuance de Productos cubre el eje
*categoría inactiva* (`categoria.activa=False`), **no** el eje *MutacionMaestro
`CONFLICTO`* — ese lo cubre la pantalla de Conflictos. Ambos bloquean la venta
en el POS, pero por motivos distintos; este handoff (backend) es el que hace
efectivo el bloqueo por `CONFLICTO`.
