# apps/facturacion_electronica — mapa para agentes

<!-- Última revisión: 2026-09-08 -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

Comprobantes fiscales electrónicos (e-CF, DGII / Ley 32-23) a través de un
proveedor (hoy MSeller). Modelos (`models.py`): `Emisor` (RNC), `ECF` (tipo
31/32/34…, `encf`, `estado`, `track_id`, `codigo_seguridad`), `EventoECF`
(cada transición). La emisión es **asíncrona**: el POS encola, un job procesa.

## Entrypoints

| Necesito… | Voy a… |
| --- | --- |
| Encolar desde una venta / anulación | `services/cola_emision.py` → `encolar_emision(venta=, tipo_ecf=)`, `encolar_nota_credito(venta=, motivo=)` — **no** llaman al proveedor |
| Procesar la cola | `manage.py ecf_procesar_pendientes` (scheduler c/30 s) → `services/procesador.py` → `procesar_ecf` |
| Obtener el proveedor | `services/factory.py` → `get_emisor_ecf()` — **único** punto de instanciación |
| Contrato / estados / DTOs | `interfaces.py` → `EmisorECFInterface`, `EstadosECF` (`REINTENTABLES`, `TERMINALES`), `ResultadoEmision`, `EstadoECF` |
| Implementación MSeller | `services/mseller_emisor.py`, `mseller_http_client.py`, `integrations/mseller_payload.py` |
| Convertir una venta a datos fiscales (desglose ITBIS) | `services/venta_to_ecf.py` → `venta_a_ecf_data`, `_calcular_linea` |
| Estado para el POS | `views.api_estado_ecf_venta` (`/facturacion-electronica/api/ecf/estado/<venta_id>/`) |
| Configuración | `ConfiguracionNegocio.modulo_ecf`, `ecf_proveedor`, `emisor_activo`, `itbis_incluido_en_precio`, `itbis_porcentaje_global` |

## Invariantes / trampas

- Nadie fuera de `services/` importa una implementación concreta: cambiar de
  proveedor es configuración (`ecf_proveedor`), no un rewrite.
- `ECF.intentos` acota reintentos; estados terminales no transicionan.
- Tipo 31 (crédito fiscal) exige cliente con RNC; el POS ofrece 31/32
  (`TIPOS_ECF_POS` en `apps/ventas/services/ventas_service.py`).
- El ticket térmico imprime eNCF / código de seguridad / QR leyendo `ECF`
  (`utils/impresoras/manager.py`). El comprobante PDF de venta **no** lleva
  datos fiscales a propósito (`apps/ventas/pdf_comprobante.py`).
- Tests de esta app corren con **pytest** (`pytest.ini` → `testpaths`), no con
  `manage.py test`.
- Módulo `ecf` (`apps/suscripciones/registry.py`); no se desactiva con ECF en
  proceso. `modo_contingencia` es un placeholder sin efecto.
