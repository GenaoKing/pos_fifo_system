# Handoff C02/C05 — REPORTES-CHART-CDN (Chart.js offline), CERRADO

Estado: **CERRADO**, alcance estrictamente acotado al ítem residual
`REPORTES-CHART-CDN` de `docs/handoffs/cierre_prod/INVENTARIO.md` (fila 287) y
al punto 6 pendiente de
[C02-documentos-imagenes-impresion.md](C02-documentos-imagenes-impresion.md#pendientes-explícitos-del-encargo-c02-no-cubiertos-en-esta-entrega).
Fecha: **2026-09-15**. Agente: Claude (implementador/revisor de este residual).

## SHA base / resultado

- **Base consumida**: tip de `integration/cierre-prod-A05-C03`, commit
  `614d741693f373686dffac43e53eaae6f6c6f509` ("docs(cierre): registrar gates
  verdes A05 C03"). Nota: el SHA recibido en el encargo
  (`614d741693f373686dffac43e53eaae6f6f509`) tenía 2 caracteres hex de menos
  (`c6` faltante); se confirmó el commit correcto porque es exactamente el tip
  de la rama nombrada en el encargo y no hay otro candidato en el repo.
- **Worktree**: `C:/Proyectos/pos_fifo_system_c02_chart_offline`, rama nueva
  `claude/cierre-prod-C02-chart-offline` creada desde ese SHA con
  `git worktree add`.
- **Resultado**: commit `b778e24b469353ccf526c5b3a671a492715a5f91` en esa
  rama, con los archivos listados abajo (más este ajuste de handoff en un
  segundo commit sobre la misma rama). Working tree limpio al cierre
  (`git status` sin cambios pendientes). **No publicado a `origin`; no
  fusionado** a
  `integration/*`, `develop`, `staging` ni `main` — igual criterio que el
  resto de los bloques C0x: cada entrega queda en su rama, la integración se
  decide en un checkpoint mayor.
- Entorno de verificación aislado de este worktree: `DB_NAME` propio
  (`pos_fifo_dev_c02_chart_offline`, vía `deploy/env_cliente.env` —
  gitignored, no se commitea) y venv externo ya validado contra Django
  5.2.17 (`requirements-dev.txt`), sin tocar el conda compartido (ver
  `docs/TESTING.md` y la nota de entorno en `ESTADO_AUDITORIAS.md`/handoffs
  previos: el conda `pos_fifo` sigue en Django 5.0.8 y falla
  `manage.py check` con `TypeError: CheckConstraint.__init__() got an
  unexpected keyword argument 'condition'`; no es un bug de este cambio).

## Qué cubre esta entrega

Objetivo único: **`templates/reportes/on_demand.html` deja de cargar Chart.js
desde CDN (`cdn.jsdelivr.net`) y usa el asset local ya existente
`static/js/chart.min.js`** (Chart.js v4.4.0 UMD minificado, idéntica versión
a la que servía el CDN — confirmado por el header del propio archivo). En un
POS sin Internet estable, el dashboard on-demand ahora renderiza sus tres
gráficos (ventas por día, top productos, ventas por cajero) sin depender de
red externa.

### Cambio

`templates/reportes/on_demand.html`, línea 7 — único cambio de código:

```diff
-<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
+<script src="{% static 'js/chart.min.js' %}"></script>
```

`{% load static %}` ya estaba presente en la línea 2 del template — no hizo
falta agregarlo. `STATICFILES_DIRS` (`config/settings.py`) ya incluye
`BASE_DIR / 'static'`, así que `js/chart.min.js` es descubierto por el finder
de `django.contrib.staticfiles` en dev y se copia con `collectstatic` en
producción — mismo mecanismo que ya usa el resto de assets locales del
proyecto (Alpine.js, `utils.js`, `ecf_estado.js`, todos ya sin CDN en este
mismo `<head>`).

### Pruebas nuevas

`apps/reportes/tests/test_chart_offline.py` (`ChartJsOfflineTests`, 3 tests
nuevos, siguiendo el patrón de fixtures de
`apps/reportes/tests/test_modulo_gate.py`):

1. `test_no_referencia_cdn_externo` — la respuesta HTML de `reportes:on_demand`
   ya no contiene `cdn.jsdelivr.net` ni ningún `https://cdn`.
2. `test_referencia_asset_local_de_chart_js` — la respuesta contiene
   exactamente `<script src="{% static 'js/chart.min.js' %}">` resuelto
   (comparado contra `django.templatetags.static.static('js/chart.min.js')`,
   no un string mágico).
3. `test_asset_local_de_chart_js_resuelve_en_staticfiles` — el finder de
   staticfiles (`django.contrib.staticfiles.finders.find`) efectivamente
   localiza `js/chart.min.js` en disco; no alcanza con que la plantilla
   mencione la ruta si el archivo no estuviera.

**Verificación por mutación**: se revirtió temporalmente el `{% static %}` al
`<script src="https://cdn...">` original y se re-corrieron los 3 tests — los
dos primeros fallan reproduciendo exactamente el síntoma (URL de CDN presente
en la respuesta), confirmando que la prueba detecta la regresión real. Se
restauró el fix antes de continuar (`git diff` limpio, un solo cambio de
línea).

## Pruebas — comandos y resultados

Todos corridos desde el worktree, con el venv aislado (no el conda
`pos_fifo` compartido — ver nota de entorno arriba) y `DB_NAME` propio.

```bash
# Foco reportes (incluye el archivo nuevo + toda la suite de la app)
python manage.py test apps.reportes.tests.test_chart_offline \
  apps.reportes.tests.test_modulo_gate apps.reportes.tests.test_dashboard \
  apps.reportes.tests.test_auditoria_reportes apps.reportes.tests.test_pdf_generator \
  apps.reportes.tests.test_comando_cierre_diario \
  apps.reportes.tests.test_verificar_integridad_financiera \
  --settings=config.settings_development
# Ran 57 tests ... OK  (3 nuevos de test_chart_offline + 54 preexistentes, 0 regresiones)

# Confirma que el asset local es descubierto por staticfiles
python manage.py findstatic js/chart.min.js --settings=config.settings_development
# Found 'js/chart.min.js' here:
#   C:\Proyectos\pos_fifo_system_c02_chart_offline\static\js\chart.min.js

# Compatibilidad con collectstatic (dry-run, no escribe fuera del worktree)
python manage.py collectstatic --dry-run --noinput --settings=config.settings_development
# 169 static files copied to '...\staticfiles'. (incluye
# 'Pretending to copy ...\static\js\chart.min.js')

# System checks
python manage.py check --settings=config.settings_development
# System check identified no issues (0 silenced).

# Bytecode de la app y las plantillas tocadas
python -m compileall -q apps/reportes templates
# exit 0

# Espacios en blanco / fin de línea en el diff
git diff --check
# exit 0 (sin errores; solo el aviso habitual de Git sobre LF/CRLF)
```

No se corrió la suite completa del proyecto (fuera de alcance: el cambio es
un único template, sin tocar backend/modelos/migraciones de `reportes` ni de
ninguna otra app).

## Archivos

- `templates/reportes/on_demand.html` (modificado — 1 línea)
- `apps/reportes/tests/test_chart_offline.py` (nuevo)
- Este handoff.

No se tocó `apps/reportes/AGENTS.md`: el mapa no documenta la fuente de
Chart.js (no es un entrypoint, modelo ni invariante), así que no aplica la
regla de actualizar su `Última revisión` en este commit.

## Migraciones / BDs

Ninguna. Cambio puramente de plantilla/estático, sin tocar modelos, servicios
ni backend de `apps/reportes`.

## Fuera de alcance (respetado, no tocado en esta entrega)

- El bundle local `static/js/chart.min.js` (Chart.js v4.4.0) — no se
  actualizó ni se reemplazó, solo se referenció.
- `requirements*`, lockfiles, CI, APIs, modelos, migraciones, backend de
  `apps/reportes`, tenancy, sync, A05, CT-04, C04, C06.
- Despliegues, dispositivos o datos reales.
- El resto de los pendientes de C02 (COM-012, COM-013, impresión con
  cuota/auditoría, matriz física) — siguen exactamente como los dejó
  [C02-documentos-imagenes-impresion.md](C02-documentos-imagenes-impresion.md);
  esta entrega solo cierra su punto 6.

## Riesgos y hallazgo colateral (no corregido, fuera de alcance)

- **Riesgo del cambio en sí: ninguno nuevo.** El bundle local es exactamente
  la misma versión (4.4.0) que servía el CDN, mismo build UMD, misma API
  pública (`window.Chart`); los tres `renderChart*` del template no cambian.
  Un navegador con caché agresiva del script viejo del CDN simplemente deja
  de pedirlo — no hay migración de datos ni de contrato.
- **Hallazgo colateral, NO corregido a propósito** (fuera del alcance
  estricto de esta entrega): al inspeccionar la respuesta renderizada se
  observó que el comentario Django multilínea que documenta el uso de
  `json_script` (líneas 9-13 del template, inmediatamente debajo del script
  de Chart.js) **se filtra como texto literal en el HTML** en vez de
  eliminarse. Causa: la sintaxis `{# ... #}` de Django **no soporta
  comentarios multilínea** (limitación documentada de Django, no un bug de
  este cambio) — para eso existe `{% comment %}...{% endcomment %}`. Es
  cosmético (cae en un nodo de texto dentro de `<head>`, sin efecto
  funcional ni de seguridad) y preexistía antes de este commit — confirmado
  reproduciéndolo también con el `<script src="https://cdn...">` original
  durante la verificación por mutación. Se documenta acá para que quien
  toque `apps/common`/plantillas lo tenga presente; no se corrige en este
  commit por no ser parte del objetivo estricto encargado.

## Rollback

`git revert b778e24b469353ccf526c5b3a671a492715a5f91` sobre esta rama. Sin estado persistente,
sin migración, sin dato de producción tocado — revertir el commit deja el
template exactamente como estaba (CDN) sin ningún otro efecto.

## Deltas propuestos a documentos que no edita esta entrega

Por convención del proyecto (ver el mismo patrón en
[C02-documentos-imagenes-impresion.md](C02-documentos-imagenes-impresion.md#deltas-propuestos-a-documentos-que-edita-codex)),
esta entrega no edita directamente los documentos compartidos de seguimiento
para no pisar trabajo concurrente de Codex/otro agente. Deltas sugeridos para
cuando se integre:

- **`docs/handoffs/cierre_prod/INVENTARIO.md`** (fila 287,
  `REPORTES-CHART-CDN`): mover de `PENDIENTE` a `ACREDITADO`, con referencia
  a este handoff y al commit final de esta rama.
- **`docs/TODO_AUDITORIAS.md`** (sección "🔵 Presentación y rendimiento"):
  marcar el ítem "Chart.js desde CDN sin integridad ni fallback local" como
  hecho, referenciando este handoff.
- **`docs/ESTADO_AUDITORIAS.md`** (§4, "Presentación y rendimiento"): mover el
  bullet de Chart.js del listado "fuera de alcance" a la lista de resueltos,
  igual que se hizo con "Historial de turnos paginado en C05" (`eb72f69`).
- **`docs/handoffs/cierre_prod/C02-documentos-imagenes-impresion.md`**
  (sección "Pendientes explícitos... NO cubiertos"): tachar/anotar el punto 6
  como cerrado por esta entrega, dejando abiertos únicamente COM-012, COM-013,
  impresión (punto 5) y matriz física (punto 7).

## Dependencias del otro agente

Ninguna. Cambio autocontenido en `apps/reportes` (solo template + test),
sin requerir CT-01/CT-02/CT-04 ni código de Codex.

## Bloqueos

Ninguno. Entrega cerrada, worktree limpio, sin fusionar.
