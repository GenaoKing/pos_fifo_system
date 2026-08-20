# Arquitectura de módulos vendibles (SaaS)

Cómo se organizan los módulos del sistema para venderse en **tiers** (Basic/Pro/
Enterprise) y/o **custom por empresa**, qué depende de qué, y cómo se resuelve qué
módulos están activos para cada negocio/sucursal.

App: `apps/suscripciones`. Plan de diseño:
`C:\Users\Santiago\.claude\plans\abstract-skipping-parnas.md`.

> **Estado:** Fase 1 (fundación) implementada — registro, modelos, resolutor, seed
> y migración back-compat. Fases 2–4 (enforcement, admin/React, hooks de
> degradación) pendientes — ver §7.

---

## 1. Grafo de dependencias

```
NÚCLEO (core = siempre activo, no vendible)
  productos
  inventario  → productos
  clientes
  ventas      → productos, inventario
  caja        → ventas

VENDIBLES (satélites; dependen del núcleo)
  cuentas_por_cobrar (crédito) → ventas, clientes
  ecf (facturación electrónica) → ventas
  financiacion (cooperativa)    → ventas
  cotizaciones                  → ventas, productos
  etiquetas_zebra               → productos
  reportes_ondemand             → ventas
  dashboard                     → ventas
  impresion_termica             → (capacidad)
  barcode_scanner               → (capacidad)

Infra (no módulos): usuarios, sucursales, negocios, permisos, suscripciones,
configuracion, auditoria, sync, api.
```

La fuente de verdad de este grafo es `apps/suscripciones/registry.py`
(`CATALOGO_MODULOS`, cada `Modulo` con `depende_de`, `core`, `flag_legacy`).
Helpers: `cierre_dependencias()`, `dependientes_de()`, `core_keys()`, `validar()`.

**Por qué importa el grafo:**
- Habilitar un módulo activa (cierre transitivo) sus dependencias.
- Desactivar un módulo se **bloquea** si hay otro activo que dependa de él.
- El acoplamiento en código ya es **lazy** (`ventas_service.py` importa CxC/ecf solo
  en runtime), así que el core corre sin los satélites; el grafo formaliza esa realidad.

---

## 2. Modelo de entitlements

Nivel **Negocio (tenant)** con **override por sucursal**:

| Modelo (`apps/suscripciones/models.py`) | Qué hace |
|---|---|
| `Modulo` | Espejo en DB del registro (para M2M/admin). |
| `Plan` | Tier comercial = preset de módulos (M2M a `Modulo`). |
| `SuscripcionNegocio` | El plan del tenant (`negocio` OneToOne, `plan` FK, `activa`). |
| `NegocioModulo` | Override à la carte del tenant: `incluido=True` agrega, `False` quita. |
| `SucursalModuloOverride` | Apaga local en una sucursal (`activo=False`) lo que el tenant sí tiene. |

---

## 3. Resolución del set activo (`apps/suscripciones/engine.py`)

```
modulos_negocio(negocio) =
    cierre( plan.modulos ∪ {NegocioModulo incluido} − {NegocioModulo excluido} ) ∪ core

modulos_activos(negocio, sucursal) =
    modulos_negocio(negocio) − {SucursalModuloOverride apagados}   (core nunca se apaga)

modulo_activo(key, negocio, sucursal) -> bool
```

- Cacheado por negocio con versión global (invalidación por signals, igual patrón que
  `apps/permisos/engine.py`).
- **Fail-open** si `negocio` es None: `modulo_activo` devuelve True. Los entitlements son
  *comerciales*, no de seguridad; no se debe romper el POS de un cliente por un tenant sin
  resolver. La *seguridad* la dan los permisos (default-deny).

---

## 4. Composición con permisos

Dos capas ortogonales. Una función está disponible **sii**:

```
modulo_activo(tenant)   AND   usuario.tiene_permiso(...)
```

- **Módulo** = ¿el plan del negocio incluye esto? (comercial)
- **Permiso** = ¿este usuario puede usarlo? (seguridad, `apps/permisos`)

---

## 5. Seed, planes y back-compat

- `manage.py sync_modulos` — upsert de `Modulo` + planes default desde el registro/`seed.py`.
- `manage.py bootstrap_suscripciones` — por cada negocio existente crea su
  `SuscripcionNegocio` (plan=None, custom) con los módulos **derivados de sus flags actuales**
  de `ConfiguracionNegocio.modulo_*` (unión sobre sus sucursales). Así **nada cambia hoy**.
- Migración `suscripciones/0002_seed_suscripciones` hace lo anterior (en BD fresca/tests solo
  siembra módulos+planes).

**Tiers por defecto** (`seed.py:TIERS`, ajustables):
- **Básico**: núcleo + impresión térmica + lector de barras.
- **Pro**: Básico + CxC + cotizaciones + reportes + dashboard + etiquetas.
- **Empresarial**: Pro + e-CF + financiación.

---

## 6. Relación con `ConfiguracionNegocio` (legacy)

Hoy los flags `modulo_*` viven **por sucursal** en `ConfiguracionNegocio` y se consultan vía
`apps/configuracion/utils.py:modulo_activo()` + `@requiere_modulo`. La Fase 1 **no** los
modifica: solo deriva de ellos el entitlement inicial. En Fase 2, `modulo_activo`/
`@requiere_modulo` se reapuntan al resolutor por tenant (manteniendo la firma), y los flags
de `ConfiguracionNegocio` quedan como derivados/legacy.

---

## 7. Cómo seguir (Fases 2–4)

2. **Enforcement backend**: reapuntar `apps/configuracion/utils.py:modulo_activo` y
   `@requiere_modulo` al resolutor; `RequiereModulo('key')` DRF + mixin en
   `apps/api/permissions.py`; gatear CxC (`apps/api/views/cuentas_por_cobrar.py`) y el flujo
   de venta a crédito (`apps/ventas/services/ventas_service.py`) por tenant; aplicar
   `puede_desactivarse` donde se administren entitlements.
3. **Admin + React**: `PlanViewSet`/`ModuloViewSet` + gestión de entitlements (gated por un
   permiso nuevo `suscripciones.administrar`); `modulos: string[]` en el payload de
   `/login`+`/me`; `useAuth().hasModule(key)` + gating; pantalla de administración por negocio.
4. **Degradación**: ampliar los hooks `_HOOKS_DATOS` de `engine.py` (e-CF con emisiones
   pendientes, etc.) y la UX de bloqueo al desactivar.

**Cómo agregar un módulo nuevo:** añadir un `Modulo(...)` a `registry.py` (con `depende_de`),
correr `manage.py sync_modulos`, incluirlo en los planes deseados, y gatear sus
vistas/endpoints con `modulo_activo`/`RequiereModulo`.

---

## 8. Verificación

```bash
python manage.py test apps.suscripciones --settings=config.settings_development
```
Cubre: cierre de dependencias, resolutor (plan + override à la carte + apagado por sucursal,
core no apagable), `puede_desactivarse` (bloqueo por dependientes), fail-open sin negocio, y
derivación back-compat desde `ConfiguracionNegocio`.

---

## Deuda: la asimetria fail-open / fail-closed (2026-08-19)

El resolutor de modulos falla ABIERTO en un caso y CERRADO en otro, y los dos
estados se ven iguales desde afuera. Esa asimetria costo un diagnostico largo
(ver BUG-D en `docs/BUGS.md`).

```
modulo_activo(key)
  sucursal SIN negocio            -> True  (fail-OPEN, lee el flag legacy)
  negocio SIN aprovisionar        -> False (fail-CLOSED, solo core)
  negocio CON plan/NegocioModulo  -> segun el entitlement   <- el unico intencional
```

El segundo caso no es una decision de producto: es un negocio que existe pero al
que nunca se le asignaron modulos. Tratarlo como "no tiene derecho a nada" apaga
funciones que el cliente si compro -- incluida la impresion de tickets, que no es
opcional para un POS.

**Arreglo sugerido (no aplicado):** que un negocio sin suscripcion **y** sin
filas `NegocioModulo` se trate como *no aprovisionado* y falle ABIERTO, igual que
una sucursal sin negocio. Un negocio con plan o con overrides sigue resolviendo
por entitlement, que es lo que el sistema quiere expresar.

```python
# apps/suscripciones/engine.py :: _resolver_negocio
# Hoy: sin plan y sin overrides -> solo core.
# Sugerido: sin plan y sin overrides -> nunca se aprovisiono -> todos los modulos.
```

Mientras no se aplique, `manage.py verificar_instalacion` lo detecta y explica.

**Por que importa mas de lo que parece:** el sistema de modulos hoy no cobra ni
bloquea comercialmente nada -- es una fundacion para vender por tiers mas
adelante. Pero ya tiene poder para apagar funciones en produccion. Una fundacion
sin uso comercial no deberia poder dejar a un cliente sin imprimir.
