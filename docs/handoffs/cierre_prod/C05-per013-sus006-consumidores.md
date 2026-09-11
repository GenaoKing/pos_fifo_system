# Handoff C05 (parte 1) — consumidores PER-013 (CT-02) + gate de módulo SUS-006

- Estado: **REVISION** (implementado y validado localmente; **no** integrado a `develop`).
- Fecha: **2026-09-11**
- Propietario: **B / Claude**
- Base exacta: `develop@0b4fb7c` (con A03/`3e6cec1` y C01–C03 ya integrados)
- Rama aislada: `claude/cierre-prod-C05`
- Commit de implementación: `60c6dbc`
- Contratos consumidos: `rbac.capabilities.v1` (CT-02, server-side
  `usuario.tiene_permiso(codigo, sucursal=…)` + decoradores
  `apps.permisos.decorators`) y el resolutor de módulos
  (`apps.configuracion.utils.modulo_activo` / `apps.suscripciones.engine`).

Sin push, sin deploy, sin ejecución de launchers ni lectura/escritura de datos
operativos. No se editó el motor de permisos, `apps/sync/**` ni `apps/api/**` de
sync/auth/maestros. **No se integró a `develop`**: queda documentado y listo
para revisión, según lo pedido.

## Alcance cerrado

Este bloque cierra la parte de **PER-013** que A03 dejó como `PARCIAL_A03`
(consumidores de anulación y reimpresión) y **SUS-006** (gate de módulo HTML/API
en CxC y reportes on-demand). Todo con la interfaz publicada de CT-02; ningún
motor de permisos paralelo ni bypass por ocultar el botón.

### PER-013 — reautorización por la sucursal de la PROPIA venta

1. **`anular_venta_service`** (`apps/ventas/services/anulaciones_service.py`):
   se eliminó el chequeo de rol legacy (`rol in ('ADMIN','SYSADMIN')`) y se
   re-autoriza `ventas.anular` contra `venta.sucursal` **bajo el lock**
   (`select_for_update`). ADMIN/SYSADMIN siguen pasando porque el motor RBAC les
   concede acceso total (mientras `RBAC_LEGACY_ADMIN_BYPASS=True`), no por un
   chequeo de rol local. Una venta legacy sin sucursal cae al scope operativo del
   solicitante (`sucursal=…`, pasado por la vista), igual que
   `_ventas_en_alcance` mantiene esas ventas visibles.
   - Firma nueva: se agregó el kwarg opcional `sucursal=None`. Los llamadores
     existentes (admin action, tests) siguen funcionando; la vista `api_anular_venta`
     pasa `sucursal=sucursal_del_request(request)`.
   - **Detalle técnico:** *no* se usa `select_related('sucursal')` en la query
     bloqueada — `sucursal` es FK nullable y PostgreSQL rechaza
     `SELECT … FOR UPDATE` sobre el lado nullable de un outer join. La sucursal
     se carga perezosa al autorizar (una consulta extra en una operación rara).
2. **`vista_anulaciones`** (`apps/ventas/views.py`): el gate usa la sucursal
   operativa (`sucursal_del_request`) y el listado se acota con
   `_ventas_en_alcance(request)` (antes partía de `Venta.objects.all()` y el gate
   no pasaba sucursal → unía todas las asignaciones).
3. **Reimpresión** (`utils/impresoras/views.py`, B-owned): las dos superficies
   térmicas —`ReimprimirTicketView` (JSON) y `ListaVentasReimprimirView` (HTML)—
   **no tenían permiso ni scope**, solo `LoginRequiredMixin`. Ahora exigen
   `ventas.reimprimir` y resuelven la venta dentro del alcance operativo. El
   fetch scoped se movió **fuera** del `try/except Exception` para que un id
   fuera de alcance devuelva 404 (antes el except lo enmascaraba como 500).
   `ImprimirTicketAutomaticoView` (autoimpresión post-venta) también quedó
   acotado al alcance (defensa: un id de otra sucursal ya no imprime su ticket).
   - **Corrección de código de permiso:** los docstrings anunciaban
     `ventas.reimprimir_ticket`, que **no existe en el catálogo** — bajo CT-02 un
     código fuera del catálogo **siempre deniega**, así que ese gate habría
     bloqueado a todos. Se usa `ventas.reimprimir`, el código real (catálogo:
     «Reimprimir tickets de venta y emitir el comprobante PDF»), que además está
     en `PERMISOS_CAJERO_DEFAULT` → sin lockout para cajeros existentes.
   - `comprobante_venta_pdf` (`apps/ventas/views.py:656`) ya estaba con permiso +
     `_ventas_en_alcance` desde `446d29c`; se dejó como está y se cubrió con tests
     de regresión (sin permiso → redirect; cross-branch → 404).

### SUS-006 — gate de módulo ortogonal al permiso

Recomendación de la auditoría: aplicar el gate en **todos** los puntos de entrada
server-side del módulo vendible, conservando el permiso como capa adicional. Por
eso se gatearon HTML **y** APIs (no solo la vista HTML):

- Nuevo decorador **`requiere_modulo_json`** en `apps/configuracion/decorators.py`
  (404 JSON), hermano de `requiere_modulo` (404 HTML). Ambos usan el resolutor
  ya integrado (`apps.configuracion.utils.modulo_activo`).
- **CxC** (`apps/cuentas_por_cobrar/views.py`), módulo `cuentas_por_cobrar`:
  `lista_cuentas`, `estado_cuenta_cliente`, `..._pdf`, `..._excel` (HTML) y
  `api_metodos_credito`, `api_resumen_cliente`, `api_registrar_pago`,
  `api_anular_pago`, `api_imprimir_recibo` (JSON).
- **Reportes on-demand** (`apps/reportes/views.py`), módulo `reportes_ondemand`:
  `reportes_on_demand` (HTML) y `api_cierre_manual`, `api_ventas_periodo`,
  `api_top_productos`, `api_inventario_valorizado`, `api_ventas_cajero` (JSON).
- Orden de decoradores: `@login_required` → módulo → permiso. El módulo se
  evalúa primero: si el plan no lo incluye, la URL responde 404 aunque el
  usuario tenga permiso.
- **Fuera de alcance a propósito** (lecturas históricas permitidas, se
  documentan): `descargar_pdf_cierre` (descarga de un cierre ya persistido, que
  también produce el comando diario) y el `dashboard`/`api_metricas_hoy` (módulo
  `dashboard`, no `reportes_ondemand`).
- **Compatibilidad legacy:** sin negocio resuelto, `modulo_activo` cae al flag
  legacy — `cuentas_por_cobrar` es «siempre on» y `modulo_reportes_ondemand`
  tiene default `True`. Una instalación local existente no pierde funciones; el
  gate solo muerde a un tenant cuyo plan excluye el módulo (o que apagó el flag),
  que es justo la reproducción de SUS-006.

## Archivos

Implementación:
- `apps/ventas/services/anulaciones_service.py`
- `apps/ventas/views.py`
- `utils/impresoras/views.py`
- `apps/cuentas_por_cobrar/views.py`
- `apps/reportes/views.py`
- `apps/configuracion/decorators.py` (nuevo `requiere_modulo_json`)

Tests (nuevos, un módulo por concern):
- `apps/ventas/tests/test_anulacion_permisos.py`
- `apps/ventas/tests/test_reimpresion_permisos.py`
- `apps/cuentas_por_cobrar/tests/test_modulo_gate.py`
- `apps/reportes/tests/test_modulo_gate.py`

Mapas (`Última revisión: 2026-09-11`):
- `apps/ventas/AGENTS.md`, `apps/cuentas_por_cobrar/AGENTS.md`,
  `apps/reportes/AGENTS.md`, `apps/configuracion/AGENTS.md`.

`utils/impresoras` no tiene `AGENTS.md` (no está bajo `apps/`); se referenció
desde el mapa de `apps/ventas`.

## Migraciones / BDs

**Ninguna migración.** No se tocaron modelos (`makemigrations --check` → *No
changes detected*). Entorno de desarrollo aislado del worktree: venv `.venv`
(CPython 3.11.14, Django 5.2.17), BD `pos_cierre_claude`, PostgreSQL.

## Comandos exactos y resultado de tests

Todo automatizado, sobre PostgreSQL, desde el worktree
`C:/Proyectos/pos_fifo_system_cierre_claude` con `--settings=config.settings_development`:

```text
manage.py check                       -> System check identified no issues (0 silenced).
manage.py makemigrations --check --dry-run -> No changes detected
compileall (archivos tocados)         -> OK

# Focales del bloque
test apps.ventas.tests.test_anulacion_permisos        -> 8 OK
test apps.ventas.tests.test_reimpresion_permisos      -> 8 OK
test apps.cuentas_por_cobrar.tests.test_modulo_gate
     apps.reportes.tests.test_modulo_gate             -> 6 OK

# Regresión de apps afectadas
test apps.ventas apps.cuentas_por_cobrar apps.reportes apps.permisos
     -> 316 OK (170.9 s)
test apps.configuracion apps.suscripciones
     -> 182 OK (skipped=2)
```

Los WARNING de log que aparecen en la corrida (`Forbidden`, `Not Found`,
`ventas.anulr`, `disco lleno`, `Too Many Requests`) son escenarios negativos
intencionales de tests existentes o nuevos, no fallos.

## Matriz cubierta por los tests

| Caso | Anulación | Reimpresión | Módulo |
| --- | --- | --- | --- |
| Rol custom positivo (permiso en la sucursal) | ✔ servicio + URL | ✔ ticket 200 | ✔ 200 con módulo activo |
| Permiso de otra sucursal negativo | ✔ 403 | ✔ 403 | — |
| Acceso directo por URL respeta el scope | ✔ 403 | ✔ 403/404/302 | ✔ 404 por URL |
| No exposición cross-branch | ✔ listado + servicio | ✔ 404 ticket/comprobante, listado acotado | — |
| Módulo apagado deniega | — | — | ✔ 404 HTML + 404 JSON (CxC y reportes) |
| Venta legacy sin sucursal | ✔ cae al scope operador | — | — |

## Pruebas omitidas / pendientes (no simuladas como hechas)

- **Impresión física** de tickets: los tests mockean `print_ticket_venta`. La
  verificación real de impresora es de C06/visita.
- **Suite completa del proyecto** y matrices multi-tenant/multi-BD: las corre
  A/Codex al integrar (G1). Aquí se corrió la regresión de las apps afectadas.
- **Portal React** (C04): no aplica a este bloque.

## Riesgos

- Cambio de orden de validación en `anular_venta_service`: ahora un id
  inexistente para un usuario sin permiso devuelve 404 (antes 403 sin mirar
  existencia), porque la autorización depende de `venta.sucursal` y exige
  cargar la venta. Es inherente al requisito («contra la sucursal de la propia
  venta bloqueada») y no filtra datos sensibles.
- Reimpresión térmica: el código de permiso cambió de facto de
  `ventas.reimprimir_ticket` (fantasma, denegaba siempre) a `ventas.reimprimir`
  (real, en el preset Cajero). Esto **habilita** la reimpresión a quienes ya
  deberían tenerla; verificar en la visita que ningún rol dependía del código
  viejo (no podía, porque nunca autorizó).
- Enlaces/botones en templates: el enforcement quedó en las vistas. Ocultar el
  enlace de reimpresión/anulación en la UI es cosmético y opcional (CT-02:
  ocultar el botón no es enforcement). No se tocó `templates/base.html`.

## Rollback

Revertir el commit `60c6dbc` en la rama. No hay estado externo, migraciones ni
datos que revertir.

## Dependencias del otro agente (Codex)

- **Handlers de eventos de sync:** este bloque **no introduce eventos de dominio
  nuevos** ni cambia payloads. `anular_venta_service` sigue emitiendo
  `evento_venta_anulada` / `evento_inventario_snapshot` igual que antes; solo
  cambió *quién* puede invocar la anulación (autorización server-side). **No hay
  contrato de eventos nuevo que integrar en `apps/sync`** para esta entrega.
- No se solicita ninguna ruta/firma nueva del núcleo. `requiere_modulo_json` vive
  en `apps/configuracion/decorators.py` (superficie de B).

## Deltas documentales propuestos (los edita Codex)

- `docs/ESTADO_AUDITORIAS.md` (fila `apps/suscripciones`): SUS-006 pasa de
  **Abierto** a **Corregido** (enforcement HTML **y** API de módulo en CxC y
  reportes on-demand; `descargar_pdf_cierre` y `dashboard` documentados como
  lecturas/módulo distinto fuera de alcance).
- `docs/TODO_AUDITORIAS.md`: quitar SUS-006 de los pendientes de
  `apps/suscripciones`.
- `docs/handoffs/cierre_prod/INVENTARIO.md`: SUS-006 → estado REVISION/commit
  `60c6dbc`; PER-013 (consumidores C05) → cubierto (anulación, reimpresión,
  listado), pendiente solo integración.
- `CONTRATOS.md` (CT-02, columna Implementación): los consumidores C de
  anulación/reimpresión quedan cubiertos por C05; falta su integración a
  `develop` y la repetición de la matriz combinada por A.

## Siguiente tarea desbloqueada

- Revisión cruzada por Codex del diff `60c6dbc` e integración serial a `develop`
  (A es quien integra y publica el SHA verde).
- Resto de C05 (parte financiera): constraints/concurrencia/idempotencia de
  venta-inventario-abonos, cotizaciones, caja/CxC y productores de auditoría
  CT-01 en las transacciones de dominio (con sus contratos de eventos a Codex).
- C04 sigue **BLOQUEADO** por CT-04 (A05/A06 no arrancaron); no se inicia.
