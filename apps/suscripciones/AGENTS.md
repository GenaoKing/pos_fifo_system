# apps/suscripciones — mapa para agentes

<!-- Última revisión: 2026-09-17 (SUS-014: postcondición de plan divergente) -->

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
| Validar un `plan_slug` antes de escribirlo en dos bases | `seed.validar_plan_slug(slug, using=<alias>)` (SUS-014) — cableada en `bootstrap_tenant --plan` por Codex (`c003af8`); slug vacío es válido, uno inexistente levanta `PlanDesconocido` sin tocar ninguna base |
| Detectar un plan YA divergente (control plane vs. operativo) | `engine.divergencias_plan_operativo(tenant_plan_slug, negocio)` (SUS-014, mitad 2) — solo lectura, mismo formato de fila que `divergencias_identidad`; **todavía sin cablear** en `verificar_identidad_tenant` |
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
- **SUS-014 — prevención cerrada; postcondición pendiente de cablear.**
  `validar_plan_slug` valida contra la base del `using` dado, no contra
  `default` por defecto: quien la llame bajo tenancy debe pasar el alias del
  tenant explícito. Codex ya la consume en `bootstrap_tenant` (`c003af8`,
  2026-09-15): el slug se valida en el alias tenant después de migrar y antes
  de sembrar la suscripción o publicar `Tenant.plan_slug`; un slug inválido no
  toca ninguna base y el checkpoint queda `FAILED` reanudable (verificado de
  nuevo el 2026-09-17: `apps.tenancy.tests.test_bootstrap_tenant_plan_slug` +
  `apps.suscripciones.tests.test_sus014_validar_plan_slug`, 9 OK). Lo que
  ESTO no cubre es un plan que ya divergió por otra vía (edición manual, un
  bootstrap corrido antes del fix): para eso está
  `engine.divergencias_plan_operativo`, pensada para sumarse a
  `divergencias_identidad`/`verificar_identidad_tenant` — pedido exacto en
  `docs/handoffs/cierre_prod/SUS014-divergencias-plan.md`.
- Auditoría 2026-08-30 (`docs/exploracion/AUDITORIA_CODIGO_APPS_SUSCRIPCIONES.md`)
  — **snapshot histórico**. Cierre en curso (bloque C03): cerrados SUS-008, -009,
  -010, -012, -013, -015, -016 (parcial), -017, -018; SUS-014 prevención
  cerrada (postcondición mitad 2 propuesta, sin cablear); abiertos SUS-006,
  -007, -011, -019.
