# apps/suscripciones — mapa para agentes

<!-- Última revisión: 2026-09-08 -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

Entitlements de módulos por negocio (`models.py`): `Modulo` (espejo en BD del
registro), `Plan`, `SuscripcionNegocio`, `NegocioModulo` (± a la carta),
`SucursalModuloOverride` (una sucursal solo puede **apagar**). Fórmula:

```
cierre( plan.modulos ∪ {incluidos} − {excluidos} ) ∪ core − {overrides apagados}
```

## Entrypoints

| Necesito… | Voy a… |
| --- | --- |
| **¿Módulo activo?** | `engine.modulo_activo(key, negocio=, sucursal=)` · `modulos_activos` — desde una vista, vía `apps.configuracion.utils.modulo_activo(key)` |
| Estado comercial de un negocio | `engine.estado_suscripcion` (`SIN_APROVISIONAR` / `SUSPENDIDA` / `CON_PLAN` / `CUSTOM`) |
| ¿Se puede desactivar? | `engine.puede_desactivarse(negocio, key)` (bloquea si hay dependientes o datos en vuelo: CxC abiertas, ECF en proceso) |
| Declarar un módulo | `registry.CATALOGO_MODULOS` (`key`, `depende_de`, `core`, `flag_legacy`) → `manage.py sync_modulos` |
| Aprovisionar negocios existentes | `manage.py bootstrap_suscripciones` · `seed.py` |
| Gate en vistas / DRF | `apps.configuracion.decorators.requiere_modulo` · `apps/api/permissions.RequiereModulo` |
| Admin por API | `apps/api/views/suscripciones.py` (permiso `suscripciones.administrar`, solo operador global) |

## Invariantes / trampas

- **Fail-open** sin negocio o `SIN_APROVISIONAR` (BUG-D): un entitlement es
  comercial, no seguridad. La seguridad es `apps/permisos` (default deny).
- Cache con versión y clave por tenant; TTL 30 s con `LocMemCache` (SUS-003).
  Las signals invalidan ante cualquier cambio de plan/suscripción/override.
- El grafo de dependencias vive **solo** en `registry.py`; la tabla `Modulo` es
  un espejo.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_SUSCRIPCIONES.md`)
  — **snapshot histórico**; SUS-006..010 abiertos en `docs/TODO_AUDITORIAS.md`.
