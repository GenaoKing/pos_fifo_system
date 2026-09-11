# apps/configuracion — mapa para agentes

<!-- Última revisión: 2026-09-11 -->

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
| **Leer la config del contexto actual** | `utils.get_config()` — por `SUCURSAL_CODIGO`, cacheada con clave por tenant (CFG-001), TTL 30 s local / 600 s compartido (CFG-005) |
| Config para **encabezar un documento** | `utils.config_para_documento(sucursal)` (COM-001) · `config_de_sucursal` |
| ¿Módulo activo? | `utils.modulo_activo(key)` → delega en `apps.suscripciones.engine` si resuelve negocio; si no, flag legacy |
| Gatear una vista por módulo | `decorators.requiere_modulo` (HTML → 404) · `requiere_modulo_json` (fetch → 404 JSON) · `requiere_sysadmin` |
| `{{ config }}` / `modulos_efectivos` en templates | `context_processors.config_negocio` (inyecta ambos) |
| Gatear un menú/pantalla por módulo | `{% if 'key' in modulos_efectivos %}` — **no** `config.modulo_*` (CFG-009/SUS-007) |
| ¿Este descuento pide autorización? | `ConfiguracionNegocio.descuento_requiere_token(subtotal=, descuento_total=)` |
| Instalar / diagnosticar | `manage.py crear_config_inicial`, `migrar_env_cliente` (`.bat` → `.env`), `verificar_instalacion` (solo lectura) |
| ¿Quién cambió esta config? | `Auditoria` (CT-01), acción `configuracion.negocio.creado`/`.actualizado` — la registra `ConfiguracionNegocioAdmin.save_model` (CFG-017; único punto de escritura, no hay viewset API) |

## Invariantes / trampas

- `save()` invalida el cache (`utils.cache_key_config`); `delete()` **y**
  `QuerySet.delete()` levantan `ConfiguracionProtegidaError` (CFG-011; antes
  `delete()` era un `pass` silencioso y el QuerySet borraba de verdad). Ya **no**
  se fuerza `pk=1`.
- `full_clean()` valida reglas cruzadas (CFG-006): al menos un medio de pago,
  e-CF exige `emisor_activo`, ITBIS en `[0,100]`. Es validación de aplicación
  (Admin/forms); todavía **no** hay constraints DB (esperan preflight de datos).
- `SUCURSAL_CODIGO` que no resuelve: con una sola config cae a ella con
  warning; con varias levanta `ConfiguracionNoResuelta` (CFG-002). Nunca
  `.objects.first()`.
- `crear_config_inicial` sin `--sucursal` **aborta** si ya hay configs ligadas a
  sucursal (no pisa la de menor PK — CFG-015); el modo legacy opera solo sobre
  la fila `sucursal=NULL` y falla si hay más de una.
- `verificar_instalacion`: `--strict` ⇒ exit ≠ 0 ante estado roto (gate de
  deploy, CFG-014); reporta la config de la sucursal resuelta, no `.first()`
  (CFG-014); el modo legacy enumera los flags reales (CFG-013).
- Los datos del negocio **no** van en `deploy/env_cliente.env`: el env es
  infraestructura (BD, impresoras, sync, `SUCURSAL_CODIGO`).
- Los flags `modulo_*` son legacy: la verdad de entitlements está en
  `apps/suscripciones` (`flag_legacy` en su `registry`). La UI ya **no** los lee:
  `utils.modulos_efectivos()` (via context processor) resuelve por el mismo motor
  que gatea el backend (CFG-009/SUS-007). Falta migrar el pull de sync (Codex).
- `texto_pie_ticket` / `imprimir_logo_ticket` fueron eliminados en `0002`; el
  driver térmico los lee con `getattr` y cae al default.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_CONFIGURACION.md`)
  — **snapshot histórico**, verificar contra código.
