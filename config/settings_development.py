# config/settings_development.py
"""
Configuración de desarrollo - BD experimental
"""

import os

from .settings import *

DEBUG = True

# BD de desarrollo. Sigue el patrón de config por instalación: usuario, clave,
# host y puerto se heredan de settings.py, que los lee del entorno / de
# deploy/env_cliente.env (DB_USER, DB_PASSWORD, DB_HOST, DB_PORT). Aquí solo
# cambia el nombre por defecto de la BD; definir DB_NAME en el entorno lo pisa.
if not os.environ.get('DB_NAME'):
    DATABASES['default']['NAME'] = 'pos_fifo_dev'

ALLOWED_HOSTS = ['*']  # Desarrollo acepta todo

# Útil para desarrollo
# LOGGING = {
#     'version': 1,
#     'disable_existing_loggers': False,
#     'handlers': {
#         'console': {
#             'class': 'logging.StreamHandler',
#         },
#     },
#     'root': {
#         'handlers': ['console'],
#         'level': 'INFO',
#     },
#     'loggers': {
#         'django.db.backends': {
#             'handlers': ['console'],
#             'level': 'DEBUG',  # Ver queries SQL
#             'propagate': False,
#         },
#     },
# }