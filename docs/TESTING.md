# Convenciones de testing — pos_fifo_system

## Estructura de archivos

Todas las apps usan un package `tests/` en lugar de un `tests.py` suelto.
El patrón ya establecido en `facturacion_electronica` se aplica a todo el proyecto.

```
apps/<app>/
    tests/
        __init__.py        ← vacío (marca el package)
        test_<concern>.py  ← un archivo por área de prueba
```

### Apps con tests reales

| App | Archivo | Qué prueba |
|-----|---------|------------|
| `api` | `tests/test_producto_viewset.py` | CRUD productos, permisos JWT vs token sucursal |
| `api` | `tests/test_categoria_viewset.py` | CRUD categorías, sync incremental (B11) |
| `api` | `tests/test_cliente_viewset.py` | CRUD clientes, búsqueda RNC/nombre, sync incremental (B11) |
| `sync` | `tests/test_engine.py` | Pull incremental de productos desde cloud |
| `ventas` | `tests/test_producto_precio_cache.py` | Precio actualizado post-pull sin reiniciar POS |
| `reportes` | `tests/test_dashboard.py` | Métricas de hoy con timezone Santo Domingo |
| `facturacion_electronica` | `tests/test_venta_to_ecf.py` | Mapeo Venta → e-CF |
| `facturacion_electronica` | `tests/test_mseller_emisor.py` | Emisor mSeller |
| `facturacion_electronica` | `tests/test_mseller_payload.py` | Payload mSeller |

### Apps con package vacío (sin tests aún)

`usuarios`, `productos`, `clientes`, `inventario`, `ventas` (base), `auditoria`,
`cotizaciones`, `caja`, `configuracion`, `sucursales`

El `__init__.py` está listo — agregar archivos `test_*.py` cuando se necesiten.

---

## Cómo correr los tests

**Settings a usar:** `config.settings_development` (PostgreSQL local, sin SSL).
No usar `settings_azure_pg` para tests — requiere SSL y apunta a la BD de Azure.

```bash
# Activar el entorno primero
# En Windows: usar el intérprete directamente
C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py test \
    <modulo> --settings=config.settings_development

# Un archivo
python manage.py test apps.api.tests.test_categoria_viewset --settings=config.settings_development

# Múltiples archivos
python manage.py test \
    apps.api.tests.test_producto_viewset \
    apps.api.tests.test_categoria_viewset \
    apps.api.tests.test_cliente_viewset \
    --settings=config.settings_development

# Todo el proyecto (discovery automático desde raíz)
python manage.py test --settings=config.settings_development
```

> **Nota:** `manage.py test apps.api` (apuntando a un package con `__init__.py` vacío)
> falla con `TypeError: expected str, bytes or os.PathLike`. Usar siempre el módulo
> completo (`apps.api.tests.test_X`) o sin argumentos para descubrir todo.

---

## Scripts manuales (no son TestCase)

Los scripts de prueba exploratoria legacy están en `scripts/` y **no se corren con `manage.py test`**:

```
scripts/
    manual_test_fifo_inventario.py   ← prueba el sistema FIFO de lotes
    manual_test_venta_fifo.py        ← prueba una venta completa con FIFO
    manual_test_auditoria.py         ← prueba el sistema de auditoría
```

Para correrlos:
```bash
python scripts/manual_test_fifo_inventario.py
```

---

## Convenciones al escribir tests

- Un `TestCase` por archivo, enfocado en un área (viewset, feature, etc.)
- El `setUp` crea los fixtures mínimos necesarios; no compartir estado entre tests
- Helpers de conveniencia como `self.api(user=..., token=...)` para reducir boilerplate
- Timestamps en `?desde=` deben ir URL-encoded: `from urllib.parse import quote; quote(ts.isoformat())`
- Los `WARNING:django.request:` en output de tests son esperados para casos 4xx/5xx — no indican fallo

---

## Tests de sync incremental (patrón B11)

Los tests de sync siguen este patrón para verificar el pull incremental:

```python
from urllib.parse import quote
from django.utils import timezone
import datetime

# Cursor en el pasado → debe retornar registros modificados después
pasado = timezone.now() - datetime.timedelta(days=1)
response = client.get(f'/api/v1/maestros/categorias/?desde={quote(pasado.isoformat())}')
assert response.data['count'] == 1

# Cursor en el futuro → no debe retornar nada
futuro = timezone.now() + datetime.timedelta(hours=1)
response = client.get(f'/api/v1/maestros/categorias/?desde={quote(futuro.isoformat())}')
assert response.data['count'] == 0
```
