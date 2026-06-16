"""
Production-oriented settings for the Azure cloud API.

This module is the deploy contract for Azure Container Apps. The older
settings_azure_pg module stays as a local/dev helper for connecting to Azure
PostgreSQL while exploring.
"""

import os
from datetime import timedelta

from django.core.exceptions import ImproperlyConfigured

from config.settings import *  # noqa: F401,F403


def _env_required(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise ImproperlyConfigured(f'{name} must be configured for settings_cloud.')
    return value


def _csv_env(name, default=''):
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(',') if item.strip()]


def _bool_env(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


def _int_env(name, default):
    raw = os.environ.get(name, '')
    return int(raw) if raw.strip() else default


# Identity / version.
CLOUD_ENVIRONMENT = os.environ.get('CLOUD_ENVIRONMENT', 'dev')
GIT_COMMIT_SHA = os.environ.get('GIT_COMMIT_SHA', 'unknown')
APP_VERSION = os.environ.get('APP_VERSION') or GIT_COMMIT_SHA or 'unknown'


# Security.
DEBUG = False
SECRET_KEY = _env_required('DJANGO_SECRET_KEY')

ALLOWED_HOSTS = _csv_env('ALLOWED_HOSTS')
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured('ALLOWED_HOSTS must contain at least one host.')
if '*' in ALLOWED_HOSTS:
    raise ImproperlyConfigured('ALLOWED_HOSTS cannot include "*" in settings_cloud.')

CSRF_TRUSTED_ORIGINS = _csv_env('CSRF_TRUSTED_ORIGINS')

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = _bool_env('SECURE_SSL_REDIRECT', True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = _int_env('SECURE_HSTS_SECONDS', 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = _bool_env('SECURE_HSTS_INCLUDE_SUBDOMAINS', False)
SECURE_HSTS_PRELOAD = _bool_env('SECURE_HSTS_PRELOAD', False)


# Static files.
whitenoise_middleware = 'whitenoise.middleware.WhiteNoiseMiddleware'
if whitenoise_middleware not in MIDDLEWARE:
    security_middleware = 'django.middleware.security.SecurityMiddleware'
    if security_middleware in MIDDLEWARE:
        index = MIDDLEWARE.index(security_middleware) + 1
        MIDDLEWARE = MIDDLEWARE[:index] + [whitenoise_middleware] + MIDDLEWARE[index:]
    else:
        MIDDLEWARE = [whitenoise_middleware] + MIDDLEWARE

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}

if _bool_env('AZURE_BLOB_MEDIA_ENABLED', False):
    storage_account_name = _env_required('AZURE_STORAGE_ACCOUNT_NAME')
    storage_container_name = os.environ.get('AZURE_STORAGE_MEDIA_CONTAINER', 'media-public').strip() or 'media-public'

    try:
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        raise ImproperlyConfigured(
            'azure-identity must be installed when AZURE_BLOB_MEDIA_ENABLED=true.'
        ) from exc

    if 'storages' not in INSTALLED_APPS:
        INSTALLED_APPS = INSTALLED_APPS + ['storages']

    azure_client_id = os.environ.get('AZURE_CLIENT_ID', '').strip()
    token_credential = (
        DefaultAzureCredential(managed_identity_client_id=azure_client_id)
        if azure_client_id
        else DefaultAzureCredential()
    )

    STORAGES['default'] = {
        'BACKEND': 'storages.backends.azure_storage.AzureStorage',
        'OPTIONS': {
            'account_name': storage_account_name,
            'azure_container': storage_container_name,
            'token_credential': token_credential,
            'overwrite_files': False,
        },
    }

    AZURE_MEDIA_CUSTOM_DOMAIN = os.environ.get('AZURE_MEDIA_CUSTOM_DOMAIN', '').strip()
    if AZURE_MEDIA_CUSTOM_DOMAIN:
        MEDIA_URL = f'https://{AZURE_MEDIA_CUSTOM_DOMAIN.rstrip("/")}/'
    else:
        MEDIA_URL = f'https://{storage_account_name}.blob.core.windows.net/{storage_container_name}/'


# Database.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME') or os.environ.get('AZURE_PG_DB_NAME', ''),
        'USER': os.environ.get('DB_USER') or os.environ.get('AZURE_PG_DB_USER', ''),
        'PASSWORD': os.environ.get('DB_PASSWORD') or os.environ.get('AZURE_PG_DB_PASSWORD', ''),
        'HOST': os.environ.get('DB_HOST') or os.environ.get('AZURE_PG_DB_HOST', ''),
        'PORT': os.environ.get('DB_PORT') or os.environ.get('AZURE_PG_DB_PORT', '5432'),
        'OPTIONS': {
            'sslmode': os.environ.get('DB_SSLMODE', 'require'),
        },
        'CONN_MAX_AGE': _int_env('DB_CONN_MAX_AGE', 600),
        'CONN_HEALTH_CHECKS': True,
    }
}

for key in ('NAME', 'USER', 'PASSWORD', 'HOST'):
    if not DATABASES['default'][key]:
        raise ImproperlyConfigured(f'Database {key} must be configured for settings_cloud.')


# Portal API: CORS + JWT.
if 'corsheaders' not in INSTALLED_APPS:
    INSTALLED_APPS = INSTALLED_APPS + ['corsheaders']

cors_middleware = 'corsheaders.middleware.CorsMiddleware'
if cors_middleware not in MIDDLEWARE:
    MIDDLEWARE = [cors_middleware] + MIDDLEWARE

CORS_ALLOWED_ORIGINS = _csv_env('CORS_ALLOWED_ORIGINS')
CORS_ALLOW_CREDENTIALS = _bool_env('CORS_ALLOW_CREDENTIALS', True)

REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'apps.tenancy.authentication.TenantJWTAuthentication',
        *REST_FRAMEWORK.get('DEFAULT_AUTHENTICATION_CLASSES', []),
    ],
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=_int_env('JWT_ACCESS_MINUTES', 30)),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=_int_env('JWT_REFRESH_DAYS', 7)),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': False,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}


# Logging: Azure captures stdout/stderr.
LOG_LEVEL = os.environ.get('DJANGO_LOG_LEVEL', 'INFO')

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'plain': {
            'format': '{asctime} {levelname:<8} [{name}] {message}',
            'style': '{',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'plain',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': LOG_LEVEL,
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'ecf': {
            'handlers': ['console'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'ventas.service': {
            'handlers': ['console'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'sync': {
            'handlers': ['console'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
    },
}
