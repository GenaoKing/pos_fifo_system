# apps/ventas — mapa para agentes

<!-- Última revisión: 2026-09-07 -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

El POS local: pantalla de punto de venta, procesamiento de la venta (consumo de
stock **FIFO**), pagos, anulaciones, financiación cooperativa y PDFs.

## Entrypoints (`app_name = 'pos'`, montado en `/pos/`)

| Necesito… | Voy a… |
| --- | --- |
| Pantalla del POS | `apps/ventas/views.py` → `punto_venta` |
| **Procesar una venta** (la lógica real) | `apps/ventas/services/ventas_service.py` → `procesar_venta_service` |
| Anular una venta | `apps/ventas/services/anulaciones_service.py` → `anular_venta_service` |
| Consumir/devolver stock FIFO | delega en `apps/inventario/fifo_logic.py` → `procesar_venta_fifo` |
| Errores de dominio | `apps/ventas/services/exceptions.py` |
| Rutas / APIs del POS | `apps/ventas/urls.py` |
| PDFs | `apps/ventas/pdf_comprobante.py`, `pdf_financiacion.py` |

`views.procesar_venta` es una capa fina: valida request y llama al service. **La
lógica de negocio vive en `services/`, no en las vistas.**

## Modelos (`apps/ventas/models.py`)

`Venta` → `DetalleVenta` (guarda `costo_fifo` consumido) · `Pago` ·
`FinanciacionCooperativa`.

## Flujo de `procesar_venta_service` (orden que importa)

1. **Autorización de descuento se evalúa ANTES de la transacción** — no tocar
   inventario/FIFO si la venta no procede.
2. Dentro de la transacción: `_crear_venta` → `_crear_detalles_y_consumir_fifo`
   (FIFO **bloquea los lotes candidatos**) → `_registrar_pagos` → `_verificar_pagos`.
3. Hooks post-commit: impresión de ticket, encolado ECF (`_hook_encolar_ecf`).

## Sync (cómo sale al cloud)

Emite eventos vía `apps/sync/events.py`: `evento_venta_creada`,
`evento_venta_anulada`. Registro en `apps/sync/registry.py` (`clave='ventas'`).
No hagas push directo desde aquí — solo emití el evento al outbox.

## Trampas

- Todo lo que muta stock/pagos va dentro de la transacción del service; nada de
  escribir inventario desde la vista.
- `condicion_pago` a crédito y `turno_caja` cambian validaciones de pago.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_VENTAS.md`) —
  **snapshot histórico**, verificar contra código.
