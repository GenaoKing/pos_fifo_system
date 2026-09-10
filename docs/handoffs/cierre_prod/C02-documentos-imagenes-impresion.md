# Handoff C02 — Documentos, imágenes e impresión (entrega parcial 1)

Estado: **EN_CURSO** (primera entrega de C02; quedan items del encargo sin
cerrar, listados abajo). Fecha: **2026-09-10**.
Agente: B / Claude. Encargo: [CIERRE_PROD_CLAUDE.md](../../planes/CIERRE_PROD_CLAUDE.md#c02--documentos-imágenes-e-impresión).

## SHA base / resultado

- Base consumida: tip de `claude/cierre-prod-C01`
  (`157b7ed42ee02a9493082938f1608d70326043bc`), worktree
  `C:/Proyectos/pos_fifo_system_cierre_claude`, rama `claude/cierre-prod-C02`.
- Resultado: commit(s) local(es) en esa rama con los archivos listados abajo.
  **No publicado a `origin`** (evita disparar CI/deploy de `develop`).

## Qué cubre esta entrega

Punto 1 del encargo ("revalidar deuda COM y documentos frente al código
actual") para los seis P2 de `apps/common/pdf` que el inventario dejó
deliberadamente abiertos, y el arranque del punto 2/3 ("logo inválido degrada
con aviso/auditoría, no invalida el importe ni hace caer toda la operación" /
"validar imágenes/logo con límite de 8 MiB"). Alcance exacto: **COM-005,
COM-006, COM-007, COM-008, COM-009 y COM-014**, todos dentro de
`apps/common/pdf/standard.py` — propiedad exclusiva de C02, sin tocar código
de Codex ni requerir CT-01/02 implementados.

### COM-009 (el más urgente de los seis) — logo remoto sin límite de memoria

`_logo_source()` leía `BytesIO(logo.read())` completo, sin comprobar tamaño
antes. Ahora:

- Si el logo resuelve a ruta local, se comprueba `os.path.getsize()` contra
  `LOGO_MAX_BYTES` (importado de `utils.imagenes.TAMANO_MAX_BYTES` — mismo
  límite de 8 MiB que ya rige la subida de imágenes de producto, "tratamiento
  coherente" que pide el punto 3 del encargo) antes de abrir el archivo.
- Si resuelve a storage remoto, primero se consulta `.size` (llamada de
  metadata, sin traer el blob) como atajo rápido; **además**, se lee en
  bloques de 64 KiB con corte apenas se supera el límite (`_leer_acotado`),
  como defensa en profundidad por si el storage no reporta `.size` o miente.
  Cubierto por `test_logo_remoto_declarado_sobre_el_limite_se_rechaza_sin_leerlo`
  (confirma que ni siquiera se intenta leer) y
  `test_logo_remoto_sin_size_declarado_se_acota_durante_la_lectura`.

### COM-007 — logo corrupto rompía TODO el documento

`_logo_source()` ahora decodifica con Pillow (`Image.open(...).verify()`)
antes de entregar la fuente a ReportLab, tanto para archivo local como remoto.
Un blob corrupto se descarta con `logger.warning` y el documento se sigue
generando sin logo, en vez de propagar `UnidentifiedImageError` sin capturar.

### COM-008 — fallo de storage indistinguible de "no hay logo"

Antes, cualquier `Exception` al abrir/leer el storage caía al mismo `None`
que "el negocio no configuró logo", sin registrar nada. Ahora cada causa deja
su propio `logger.warning('common.pdf', …)` con el nombre del archivo y el
tipo de excepción (fallo al consultar tamaño, al abrir, al leer, contenido
inválido) — la ausencia genuina de logo sigue sin loguear nada (verificado en
`test_ausencia_de_logo_no_registra_nada` con `assertNoLogs`).

**Nota importante — el "aviso" hoy es solo logging, no auditoría CT-01.** El
encargo pide "aviso/auditoría"; `registrar_mutacion` (CT-01) todavía no tiene
implementación de A02 (`CONTRATOS.md` lo marca `PENDIENTE A02`), así que cablear
un evento de auditoría real por cada logo degradado queda para cuando ese
commit esté integrado — el punto de enganche es exactamente este
`logger.warning`, ya con el contexto (archivo, causa) que necesitaría el
evento.

### COM-014 — todo logo se deformaba a un cuadrado de 0.9in

`_logo_flowable()` nuevo: lee el tamaño real con Pillow y escala manteniendo
proporción dentro de la misma caja de 0.9×0.9in (no cambia el footprint del
header, así que el texto no se corre). Cubierto con logos horizontal/vertical/
cuadrado en `LogoProporcionTests`.

### COM-005 / COM-006 — forma de tablas no validada, estructuras vacías crudas

Reproducción del hallazgo confirmada primero (dos headers + una fila de tres
valores generaba tres columnas de 259.2pt = 777.6pt totales sobre los 518.4pt
disponibles, sin error) y corregida: `standard_table`, `info_grid`,
`totals_table` y `_normalize_widths` ahora validan forma **antes** de
construir flowables y levantan `TablaInvalida` (nueva excepción, mismo patrón
que `ImporteInvalido`):

- Filas con longitud distinta a `len(headers)`.
- `aligns` o `col_widths` con cantidad de valores distinta a las columnas.
- `col_widths` con ceros, negativos o no-finitos (`NaN`/`inf`).
- `status_col` fuera de rango.
- `info_grid` con una fila vacía (antes: `ZeroDivisionError` crudo).
- `totals_table([])` (antes: `ValueError` interno de ReportLab).
- `business_header(..., width=angosto)` ya no resta un ancho fijo de logo sin
  comprobar el total disponible: si no queda margen para la columna de info
  (caso ticket térmico), omite el logo en vez de producir un ancho negativo.

Verificado que ningún caller real (`apps/ventas`, `apps/cotizaciones`,
`apps/cuentas_por_cobrar`, `apps/reportes`) dispara la nueva validación: sus
79 tests de PDF corren sin cambios (ver «Pruebas»).

## Pruebas

```
python manage.py test apps.common apps.tenancy.tests.test_media \
  apps.ventas.tests.test_pdf_comprobante apps.ventas.tests.test_pdf_financiacion \
  apps.reportes.tests.test_auditoria_reportes apps.reportes.tests.test_pdf_generator \
  apps.cuentas_por_cobrar.tests.test_exports \
  apps.cotizaciones.tests.test_auditoria_cotizaciones apps.cotizaciones.tests.test_pdf \
  --settings=config.settings_development
# Ran 131 tests ... OK.
# Desglose: 43 en apps.common (22 previos + 21 nuevos de esta entrega),
# 9 en apps.tenancy.tests.test_media (logo remoto/local, sin regresion tras
# ajustar el doble de prueba a lectura por bloques), 79 en los cinco
# generadores consumidores (ventas x2, reportes x2, cuentas_por_cobrar,
# cotizaciones x2). Cero regresiones.
```

31 tests nuevos/tocados en `apps/common/tests/test_auditoria_common.py`
(`LogoRobustezTests`, `LogoProporcionTests`, `TablaFormaTests`,
`EstructurasVaciasTests`) más el fix del doble de `apps/tenancy/tests/test_media.py`
(`_BlobBackedLogo.read()` ahora acepta `size=` para simular lectura por
bloques — el fallo real de storage que simula ese test sigue cubierto).

## Archivos

- `apps/common/pdf/standard.py` (modificado)
- `apps/common/tests/test_auditoria_common.py` (modificado)
- `apps/tenancy/tests/test_media.py` (modificado — solo el doble de prueba,
  sin tocar código de producción de Codex)
- `apps/common/AGENTS.md` (actualizado, fecha de revisión)
- Este handoff.

## Migraciones / BDs

Ninguna.

## Deltas propuestos a documentos que edita Codex

Para `docs/TODO_AUDITORIAS.md` (sección "Pendientes de `apps/common`"):
mover **COM-005, COM-006, COM-007, COM-008, COM-009, COM-014** de abierto a
corregido; quedan abiertos únicamente **COM-012** (tablas materializan todos
los registros antes de maquetar) y **COM-013** (builds no fijan
ReportLab/Pillow).

Para `docs/ESTADO_AUDITORIAS.md`: registrar que C02 cerró el clúster de logo y
de forma-de-tabla de `apps/common/pdf`, con referencia a este handoff.

Para `docs/exploracion/AUDITORIA_CODIGO_APPS_COMMON.md`: es snapshot
histórico, no se edita; su tabla de mitigación queda desactualizada a
propósito (la fuente de verdad pasa a ser este handoff + el código).

## Pendientes explícitos del encargo C02, NO cubiertos en esta entrega

1. **COM-012** (tablas materializan todos los registros antes de maquetar —
   punto 4 del encargo, "PDFs de listas grandes paginados y consumo acotado").
2. **COM-013**: pedido de dependencias a Codex para fijar ReportLab/Pillow en
   `requirements*` (no es archivo mío — `requirements*, locks... | A` en
   `CONTRATOS.md`); solo puedo solicitarlo, no fijarlo.
3. Punto 5 del encargo: impresión autorizada con cuota/auditoría,
   reimpresión explícita y trazable, gramática de códigos de barra —
   `utils/impresoras/*` sin tocar todavía.
4. Punto 6: quitar la dependencia de Chart.js por CDN
   (`templates/reportes/on_demand.html`, TODO_AUDITORIAS lo lista en
   "Presentación y rendimiento").
5. Punto 7: matriz física (ticket, etiqueta, comprobante, reimpresión,
   nombres largos, imágenes ausentes, importes límite, drivers/cuenta NSSM) —
   requiere hardware real, corresponde a C06 y la visita.
6. `ConfiguracionNegocio.logo` (modelo, `apps/configuracion`) sigue sin
   validator de tamaño/formato en el momento de subir — lo que se corrigió
   acá es la lectura defensiva **al renderizar** (la otra mitad que pide
   COM-007/009: "validar... al subir Y NUEVAMENTE al renderizar"). Ese modelo
   es de C03 (`apps configuracion` es propiedad de C03, no de C02); queda
   como dependencia hacia ese bloque, no hacia Codex.

## Riesgos y rollback

- Riesgo: un logo de producción que hoy supera 8 MiB o no decodifica (viejo,
  corrupto en el storage) dejará de aparecer en los documentos a partir de
  este cambio — antes probablemente ya fallaba o tumbaba el documento (COM-007
  garantiza que **no** funcionaba bien), pero conviene que el operador lo sepa
  antes de desplegar. Sin datos de producción para confirmar si algún cliente
  actual está en ese caso.
- Rollback: `git revert` del/de los commit(s); sin estado persistente ni
  migración que deshacer.

## Dependencias del otro agente

- Ninguna nueva de Codex en esta entrega (COM-013 es un pedido, no un
  bloqueo — se puede seguir trabajando sin él).

## Siguiente tarea desbloqueada

Quedan del encargo C02: COM-012 (paginación/streaming de listas grandes),
impresión con cuota/auditoría (`utils/impresoras`), retirar Chart.js,
y la matriz física para C06. Se continúa en el mismo hilo salvo indicación
distinta del usuario.
