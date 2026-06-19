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
| `api` | `tests/test_cuentas_por_cobrar_viewset.py` | Cartera read-only (B15): permisos, filtros, `esta_vencida` por fecha, `resumen/` |
| `cuentas_por_cobrar` | `tests/test_credito_services.py` | Crédito, abonos FIFO de cuotas, anulación |
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

> **Nota CI:** `facturacion_electronica` usa fixtures de pytest (`monkeypatch`,
> fixtures propias, `pytest.raises`), por eso no debe correr con `manage.py test`.
> GitHub Actions instala `requirements_ci.txt`, corre la suite Django por archivos
> excluyendo e-CF, y luego ejecuta e-CF con pytest.

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
python manage.py test <modulos_test_django> --settings=config.settings_development

# Suite e-CF (usa pytest fixtures/pytest-django)
python -m pytest apps/facturacion_electronica/tests --ds=config.settings_development -q
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

## Aislamiento de caché entre tests

`apps.configuracion.utils.get_config()` y `apps.sucursales.models.get_sucursal_actual()`
cachean **instancias de modelo** (ConfiguracionNegocio / Sucursal) en `LocMemCache`
con `timeout=None`. Django **no limpia el caché entre tests**, así que un objeto
cacheado en un test sobrevive al rollback de su transacción y se filtra al siguiente.

Síntoma típico (solo aparece corriendo la suite en conjunto, no aislada):

```text
django.db.utils.IntegrityError: insert or update on table
"configuracion_configuracionnegocio" violates foreign key constraint ...
Key (sucursal_id)=(1) is not present in table "sucursales_sucursal".
```

Un test crea la `Sucursal SD-001` (= `settings.SUCURSAL_CODIGO`), `get_config()`
cachea esa instancia + su config; tras el rollback las filas desaparecen pero los
objetos Python siguen en caché y un test posterior escribe un `ConfiguracionNegocio`
apuntando a una sucursal inexistente.

**Solución:** `config/test_runner.py::CacheIsolatedTestRunner` (registrado en
`settings.TEST_RUNNER`) limpia todos los cachés configurados **antes de cada test**.
No hace falta repetir `cache.clear()` en cada `setUp`. Solo se usa al correr
`manage.py test`; no afecta el runtime de producción.

> Si escribes un test que depende explícitamente del comportamiento de caché
> (p.ej. `test_producto_precio_cache.py`), gestiona el caché dentro del propio test
> (`cache.clear()` + poblarlo); el runner solo garantiza un punto de partida en frío.

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
