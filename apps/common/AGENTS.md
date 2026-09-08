# apps/common — mapa para agentes

<!-- Última revisión: 2026-09-08 -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

Librería transversal, **no** es una app Django (sin modelos, no está en
`INSTALLED_APPS`). Hoy contiene solo el kit de PDF con ReportLab:
`apps/common/pdf/standard.py`.

## Entrypoints (`from apps.common.pdf.standard import ...`)

| Necesito… | Voy a… |
| --- | --- |
| Documento Carta con márgenes | `document(target=None, pagesize=PAGE_SIZE)` |
| Encabezado con logo/RNC/tel/dirección | `business_header(config)` — el logo se resuelve local **o** Azure Blob (`_logo_source`) |
| Título, secciones, grillas, tablas, totales, firmas, nota | `document_title`, `section_title`, `info_grid`, `standard_table`, `totals_table`, `signature_block`, `note` |
| Pie con fecha y página | `footer_canvas` (usa `doc.pagesize`, hora local) |
| Formatear dinero / fecha / texto | `money` (`RD$`, **levanta `ImporteInvalido`**), `date`, `clean` (escapa y trunca a 4000) |

## Quién lo usa

`apps/cotizaciones/pdf_generator.py`, `apps/cuentas_por_cobrar/pdf_generator.py`,
`apps/reportes/pdf_generator.py`, `apps/ventas/pdf_financiacion.py`,
`apps/ventas/pdf_comprobante.py`. **Todos** encabezan con
`config_para_documento(sucursal)` — nunca `get_config()` (COM-001). Lo vigila
`test_los_generadores_ya_no_resuelven_por_settings`
(`apps/common/tests/test_auditoria_common.py`): un generador nuevo va ahí.

## Invariantes / trampas

- Tamaño **Carta** (`PAGE_SIZE = letter`). `CONTENT_WIDTH` es una constante de
  Carta vertical: con otro `pagesize`, pasá `width=` a las tablas.
- `money()` no convierte basura en `0.00`: capturá `ImporteInvalido` en la vista.
- ReportLab **consume** la lista de flowables en `build()`: si la inspeccionás
  en un test, copiala antes.
- Contrato mínimo de test: la salida empieza con `%PDF`.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_COMMON.md`) —
  **snapshot histórico**, verificar contra código.
