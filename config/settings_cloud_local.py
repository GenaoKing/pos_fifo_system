# config/settings_cloud_local.py
"""
Instancia CLOUD simulada, corriendo en la maquina de desarrollo.

Sirve para probar el ciclo completo sucursal <-> cloud con codigo nuevo en
AMBOS lados, sin depender de Azure. Es lo que permite validar cambios del
contrato de sync antes de desplegar: los entornos cloud reales van por CI
(develop -> dev, main -> prod), asi que hasta que un cambio no esta desplegado,
apuntar el rig a ellos prueba el codigo VIEJO del otro lado.

Topologia de la prueba:

    POS local (rig)                      Cloud local (esto)
    settings_demo_branch     --HTTP-->   settings_cloud_local
    BD pos_fifo_demo_branch              BD pos_fifo_cloud_local
                                         (clon de royal_eval: 273 productos,
                                          supera el page size de 200 y por eso
                                          ejercita paginacion de verdad)

Levantar con:
    python manage.py runserver 8001 --settings=config.settings_cloud_local

Artefacto de PRUEBA, no de produccion. El cloud real usa settings_cloud.py /
settings_azure_pg.py.
"""
from .settings_development import *  # noqa: F401,F403

DATABASES['default']['NAME'] = 'pos_fifo_cloud_local'  # noqa: F405

# El cloud no emite eventos de sync: los recibe.
SYNC_ENABLED = False
