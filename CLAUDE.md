# CLAUDE.md — Guía de desarrollo para pos_fifo_system

## Stack

- Django 4.x + DRF · PostgreSQL · Python 3.11
- Entorno conda: `pos_fifo` (`C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe`)
- Settings de desarrollo local: `config.settings_development`
- Settings de Azure (producción/cloud): `config.settings_azure_pg`

---

## Estructura del proyecto

```
apps/
  api/          ← API REST del portal cloud (DRF)
  productos/    ← Catálogo de productos y categorías
  clientes/     ← Clientes (modelo + vistas POS local)
  ventas/       ← Ventas y FIFO
  inventario/   ← Lotes, compras, FIFO logic
  sync/         ← Motor de sincronización cloud ↔ sucursal
  reportes/     ← Dashboard y métricas
  sucursales/   ← Modelo Sucursal y tokens
  usuarios/     ← Modelo Usuario personalizado
  ...
scripts/        ← Scripts manuales de prueba (NO para manage.py test)
docs/           ← Roadmap, handoffs, contratos de API
```

---

## Convenciones de tests

Ver [docs/TESTING.md](docs/TESTING.md) para la referencia completa.

**Regla principal:** cada app usa un package `tests/` (no un `tests.py` suelto).

```
apps/<app>/tests/
    __init__.py        ← vacío
    test_<concern>.py  ← un archivo por área/viewset/feature
```

**Cómo correr:**

```bash
# Un archivo específico
python manage.py test apps.api.tests.test_categoria_viewset --settings=config.settings_development

# Varios archivos
python manage.py test apps.api.tests.test_producto_viewset apps.sync.tests.test_engine --settings=config.settings_development

# Todo el proyecto (discovery automático)
python manage.py test --settings=config.settings_development
```

> `manage.py test apps.api` (sin el módulo concreto) falla con el runner de unittest — siempre especificar el módulo completo o dejar sin argumentos para todo.

---

## Dos espacios de URL para clientes — no confundir

| Espacio | Prefijo | Quién lo usa | Propósito |
|---|---|---|---|
| POS local (templates Django) | `/clientes/` | Navegador en sucursal | CRUD desde la UI del POS |
| API portal cloud (DRF) | `/api/v1/maestros/clientes/` | Frontend portal React | CRUD desde el portal cloud |

**Contrato del portal:**

```
POST   /api/v1/maestros/clientes/          ← crear cliente
GET    /api/v1/maestros/clientes/          ← listar (con ?search=, ?tipo=, ?activo=)
PATCH  /api/v1/maestros/clientes/<id>/     ← editar
DELETE /api/v1/maestros/clientes/<id>/     ← borrar
```

La ruta POS `GET /clientes/<id>/` es el detalle en template Django — **no** es el endpoint de creación del portal.

---

## Patrón ViewSet (portal cloud)

Todos los ViewSets de datos maestros siguen el mismo patrón — ver `ProductoViewSet` como referencia canónica:

- `get_serializer_class()`: serializer de lectura para list/retrieve, serializer de escritura para el resto
- `get_permissions()`: `IsAuthenticated + EsSoloLectura` para lecturas (sucursal + admin), `IsAuthenticated + EsAdminOSysadmin` para escrituras
- `create/update` overrides devuelven el read serializer completo (no el write)
- `SyncIncrementalMixin` agrega filtro `?desde=<ISO timestamp>` para sync incremental

**Nota URL-encoding:** al construir `?desde=` en el cliente, usar `encodeURIComponent()` — el `+` del offset UTC (`+00:00`) se interpreta como espacio en query params y rompe `parse_datetime`.

---

## Sincronización cloud → sucursal

No se usan eventos (`EventoSync`) para propagar cambios de datos maestros. El mecanismo es pull incremental:

1. Admin edita producto/categoría/cliente en el portal → `fecha_modificacion` se actualiza (`auto_now=True`)
2. La sucursal llama `GET /api/v1/maestros/<recurso>/?desde=<cursor>` periódicamente
3. Recibe solo los registros modificados desde el cursor y aplica `update_or_create`

---

## Runbooks operativos (leer ANTES de tocar una instalación o el sync)

Procedimientos deterministas, escritos para ejecutarse paso a paso — por una
persona o por un agente. Cada paso trae su comando exacto y su salida esperada.

| Documento | Cuándo usarlo |
|---|---|
| `docs/runbooks/INSTALACION_CLIENTE_NUEVO.md` | Instalar un cliente desde cero. Incluye tabla de fallos conocidos. |
| `docs/runbooks/PRUEBAS_SYNC_LOCAL.md` | Probar cambios del contrato de sync **sin desplegar**, con código nuevo en ambos lados. |
| `docs/runbooks/SYNC_EMULACION_SUCURSAL_PROD.md` | Emular una sucursal contra el cloud de producción (tenant `demo`/`royalplastdemo`). |
| `docs/BUGS.md` | Bugs vivos con su causa raíz y firma para reconocerlos. Consultarlo ante cualquier síntoma raro. |

**Diagnósticos, antes de suponer nada:**

```bash
python manage.py verificar_instalacion   # config, BD, seeds, módulos activos
python manage.py verificar_sync          # outbox, huecos, cursores de pull
```

**Configuración de una instalación:** vive en `deploy/env_cliente.env` y la lee
la aplicación (`config/settings.py`). Cambiar un valor = editar el archivo +
reiniciar el servicio. **Nunca** hace falta re-registrar el servicio; si alguien
lo indica, está siguiendo el procedimiento anterior a la Fase 4.

---

## Datos de acceso de desarrollo

- Portal admin: `Santiago / Prueba123`
- DB local: `pos_fifo_dev` · user `pos_user` · password `Prueba123` · host `localhost:5432`
- Azure DB (cloud): ver `config/settings_azure_pg.py` (no commitear credenciales)
