# Handoff C02 — COM-012 (tablas de PDF acotadas en memoria), CERRADO

Estado: **CERRADO**, alcance estrictamente acotado al ítem residual
`COM-012` de `docs/handoffs/cierre_prod/INVENTARIO.md` (fila 205).
Fecha: **2026-09-16**. Agente: Claude (implementador de este residual).

## SHA base / resultado

- **Base consumida**: `develop@fffd02b` ("docs(cierre): integrar evidencia
  revisada de C05 parte 2").
- **Worktree**: `C:/Proyectos/pos_fifo_system_c02_com012`, rama nueva
  `claude/cierre-prod-C02-com012` creada desde ese SHA con `git worktree add`.
  DB propia `pos_fifo_dev_c02_com012` (env gitignored), venv externo
  `.venvs/pos_cierre_claude_c03_20260910` (Django 5.2.17), sin tocar el conda
  compartido (que sigue en 5.0.8 y falla `check`).
- **Resultado**: commit `78bec9a` ("fix(common): acotar filas de standard_table
  en memoria (COM-012)") en esa rama (más este ajuste de handoff en un segundo
  commit). **NO publicado a `origin`; NO fusionado** a `develop`/
  `integration/*`/`staging`/`main` — igual criterio que el resto de los bloques
  C0x: cada entrega queda en su rama, la integración se decide en un checkpoint.

## Qué cubre esta entrega

Objetivo único: **`apps/common/pdf/standard.py::standard_table()` deja de
materializar el iterable completo (`list(rows)`) y acota las filas a un tope de
seguridad**, informando el excedente en vez de romper el documento. En un
reporte/estado de cuenta grande, la memoria del worker síncrono ya no queda
atada al tamaño del dato.

### Hallazgo (COM-012)

`standard_table()` hacía `rows = list(rows)` incondicional y luego una segunda
copia al renderizar. Un generador de miles/millones de filas se consumía entero
antes de `doc.build()` (repro de auditoría: 1.000 filas). Es una válvula de
memoria/escalabilidad, no un bug de corrección.

### Cambio (un solo archivo de código)

`apps/common/pdf/standard.py`:

- Nueva constante `TABLA_MAX_FILAS = 5000` — techo de seguridad. Un documento
  real queda muy por debajo, así que **no cambia**; solo el volumen patológico
  se corta.
- Nuevo parámetro `standard_table(..., max_rows: int | None = None)`. `None`
  usa el techo por defecto; un consumidor puede pasar un tope más estricto.
  `max_rows < 0` levanta `TablaInvalida`.
- El cuerpo recorre `rows` **perezosamente** (sin `list(rows)`): corta al llegar
  a `tope`, valida la forma de cada fila dibujada (COM-005 se mantiene) y, si hay
  excedente, agrega **una fila de aviso** (`SPAN` a lo ancho, fondo `LIGHT_BLUE`)
  con el texto "Se muestran las primeras N filas; el resto no se incluye…". Se
  registra un `logger.warning` al truncar.
- Memoria acotada a O(`tope`) sin importar el tamaño del iterable de entrada.

**Por qué solo la primitiva y no los consumidores**: el tope central en
`standard_table()` protege a **todos** los generadores que la usan
(`apps/cotizaciones`, `apps/cuentas_por_cobrar`, `apps/reportes`, `apps/ventas`)
desde un único punto, sin que esta entrega toque código de C05/otros bloques.

### Pruebas nuevas

`apps/common/tests/test_auditoria_common.py::TablaTopeFilasTests` (6 tests):

1. `test_iterable_no_acotado_se_corta_en_el_tope` — un generador **infinito** con
   `max_rows=10` devuelve una tabla de header + 10 + aviso (si volviera el
   `list(rows)`, el test colgaría: es la prueba dura de que el corte es real).
2. `test_excedente_agrega_fila_de_aviso` — la última fila trae el aviso.
3. `test_debajo_del_tope_no_agrega_aviso` — una tabla chica no cambia.
4. `test_tope_por_defecto_generoso_no_trunca_documento_real` — 500 filas sin
   `max_rows` explícito no se truncan; `TABLA_MAX_FILAS == 5000`.
5. `test_max_rows_negativo_se_rechaza` — `TablaInvalida`.
6. `test_tabla_truncada_sigue_generando_pdf` — el documento truncado igual
   produce un `%PDF` válido.

## Pruebas — comandos y resultados

Desde el worktree, venv aislado, `DB_NAME` propio:

```bash
python manage.py test apps.common --settings=config.settings_development
# Ran 49 tests ... OK  (43 previos + 6 nuevos de COM-012, 0 regresiones)

# Regresión de consumidores de standard_table
python manage.py test apps.reportes apps.cotizaciones apps.cuentas_por_cobrar apps.ventas \
  --settings=config.settings_development
# exit 0 — 0 fallos/errores en las 4 suites (el runner sale != 0 ante cualquier fallo).

python manage.py check --settings=config.settings_development
# System check identified no issues (0 silenced).
```

## Archivos

- `apps/common/pdf/standard.py` (modificado — constante + parámetro + cuerpo de
  `standard_table`).
- `apps/common/tests/test_auditoria_common.py` (import + `TablaTopeFilasTests`).
- Este handoff.

`apps/common/AGENTS.md`: no describe la implementación interna de
`standard_table` (entrypoint/invariante), así que no aplica actualizar su
`Última revisión` — verificar al integrar si el mapa menciona el tope.

## Migraciones / BDs

Ninguna. Cambio puramente de rendering.

## Riesgos y alcance

- **Riesgo del cambio: bajo.** Documentos reales (cientos de filas) quedan
  idénticos; solo el volumen > 5.000 filas se corta con aviso visible. La
  validación de forma (COM-005) y el resto del estilo no cambian.
- **Fuera de alcance (respetado):** el pre-armado de querysets en los
  `pdf_generator.py` de los consumidores (p. ej. `apps/cuentas_por_cobrar`
  reúne cuentas/cuotas/pagos antes del build) — bajar eso a lazy/iterador es
  del dueño de cada consumidor (C05/otros). Con el tope central, el riesgo de
  memoria ya queda contenido; optimizar la consulta es una mejora aparte.
- **COM-013** (pin de ReportLab/Pillow) sigue abierto: es A+C, la parte de deps
  es de Codex.

## Rollback

`git revert` del commit de esta rama. Sin estado persistente, sin migración.

## Deltas propuestos a documentos de seguimiento

- `INVENTARIO.md` fila 205 (`COM-012`): `PENDIENTE` → `ACREDITADO` con el commit
  de esta rama, al integrar.
- `TODO_AUDITORIAS.md` (`apps/common`): quitar COM-012 de la lista de abiertos,
  dejando solo COM-013.

## Bloqueos

Ninguno. Entrega cerrada, worktree limpio tras el commit, sin fusionar.
