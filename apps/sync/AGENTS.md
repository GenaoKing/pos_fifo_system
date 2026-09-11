# apps/sync — mapa para agentes

<!-- Última revisión: 2026-09-11 -->

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
| Reparar BUG-K | `python manage.py reparar_bug_k` — sonda read-only/dry-run; ejecutar exige plan y digest aprobados |

## Modelos (`apps/sync/models.py`)

- `EventoSync` — outbox local; `event_id`/hash scopeados por sucursal, estado,
  payload y lease durable `EN_VUELO`.
- `DiferidoSync` — cola durable de elementos de pull que todavía no aplican.
- `VersionMaestro` — cursor/versión por tabla maestra (soporte del pull keyset).
- `InventarioMovimientoSync`, `InventarioSucursalSnapshot` — replicación de stock.
- `LogSync` — bitácora de ciclos.

## Invariantes / trampas

- El cursor de pull es **keyset `(fecha_modificacion, id)`**, no offset. Solo
  atraviesa un elemento si fue aplicado o persistido en `DiferidoSync`; si la
  cola no puede escribirse, se congela antes del elemento. Pendientes durables
  vuelven el ciclo `PARCIAL`.
- El `INSERT` en el outbox es parte de la transacción del hecho de negocio: si el
  hecho ocurre, el evento existe. No tragarse errores de inserción.
- El push reclama con `select_for_update(skip_locked=True)` y persiste un lease
  antes del HTTP. Un proceso solo puede confirmar/fallar el lease que posee;
  otro recupera el evento después de `SYNC_LEASE_SECONDS`.
- La deduplicación cloud usa `(sucursal, event_id)` y `(sucursal, hash_payload)`;
  identidad ocupada con otro contenido es error, no duplicado.
- Al construir `?desde=` en un cliente, `encodeURIComponent()` (el `+` del offset
  UTC rompe `parse_datetime`).
- Roles/asignaciones negocian `rbac.sync.v2` pero aceptan la lista legacy. Solo
  un envelope completo, sin error/bloqueo y del mismo tenant/sucursal puede
  revocar por ausencia; además solo revoca filas `origen_cloud=True`.
- Una baja `active=false` se aplica antes de resolver códigos/usuarios/roles
  nuevos. Una revisión menor se ignora; `cloud_id` no puede cambiar de terna.
- Producto, Categoría y Cliente se buscan primero por `origen_cloud_id`; la
  clave natural solo adopta una fila no sellada en la primera bajada. Colisiones
  o coincidencias ambiguas quedan en `DiferidoSync` con código `MASTER_*`, nunca
  crean un duplicado ni eligen una fila con `.first()`.
- El SKU de Producto es inmutable. `origen_sucursal` y `pendiente_revision`
  conservan el patrón de stub BUG-H y no forman parte de la identidad cloud.

## Antes de tocar el contrato de sync

- Runbooks: `docs/runbooks/PRUEBAS_SYNC_LOCAL.md`,
  `docs/runbooks/SYNC_EMULACION_SUCURSAL_PROD.md` y, para tipos que alimentan
  avisos, `docs/runbooks/EXTENDER_NOTIFICACIONES.md`.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_SYNC.md`) —
  **snapshot histórico de hallazgos, no estado actual**; verificar contra código.
