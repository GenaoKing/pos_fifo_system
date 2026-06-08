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

# ============================================================================
# FASE 5 — Portal administrativo cloud
# ============================================================================
# Estos bloques solo se activan en la instancia cloud collector.
# El POS local de cada sucursal no los necesita (corre con settings.py base).

from datetime import timedelta

# ----------------------------------------------------------------------------
# CORS — el portal React (Vite dev en :5173, prod en Azure Static Web Apps)
# vive en otro origin distinto al de la API Django.
# ----------------------------------------------------------------------------
INSTALLED_APPS = INSTALLED_APPS + ['corsheaders']

# CorsMiddleware debe ir lo más arriba posible, antes de CommonMiddleware.
MIDDLEWARE = ['corsheaders.middleware.CorsMiddleware'] + MIDDLEWARE

# Lista separada por coma en env var. Default cubre Vite dev.
_cors_raw = os.environ.get(
    'CORS_ALLOWED_ORIGINS',
    'http://localhost:5173,http://127.0.0.1:5173',
)
CORS_ALLOWED_ORIGINS = [o.strip() for o in _cors_raw.split(',') if o.strip()]

# Permitir cookies/credentials en CORS (por si el refresh token va en httpOnly cookie a futuro).
CORS_ALLOW_CREDENTIALS = True

# TENANCY: cuando entre django-tenants con subdominio por tenant
# (ej: royalplast.portal.tudominio.com) cambiar a regex:
#   CORS_ALLOWED_ORIGIN_REGEXES = [r'^https://[a-z0-9-]+\.portal\.tudominio\.com$']

# ----------------------------------------------------------------------------
# JWT — autenticación del portal administrativo
# ----------------------------------------------------------------------------
# Extiende DEFAULT_AUTHENTICATION_CLASSES preservando los que vienen del base:
#   - SucursalTokenAuthentication (para sync sucursal→cloud)
#   - SessionAuthentication       (para el admin Django y la web local)
# Añadimos JWTAuthentication al frente para que tenga prioridad en requests
# del portal.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        *REST_FRAMEWORK.get('DEFAULT_AUTHENTICATION_CLASSES', []),
    ],
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(
        minutes=int(os.environ.get('JWT_ACCESS_MINUTES', '30'))
    ),
    'REFRESH_TOKEN_LIFETIME': timedelta(
        days=int(os.environ.get('JWT_REFRESH_DAYS', '7'))
    ),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': False,  # requiere app token_blacklist; pendiente
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
    # TENANCY: cuando entre django-tenants, configurar
    # 'TOKEN_OBTAIN_SERIALIZER' apuntando a un serializer que inyecte el claim
    # 'tenant_id' resolviendo subdomain o header X-Tenant del request de login.
}
