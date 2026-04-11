"""
Django settings for POS FIFO System.
"""

from pathlib import Path
import os

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-tu-clave-secreta-aqui-cambiarla-en-produccion'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ['*']


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_extensions',
    
    # Apps del proyecto
    'apps.usuarios',
    'apps.productos',
    'apps.ventas',
    'apps.inventario',
    'apps.auditoria',
    'apps.reportes',
    'apps.clientes',
    'apps.cotizaciones',
    'apps.configuracion',
    'apps.caja',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Middleware de auditoría (agregar después)
    # 'apps.auditoria.middleware.AuditoriaMiddleware',
    'apps.auditoria.middleware.AuditoriaMiddleware',
    'apps.auditoria.middleware.SesionAuditoriaMiddleware',
    
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
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'pos_fifo_db',
        'USER': 'pos_user',
        'PASSWORD': 'Prueba123',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}


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

# Roles del sistema
ROLES_SISTEMA = {
    'ADMIN': 'Administrador',
    'CAJERA': 'Cajera',
}

# Configuración de impresión
IMPRESORA_TERMICA = {
    'nombre': 'Térmica 80mm',
    'ancho_papel': 80,  # mm
    'caracteres_por_linea': 42,
}

# Configuración de tickets
TICKET_CONFIG = {
    'reimpresion_max_dias': 30,
    'mostrar_logo': True,
    'pie_pagina': 'Gracias por su compra',
}

# Configuración de anulaciones
ANULACION_CONFIG = {
    'dias_permitidos': 15,
    'requiere_autorizacion': False,  # Cambiar a True si Admin debe autorizar
}

# Configuración de inventario
INVENTARIO_CONFIG = {
    'permitir_negativo': True,
    'alertar_stock_minimo': True,
}

# Formato de números de documentos
FORMATO_DOCUMENTOS = {
    'venta': 'V-{fecha}-{secuencia:05d}',  # V-20260201-00001
    'compra': 'C-{fecha}-{secuencia:05d}',
    'lote': 'LOTE-{fecha}-{secuencia:05d}',
}


"""
Configuración de Impresión para Sistema POS FIFO
Agregar esta sección al archivo config/settings.py
"""

# ============================================================================
# CONFIGURACIÓN DE IMPRESORA TÉRMICA 2CONNECT
# ============================================================================

THERMAL_PRINTER = {
    # Habilitación del sistema de impresión
    'ENABLED': True,
    
    # Configuración de conexión USB
    'INTERFACE': 'usb',
    'PRINTER_NAME': '2connect pos',  # Nombre en "Dispositivos e impresoras" de Windows
    
    # Vendor ID y Product ID (detectados automáticamente por python-escpos)
    # Si hay problemas, ejecutar: python -m escpos.cli ls
    'USB_VENDOR_ID': None,  # Auto-detect
    'USB_PRODUCT_ID': None,  # Auto-detect
    
    # Configuraciones de impresión
    'AUTO_CUT': True,        # La 2Connect tiene cortador automático
    'CHARSET': 'CP850',      # Encoding para caracteres latinos/españoles
    'CODE_PAGE': 'CP850',    # Página de códigos
    
    # Cajón de dinero
    'CASH_DRAWER': True,     # Habilitar apertura automática
    'CASH_DRAWER_PIN': 0,    # Pin del cajón (0 = pin 2, 1 = pin 5)
    
    # Dimensiones del papel
    'PAPER_WIDTH': 48,       # Ancho en caracteres (80mm = 48 chars)
    'HIGH_QUALITY': True,     # Configuración de alta calidad (si la impresora lo soporta)
    # Logo de la empresa
    'LOGO_ENABLED': True,
    'LOGO_PATH': 'static/img/logo-royal.jpeg',  # Ruta al logo
    'LOGO_WIDTH': 200,       # Ancho en píxeles (mediano-pequeño)
    'LOGO_HEIGHT': None,     # Auto-proporcional
}

# ============================================================================
# INFORMACIÓN DEL NEGOCIO (PARA TICKETS)
# ============================================================================

BUSINESS_INFO = {
    'NAME': 'Royal Plast EIRL',
    'RNC': '1-32-33458-2',  # TODO: Actualizar cuando se confirme
    'PHONE': '829-986-6443',
    'ADDRESS': 'Aut. Joaquin Balaguer Km 4',  # TODO: Actualizar con dirección
    'CITY': 'Estancia del yaque, Santiago, R.D.',
    'WEBSITE': '',  # Opcional
    'EMAIL': '',    # Opcional
    
    # Mensaje en el footer del ticket
    'FOOTER_MESSAGE': 'Gracias por su compra',
    'FOOTER_LINE2': 'Visítenos nuevamente',
    
    # Redes sociales (opcional)
    'WHATSAPP': '829-986-6443',  # Número de WhatsApp
    'INSTAGRAM': '',  # Usuario de Instagram sin @
    'FACEBOOK': '',   # Página de Facebook
}

# ============================================================================
# CONFIGURACIÓN DE CÓDIGO QR
# ============================================================================

QR_CONFIG = {
    'ENABLED': True,
    'SIZE': 4,  # Tamaño del QR (1-10, más grande = más fácil de escanear)
    'ERROR_CORRECTION': 'M',  # L, M, Q, H (M = medio, recomendado)
    
    # URL base para trazabilidad (si tienes sistema web)
    # El código QR contendrá: BASE_URL + numero_venta
    # Ejemplo: https://sistema.royalplastic.com/ticket/V-00001
    'BASE_URL': '',  # Dejar vacío si no hay sistema web aún
}

# ============================================================================
# INSTRUCCIONES DE ACTUALIZACIÓN
# ============================================================================

"""
PARA ACTUALIZAR LA INFORMACIÓN DEL NEGOCIO:

1. Abrir: config/settings.py
2. Buscar la sección: BUSINESS_INFO
3. Modificar los campos marcados como [PENDIENTE - CONFIGURAR]:
   
   BUSINESS_INFO = {
       'NAME': 'Royal Plastic',
       'RNC': '123-4567890-1',  # ← Colocar RNC real aquí
       'PHONE': '829-986-6443',
       'ADDRESS': 'Calle Principal #123',  # ← Colocar dirección aquí
       'CITY': 'Santo Domingo, R.D.',
       ...
   }

4. Guardar el archivo
5. Reiniciar el servidor Django: python manage.py runserver

NO REQUIERE CAMBIOS EN EL CÓDIGO, solo actualizar estos valores.
"""

