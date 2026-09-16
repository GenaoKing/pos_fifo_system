# apps/productos — mapa para agentes

<!-- Última revisión: 2026-09-15 -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

Catálogo: `Categoria` y `Producto` (`apps/productos/models.py`). Incluye
atributos configurables por categoría e imágenes con miniatura.

## Dos espacios de URL — no confundir

| Espacio | Prefijo | Dónde vive |
| --- | --- | --- |
| POS local (templates Django) | `/productos/` | `apps/productos/views.py` + `urls.py` |
| API portal cloud (DRF) | `/api/v1/maestros/productos/` | `apps/api/views/maestros.py` → `ProductoViewSet` |

`ProductoViewSet` es el **patrón canónico** de todos los ViewSets maestros
(read/write serializer split, permisos, `SyncIncrementalMixin`).

## Imágenes (regla estricta)

- `imagen` = original intacto · `imagen_miniatura` = JPEG 320px en `thumbs/`,
  lo escribe **solo el modelo**.
- **Para mostrar: `producto.imagen_preview`** (miniatura, o original si aún no hay).
  Nunca deducir la ruta de la miniatura.
- API expone `imagen_thumb_url` (grilla) y `imagen_url` (original).
- Estándar en `utils/imagenes.py`. Backfill: `python manage.py generar_miniaturas --apply`.

## Sync (cómo llega desde el cloud)

Pull incremental, **no eventos**. La sucursal aplica cambios vía
`apps/sync/engine.py` → `_pull_productos`, `_pull_categorias`,
`_descargar_imagen_producto`. Categoría y Producto se adoptan primero por
`origen_cloud_id`; el SKU de Producto solo es clave natural de la primera
adopción y queda inmutable tras el alta. `origen_sucursal` conserva la
procedencia de un stub BUG-H y nunca sustituye esa identidad. Backfill de
imágenes: `python manage.py descargar_imagenes_productos`.

Las escrituras locales del catálogo pasan por `services.py` (`crear_*_local`,
`editar_*_local`, `cambiar_estado_*_local`): crean maestro, auditoría CT-01 y
`MutacionMaestro` A05.2a dentro de una sola transacción. Esa cola no es
`EventoSync`, no se empuja aún y requiere actor, sucursal y permiso CT-02.
Solo un estado `CONFLICTO` bloquea la vendibilidad nueva; `PENDIENTE` sigue
usable hasta que A05.3/A06 reciba y resuelva la propuesta.

## Trampas

- Editar en el portal actualiza `fecha_modificacion` (`auto_now`) → eso es lo que
  el pull incremental de la sucursal usa como cursor. No pisar ese campo a mano.
- `Producto.origen_cloud_id` admite solo la adopción `NULL → id`; luego es
  inmutable. Una colisión de identidad/SKU se difiere explícitamente en sync.
- `Categoria "Sin clasificar"` tiene manejo especial (ver `test_categoria_sin_clasificar`).
- Las respuestas JSON de fallos inesperados son genéricas y se registran con
  traza; no devuelven `str(exc)`. Los fixtures que necesitan código de barras
  crean `ConfiguracionNegocio` explícitamente: el catálogo no la crea por sí mismo.
- Auditoría 2026-08-20 (`docs/exploracion/AUDITORIA_CODIGO_APPS_PRODUCTOS.md`) —
  **snapshot histórico**, verificar contra código.
