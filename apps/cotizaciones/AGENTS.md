# apps/cotizaciones — mapa para agentes

<!-- Última revisión: 2026-09-08 -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

`Cotizacion` (`PENDIENTE` / `CONVERTIDA`, vence a 15 días, `venta` OneToOne
cuando se convierte) → `DetalleCotizacion`. PDF de cotización y carga de la
cotización en el POS para convertirla en venta. `apps/cotizaciones/models.py`,
`views.py`, `pdf_generator.py`.

## Entrypoints (`app_name = 'cotizaciones'`, montado en `/cotizaciones/`)

| Necesito… | Voy a… |
| --- | --- |
| Listar / crear / detalle | `views.lista_cotizaciones`, `crear_cotizacion`, `detalle_cotizacion` |
| Guardar desde la UI | `views.guardar_cotizacion` (`api/guardar/`) |
| Cargarla en el POS | `views.obtener_datos_cotizacion` (`api/<id>/datos/`) |
| **Convertirla en venta** | pasa `cotizacion_id` a `procesar_venta_service` (`apps/ventas`): la marca `CONVERTIDA` y la vincula **en la misma transacción**. `views.marcar_convertida` es el endpoint legacy |
| PDF | `views.descargar_pdf_cotizacion` → `pdf_generator.CotizacionPDF` |
| Alcance | `views._cotizaciones_en_alcance(request)` (COT-005) |

## Invariantes / trampas

- **El precio lo decide el servidor** (`_precio_autorizado`, COT-002): cotizar
  por debajo del precio vigente exige `cotizaciones.precio_negociado`. La
  cotización es *fuente autorizada de precio* para la venta, así que este gate
  es una decisión financiera.
- No se convierte en otra sucursal, para otro cliente, vencida, ni por más
  unidades que las cotizadas (ver `tests/test_auditoria_cotizaciones.py`).
- Permisos: `cotizaciones.ver`, `cotizaciones.crear`; módulo `cotizaciones`.
- Sync: `evento_cotizacion_creada`, `evento_cotizacion_convertida`
  (`apps/sync/events.py`); handlers en `apps/api/views/sync.py`.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_COTIZACIONES.md`)
  — **snapshot histórico**, verificar contra código.
