# apps/cotizaciones — mapa para agentes

<!-- Última revisión: 2026-09-18 -->

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
- **Invariantes de BD (COT-008/015):** `DetalleCotizacion` con cantidad `>= 1`,
  precio `>= 0.01`, descuento `>= 0` y `<= subtotal`; una `Cotizacion` en estado
  `CONVERTIDA` **exige** `venta` y una no convertida no puede quedar vinculada
  (constraint `cotizacion_estado_venta_consistente`); `venta` usa
  `on_delete=PROTECT`. Los números legacy sin sucursal también son únicos.
  Preflight:
  `manage.py verificar_integridad_financiera`.
- Permisos: `cotizaciones.ver`, `cotizaciones.crear`; módulo `cotizaciones`.
- **Numeración (COT-010):** `Cotizacion.save()` usa máximo sufijo + reintento en
  savepoint (no `count()+1`), igual que `Venta`/`_guardar_con_correlativo`.
- **Totales (COT-011):** guardar o borrar un `DetalleCotizacion` reconcilia la
  cabecera; el Admin muestra sus totales como campos derivados de solo lectura.
- **Auditoría (COT-012):** crear (`guardar_cotizacion`) y convertir por endpoint
  (`marcar_convertida`) usan el contrato CT-01 `registrar_mutacion`
  (`cotizaciones.cotizacion.creada` / `.convertida`) DENTRO de la transacción
  (C05 p6). La conversión POR VENTA (`ventas_service`) sigue con el productor
  legacy — follow-up. `lista_cotizaciones` pagina (COT-017); el
  descuento/cantidad se sanean en el servidor (COT-008).
- Sync: `evento_cotizacion_creada`, `evento_cotizacion_convertida`
  (`apps/sync/events.py`); handlers en `apps/api/views/sync.py`.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_COTIZACIONES.md`)
  — **snapshot histórico**, verificar contra código.
