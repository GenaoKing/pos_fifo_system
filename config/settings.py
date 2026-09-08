"""
Django settings for POS FIFO System.
"""

from pathlib import Path
import os

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# ============================================================================
# Configuracion de la instalacion: archivo .env
# ============================================================================
#
# La configuracion de cada sucursal vive en `deploy/env_cliente.env` y la lee
# la APLICACION, no el gestor de servicios.
#
# Antes las ~46 variables se enumeraban a mano en los .bat y se le pasaban a
# `nssm` como AppEnvironmentExtra. `nssm` guarda un SNAPSHOT ESTATICO al
# registrar: no sabe releer un archivo al arrancar. Consecuencias que costaron
# caro:
#   - Cambiar cualquier valor obligaba a RE-REGISTRAR el servicio.
#   - Cada valor cruzaba el interprete de `cmd` dos veces, y ahi se perdian los
#     caracteres especiales: el DJANGO_SECRET_KEY de un cliente quedo truncado a
#     5 caracteres por un `&` sin comillas (bug #9 de docs/BUGS.md).
#
# Cargarlo aqui cubre de una sola vez server.py, manage.py, el daemon de sync y
# cualquier comando: todos ven exactamente la misma configuracion.
#
# `override=False` es deliberado: **las variables de entorno reales le ganan al
# archivo**. Eso mantiene funcionando el rig de pruebas, los tests y Azure, donde
# la configuracion llega por entorno y no por archivo.

def _cargar_env_file():
    """Carga el .env de la instalacion. Devuelve la ruta usada, o None."""
    ruta = os.environ.get('POS_ENV_FILE') or (BASE_DIR / 'deploy' / 'env_cliente.env')
    ruta = Path(ruta)
    if not ruta.is_file():
        return None
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - entorno sin la dependencia
        return None
    load_dotenv(ruta, override=False, encoding='utf-8')
    return ruta


POS_ENV_FILE_CARGADO = _cargar_env_file()


def _env_text(name, default=''):
    value = os.environ.get(name, default)
    return value.strip().strip('"').strip("'")


def _env_bool(name, default=False):
    value = _env_text(name, '')
    if value == '':
        return default
    return value.lower() in ('1', 'true', 'yes', 'on')


def _env_int(name, default):
    value = _env_text(name, '')
    if value == '':
        return default
    try:
        return int(value)
    except ValueError:
        return default


# SECURITY WARNING: keep the secret key used in production secret!
# En instalaciones reales definir DJANGO_SECRET_KEY en el entorno.
SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-tu-clave-secreta-aqui-cambiarla-en-produccion',
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DJANGO_DEBUG', 'true').lower() == 'true'

# Lista separada por comas, ej: DJANGO_ALLOWED_HOSTS=pos.local,192.168.1.10
ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get('DJANGO_ALLOWED_HOSTS', '*').split(',')
    if h.strip()
]


# Application definition

INSTALLED_APPS = [
    # AdminSite propio: bajo tenancy exige identidad global del control
    # plane, para que /admin/ no sea una puerta paralela al portal (USR-002).
    'apps.usuarios.admin_site.PosAdminConfig',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_extensions',
    
    # Apps del proyecto
    'apps.tenancy',
    'apps.negocios',
    'apps.usuarios',
    'apps.permisos',
    'apps.suscripciones',
    'apps.productos',
    'apps.ventas',
    'apps.inventario',
    'apps.auditoria',
    'apps.reportes',
    'apps.clientes',
    'apps.cuentas_por_cobrar',
    'apps.cotizaciones',
    'apps.configuracion',
    'apps.caja',
    'apps.sucursales',
    'rest_framework',
    'rest_framework.authtoken',
    # Blacklist de refresh tokens (TEN-002). Sin ella, `ROTATE_REFRESH_TOKENS`
    # daba apariencia de reemplazo pero el refresh anterior seguia siendo
    # valido: una copia robada se podia canjear indefinidamente, y no habia
    # forma de cerrar sesion del lado del servidor.
    'rest_framework_simplejwt.token_blacklist',
    'apps.api',
    'apps.sync',
    'apps.notificaciones',
    'apps.facturacion_electronica',
    
]

# Web Push (solo cloud). La privada llega de Key Vault; la publica se expone
# autenticada al navegador para crear la suscripcion Push API.
WEB_PUSH_ENABLED = _env_bool('WEB_PUSH_ENABLED', False)
WEB_PUSH_VAPID_PUBLIC_KEY = _env_text('WEB_PUSH_VAPID_PUBLIC_KEY')
WEB_PUSH_VAPID_PRIVATE_KEY = _env_text('WEB_PUSH_VAPID_PRIVATE_KEY')
WEB_PUSH_VAPID_SUBJECT = _env_text(
    'WEB_PUSH_VAPID_SUBJECT', 'mailto:admin@example.com',
)
WEB_PUSH_TIMEOUT_SECONDS = _env_int('WEB_PUSH_TIMEOUT_SECONDS', 10)
WEB_PUSH_TTL_SECONDS = _env_int('WEB_PUSH_TTL_SECONDS', 14400)

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    # Acota el memo de permisos a un request. Va DESPUES de Authentication
    # (necesita request.user resuelto) y ANTES de cualquier cosa que consulte
    # permisos.
    'apps.permisos.middleware.PermisosRequestCacheMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'apps.tenancy.middleware.ClearTenantContextMiddleware',
    # Middleware de auditoría (agregar después)
    # 'apps.auditoria.middleware.AuditoriaMiddleware',
    'apps.auditoria.middleware.AuditoriaMiddleware',
    'apps.auditoria.middleware.SesionAuditoriaMiddleware',
    'apps.sucursales.middleware.SucursalMiddleware',
    
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.configuracion.context_processors.config_negocio',
                'apps.sucursales.context_processors.sucursal_actual',
                'apps.sync.context_processors.estado_sync',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Test runner: aisla el cache entre tests (ver config/test_runner.py).
# Solo se usa al correr `manage.py test`; no afecta runtime de produccion.
TEST_RUNNER = 'config.test_runner.CacheIsolatedTestRunner'


# Database
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases

# Credenciales configurables por instalación vía variables de entorno.
# Los defaults mantienen el comportamiento del entorno de desarrollo local.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'pos_fifo_db'),
        'USER': os.environ.get('DB_USER', 'pos_user'),
        'PASSWORD': os.environ.get('DB_PASSWORD', 'Prueba123'),
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': os.environ.get('DB_PORT', '5432'),
    }
}

TENANCY_DB_PER_TENANT_ENABLED = (
    os.environ.get('TENANCY_DB_PER_TENANT_ENABLED', 'false').lower() == 'true'
)
TENANCY_ALLOW_UNSCOPED_OPERATIONS = (
    os.environ.get('TENANCY_ALLOW_UNSCOPED_OPERATIONS', 'false').lower() == 'true'
)
DATABASE_ROUTERS = ['apps.tenancy.router.TenantDatabaseRouter']


# Password validation
# https://docs.djangoproject.com/en/5.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.0/topics/i18n/

LANGUAGE_CODE = 'es-do'

TIME_ZONE = 'America/Santo_Domingo'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.0/howto/static-files/

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Media files (uploads)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'


# Default primary key field type
# https://docs.djangoproject.com/en/5.0/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Configuración personalizada del sistema

# Modelo de usuario personalizado
AUTH_USER_MODEL = 'usuarios.Usuario'

# ============================================================================
# AUTENTICACION Y SESIONES
# ============================================================================
# config/settings.py
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/reportes/'
LOGOUT_REDIRECT_URL = '/login/'

SESSION_COOKIE_AGE = 43200              # 12 horas (jornada larga)
SESSION_SAVE_EVERY_REQUEST = True       # Renueva con cada request activo
SESSION_EXPIRE_AT_BROWSER_CLOSE = True  # Cierra sesion al cerrar navegador

# =============================================================================
# LOGGING — config básico, principalmente para módulos e-CF y ventas
# =============================================================================
# En desarrollo todo va a consola. En producción (settings_production.py)
# se sobrescribe con rotación a archivo en logs/ecf.log.
#
# Loggers configurados:
#   ecf              — app facturacion_electronica (general)
#   ecf.mseller      — HTTP client + emisor MSeller
#   ecf.procesador   — management command y procesador de cola
#   ecf.cola         — encolar_emision / encolar_nota_credito
#   ecf.views        — endpoint AJAX de estado
#   ecf.mapper       — mapper venta_to_ecf (warnings de tasas raras)
#   ventas.service   — services de procesar_venta y anular_venta
#
# Ajustar level a 'DEBUG' temporalmente cuando estés debuggeando flujos.

import os

LOGS_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOGS_DIR, exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{asctime} {levelname:<8} [{name}] {message}',
            'style': '{',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
        'simple': {
            'format': '{levelname:<8} [{name}] {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
            'level': 'DEBUG',
        },
        'ecf_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': os.path.join(LOGS_DIR, 'ecf.log'),
            'maxBytes': 10 * 1024 * 1024,  # 10 MB
            'backupCount': 5,
            'formatter': 'verbose',
            'level': 'DEBUG',
            'encoding': 'utf-8',
        },
    },
    'loggers': {
        'ecf': {
            'handlers': ['console', 'ecf_file'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'ecf.mseller': {
            'handlers': ['console', 'ecf_file'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'ecf.procesador': {
            'handlers': ['console', 'ecf_file'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'ecf.cola': {
            'handlers': ['console', 'ecf_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'ecf.views': {
            'handlers': ['console', 'ecf_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'ecf.mapper': {
            'handlers': ['console', 'ecf_file'],
            'level': 'WARNING',
            'propagate': False,
        },
        'ventas.service': {
            'handlers': ['console', 'ecf_file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}





# Roles del sistema
ROLES_SISTEMA = {
    'ADMIN': 'Administrador',
    'CAJERA': 'Cajera',
}

# NOTA: la configuración de negocio (días de anulación, pie de ticket, logo,
# inventario negativo, etc.) NO vive aquí: vive en ConfiguracionNegocio (BD),
# configurable por sucursal desde el admin/panel de configuración.

# ============================================================================
# CONFIGURACIÓN DE IMPRESORAS (hardware, configurable por instalación vía env)
# ============================================================================

# python-escpos solo acepta el pin 2 o 5 para el cajon; cualquier otro valor
# lanza CashDrawerError. Validamos para no quedar nunca en un valor invalido.
_THERMAL_CASH_DRAWER_PIN = _env_int('THERMAL_CASH_DRAWER_PIN', 2)
if _THERMAL_CASH_DRAWER_PIN not in (2, 5):
    _THERMAL_CASH_DRAWER_PIN = 2

THERMAL_PRINTER = {
    # Habilitación del sistema de impresión
    'ENABLED': _env_bool('THERMAL_PRINTER_ENABLED', True),

    # Configuración de conexión USB
    'INTERFACE': 'usb',
    # Nombre en "Dispositivos e impresoras" de Windows
    'PRINTER_NAME': _env_text('THERMAL_PRINTER_NAME', '2connect pos'),

    # Vendor ID y Product ID (detectados automáticamente por python-escpos)
    # Si hay problemas, ejecutar: python -m escpos.cli ls
    'USB_VENDOR_ID': os.environ.get('THERMAL_USB_VENDOR_ID') or None,
    'USB_PRODUCT_ID': os.environ.get('THERMAL_USB_PRODUCT_ID') or None,

    # Configuraciones de impresión
    'AUTO_CUT': True,        # Cortador automático
    'CHARSET': _env_text('THERMAL_CHARSET', 'CP850'),    # Caracteres latinos/españoles
    'CODE_PAGE': _env_text('THERMAL_CHARSET', 'CP850'),  # Página de códigos

    # Cajón de dinero
    'CASH_DRAWER': _env_bool('THERMAL_CASH_DRAWER', True),
    'CASH_DRAWER_PIN': _THERMAL_CASH_DRAWER_PIN,  # python-escpos solo acepta 2 o 5

    # Dimensiones del papel
    'PAPER_WIDTH': _env_int('THERMAL_PAPER_WIDTH', 48),  # 80mm = 48 chars
    'HIGH_QUALITY': True,
    # Ancho del logo del ticket en píxeles (la imagen viene de ConfiguracionNegocio.logo)
    'LOGO_WIDTH': _env_int('THERMAL_LOGO_WIDTH', 200),
    'LOGO_HEIGHT': None,     # Auto-proporcional
}

# Impresora de etiquetas Zebra (nombre en "Dispositivos e impresoras" de Windows)
ZEBRA_PRINTER_NAME = _env_text('ZEBRA_PRINTER_NAME', 'ZDesigner LP 2824')


REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'apps.tenancy.authentication.TenantJWTAuthentication',
        'apps.api.authentication.SucursalTokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'apps.api.pagination.StandardPagination',
    'PAGE_SIZE': 50,
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.ScopedRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'sync': '120/min',       # Sucursales sincronizando
        'maestros': '60/min',    # Pull de datos maestros
        'reportes': '30/min',    # Dashboard consultando
        # Login del portal. No existia ningun scope de auth: quince passwords
        # incorrectos seguidos devolvian quince 400 y ningun 429, y cada intento
        # ejecuta un hash de password costoso.
        #
        # Dos ventanas: una corta que corta la rafaga y una sostenida que corta
        # el goteo lento. El scope se aplica por IP Y por email (ver
        # apps/api/throttling.py), asi que un atacante detras de una IP no
        # bloquea a un usuario legitimo desde otra.
        'login': _env_text('THROTTLE_LOGIN', '10/min'),
        'login_sostenido': _env_text('THROTTLE_LOGIN_SOSTENIDO', '50/hour'),
    },
    'DATETIME_FORMAT': '%Y-%m-%dT%H:%M:%S.%f%z',
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
}

# Código de sucursal de esta instalación, usado para cargar la configuración específica.
SUCURSAL_CODIGO = os.environ.get('SUCURSAL_CODIGO', 'SD-001')
# ============================================================================
# INFORMACIÓN DEL NEGOCIO (PARA TICKETS)
# ============================================================================



# =========================================================================
# SYNC ENGINE (Fase 4)
# =========================================================================
# Configuracion de sincronizacion con el cloud.
# Si SYNC_ENABLED=False, la app sync no hace nada (modo standalone).
# Los valores reales (CLOUD_API_URL, CLOUD_API_TOKEN) deben venir de env vars
# en el .env de cada sucursal, para no hardcodear secrets en settings.py.
SYNC_ENABLED = os.environ.get('SYNC_ENABLED', 'false').lower() == 'true'
CLOUD_API_URL = os.environ.get('CLOUD_API_URL', '')
CLOUD_API_TOKEN = os.environ.get('CLOUD_API_TOKEN', '')
 
# Parametros del engine (todos con defaults razonables)
SYNC_INTERVAL = int(os.environ.get('SYNC_INTERVAL', '60'))
SYNC_BATCH_SIZE = int(os.environ.get('SYNC_BATCH_SIZE', '50'))
SYNC_MAX_RETRIES = int(os.environ.get('SYNC_MAX_RETRIES', '10'))
SYNC_HTTP_TIMEOUT = int(os.environ.get('SYNC_HTTP_TIMEOUT', '10'))

# Conciliacion diaria (Fase 3, anti-entropia). El daemon la corre como mucho
# una vez por dia, a partir de esta hora local: la PC de una sucursal suele
# apagarse de noche, asi que "6 AM" en la practica significa "en el primer
# ciclo del dia", no a una hora exacta.
SYNC_CONCILIACION_ENABLED = _env_bool('SYNC_CONCILIACION_ENABLED', True)
SYNC_CONCILIACION_HORA = _env_int('SYNC_CONCILIACION_HORA', 6)
SYNC_CONCILIACION_DIAS = _env_int('SYNC_CONCILIACION_DIAS', 30)
