# Handoff C05 punto 6 — productores de auditoría CT-01 en el dominio

Estado: **C05 p6 COMPLETO — todos los productores del alcance migrados y
validados.** Fecha: **2026-09-18** (actualiza el corte del 2026-09-17, que solo
tenía la venta creada de referencia). Agente: Claude. Base:
`integration/cierre-prod-A05-C03@609c98f` (tiene CT-01 `registrar_mutacion` +
A05 + CT-04). Rama `claude/cierre-prod-C05-p6-auditoria-ct01`. **NO publicado,
NO fusionado, NO deployado.**

## Resumen del cierre (2026-09-18)

Se migraron al contrato CT-01 (`registrar_mutacion`) todos los productores del
alcance de C05 p6, priorizando por valor financiero:

| Productor | `accion` CT-01 | Archivo |
|---|---|---|
| Venta creada (referencia) | `ventas.venta.creada` | `apps/ventas/services/ventas_service.py` |
| **Venta anulada** | `ventas.venta.anulada` | `apps/ventas/services/anulaciones_service.py` |
| **Abono CxC registrado** | `cuentas_por_cobrar.abono.registrado` | `apps/cuentas_por_cobrar/services.py` |
| Abono CxC anulado | `cuentas_por_cobrar.abono.anulado` | idem |
| Cuenta CxC creada | `cuentas_por_cobrar.cuenta.creada` | idem |
| Cuenta CxC anulada por venta | `cuentas_por_cobrar.cuenta.anulada` | idem |
| Override de crédito | `cuentas_por_cobrar.credito.override_autorizado` | idem |
| Reprogramación de plazo | `cuentas_por_cobrar.plazo.reprogramado` | idem |
| Ajuste de inventario | `inventario.ajuste.creado` | `apps/inventario/services/ajustes_service.py` |
| Descuento autorizado | `ventas.descuento.autorizado` | `apps/ventas/services/ventas_service.py` |
| Cotización creada | `cotizaciones.cotizacion.creada` | `apps/cotizaciones/views.py` |
| Cotización convertida | `cotizaciones.cotizacion.convertida` | `apps/cotizaciones/views.py` |

**Bug de regresión encontrado y corregido en el productor de referencia.** El
commit `508a66e` pasaba `correlacion_id=clave_idempotencia` — pero
`clave_idempotencia` es un **texto opaco** (`CharField`, ≤64) y
`Auditoria.correlacion_id` es un **`UUIDField`**. Una venta con clave no-UUID
(ej. `V-K1`, que `_normalizar_clave_idempotencia` acepta) reventaba el `save()`
del evento → `AUDIT_WRITE_FAILED` → **revertía la venta**.
`apps/ventas/tests/test_idempotencia_venta.py` lo reproducía con 4 errores; el
handoff anterior no lo detectó porque solo corrió `test_ventas_service` (32),
no ese módulo. **Corregido en todos los productores:** la clave opaca va en
`idempotencia_key` (CharField); `correlacion_id` queda en `None` salvo que haya
un UUID real. Regla para futuros productores: nunca meter texto no-UUID en
`correlacion_id`.

**Tests agregados/actualizados:**
- `apps/ventas/tests/test_ventas_service.py::AuditoriaCT01VentaTests` — +anulada.
- `apps/cuentas_por_cobrar/tests/test_ct01_productores.py` — nuevo (cuenta
  creada, abono registrado con idempotencia, abono anulado, cuenta anulada).
- `apps/inventario/tests/test_ct01_ajuste.py` — nuevo.
- `apps/ventas/tests/test_descuento_autorizacion.py::AuditoriaDescuentoTests` y
  `apps/cotizaciones/tests/test_cotizacion_hardening.py::COT012AuditoriaTests` —
  actualizados a CT-01.
- `apps/cotizaciones/tests/test_auditoria_cotizaciones.py` — el helper `_usuario`
  ahora liga al operador al MISMO negocio que las sucursales del fixture (era un
  artefacto: `habilitar_cajero` sin `negocio=` crea un negocio nuevo por usuario,
  y el guard CT-01 `_actor_data` rechaza auditar un hecho de otro tenant; en el
  POS local real, con un solo negocio, esto nunca pasa).

## Deltas de Codex aplicados en la integración local

1. **`apps/auditoria/productores.py::MATRIZ_V1`** — registrados los productores
   CT-01 nuevos, con prueba que exige sus rutas reales:
   - `ventas.venta.creada` → `apps.ventas.services.ventas_service:procesar_venta_service`
   - `ventas.venta.anulada` → `apps.ventas.services.anulaciones_service:anular_venta_service`
   - `ventas.descuento.autorizado` → `apps.ventas.services.ventas_service:_consumir_autorizacion_descuento`
   - `inventario.ajuste.creado` → `apps.inventario.services.ajustes_service:registrar_ajuste_service`
   - `cuentas_por_cobrar.cuenta.creada` / `...credito.override_autorizado` → `apps.cuentas_por_cobrar.services:crear_cuenta_para_venta`
   - `cuentas_por_cobrar.abono.registrado` → `apps.cuentas_por_cobrar.services:registrar_pago_cxc_service`
   - `cuentas_por_cobrar.abono.anulado` → `apps.cuentas_por_cobrar.services:anular_pago_cxc_service`
   - `cuentas_por_cobrar.cuenta.anulada` → `apps.cuentas_por_cobrar.services:anular_cuenta_por_venta`
   - `cuentas_por_cobrar.plazo.reprogramado` → `apps.cuentas_por_cobrar.services:reprogramar_cxc_por_plazo_cliente`
   - `cotizaciones.cotizacion.creada` / `...convertida` → `apps.cotizaciones.views:<vista>`
2. **`MATRIZ_LEGACY` drift:** remapeadas las entradas legacy `VENTA_CREADA`, `VENTA_ANULADA`,
   `AJUSTE_INVENTARIO`, `DESCUENTO_AUTORIZADO`, `CONFIGURACION`, `CREAR`, `EDITAR`
   siguen apuntando a funciones que **ya no emiten** esos códigos (ahora emiten
   CT-01). El test solo verifica que la ruta resuelva a un callable, así que no
   rompe, pero conviene remapear/anotar para no prometer cobertura legacy que ya
   no existe. `EDITAR` se conserva únicamente para sus cuatro productores
   realmente activos, fuera del alcance p6.
3. **`CONTRATOS.md` §CT-01** — registrados como productores de dominio migrados
   por C05 p6, con su regla de `idempotencia_key`/`correlacion_id`.
4. **Handlers de sync/API sync** — confirmados sin cambio: cada `accion` de la
   tabla viaja como evento `audit.event.v1` vía `evento_transportable`.
   `before`/`after` de cada uno están en el código del productor.

## Fuera de alcance (no pedidos en C05 p6, quedan como follow-up)

- `apps/ventas/services/ventas_service.py:~984` (`ventas.venta.editada`) y
  `apps/ventas/views.py:~722` — requieren definir la operación exacta.
- `apps/inventario/views.py:~671` — ídem.
- Conversión de cotización **por venta** (dentro de `procesar_venta_service`):
  emite todavía el código legacy `EDITAR` por un productor distinto al de
  `marcar_convertida`; no estaba en el alcance de p6.

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

El corte `508a66e` hizo el productor **canónico y de mayor valor — la venta
creada — end-to-end**. El cierre `bbb5246` aplica ese patrón a todos los demás
productores de la tabla del resumen; dicha tabla es el estado autoritativo de
este handoff.

## Corte inicial de referencia (2026-09-17)

`apps/ventas/services/ventas_service.py` — el registro de auditoría de la venta
dentro del `transaction.atomic()` de `procesar_venta_service`:

- **Antes:** `Auditoria.registrar_venta(venta, usuario, ip_address)` (legacy,
  `TipoAccion.VENTA_CREADA` en mayúsculas, sin canal/identidad/correlación
  garantizados por contrato).
- **Ahora (corte inicial):** `registrar_mutacion(accion='ventas.venta.creada', ...)` — mismo
  `using` que la venta (`venta._state.db or 'default'`), dentro de la misma
  transacción, con `actor`/`sucursal`/`tenant` como identidad histórica,
  `canal=POS_LOCAL`, `idempotencia_key` = clave de idempotencia opaca de la
  venta y `correlacion_id=None` salvo UUID real, `resultado=SUCCEEDED`.

`tenant=None` a propósito: `registrar_mutacion._derivar_tenant` lo deriva de la
sucursal/negocio de la venta (vacío en una instalación local sin tenancy, que
corre sobre `default`; el contrato solo exige tenant_key para BDs `tnt_`).

**No se tocó `apps/auditoria`** (Codex): el adaptador legacy
`Auditoria.registrar_venta` sigue existiendo intacto — solo cambió el llamador.

## Ejemplo de contrato de evento (sync / API sync)

Ejemplo para la venta creada; las doce acciones de la tabla superior se
transportan sin cambios como `audit.event.v1` mediante `evento_transportable`
(los handlers de sync/API no requieren edición):

| Campo | Valor |
|---|---|
| `schema_version` | `audit.event.v1` |
| `action` | `ventas.venta.creada` |
| `channel` | `POS_LOCAL` |
| `result` | `SUCCEEDED` |
| `entity.type` | `ventas.Venta` |
| `before` | `{}` (la venta nace en este evento) |
| `after` | `{numero_venta, total, subtotal, descuento_total, cantidad_items, es_credito}` |
| `idempotencia_key` | clave opaca de idempotencia de la venta |
| `correlation` | `null`, salvo que el productor disponga de UUID real |

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

## Gates del corte inicial (histórico)

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

Estos son los gates del corte `508a66e`. El gate vigente del cierre `bbb5246`
queda registrado en `INTEGRACION-A06-C04-C05.md` (363 pruebas, OK).

Tests nuevos (`apps/ventas/tests/test_ventas_service.py::AuditoriaCT01VentaTests`):
1. la venta creada emite un evento CT-01 (`ventas.venta.creada`, `audit.event.v1`,
   `POS_LOCAL`, `SUCCEEDED`, entidad = venta, `after` con el estado del hecho);
2. ya no se emite el código legacy `TipoAccion.VENTA_CREADA` para una venta nueva.

Sin migración: no se tocó ningún modelo.

## Patrón aplicado (para los follow-up que queden)

Cada migración es del mismo tipo: dentro del `atomic` del dominio, se cambia
`Auditoria.registrar*(...)` por `registrar_mutacion(accion='<namespaced>', ...)`
con `using = <entidad>._state.db or 'default'`, `canal=POS_LOCAL`,
`resultado=SUCCEEDED`, `actor=usuario`, `sucursal=` la identidad del hecho (la
sucursal de la venta/lote/cuenta, no la del operador), y `before`/`after` del
estado de dominio. La clave de idempotencia (texto opaco) va en
`idempotencia_key`, nunca en `correlacion_id` (UUIDField). Ver la tabla de
productores migrados en el resumen del cierre, arriba.

## Rollback

`git revert` de los commits de esta rama. Sin migraciones ni estado remoto: el
cambio es un swap de llamador de auditoría, atómico con cada hecho de dominio.
