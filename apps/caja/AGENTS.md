# apps/caja — mapa para agentes

<!-- Última revisión: 2026-09-08 -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

Arqueo y gestión de caja: `Caja` (física, `origen_id` UUID estable) →
`TurnoCaja` (`ABIERTO`/`CERRADO`, fondo inicial, esperado vs contado) →
`MovimientoCaja` (retiros, gastos, ingresos). Cuadre por método de pago y ticket
de cuadre imprimible. Todo en `apps/caja/models.py` y `views.py`.

## Entrypoints (`app_name = 'caja'`, montado en `/caja/`)

| Necesito… | Voy a… |
| --- | --- |
| Pantalla / historial | `views.caja_index`, `views.historial_turnos` |
| Abrir / cerrar turno | `views.api_abrir_turno`, `views.api_cerrar_turno` (→ `TurnoCaja.cerrar`) |
| Estado / detalle de turno | `views.api_estado_turno`, `views.api_detalle_turno` |
| Registrar movimiento | `views.api_registrar_movimiento` |
| **Autorización puntual** (retiro, descuento, exceso de crédito) | `views.api_validar_admin` → emite token de un solo uso, `apps.permisos.models.AutorizacionOverride` (usuario+clave o carnet `CredencialFisica`) |
| Cuadre imprimible | `views.cuadre_ticket` (HTML) · `views.api_imprimir_cuadre_termica` (→ `utils/impresoras/`) |
| Alcance | `views.cajas_en_alcance`, `turnos_en_alcance`, `es_admin(user, sucursal)` |

## Modelos que importan

- `Caja.turno_activo()`; un solo turno `ABIERTO` por caja (constraint condicional).
- `TurnoCaja.calcular_esperado()` **prefiere `Pago.turno_caja`**; la heurística
  por fecha/usuario queda solo para pagos históricos sin vínculo.
- `TurnoCaja.resumen_operativo()` → desglose por método (base del cuadre).

## Sync / notificaciones

Emite `evento_apertura_caja`, `evento_movimiento_caja`, `evento_cierre_caja`
(`apps/sync/events.py`); handlers en `apps/api/views/sync.py`. Las
notificaciones push del cloud se derivan de **estos** eventos
(`apps/notificaciones/catalogo.py`).

## Trampas

- `ConfiguracionNegocio.conteo_ciego_caja` (migración `configuracion.0010`)
  oculta el efectivo esperado al cajero al cerrar
  (`_oculta_efectivo_por_conteo_ciego`).
- No reconstruir pertenencia de pagos por rango de fechas: usar `turno_caja`.
- `api_validar_admin` tiene freno de intentos (`apps/permisos/throttling.py`).
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_CAJA.md`) —
  **snapshot histórico**, verificar contra código.
