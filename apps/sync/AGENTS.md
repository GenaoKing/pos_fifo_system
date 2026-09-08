# apps/sync — mapa para agentes

<!-- Última revisión: 2026-09-07 -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

Único puente automático entre cada POS local y el cloud. Dos mecanismos, no
mezclarlos:

- **Hechos de negocio** (ventas, pagos, caja, compras, inventario, CxC,
  cotizaciones) → **push** desde un *outbox* de eventos.
- **Datos maestros** (productos, categorías, clientes, roles, permisos,
  configuración) → **pull incremental** con cursor keyset; **no** viajan por
  eventos.

## Entrypoints

| Necesito… | Voy a… |
| --- | --- |
| Motor de sync (push + pull + ciclo) | `apps/sync/engine.py` → `SyncEngine.ciclo_completo`, `.push_eventos`, `.pull_maestros` |
| Emitir un evento desde una app | `apps/sync/events.py` → `evento_*` (p. ej. `evento_venta_creada`) |
| Registrar/consultar tipos de hecho | `apps/sync/registry.py` → `HECHOS` (`OrderedDict` de `HechoSync`) |
| **Receptor cloud** (aplica lo que llega) | `apps/api/views/sync.py` — vive en `apps/api`, no aquí |
| Correr un ciclo a mano | `python manage.py sincronizar` |
| Diagnóstico (outbox, huecos, cursores) | `python manage.py verificar_sync` · `sync_status` |
| Reconciliar divergencias | `python manage.py conciliar` · `reconciliar_cloud` (ver `apps/sync/conciliacion.py`) |

## Modelos (`apps/sync/models.py`)

- `EventoSync` — el outbox local; `hash_unico` idempotente, `estado`, `payload`.
- `VersionMaestro` — cursor/versión por tabla maestra (soporte del pull keyset).
- `InventarioMovimientoSync`, `InventarioSucursalSnapshot` — replicación de stock.
- `LogSync` — bitácora de ciclos.

## Invariantes / trampas

- El cursor de pull es **keyset `(fecha_modificacion, id)`**, no offset. La marca
  de agua debe avanzar de forma **contigua**; un pull que "omite" no debe avanzar
  el cursor ni marcar el ciclo `EXITOSO`.
- El `INSERT` en el outbox es parte de la transacción del hecho de negocio: si el
  hecho ocurre, el evento existe. No tragarse errores de inserción.
- La deduplicación cloud depende de `hash_unico` — no dupliques la lógica de hash.
- Al construir `?desde=` en un cliente, `encodeURIComponent()` (el `+` del offset
  UTC rompe `parse_datetime`).

## Antes de tocar el contrato de sync

- Runbooks: `docs/runbooks/PRUEBAS_SYNC_LOCAL.md`,
  `docs/runbooks/SYNC_EMULACION_SUCURSAL_PROD.md`.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_SYNC.md`) —
  **snapshot histórico de hallazgos, no estado actual**; verificar contra código.
