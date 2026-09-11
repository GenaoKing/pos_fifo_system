# Handoff C05 (parte 2) — correcciones financieras de operación comercial

- Estado: **REVISION** (implementado y validado localmente; **no** integrado a `develop`).
- Fecha: **2026-09-11**
- Propietario: **B / Claude**
- Base exacta original: `develop@0b4fb7c`.
- **Actualización — integrada la base A04-C05:** tras la integración de Codex
  (`INTEGRACION_A04-C05.md`), se hizo `git merge develop@f48d2b3` sobre
  `claude/cierre-prod-C05` (merge commit `eaff12b`). Ese `develop` trae **A04**
  (transporte de sync durable + reparación BUG-K, `be15ea0`) y la **parte 1 de C05**
  (`60c6dbc`) ya integradas. El merge fue **limpio, sin conflictos**: A04 solo tocó
  `apps/sync/**`, `apps/api/**` de sync y `config/settings.py`; la parte 2 solo tocó
  modelos/servicios de dominio (ventas, inventario, cotizaciones, caja, reportes,
  CxC) y no editó los docs compartidos. Las firmas de `apps/sync/events.py` que la
  parte 2 emite (`evento_venta_creada/anulada`, `evento_cotizacion_creada/convertida`,
  `evento_inventario_snapshot`, `evento_ajuste_inventario`, `evento_cxc_*`) siguen
  intactas; A04 cambió el transporte, no las firmas.
- **Validación combinada (A04 + C05 parte 1 + parte 2):** suite conjunta de las 6
  apps de dominio de C05 **más** `apps.sync` y `apps.api.tests.test_sync_reconciliacion_bug_k`
  → **564 tests OK** (240.8 s, serial, PostgreSQL, BD `pos_cierre_claude`).
  `check`, `makemigrations --check` y la migración `sync.0011_transporte_durable_bug_k`
  aplican limpias sobre el árbol combinado.
- Rama aislada: `claude/cierre-prod-C05` (parte 2 sobre la base A04-C05).
- Commits de esta entrega (sobre `44e571e`):
  - `b6e898a` DB-CONSTRAINTS + COT-008/011/015 (constraints de BD + preflight)
  - `95dddb3` INV-RBAC-SCOPE + CXC-MIG-ALIAS
  - `460e05e` COT-008/009/010/012/014/017 (endurecer cotizaciones)
  - `b066636` Idempotencia de venta + CXC-IDEMP-CONC
  - `eb72f69` PAG-CXC-CAJA (paginar historial de turnos)
  - `ed47249` RPT-005 (cierre manual: retirar launcher + documentar)
- Contratos consumidos: **CT-01** (auditoría, vía el adaptador legacy
  `Auditoria.registrar` que el resto del dominio ya usa — ver nota abajo) y
  **CT-02** (`usuario.tiene_permiso(codigo, sucursal=…)` + decoradores de
  `apps.permisos`). **No** se editó `apps/sync/**`, `apps/api/**` de
  sync/auth/maestros, ni el motor `apps/permisos/**`.

Sin push, sin deploy, sin ejecución de launchers ni datos reales.

## Alcance cerrado (IDs vigentes del inventario)

### Integridad financiera de BD

- **DB-CONSTRAINTS** (`b6e898a`): `CheckConstraint` a nivel de BD para que
  cantidades/importes imposibles no persistan ni por escritura directa que salte
  `full_clean()`:
  - `ventas`: `Venta.total>=0.01`, `subtotal`/`descuento_total>=0`; `DetalleVenta`
    cantidad`>=1`, precio`>=0.01`, descuento`>=0` y `<=subtotal`, `total_linea>=0`;
    `Pago.monto>=0.01`.
  - `inventario`: `Compra.total>=0` (la cabecera se crea con 0 y se completa tras
    los detalles — un `>=0.01` rompería ese INSERT intermedio, que también hace el
    código de producción); `DetalleCompra`/`Lote` cantidad`>=1`, costo`>=0.01`.
    `Lote.cantidad_actual` **no** se restringe (inventario negativo es política).
  - **Preflight de datos:** cada migración corre la negación de sus constraints y
    aborta con las PKs infractoras **antes** del `ADD CONSTRAINT` (patrón NEG-011).
    Comando read-only `manage.py verificar_integridad_financiera`
    (`--tenant`/`--todos-los-tenants`) para el preflight previo al despliegue.

### Cotizaciones (deuda COT)

- **COT-008** (`b6e898a` constraints + `460e05e` servicio): `DetalleCotizacion`
  con cantidad`>=1`, precio`>=0.01`, descuento`>=0` y `<=subtotal`, `total_linea>=0`;
  `Cotizacion` subtotal/descuento/total`>=0`. Además `guardar_cotizacion` sanea
  cantidad (entera, 1..1e6) y descuento (finito, `>=0`, `<=subtotal`) → **400**, no
  el 500 de la constraint.
- **COT-011** (`b6e898a`): consistencia cabecera↔línea por constraint de una fila
  (`total_linea>=0`, `descuento<=subtotal`) + totales calculados en el servidor
  dentro de la transacción.
- **COT-015** (`b6e898a`): una `Cotizacion` `CONVERTIDA` **exige** `venta`
  (constraint `cotizacion_convertida_exige_venta`) y `venta` pasa a
  `on_delete=PROTECT` (antes `SET_NULL` dejaba orfandad al borrar la venta).
- **COT-009** (`460e05e`): no se cotiza a un cliente **inactivo** (revalidación
  server-side al guardar; la UI pudo cargar la lista antes de la desactivación).
  El contado queda exento. *Los selectores de "solo maestros operativamente
  activos" y estados pendiente/conflicto siguen bloqueados por CT-04 (ver abajo).*
- **COT-010** (`460e05e`): `Cotizacion.save()` numera por **máximo sufijo +
  reintento en savepoint**, no `count()+1` (un hueco en la secuencia dejaba de
  colisionar con un 500 tras cobrar).
- **COT-012** (`460e05e`): crear (`guardar_cotizacion`) y convertir
  (`ventas_service._marcar_cotizacion_convertida` y el legacy `marcar_convertida`)
  dejan `Auditoria.registrar` **dentro** de la transacción (antes solo emitían el
  evento de sync).
- **COT-014** (`460e05e`): `descargar_pdf_cotizacion` ya no filtra el texto de la
  excepción (residual; `guardar_cotizacion` ya era genérico).
- **COT-017** (`460e05e`): `lista_cotizaciones` pagina (Paginator 50) + controles.

### Concurrencia, idempotencia y autorización servicio+API

- **Idempotencia de venta** (`b066636`): `procesar_venta_service` lee
  `datos['clave_idempotencia']`; un reintento con la misma clave devuelve la venta
  **original** sin cobrar ni consumir inventario otra vez. Campo
  `Venta.clave_idempotencia` con única PARCIAL (`uniq_venta_clave_idempotencia`)
  como respaldo. **Un solo efecto financiero** garantizado por la constraint
  (paridad con los abonos CxC). `clave_idempotencia` es read-only en el admin.
- **CXC-IDEMP-CONC** (`b066636`): prueba de N reintentos (6) con la misma clave =
  un solo abono + respaldo de la única parcial ante inserción directa. Complementa
  la prueba de 2 llamadas ya existente (`IdempotenciaCobroTests`).
- **INV-RBAC-SCOPE** (`95dddb3`): `registrar_ajuste_service` re-autoriza
  `inventario.ajustar` contra la sucursal del **lote** ya bloqueado — en el
  **servicio**, no solo el decorador de la vista (que resuelve la sucursal del
  operador). Nueva excepción tipada `PermisoAjusteDenegadoError` (403); la vista
  pasa `sucursal=sucursal_del_request`. Espeja `_puede_anular` (PER-013).
- **VEN-ANULAR-LEGACY**: **ya cerrado en la parte 1** (`60c6dbc`): `_puede_anular`
  re-autoriza `ventas.anular` contra `venta.sucursal` con CT-02; no queda rol
  legacy. Se revalidó verde en esta corrida.

### Migración, paginación, políticas y launcher

- **CXC-MIG-ALIAS** (`95dddb3`): `cuentas_por_cobrar.0002` usaba el manager sin
  `.using(schema_editor.connection.alias)`; en un grafo tenant desde cero el router
  podía sembrar los métodos default en `default`. Se fija el alias en crear y
  revertir (patrón `negocios.0002`).
- **PAG-CXC-CAJA** (`eb72f69`): `historial_turnos` cortaba en 50 **sin aviso**;
  ahora pagina (50/pág) con controles y conteo. La cartera CxC (`lista_cuentas`)
  ya informaba su tope de 300 (`cuentas_ocultas`/`tope_lista`), no era silenciosa.
- **RPT-005** (`ed47249`): se retira `instalar_cierre.ps1` (registraba un servicio
  NSSM `CierreCajaProgramado` apuntando a un `.bat` inexistente; un servicio NSSM
  corre en bucle, no una tarea puntual). Runbook nuevo
  `docs/runbooks/CIERRE_DIARIO_MANUAL.md` con el comando manual y cómo programarlo
  vía Task Scheduler si un cliente lo pide. Test end-to-end del comando.

### Políticas ya decididas — revalidadas verdes (sin cambio de código)

- **CAJA-002** (efectivo sin turno abierto es válido): cubierto por
  `test_auditoria_caja.PertenenciaAlTurnoTests.test_sin_turno_abierto_el_pago_queda_sin_turno`.
- **CXC-006** (no anular venta con abonos aplicados hasta revertirlos): cubierto
  por `test_auditoria_cxc.AnulacionConAbonosTests` (`anular_cuenta_por_venta`
  levanta `AnulacionConAbonosError`).
- **RPT-004** (cierre nace BORRADOR, se finaliza manual): cubierto por
  `test_auditoria_reportes` (`RPT-004: un borrador se recalcula`) + el nuevo test
  del comando.
- **BUG-I** (autocomplete en caja): código + test ya en base
  (`test_auditoria_caja` verifica `autocomplete="off"`). Reverificación visual con
  Chrome real es de C06.
- **BUG-J** (comentario Django en sidebar): CORREGIDO + cubierto en base
  (`{% comment %}`); reverificación visual diferida a C06.
- **DEC-COT-VENCIDAS**: respetada — **no** se caducan ni sanean cotizaciones
  viejas. `puede_convertirse`/`esta_vencida` (COT-007) ya existía; no se tocó.

## Archivos

Modelos/migraciones:
- `apps/ventas/models.py`, `apps/ventas/migrations/0009_*`, `0010_*`
- `apps/inventario/models.py`, `apps/inventario/migrations/0007_*`
- `apps/cotizaciones/models.py`, `apps/cotizaciones/migrations/0003_*`
- `apps/cuentas_por_cobrar/migrations/0002_metodos_plazo_default.py` (alias)

Servicios/vistas:
- `apps/ventas/services/ventas_service.py`, `apps/ventas/views.py`, `apps/ventas/admin.py`
- `apps/inventario/services/ajustes_service.py`, `apps/inventario/services/exceptions.py`,
  `apps/inventario/views.py`
- `apps/cotizaciones/views.py`, `templates/cotizaciones/lista_cotizaciones.html`
- `apps/caja/views.py`, `templates/caja/historial.html`

Comando nuevo:
- `apps/reportes/management/commands/verificar_integridad_financiera.py`

Runbook nuevo / launcher retirado:
- `docs/runbooks/CIERRE_DIARIO_MANUAL.md`, se elimina `instalar_cierre.ps1`

Tests nuevos (un archivo por concern):
- `apps/ventas/tests/test_constraints_db.py`, `test_idempotencia_venta.py`
- `apps/inventario/tests/test_constraints_db.py`, `test_ajuste_permisos.py`
- `apps/cotizaciones/tests/test_constraints_db.py`, `test_cotizacion_hardening.py`
- `apps/cuentas_por_cobrar/tests/test_idempotencia_cobro.py`
- `apps/caja/tests/test_paginacion_historial.py`
- `apps/reportes/tests/test_comando_cierre_diario.py`

Mapas actualizados (`Última revisión: 2026-09-11`): `apps/ventas`, `apps/inventario`,
`apps/cotizaciones`, `apps/caja`, `apps/reportes` (los de la parte 1 ya estaban).

## Migraciones / BDs

Tres migraciones de constraints (`ventas.0009`, `inventario.0007`,
`cotizaciones.0003`) + una de idempotencia (`ventas.0010`) + el fix de alias en
`cuentas_por_cobrar.0002` (edición in-place de una migración ya publicada; segura
porque solo cambia el alias de escritura, no el esquema). Todas aplican limpias
en la BD de dev del worktree (`pos_cierre_claude`, PostgreSQL) y en las BDs de
test derivadas. `makemigrations --check` → *No changes detected*.

> **Nota de despliegue:** las migraciones de constraints validan filas existentes.
> Correr `verificar_integridad_financiera` (por tenant en el cloud) **antes** de
> migrar en cualquier instalación con datos reales; el preflight embebido aborta
> con las PKs infractoras si las hubiera.

## Comandos y resultado de tests

Desde `C:/Proyectos/pos_fifo_system_cierre_claude`, venv `.venv`
(CPython 3.11.14, Django 5.2.17), `--settings=config.settings_development`:

```text
check                                  -> no issues
makemigrations --check --dry-run       -> No changes detected

# Focales de la entrega
test apps.ventas.tests.test_constraints_db
     apps.inventario.tests.test_constraints_db
     apps.cotizaciones.tests.test_constraints_db          -> 24 OK
test apps.inventario.tests.test_ajuste_permisos           -> incluido en inventario
test apps.cotizaciones.tests.test_cotizacion_hardening    -> 12 OK
test apps.ventas.tests.test_idempotencia_venta
     apps.cuentas_por_cobrar.tests.test_idempotencia_cobro -> 7 OK
test apps.caja.tests.test_paginacion_historial            -> 1 OK
test apps.reportes.tests.test_comando_cierre_diario       -> 3 OK

# Regresión por app (iterando)
test apps.cotizaciones apps.ventas                        -> 162 OK
test apps.inventario                                      -> 55 OK
test apps.cuentas_por_cobrar                              -> parte de 76 OK (con inventario)
# Regresión combinada de las 6 apps tocadas: ver sección de cierre del handoff.
```

## Pruebas omitidas / pendientes (no simuladas como hechas)

- **Impresión física**, matrices multi-tenant/multi-BD y la suite serial completa
  del proyecto (G1): las corre A/Codex al integrar.
- **Reverificación visual** de BUG-I (Chrome con credenciales reales) y BUG-J:
  C06/visita.
- **Carrera concurrente real** (dos procesos simultáneos) de venta e idempotencia:
  las pruebas son secuenciales + respaldo de constraint. La invariante financiera
  ("un solo efecto") la garantiza la única parcial, no el timing.

## Riesgos

- `Cotizacion.venta` pasa a `PROTECT`: una venta vinculada a una cotización
  CONVERTIDA ya no se puede **borrar** (se anula, no se borra). Ningún flujo de
  producción borra ventas; el único `delete()` de venta en tests es de una venta
  sin cotización (no afectada).
- Idempotencia de venta: una **carrera verdadera** (dos peticiones simultáneas que
  ambas pasan el chequeo previo) hace que la perdedora reciba un error (el
  reintento devuelve la original vía el chequeo previo). Es la misma conducta que
  los abonos CxC; la constraint garantiza que **nunca** se cree una segunda venta.
- Constraints de BD sobre datos legacy: si una instalación real tuviera filas con
  importes imposibles, la migración aborta con las PKs (por diseño). Correr el
  preflight antes de migrar.

## Rollback

Revertir los commits `b6e898a..ed47249` en la rama. Las migraciones tienen reversa
(las de constraint quitan el CHECK; la de idempotencia quita campo+constraint). El
fix de `cuentas_por_cobrar.0002` es una edición de una data migration ya aplicada:
revertirlo es re-editar el archivo (no hay estado de esquema que deshacer).

## Dependencias / entregas a Codex (dueño de sync/api/docs)

1. **Productores de auditoría CT-01 (point 4).** Los productores de dominio de C05
   (venta, anulación, ajuste, CxC, y ahora cotización) usan el **adaptador legacy
   `Auditoria.registrar`**, que es lo que usa TODO el dominio hoy y que el contrato
   CT-01 declara conservar durante la migración de productores. **Esta entrega no
   introduce eventos de dominio de sync nuevos** ni cambia payloads: cotización
   sigue emitiendo `evento_cotizacion_creada`/`evento_cotizacion_convertida` (ya
   con handlers de Codex). **No hay contrato de eventos nuevo para `apps/sync`.**
   La migración de estos productores a `apps.auditoria.services.registrar_mutacion`
   es un trabajo transversal de A02 (no un reescrito por bloque); si A lo prioriza,
   la lista de productores de C05 es: `ventas_service` (venta creada, cotización
   convertida), `anulaciones_service` (venta anulada), `ajustes_service` (ajuste),
   `cuentas_por_cobrar.services` (cxc creada, abono, abono anulado, cxc anulada),
   `cotizaciones.views.guardar_cotizacion` (cotización creada).
2. **COT-013** (borrado de cotización no converge local/cloud): es C+A con hook de
   sync de A. Local: no hay borrado de cotización expuesto (solo admin). Cuando A
   defina el tombstone/replay de cotización, C ajusta el productor local. **Queda
   fuera de esta entrega**; se documenta el bloqueo.
3. **Deltas documentales propuestos** (los edita Codex para evitar conflictos):
   - `INVENTARIO.md`: marcar REVISION/commit para DB-CONSTRAINTS, COT-008/010/011/012/014/015/017,
     INV-RBAC-SCOPE, CXC-MIG-ALIAS, CXC-IDEMP-CONC, PAG-CXC-CAJA, RPT-005,
     VEN-ANULAR-LEGACY (parte 1). CAJA-002/CXC-006/RPT-004 → revalidadas.
   - `ESTADO_AUDITORIAS.md`: constraints nuevas por desplegar (con nota de preflight),
     migración `ventas.0010`, decisión RPT-005 código-hecho.
   - `TODO_AUDITORIAS.md`: quitar los IDs cerrados arriba de los pendientes de C05.
   - `BUGS.md`: BUG-I/J sin cambio (ya CORREGIDO+CUBIERTO; visual diferida a C06).

## Bloqueos declarados

- **CT-04 (maestros offline) sigue SIN publicar.** Por eso **no** se hicieron los
  selectores comerciales de "solo maestros operativamente activos" ni los estados
  pendiente/conflicto (COT-009 selector, snapshots): quedan fuera de esta entrega.
  La revalidación server-side de cliente activo al guardar (COT-009 núcleo) sí se
  hizo, porque es un chequeo de integridad que no depende de la maquinaria offline.
- **C04 (portal React) NO se inició** (depende de CT-04).

## Siguiente tarea desbloqueada

- Revisión cruzada por Codex del diff `b6e898a..ed47249` e integración serial a
  `develop` (A integra y publica el SHA verde), junto con la parte 1 (`60c6dbc`,
  `44e571e`) que sigue en REVISION.
- Cuando A publique CT-04: selectores comerciales y snapshots de C05/C04.
