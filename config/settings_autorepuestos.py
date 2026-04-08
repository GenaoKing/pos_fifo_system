from .settings import *

DEBUG = True

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'pos_autorepuestos_dev',
        'USER': 'pos_user',
        'PASSWORD': 'Prueba123',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}