"""
Settings para Azure SQL Database
=================================
Uso: python manage.py runserver --settings=config.settings_azure_sql
Requiere: variables de entorno configuradas (ver deploy/env_azure_sql.bat)
Requiere: pip install mssql-django pyodbc
Requiere: ODBC Driver 18 for SQL Server instalado en Windows

Tier gratuito: Azure SQL Database Free (100,000 vCore seconds/mes, 32 GB) — PERMANENTE
Región recomendada: East US 2

NOTA IMPORTANTE:
    mssql-django tiene limitaciones conocidas con Django ORM:
    - JSONField: funciona en SQL Server 2016+ pero con sintaxis diferente
    - DateTimeField con auto_now_add: puede requerir ajustes
    - Algunas operaciones de migración pueden fallar (RunPython con schema changes)
    - FIFO queries con annotate/aggregate: verificar compatibilidad
    Documentar TODO lo que falle en docs/FASE1_AZURE_SQL_COMPAT.md
"""

import os
from config.settings import *  # noqa: F401,F403

# ============================================================================
# IDENTIFICADOR DE ENTORNO
# ============================================================================
CLOUD_ENVIRONMENT = 'azure_sql'

# ============================================================================
# BASE DE DATOS — Azure SQL Database
# ============================================================================
DATABASES = {
    'default': {
        'ENGINE': 'mssql',
        'NAME': os.environ.get('AZURE_SQL_DB_NAME', 'pos_fifo_db'),
        'USER': os.environ.get('AZURE_SQL_DB_USER', ''),
        'PASSWORD': os.environ.get('AZURE_SQL_DB_PASSWORD', ''),
        'HOST': os.environ.get('AZURE_SQL_DB_HOST', ''),  # ej: myserver.database.windows.net
        'PORT': os.environ.get('AZURE_SQL_DB_PORT', '1433'),
        'OPTIONS': {
            'driver': 'ODBC Driver 18 for SQL Server',
            'extra_params': 'Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30',
        },
        'CONN_MAX_AGE': 600,
        'CONN_HEALTH_CHECKS': True,
    }
}

# ============================================================================
# SEGURIDAD
# ============================================================================
DEBUG = True
ALLOWED_HOSTS = ['*']

# ============================================================================
# CACHE
# ============================================================================
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'pos-azure-sql',
    }
}

# ============================================================================
# LOGGING — Queries SQL para diagnóstico de compatibilidad
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
            'filename': 'cloud_debug_sql.log',
            'maxBytes': 5 * 1024 * 1024,
            'backupCount': 3,
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django.db.backends': {
            'level': 'DEBUG',
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
