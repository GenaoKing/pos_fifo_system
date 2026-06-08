"""
config/test_runner.py

Test runner que aisla el cache entre tests.

Motivo
------
`apps.configuracion.utils.get_config()` y
`apps.sucursales.models.get_sucursal_actual()` cachean INSTANCIAS de modelo
(ConfiguracionNegocio / Sucursal) en LocMemCache con `timeout=None`. Django
NO limpia el cache entre tests, asi que un objeto cacheado en un test
sobrevive al rollback de su transaccion y se filtra al siguiente test.

Sintoma concreto: un test crea la Sucursal `SD-001` (settings.SUCURSAL_CODIGO),
`get_config()` cachea esa instancia y su ConfiguracionNegocio; tras el rollback
las filas desaparecen pero los objetos Python siguen en cache, y un test
posterior termina escribiendo un ConfiguracionNegocio con FK a una sucursal
inexistente -> ForeignKeyViolation en el teardown (`check_constraints`).

Solucion
--------
Limpiar todos los caches configurados ANTES de cada test, de modo que cada
test arranque en frio sin tener que repetir `cache.clear()` en cada `setUp`.
`startTest` se invoca justo antes del `setUp` de cada TestCase, asi que es el
punto correcto para garantizar el aislamiento.
"""
from django.conf import settings
from django.core.cache import caches
from django.test.runner import DiscoverRunner


def _clear_all_caches():
    for alias in settings.CACHES:
        try:
            caches[alias].clear()
        except Exception:
            # Un backend de cache mal configurado no debe tumbar la suite.
            pass


class CacheIsolatedTestRunner(DiscoverRunner):
    """DiscoverRunner que limpia el cache antes de cada test."""

    def get_resultclass(self):
        base = super().get_resultclass()
        if base is None:
            from unittest import TextTestResult as base

        class _CacheClearingResult(base):
            def startTest(self, test):
                _clear_all_caches()
                super().startTest(test)

        return _CacheClearingResult
