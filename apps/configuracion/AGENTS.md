# apps/configuracion — mapa para agentes

<!-- Última revisión: 2026-09-24 (CT-03 y administración integrados) -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

`ConfiguracionNegocio` (`apps/configuracion/models.py`): **una por sucursal**.
Identidad fiscal (nombre, RNC, dirección, teléfono, logo), flags legacy de
módulos, métodos de pago, parámetros operativos (copias de ticket, días de
anulación), control de caja (`conteo_ciego_caja`), política de descuentos y
e-CF/ITBIS. También `AccesoRapidoPOS` (botones del POS). No es preferencia de
UI: es el *control plane* de la instalación. Se edita por Django admin
(`views.py` es un stub).

## Entrypoints

| Necesito… | Voy a… |
| --- | --- |
| **Leer la config del contexto actual** | `utils.get_config()` — por `SUCURSAL_CODIGO`, cacheada con clave por tenant (CFG-001), TTL 30 s local / 600 s compartido (CFG-005). **Nunca crea** (CFG-012): sin fila, levanta `ConfiguracionNoInicializada` |
| **Leer solo para MOSTRAR** (login, nav, error) sin tumbar el render si no hay config | `utils.config_o_none()` / `modulos_efectivos_o_vacio()` — devuelven `None`/`set()` en vez de propagar (CFG-012) |
| **Crear la config si no existe** (fixtures de test, futuro primer-pull de sync) | `ConfiguracionNegocio.bootstrap(sucursal=...)` — get-or-create explícito, idempotente. Sin `sucursal` (legacy), no es un get-or-create ciego: cero filas crea una sola, una fila existente (sea cual sea su PK) se reutiliza, y más de una levanta `ConfiguracionAmbigua` en vez de elegir. La instalación real usa `crear_config_inicial`, no esto |
| Config para **encabezar un documento** | `utils.config_para_documento(sucursal)` (COM-001) · `config_de_sucursal` |
| ¿Módulo activo? | `utils.modulo_activo(key)` → delega en `apps.suscripciones.engine` si resuelve negocio; si no, flag legacy |
| Gatear una vista por módulo | `decorators.requiere_modulo` (HTML → 404) · `requiere_modulo_json` (fetch → 404 JSON) |
| `{{ config }}` / `modulos_efectivos` en templates | `context_processors.config_negocio` (inyecta ambos) |
| Gatear un menú/pantalla por módulo | `{% if 'key' in modulos_efectivos %}` — **no** `config.modulo_*` (CFG-009/SUS-007) |
| ¿Este descuento pide autorización? | `ConfiguracionNegocio.descuento_requiere_token(subtotal=, descuento_total=)` |
| Instalar / diagnosticar | `manage.py crear_config_inicial`, `migrar_env_cliente` (`.bat` → `.env`), `verificar_instalacion` (solo lectura) |
| ¿Quién cambió esta config? | `Auditoria` (CT-01): Admin local y `services.actualizar_configuracion(...)` para `/api/v1/administracion/configuraciones/{id}/` |

## Invariantes / trampas

- **Leer no crea (CFG-012).** `ConfiguracionNegocio.load()` es lectura PURA:
  sin fila, levanta `ConfiguracionNoInicializada` con la acción correcta
  (`crear_config_inicial`), nunca crea ni cachea el fallo. El context
  processor corre en CADA render (login, error, admin incluidos) y usa las
  variantes `_o_none()`/`_o_vacio()` a propósito — una decisión de negocio
  real (medios de pago, e-CF) sigue usando `get_config()` directo, que sigue
  fallando fuerte. Crear la fila fuera de `crear_config_inicial` es
  `ConfiguracionNegocio.bootstrap(...)`, explícito y con otro nombre. Su rama
  legacy (sin `sucursal`) ya no es un `get_or_create(pk=1)` ciego: sigue la
  misma regla que `_config_sin_sucursal` (CFG-002) aplicada a escribir — cero
  filas crea una, una existente con cualquier PK se reutiliza, más de una
  levanta `ConfiguracionAmbigua` (nunca `.first()`, nunca crea otra).
- `save()` invalida el cache (`utils.cache_key_config`); `delete()` **y**
  `QuerySet.delete()` levantan `ConfiguracionProtegidaError` (CFG-011; antes
  `delete()` era un `pass` silencioso y el QuerySet borraba de verdad). Ya **no**
  se fuerza `pk=1`.
- `full_clean()` valida reglas cruzadas (CFG-006): al menos un medio de pago,
  e-CF exige `emisor_activo`, ITBIS en `[0,100]` y el formato de código de
  barras es `PREFIJO-XXXXXX` que el generador realmente puede producir. Es
  validación de aplicación (Admin/forms); todavía **no** hay constraints DB
  (esperan preflight de datos).
- Reemplazar `logo` borra el archivo previo después de guardar; borrar una
  configuración sigue prohibido. La autoridad y propagación del logo por sync
  no están definidas en esta fase.
- `AccesoRapidoPOS` exige producto XOR categoría también en `save()` y se
  acota a `sucursal`; las filas legacy con `sucursal=NULL` son globales hasta un
  backfill explícito. El endpoint del POS solo muestra los de la sucursal actual
  más esos legacy globales.
- `SUCURSAL_CODIGO` que no resuelve: con una sola config cae a ella con
  warning; con varias levanta `ConfiguracionNoResuelta` (CFG-002). Nunca
  `.objects.first()`.
- `crear_config_inicial` sin `--sucursal` **aborta** si ya hay configs ligadas a
  sucursal (no pisa la de menor PK — CFG-015); el modo legacy opera solo sobre
  la fila `sucursal=NULL` y falla si hay más de una.
- El portal solo edita una allowlist operativa con `configuracion.administrar`
  global y motivo obligatorio. No crea/borra filas, ni toca logo, emisor e-CF o
  `modulo_*`: esos campos mantienen sus flujos y entitlements propios.
- `verificar_instalacion`: `--strict` ⇒ exit ≠ 0 ante estado roto (gate de
  deploy, CFG-014); reporta la config de la sucursal resuelta, no `.first()`
  (CFG-014); el modo legacy enumera los flags reales (CFG-013).
- Los datos del negocio **no** van en `deploy/env_cliente.env`: el env es
  infraestructura (BD, impresoras, sync, `SUCURSAL_CODIGO`).
- Los flags `modulo_*` son legacy: la verdad de entitlements está en
  `apps/suscripciones` (`flag_legacy` en su `registry`). La UI ya **no** los lee:
  `utils.modulos_efectivos()` (via context processor) resuelve por el mismo motor
  que gatea el backend (CFG-009/SUS-007). El pull de sync ya deriva los flags
  del mismo motor y valida el payload antes de guardar (CT-03, `1019500`).
- `texto_pie_ticket` / `imprimir_logo_ticket` fueron eliminados en `0002`; el
  driver térmico los lee con `getattr` y cae al default.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_CONFIGURACION.md`)
  — **snapshot histórico**, verificar contra código.
