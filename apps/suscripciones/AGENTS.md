# apps/suscripciones — mapa para agentes

<!-- Última revisión: 2026-09-11 (SUS-014: validación de plan_slug, parte2 de C03) -->

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
| Resincronizar los planes default (Basico/Pro/Empresarial) | `manage.py sync_modulos` · `seed.sincronizar_planes_preset` — solo toca planes con `preset_version` no nulo (SUS-017) |
| Validar un `plan_slug` antes de escribirlo en dos bases | `seed.validar_plan_slug(slug, using=<alias>)` (SUS-014) — pensada para `bootstrap_tenant --plan`; slug vacío es válido, uno inexistente levanta `PlanDesconocido` sin tocar nada |
| Gate en vistas / DRF | `apps.configuracion.decorators.requiere_modulo` · `apps/api/permissions.RequiereModulo` |
| Admin por API | `apps/api/views/suscripciones.py` (permiso `suscripciones.administrar`, solo operador global) |
| ¿Quién cambió un plan/override? | `Auditoria` (CT-01), acción `suscripciones.suscripcion.*` / `suscripciones.override_negocio.*` — la registra `GuardDegradacionMixin._aplicar` en la misma transacción que la escritura (SUS-015) |

## Invariantes / trampas

- **Fail-open** sin negocio o `SIN_APROVISIONAR` (BUG-D): un entitlement es
  comercial, no seguridad. La seguridad es `apps/permisos` (default deny). Pero
  una `key` que no está en `registry` **siempre deniega** aunque no haya negocio
  (SUS-018): el fail-open es para keys reales, no para un typo en un gate.
- `puede_desactivarse` es **fail-closed**: si un hook de datos en vuelo
  (`_HOOKS_DATOS`) revienta, bloquea la baja y loguea; un fallo de infra no es
  permiso para apagar (SUS-010).
- Cache con versión y clave por tenant; TTL 30 s con `LocMemCache` (SUS-003).
  Las signals invalidan ante cualquier cambio de plan/suscripción/override,
  diferido con `transaction.on_commit` (SUS-011): un rollback no invalida
  nada; en tests, escribir y esperar ver el cambio requiere
  `self.captureOnCommitCallbacks(execute=True)` alrededor de la escritura.
- El grafo de dependencias vive **solo** en `registry.py`; la tabla `Modulo` es
  un espejo. `checks.py` (system checks) falla si el registro es inconsistente o
  si la DB y el registro divergen (SUS-012).
- `Plan.activo=False` = **no vendible a nuevas altas**; NO suspende clientes
  existentes (la suspensión es `SuscripcionNegocio.activa`). El serializer
  rechaza asignar un plan inactivo salvo re-guardar a quien ya lo tiene (SUS-013).
- Excluir un módulo core (`NegocioModulo incluido=False`) o apagarlo por sucursal
  (`SucursalModuloOverride`) es un no-op enganoso: ambos se rechazan (SUS-013).
- Cambios comerciales (`SuscripcionNegocio`, `NegocioModulo` vía API) dejan
  evento CT-01 **después** de que el guard de degradación pasa: un rechazo no
  deja ni escritura ni evento. `suscripciones.suscripcion.creado`/`.eliminado`
  no se emiten — ese viewset no permite POST ni DELETE.
- `Plan.preset_version=None` = personalizado: `sync_modulos` nunca lo toca.
  Un valor = versión de `seed.TIERS` aplicada; desactualizado se resincroniza
  solo, con la sincronizacion real, al correr el comando (SUS-017).
- **SUS-014 — `validar_plan_slug` es la mitad C03; falta cablearla.** Valida
  contra la base del `using` dado, no contra `default` por defecto: quien la
  llame bajo tenancy debe pasar el alias del tenant explícito. Todavía no la
  consume nadie (`bootstrap_tenant` es de Codex — ver handoff parte2 para el
  pedido exacto de dónde y cuándo llamarla).
- Auditoría 2026-08-30 (`docs/exploracion/AUDITORIA_CODIGO_APPS_SUSCRIPCIONES.md`)
  — **snapshot histórico**. Cierre en curso (bloque C03): cerrados SUS-008, -009,
  -010, -012, -013, -015, -016 (parcial), -017, -018; abiertos SUS-006, -007,
  -011, -014 (mitad C, falta cableado A), -019.
