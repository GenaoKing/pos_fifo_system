"""
config/settings_production.py

Settings de PRODUCCION LOCAL (instalacion en sucursal: Royal Plast y similares).
La app se sirve con Waitress (server.py) dentro de la LAN, sobre HTTP.

Hereda TODO de config.settings (apps, base de datos y el bloque SYNC_*, todos
parametrizados por variables de entorno) y aplica los ajustes de produccion local:
  - DEBUG apagado.
  - WhiteNoise para servir archivos estaticos.
  - ALLOWED_HOSTS derivado de SERVER_IP / EXTRA_HOSTS (deploy/env_cliente.bat).
  - Logging a archivo en logs/ (incluye sync.log para el daemon de sincronizacion).

NOTA: este modulo NO es para el cloud (Azure). El contrato cloud vive en
config.settings_cloud. Las credenciales (DB, SECRET_KEY, CLOUD_API_TOKEN) vienen de
variables de entorno; nunca se hardcodean aqui.
"""
import os

from .settings import *  # noqa: F401,F403


def _csv_env(name, default=''):
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(',') if item.strip()]


# ---------------------------------------------------------------------------
# Seguridad
# ---------------------------------------------------------------------------
DEBUG = os.environ.get('DJANGO_DEBUG', 'false').lower() == 'true'

# ALLOWED_HOSTS se arma desde la red de la sucursal:
#   - DJANGO_ALLOWED_HOSTS (csv) tiene prioridad si esta definido.
#   - Si no, se usa loopback + SERVER_IP + EXTRA_HOSTS (de env_cliente.bat).
#   - Fallback '*' para no dejar la instalacion inaccesible si nada esta
#     configurado: el acceso es LAN interna, no esta expuesto a internet.
_hosts = _csv_env('DJANGO_ALLOWED_HOSTS')
if not _hosts:
    _hosts = ['localhost', '127.0.0.1']
    server_ip = os.environ.get('SERVER_IP', '').strip()
    if server_ip and server_ip not in ('0.0.0.0',):
        _hosts.append(server_ip)
    _hosts += _csv_env('EXTRA_HOSTS')
ALLOWED_HOSTS = _hosts or ['*']

# Se sirve en HTTP dentro de la LAN: no forzar HTTPS ni cookies Secure
# (de lo contrario el navegador no enviaria la cookie de sesion y nadie entra).
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# ---------------------------------------------------------------------------
# Archivos estaticos (WhiteNoise) — mismo patron que config.settings_cloud
# ---------------------------------------------------------------------------
_whitenoise = 'whitenoise.middleware.WhiteNoiseMiddleware'
if _whitenoise not in MIDDLEWARE:
    _security = 'django.middleware.security.SecurityMiddleware'
    if _security in MIDDLEWARE:
        _i = MIDDLEWARE.index(_security) + 1
        MIDDLEWARE = MIDDLEWARE[:_i] + [_whitenoise] + MIDDLEWARE[_i:]
    else:
        MIDDLEWARE = [_whitenoise] + MIDDLEWARE

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}

# ---------------------------------------------------------------------------
# Logging a archivo (produccion)
# El base (config.settings) ya define console + ecf_file y los loggers ecf*/
# ventas.service. Aqui agregamos el handler y logger 'sync' que usa el daemon
# de sincronizacion (apps/sync), para que persista en logs/sync.log.
# ---------------------------------------------------------------------------
LOGGING['handlers']['sync_file'] = {
    'class': 'logging.handlers.RotatingFileHandler',
    'filename': os.path.join(LOGS_DIR, 'sync.log'),
    'maxBytes': 10 * 1024 * 1024,  # 10 MB
    'backupCount': 5,
    'formatter': 'verbose',
    'level': 'INFO',
    'encoding': 'utf-8',
}
LOGGING['loggers']['sync'] = {
    'handlers': ['console', 'sync_file'],
    'level': 'INFO',
    'propagate': False,
}
