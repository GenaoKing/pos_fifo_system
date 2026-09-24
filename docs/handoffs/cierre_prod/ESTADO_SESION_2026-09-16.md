# Estado de la sesión — 2026-09-16 (Claude, deuda propia self-contained)

Punto único de estado del trabajo de esta sesión. **Todo vive en ramas sobre
`develop@fffd02b`, sin `push` ni `merge`.** Se integran en el checkpoint que
acumula sobre `integration/cierre-prod-A05-C03` (terreno de Codex); ninguna toca
a Codex ni a A05.

## Ramas entregadas

| Rama | Tip | Cierra | Migración | Tests | Handoff |
|---|---|---|---|---|---|
| `claude/cierre-prod-ledger-reconcile` | `5e1e843`+doc | Reconciliación INVENTARIO/TODO + este estado | — | — | (este doc) |
| `claude/cierre-prod-C02-com012` | `78bec9a` | COM-012 (tablas de PDF acotadas en memoria) | No | `apps.common` 49 OK + 4 consumidores exit 0 | `C02-com012-tablas-acotadas.md` |
| `claude/cierre-prod-C03-cfg010` | `0db8f57` | CFG-010 pata 1 (integridad) + pata 2 (ámbito por sucursal) | **`0011`** (FK nullable) | `apps.configuracion`+accesos 120 OK | `C03-cfg010-acceso-rapido.md` |
| `claude/cierre-prod-SUS019-coverage` | `0548384` | SUS-019 (guard por plan/`activa`) + hallazgo CFG-021 | No | `test_suscripciones_admin` 16 OK | `SUS019-CFG021-cobertura.md` |
| `claude/cierre-prod-C03-menores` | `1a7b767` | CFG-018, CFG-019, CFG-020 (mi parte) | No | `apps.configuracion` 119 OK | `C03-menores-cfg018-019-020.md` |

**Aparte (Codex):** `REPORTES-CHART-CDN` (Chart.js offline, revisado PASS
`f085d77`) ya está **integrado por Codex** en `integration/cierre-prod-A05-C03`
(`1009ba3`).

## Qué cierra cada una (resumen)

- **COM-012** — `standard_table()` deja de hacer `list(rows)`: recorre perezoso,
  corta en `TABLA_MAX_FILAS=5000` con fila de aviso. Memoria acotada; protege a
  todos los consumidores desde un punto.
- **CFG-010** — pata 1: `AccesoRapidoPOS.save()` llama `full_clean()` (no
  persisten filas inválidas por ORM/import). Pata 2: FK `sucursal` (nullable,
  `NULL`=legacy global) + el endpoint POS filtra por `get_sucursal_actual()` →
  se cierra la fuga entre sucursales.
- **SUS-019** — regresiones del guard de degradación por los canales **plan**
  (downgrade auditado; downgrade con datos en vuelo → 400, rollback sin evento) y
  **`activa`** (suspensión auditada). Antes solo se probaba override + upgrade.
- **CFG-018** — `save()` borra el logo anterior al reemplazarlo (fin de
  huérfanos; el config no se borra —CFG-011—).
- **CFG-019** — se retiran `requiere_sysadmin`/`requiere_admin_o_sysadmin` (sin
  consumidor) + `AGENTS.md`.
- **CFG-020** — `clean()` valida `formato_codigo_barras` contra la gramática que
  el generador consume (`[A-Z0-9]{1,13}-XXXXXX`).
- **CFG-021** — verificado ya cubierto por los tests de C03; no requiere código.

## Orden e indicaciones de integración

1. Integrar sobre `integration/cierre-prod-A05-C03`. Todas parten de
   `develop@fffd02b`, que ya es ancestro de esa integración.
2. **`ledger-reconcile` es doc-only** (INVENTARIO/TODO + este doc). Si otra rama
   editó los mismos docs, resolver el merge a mano (no hay conflicto de código).
3. **`C03-cfg010` trae la migración `0011_accesorapidopos_sucursal`.** Verificar
   que el número `0011` no colisione con otra migración de `configuracion` en la
   integración; si colisiona, `makemigrations --merge`.
4. El resto (`com012`, `sus019`, `menores`) es lógica/tests, sin migración.

## Preflights operativos (NO ejecutar a ciegas; quedan para el despliegue)

- **CFG-010**: backfill de las filas legacy (`AccesoRapidoPOS.sucursal IS NULL`)
  a su sucursal; y el `CheckConstraint` de exclusividad producto/categoría
  (criterio CFG-006 — la invariante ya está a nivel app por pata 1).
- **COM-013** (A+C): fijar ReportLab/Pillow en el build — la parte de deps es de
  Codex.

## Mi deuda propia que queda (self-contained, sin permisos)

Prácticamente agotada. Lo que resta ya no es limpio:

- **SUS-014** — el plan del control-plane puede divergir del operativo
  (cross-DB); conviene coordinar por el tema multi-DB/tenancy.
- **CFG-007** — el pull omite validadores; es **C+A** con contrato **abierto**
  (necesita la mitad de sync de Codex).

El resto de lo abierto es de Codex (clientes/productos/A05), o son decisiones y
preflights de negocio (backfills históricos, Redis compartido, retirar el bypass
de ADMIN tras su preflight). Ver `INVENTARIO.md` y `TODO_AUDITORIAS.md`.
