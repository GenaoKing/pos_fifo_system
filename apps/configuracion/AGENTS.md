# apps/configuracion — mapa para agentes

<!-- Última revisión: 2026-09-08 -->

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
| Gatear una vista por módulo | `decorators.requiere_modulo` (404) · `requiere_sysadmin` |
| `{{ config }}` en templates | `context_processors.config_negocio` |
| ¿Este descuento pide autorización? | `ConfiguracionNegocio.descuento_requiere_token(subtotal=, descuento_total=)` |
| Instalar / diagnosticar | `manage.py crear_config_inicial`, `migrar_env_cliente` (`.bat` → `.env`), `verificar_instalacion` (solo lectura) |

## Invariantes / trampas

- `save()` invalida el cache (`utils.cache_key_config`); `delete()` es no-op.
  Ya **no** se fuerza `pk=1`.
- `SUCURSAL_CODIGO` que no resuelve: con una sola config cae a ella con
  warning; con varias levanta `ConfiguracionNoResuelta` (CFG-002). Nunca
  `.objects.first()`.
- Los datos del negocio **no** van en `deploy/env_cliente.env`: el env es
  infraestructura (BD, impresoras, sync, `SUCURSAL_CODIGO`).
- Los flags `modulo_*` son legacy: la verdad de entitlements está en
  `apps/suscripciones` (`flag_legacy` en su `registry`).
- `texto_pie_ticket` / `imprimir_logo_ticket` fueron eliminados en `0002`; el
  driver térmico los lee con `getattr` y cae al default.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_CONFIGURACION.md`)
  — **snapshot histórico**, verificar contra código.
