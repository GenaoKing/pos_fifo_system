"""
apps/facturacion_electronica/tests/conftest.py

Fixtures compartidas para todos los tests de la app facturacion_electronica.

Fixtures definidas:
- `_limpiar_cache_config` (autouse): antes y después de cada test limpia
  el cache de Django. Necesario porque `get_config()` cachea la instancia
  de ConfiguracionNegocio en LocMemCache, y entre tests la BD se rollbackea
  pero el cache en memoria del proceso pytest persiste.

- `config_negocio` (db): crea una instancia de ConfiguracionNegocio con
  valores razonables para emitir e-CF. Limpia el cache para que
  `get_config()` la levante. Los tests pueden mutar campos y guardar; al
  hacerlo deben volver a llamar `cache.clear()`.

Convención: cualquier test que llame indirectamente a `get_config()`
(directamente o vía `venta_a_ecf_data`) debe recibir el fixture
`config_negocio` para garantizar que existe.
"""
from decimal import Decimal

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _limpiar_cache_config():
    """
    Limpia el cache de Django antes y después de cada test.

    Sin esto, una ConfiguracionNegocio creada en el test A queda cacheada
    en el proceso pytest y el test B la recibe aunque pytest-django haya
    rollbackeado la transacción. El resultado es que el test B ve datos
    que no existen en BD, lo cual es muy confuso de debuggear.
    """
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def config_negocio(db):
    """
    Crea una ConfiguracionNegocio con valores default razonables para
    tests del mapper e-CF:
        - modulo_ecf=True
        - itbis_incluido_en_precio=True (modo más común en POS)
        - itbis_porcentaje_global=18.00

    Tests que necesiten variar estos valores pueden mutar el objeto
    devuelto y llamar `.save()` + `cache.clear()`.

    Args (implícito):
        db: fixture de pytest-django que habilita acceso a BD y hace
            rollback al terminar el test.
    """
    from apps.configuracion.models import ConfiguracionNegocio

    config = ConfiguracionNegocio.objects.create(
        nombre_negocio='Negocio Test',
        rnc='130000000',
        modulo_ecf=True,
        ecf_proveedor='mseller',
        itbis_incluido_en_precio=True,
        itbis_porcentaje_global=Decimal('18.00'),
    )
    cache.clear()
    return config