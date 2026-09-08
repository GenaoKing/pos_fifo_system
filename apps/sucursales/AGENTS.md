# apps/sucursales — mapa para agentes

<!-- Última revisión: 2026-09-08 -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

`Sucursal` (`apps/sucursales/models.py`): punto de venta físico — `codigo`
único (prefijo de `numero_venta`), `negocio`, `api_key`, `usuario_servicio`
(dueño del token DRF con el que la sucursal sincroniza), `ultima_sync`. Cada
instalación local **es** una sucursal, identificada por
`settings.SUCURSAL_CODIGO` (`deploy/env_cliente.env`).

## Entrypoints

| Necesito… | Voy a… |
| --- | --- |
| **La sucursal de esta instalación** | `models.get_sucursal_actual()` (o `request.sucursal`) |
| `request.sucursal` | `middleware.SucursalMiddleware` (es `None` bajo tenancy) |
| `{{ sucursal }}` en templates | `context_processors.sucursal_actual` |
| Crear una sucursal al instalar | `manage.py crear_sucursal --codigo SD-001 --nombre ... [--preset]` |
| Vincular su token de sync | `manage.py vincular_sucursal_token` (vive en `apps/api`) |
| Estado de sucursales en el portal | `apps/api/views/sucursales.py` → `sucursales_status` |

## Invariantes / trampas

- `get_sucursal_actual()` cachea `sucursal_actual_<codigo>` **sin TTL**: tras
  cambiar código o datos, reiniciar o limpiar cache. En tests, usar
  `with self.settings(SUCURSAL_CODIGO=...)`.
- `SUCURSAL_CODIGO` trae `SD-001` por defecto en toda instalación: que "haya un
  código" no prueba que alguien lo configuró (CFG-002).
- `ConfiguracionNegocio.sucursal` es OneToOne: la identidad fiscal es de la
  sucursal, no del negocio.
- Sin `tests/` propios; la cobertura vive en las apps que la consumen.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_SUCURSALES.md`)
  — **snapshot histórico**, verificar contra código.
