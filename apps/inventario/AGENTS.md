# apps/inventario — mapa para agentes

<!-- Última revisión: 2026-09-08 -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

Compras y stock por lotes **FIFO** (`apps/inventario/models.py`): `Compra` →
`DetalleCompra` (crea su `Lote`) · `Lote` (`cantidad_actual`, `activo`) ·
`MovimientoLote` (`COMPRA`/`VENTA`/`AJUSTE`/`ANULACION`/`MERMA`/`DANO`) ·
`AjusteInventario`. El costo que consume cada venta sale de aquí.

## Entrypoints (`app_name = 'inventario'`, montado en `/inventario/`)

| Necesito… | Voy a… |
| --- | --- |
| **Consumir / devolver stock FIFO** | `fifo_logic.py` → `procesar_venta_fifo`, `anular_venta_devolver_stock`; `obtener_lotes_fifo(..., bloquear=True)` |
| Stock disponible / valuación / mínimos | `fifo_logic.obtener_stock_disponible`, `calcular_valuacion_fifo`, `verificar_stock_minimo`, `obtener_productos_bajo_stock` |
| Compras | `views.compras_lista`, `compra_crear`, `compra_detalle`, `compra_editar` (corrige lotes: `_anular_lote_por_correccion`), `compra_imprimir_etiquetas` (Zebra, `utils/impresoras/zebra.py`) |
| Ajustes manuales | `views.vista_ajustes`, `api_lotes_producto`, `api_ajustar_inventario` → `services.registrar_ajuste_service` |
| Errores de dominio | `services/exceptions.py` |

## Invariantes / trampas

- Correlativos (`numero_compra`, lotes) por **máximo sufijo + reintento en
  savepoint** (`_guardar_con_correlativo`), no por `count()+1`.
- Los lotes candidatos se **bloquean** dentro de la transacción del service de
  venta (`apps/ventas/services/ventas_service.py`); nunca mutar stock desde una
  vista.
- Inventario negativo solo si `ConfiguracionNegocio.permitir_inventario_negativo`.
- Permisos: `compras.ver` / `compras.registrar` / `inventario.ver` /
  `inventario.ajustar`.
- Sync: `evento_compra_registrada`, `evento_inventario_movimiento`,
  `evento_ajuste_inventario`, `evento_inventario_snapshot`
  (`views._encolar_compra_y_movimientos`); el cloud lleva un ledger, no
  reconstruye FIFO.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_INVENTARIO.md`)
  — **snapshot histórico**, verificar contra código.
