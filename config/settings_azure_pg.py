"""
Settings para Azure Database for PostgreSQL — Flexible Server
=============================================================
Uso: python manage.py runserver --settings=config.settings_azure_pg
Requiere: variables de entorno configuradas (ver deploy/env_azure_pg.bat)

Tier gratuito: B1ms (1 vCPU, 2 GB RAM, 32 GB storage) — 12 meses gratis
Región recomendada: East US 2 (menor latencia desde RD según pruebas Azure)
"""

import os
from config.settings import *  # noqa: F401,F403

# ============================================================================
# IDENTIFICADOR DE ENTORNO
# ============================================================================
CLOUD_ENVIRONMENT = 'azure_pg'

# ============================================================================
# BASE DE DATOS — Azure PostgreSQL Flexible Server
# ============================================================================
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('AZURE_PG_DB_NAME', 'pos_fifo_db'),
        'USER': os.environ.get('AZURE_PG_DB_USER', ''),
        'PASSWORD': os.environ.get('AZURE_PG_DB_PASSWORD', ''),
        'HOST': os.environ.get('AZURE_PG_DB_HOST', ''),  # ej: myserver.postgres.database.azure.com
        'PORT': os.environ.get('AZURE_PG_DB_PORT', '5432'),
        'OPTIONS': {
            'sslmode': 'require',
        },
        # --- Connection pooling y health checks ---
        'CONN_MAX_AGE': 600,           # Reusar conexiones por 10 min (reduce overhead SSL)
        'CONN_HEALTH_CHECKS': True,    # Verificar conexión antes de reusar (Django 4.1+)
    }
}

# ============================================================================
# SEGURIDAD
# ============================================================================
# En exploración mantenemos DEBUG=True para ver errores detallados
DEBUG = True

# Permitir conexión desde cualquier host durante exploración
ALLOWED_HOSTS = ['*']

# ============================================================================
# CACHE — LocMemCache funciona para un solo worker (Waitress single-thread)
# ============================================================================
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'pos-azure-pg',
    }
}

# ============================================================================
# LOGGING — Agregar logging de queries para medir latencia
# ============================================================================
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'file_cloud': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': 'cloud_debug.log',
            'maxBytes': 5 * 1024 * 1024,  # 5 MB
            'backupCount': 3,
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django.db.backends': {
            'level': 'DEBUG',  # Loguear TODAS las queries SQL
            'handlers': ['file_cloud'],
            'propagate': False,
        },
        'django': {
            'level': 'INFO',
            'handlers': ['console'],
            'propagate': True,
        },
    },
}
