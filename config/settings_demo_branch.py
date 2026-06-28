# config/settings_demo_branch.py
"""
Clean-install simulado de una sucursal local para el demo de Royal Plast.

Hereda settings_development pero apunta a una BD local dedicada
(`pos_fifo_demo_branch`) para no tocar `pos_fifo_dev`. Las variables de sync
(SYNC_ENABLED, CLOUD_API_URL, CLOUD_API_TOKEN, SUCURSAL_CODIGO) se leen de env
(definidas en config/settings.py). Es un artefacto de prueba, no de producción.
"""
from .settings_development import *  # noqa: F401,F403

DATABASES['default']['NAME'] = 'pos_fifo_demo_branch'  # noqa: F405
