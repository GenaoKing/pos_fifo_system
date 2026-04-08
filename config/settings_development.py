# config/settings_development.py
"""
Configuración de desarrollo - BD experimental
"""

from .settings import *

DEBUG = True

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'pos_fifo_dev',  # ← BD de desarrollo
        'USER': 'pos_user',
        'PASSWORD': 'Prueba123',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}

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