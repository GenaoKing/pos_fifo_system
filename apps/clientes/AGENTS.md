# apps/clientes — mapa para agentes

<!-- Última revisión: 2026-09-08 -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

`Cliente` (`apps/clientes/models.py`): tipo (`CONTADO` genérico, personal,
corporativo…), `cedula_rnc`, crédito (`limite_credito`, `plazo_credito_dias`,
`condiciones_pago`) e identidad de sync (`origen_sucursal`, `origen_id_local`,
`origen_cloud_id`).

## Dos espacios de URL — no confundir

| Espacio | Prefijo | Dónde vive |
| --- | --- | --- |
| POS local (templates) | `/clientes/` | `apps/clientes/views.py` + `urls.py` |
| API portal cloud (DRF) | `/api/v1/maestros/clientes/` | `apps/api/views/maestros.py` → `ClienteViewSet` |

## Entrypoints (`app_name = 'clientes'`)

| Necesito… | Voy a… |
| --- | --- |
| Listar / crear / editar / detalle | `views.lista_clientes`, `crear_cliente`, `editar_cliente`, `detalle_cliente` |
| Activar/desactivar | `views.toggle_estado_cliente` |
| Autocompletar en POS | `views.buscar_clientes` (`api/buscar/`) |
| **El cliente contado** | `Cliente.get_cliente_contado()` — singleton (constraint condicional, CLI-007, migración `clientes.0006`) |
| Saldo/crédito de un cliente | `apps/cuentas_por_cobrar/services.py` → `saldo_pendiente_cliente`, `resumen_credito_cliente` |

## Invariantes / trampas

- Cambiar el límite de crédito exige `clientes.editar_limite_credito`, separado
  de `clientes.editar` (`_limite_credito_autorizado`).
- Los clientes que vienen del cloud (`_es_del_cloud`) se adoptan por identidad;
  el receptor es `_resolver_o_crear_cliente` en `apps/api/views/sync.py` y el
  pull incremental está en `apps/sync/engine.py`.
- `cedula_rnc` es obligatorio para e-CF tipo 31 (crédito fiscal).
- `Venta.cliente = None` también significa contado.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_CLIENTES.md`) —
  **snapshot histórico**, verificar contra código.
