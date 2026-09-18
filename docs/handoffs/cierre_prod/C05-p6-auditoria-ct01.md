# Handoff C05 punto 6 — productores de auditoría CT-01 en el dominio

Estado: **Productor de referencia migrado y validado (venta creada). Resto de
productores enumerados como follow-up.** Fecha: **2026-09-17**. Agente: Claude.
Base: `integration/cierre-prod-A05-C03@609c98f` (tiene CT-01 `registrar_mutacion`
+ A05 + CT-04). Rama `claude/cierre-prod-C05-p6-auditoria-ct01`. **NO publicado,
NO fusionado, NO deployado.**

## El encargo

`docs/planes/CIERRE_PROD_CLAUDE.md`, C05 punto 6: *"Productores de auditoría
CT-01 en transacciones del dominio. Entregar contratos de eventos y casos a
Codex para sus handlers de sync/API sync; no editar esos archivos. Los
conflictos de maestro no deben atascar hechos financieros."*

## Hallazgo antes de tocar código

Las transacciones del dominio (`apps/ventas`, `apps/cotizaciones`,
`apps/inventario`, `apps/cuentas_por_cobrar`) **no** usaban el contrato CT-01
`registrar_mutacion`; usaban el adaptador **legacy** `Auditoria.registrar*(...)`.
CT-01 (`CONTRATOS.md` §CT-01) es explícito: *"El adaptador histórico
`Auditoria.registrar(...)` se conserva durante la migración de productores. No
es el contrato para código nuevo de C; no garantiza por sí solo canal, identidad
estable, correlación o transacción correcta."* Entonces p6 = **migrar** esos
productores al contrato nuevo.

Es una migración amplia. Esta entrega hace el productor **canónico y de mayor
valor — la venta creada — end-to-end y validado**, dejando establecido el
patrón que siguen los demás, más el contrato de eventos y la lista de restantes.

## Qué se migró

`apps/ventas/services/ventas_service.py` — el registro de auditoría de la venta
dentro del `transaction.atomic()` de `procesar_venta_service`:

- **Antes:** `Auditoria.registrar_venta(venta, usuario, ip_address)` (legacy,
  `TipoAccion.VENTA_CREADA` en mayúsculas, sin canal/identidad/correlación
  garantizados por contrato).
- **Ahora:** `registrar_mutacion(accion='ventas.venta.creada', ...)` — mismo
  `using` que la venta (`venta._state.db or 'default'`), dentro de la misma
  transacción, con `actor`/`sucursal`/`tenant` como identidad histórica,
  `canal=POS_LOCAL`, `correlacion_id`/`idempotencia_key` = clave de
  idempotencia de la venta, `resultado=SUCCEEDED`.

`tenant=None` a propósito: `registrar_mutacion._derivar_tenant` lo deriva de la
sucursal/negocio de la venta (vacío en una instalación local sin tenancy, que
corre sobre `default`; el contrato solo exige tenant_key para BDs `tnt_`).

**No se tocó `apps/auditoria`** (Codex): el adaptador legacy
`Auditoria.registrar_venta` sigue existiendo intacto — solo cambió el llamador.

## Contrato de evento para Codex (sync / API sync)

Evento CT-01 que ahora produce la venta, para los handlers de sync de Codex
(`evento_transportable` ya lo serializa; no edité esos archivos):

| Campo | Valor |
|---|---|
| `schema_version` | `audit.event.v1` |
| `action` | `ventas.venta.creada` |
| `channel` | `POS_LOCAL` |
| `result` | `SUCCEEDED` |
| `entity.type` | `ventas.Venta` |
| `before` | `{}` (la venta nace en este evento) |
| `after` | `{numero_venta, total, subtotal, descuento_total, cantidad_items, es_credito}` |
| `correlation` | clave de idempotencia de la venta |

Nota: el evento de auditoría CT-01 es **independiente** del evento de sync de
dominio `EventoSync(tipo_evento='VENTA_CREADA')` que ya existía y sigue
firmando igual (outbox transaccional). Son dos rieles distintos: uno replica
el hecho de negocio (sync), otro el rastro de auditoría (CT-01).

## "Los conflictos de maestro no deben atascar hechos financieros"

Se cumple por diseño y quedó verificado: el bloqueo por `MutacionMaestro` en
`CONFLICTO` (C05 p5, `productos_vendibles()`) ocurre **al cargar el carrito**,
antes de que la venta sea un hecho. Una vez que la venta procede, su auditoría
CT-01 se emite sin volver a consultar el estado de maestros — un conflicto que
aparezca después no puede impedir ni revertir el registro del hecho financiero.
El único caso en que auditar revierte la venta es un fallo de la **propia**
escritura de auditoría (invariante CT-01), que es lo correcto: no queremos un
hecho financiero sin su registro. Ver `[[verificar-ledger-antes-de-status]]`.

## Pruebas — comandos y resultado

Worktree `C:/Proyectos/pos_fifo_system_c05_p6`, venv reutilizado (Python
3.11.14), DB de test aislada `test_pos_c05_p6`.

```bash
DB_NAME=pos_c05_p6 python manage.py test \
  apps.ventas.tests.test_ventas_service \
  --settings=config.settings_development --noinput
# Ran 32 tests — OK (incluye 2 nuevos en AuditoriaCT01VentaTests)

DB_NAME=pos_c05_p6 python manage.py test apps.auditoria apps.sync.tests.test_outbox_transaccional \
  --settings=config.settings_development --noinput
# Ran 54 tests — OK (el legacy registrar_venta sigue verde; el EventoSync
# VENTA_CREADA sigue firmando tras una venta real)

python manage.py makemigrations --check --dry-run   # No changes detected
python manage.py check                              # 0 issues
```

Tests nuevos (`apps/ventas/tests/test_ventas_service.py::AuditoriaCT01VentaTests`):
1. la venta creada emite un evento CT-01 (`ventas.venta.creada`, `audit.event.v1`,
   `POS_LOCAL`, `SUCCEEDED`, entidad = venta, `after` con el estado del hecho);
2. ya no se emite el código legacy `TipoAccion.VENTA_CREADA` para una venta nueva.

Sin migración: no se tocó ningún modelo.

## Productores restantes por migrar (mismo patrón)

Enumerados desde el código; cada uno es una migración del mismo tipo (envolver
en/confirmar `atomic(using)`, cambiar `Auditoria.registrar*` por
`registrar_mutacion` con `accion` namespaced). Códigos de acción propuestos:

| Archivo:línea | Legacy | `accion` CT-01 propuesta |
|---|---|---|
| `apps/ventas/services/anulaciones_service.py:165` | `registrar_anulacion_venta` | `ventas.venta.anulada` |
| `apps/ventas/services/ventas_service.py:660` | `registrar` (DESCUENTO_AUTORIZADO) | `ventas.descuento.autorizado` |
| `apps/ventas/services/ventas_service.py:984` | `registrar` (EDITAR) | `ventas.venta.editada` |
| `apps/ventas/views.py:722` | `registrar` | (revisar operación exacta) |
| `apps/cotizaciones/views.py:327` | `registrar` | `cotizaciones.cotizacion.creada` |
| `apps/cotizaciones/views.py:543` | `registrar` | `cotizaciones.cotizacion.<op>` |
| `apps/inventario/services/ajustes_service.py:181` | `registrar_ajuste_inventario` | `inventario.ajuste.creado` |
| `apps/inventario/views.py:671` | `registrar` | (revisar operación exacta) |
| `apps/cuentas_por_cobrar/services.py:396,414,516,636,755,821` | `registrar` | `cuentas_por_cobrar.abono.registrado`, etc. |

Prioridad sugerida por valor financiero/de auditoría: anulación de venta y
abono CxC (mueven dinero y saldos), luego ajuste de inventario, luego
cotizaciones y descuentos. Cada uno necesita resolver su `using`/`sucursal`
(análogo a la venta) y elegir `before`/`after` del estado de dominio.

## Deltas propuestos a documentos de seguimiento (no aplicados aquí)

- `CONTRATOS.md` §CT-01: agregar `ventas.venta.creada` como primer productor
  de dominio migrado por C05 (rama `claude/cierre-prod-C05-p6-auditoria-ct01`).
  No lo edito porque ese archivo es propiedad de Codex.

## Rollback

`git revert` del commit de esta rama. Sin migraciones ni estado remoto: el
cambio es un swap de llamador de auditoría, atómico con la venta.
