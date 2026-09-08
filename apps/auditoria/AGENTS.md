# apps/auditoria — mapa para agentes

<!-- Última revisión: 2026-09-08 -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

Registro **append-only** de acciones (`Auditoria`, `apps/auditoria/models.py`)
con snapshot del actor y hash de integridad por fila, middleware de captura
automática y un dashboard local en `/auditoria/`.

## Entrypoints (`app_name = 'auditoria'`)

| Necesito… | Voy a… |
| --- | --- |
| **Registrar una acción** | `Auditoria.registrar(accion, descripcion, usuario=, content_object=, metadata=, sucursal=, ...)`; atajos `registrar_venta`, `registrar_anulacion_venta`, `registrar_error`, … |
| Tipos de acción | `Auditoria.TipoAccion` (`TextChoices`) |
| Captura automática por request | `middleware.py` → `AuditoriaMiddleware`, `SesionAuditoriaMiddleware` |
| Dashboard / búsqueda | `views.py` → `dashboard_auditoria`, `api_auditoria_buscar` |
| Alcance por sucursal | `scope.py` → `alcance_de` (`auditoria.ver` / `auditoria.consolidado.ver`, sobre `apps.permisos.alcance`) |
| Verificar que nadie tocó la tabla por fuera | `manage.py verificar_auditoria` (hash por fila + huecos de `id`) |
| Retención | `AuditoriaQuerySet.purgar_hasta(...)` — la **única** vía de borrado |

## Invariantes / trampas

- `update()` / `delete()` lanzan `AuditoriaInmutable`. `save()` congela el actor
  (`actor_*`) y calcula el hash; los registros previos a `0005` quedan sin hash.
- Agregar una acción = nuevo miembro en `TipoAccion` + migración (solo
  `choices`), p. ej. `auditoria.0007` (`COMPROBANTE_PDF`).
- Quien registra lo hace **best-effort**: la operación de negocio no se cae si
  falla la auditoría (patrón en `utils/impresoras/manager.py` y
  `apps/ventas/views.comprobante_venta_pdf`). Excepción deliberada: el logout
  audita **después** de cerrar sesión (USR-004).
- `Auditoria.derivar_sucursal(objeto)` para inferir la sucursal de un hecho.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_AUDITORIA.md`) —
  **snapshot histórico**, verificar contra código.
