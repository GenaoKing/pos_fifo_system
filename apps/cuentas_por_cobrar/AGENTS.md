# apps/cuentas_por_cobrar — mapa para agentes

<!-- Última revisión: 2026-09-08 -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

Ventas a crédito: `MetodoPlazoCredito` (`VENCIMIENTO_UNICO` / `CUOTAS`) →
`CuentaPorCobrar` (`ABIERTA`/`PARCIAL`/`PAGADA`/`VENCIDA`/`ANULADA`) →
`CuotaCxC` → `PagoCxC`. Estado de cuenta en PDF/Excel y recibo térmico de abono.

## Entrypoints (`app_name = 'cuentas_por_cobrar'`, montado en `/cuentas-por-cobrar/`)

| Necesito… | Voy a… |
| --- | --- |
| **La lógica** (crear cuenta, cobrar, anular, reprogramar) | `services.py` → `crear_cuenta_para_venta`, `registrar_pago_cxc_service`, `anular_pago_cxc_service` (reversa LIFO), `anular_cuenta_por_venta`, `reprogramar_cxc_por_plazo_cliente` |
| Saldo / resumen de un cliente | `services.saldo_pendiente_cliente`, `resumen_credito_cliente` |
| Vistas del POS | `views.lista_cuentas`, `estado_cuenta_cliente` (+ `_pdf`, `_excel`), `api_metodos_credito`, `api_resumen_cliente`, `api_registrar_pago`, `api_anular_pago`, `api_imprimir_recibo` |
| Exportar | `pdf_generator.EstadoCuentaPDF`, `excel_generator.generar_estado_cuenta_xlsx` |
| Alcance | `views.cuentas_en_alcance`, `obtener_cuenta_en_alcance` |
| Cloud (read-only) | `apps/api/views/cuentas_por_cobrar.py` |

## Invariantes / trampas

- La cuenta se crea **dentro** de `procesar_venta_service` cuando
  `metodo_pago == 'credito'` — no desde una vista.
- Superar el límite de crédito exige una autorización puntual
  (`AutorizacionOverride.OP_CREDITO_EXCEDER_LIMITE`, permiso
  `cuentas_por_cobrar.autorizar_exceso_credito`), consumida en
  `_consumir_autorizacion_credito`.
- Las excepciones de dominio viven en `apps/ventas/services/exceptions.py`.
- `PagoCxC.clave_idempotencia` (migración `0006`): un reintento no duplica un abono.
- Permisos: `cuentas_por_cobrar.ver` / `.cobrar` / `.anular_pago`. Módulo
  `cuentas_por_cobrar` (depende de `ventas` y `clientes`); no se puede
  desactivar con cuentas abiertas.
- Sync: `evento_cxc_creada`, `evento_cxc_pago_registrado`,
  `evento_cxc_pago_anulado`, `evento_cxc_anulada`.
- Auditoría 2026-08-20
  (`docs/exploracion/AUDITORIA_CODIGO_APPS_CUENTAS_POR_COBRAR.md`) — **snapshot
  histórico**, verificar contra código.
