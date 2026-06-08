# TESTING e-CF automatizado - 2026-05-18

## Objetivo

Convertir los hallazgos manuales de TesteCF/MSeller en contratos
automatizados. Esta tanda complementa la bitacora
`docs/TESTING_ECF_2026-05-09.md`.

## Cobertura agregada

### `test_venta_to_ecf.py`

- Mapper neutro Venta -> `ecf_data`.
- Validaciones para tipos `31`, `32`, `34`.
- Comprador fiscal, cliente CONTADO, RNC normalizado.
- ITBIS incluido vs sumado, tasas 18/16/0, descuentos.
- Redondeo half-up y estructura del dict resultante.

### `test_mseller_payload.py`

- Builder `build_mseller_payload()`.
- Orden exacto de `Encabezado`, `IdDoc`, `Comprador` y `Totales`.
- Payload minimalista de tipo `31`: sin `Paginacion`, `TotalPaginas` ni
  `FechaHoraFirma`.
- Omision de campos en cero cuando no aplican.
- `InformacionReferencia` para NC tipo `34`.
- Error explicito si el orquestador no inyecta `emisor`.

### `test_mseller_emisor.py`

- `MSellerEmisor.emitir()` con cliente HTTP mockeado.
- Inyeccion de datos del Emisor y asignacion local de eNCF.
- Garantia de que el flujo normal llama `enviar_documento(..., validar=False)`.
- Mapeo de `MSellerValidationError` a `RECHAZADO`.
- Mapeo de errores auth/transitorios a `ERROR`.
- Polling `consultar_estado()` para estados MSeller conocidos y desconocidos.
- NC tipo `34` desde un ECF original aprobado.
- Limitacion documentada de `descargar_xml_aprobado()` con MSeller.

## Como correrlos

Desde `cmd.exe` en Windows:

```bat
C:\Users\Santiago\anaconda3\Scripts\activate && conda activate pos_fifo && python -m pytest apps\facturacion_electronica\tests -q
```

Corridas por capa:

```bat
C:\Users\Santiago\anaconda3\Scripts\activate && conda activate pos_fifo && python -m pytest apps\facturacion_electronica\tests\test_venta_to_ecf.py -q
C:\Users\Santiago\anaconda3\Scripts\activate && conda activate pos_fifo && python -m pytest apps\facturacion_electronica\tests\test_mseller_payload.py -q
C:\Users\Santiago\anaconda3\Scripts\activate && conda activate pos_fifo && python -m pytest apps\facturacion_electronica\tests\test_mseller_emisor.py -q
```

## Pendientes recomendados

- Tests de `procesar_ecf()` con proveedor mockeado para validar persistencia
  de `ECF`, `EventoECF`, `xml_firmado`, `xml_respuesta` y reintentos.
- Tests del management command `ecf_procesar_pendientes` en `dry-run`,
  `--solo-emitir`, `--solo-consultar` y `--ecf-id`.
- Tests de hooks post-commit en `procesar_venta_service()` y
  `anular_venta_service()`.
