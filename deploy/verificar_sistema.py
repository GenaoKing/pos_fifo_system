"""
Royal Plastic POS - Verificacion del Sistema
Ejecutar despues de la instalacion para confirmar que todo funciona.
Uso: python deploy/verificar_sistema.py
"""
import os
import sys
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings_production')
sys.path.insert(0, str(BASE_DIR))

PASS = '[OK]'
FAIL = '[ERROR]'
WARN = '[AVISO]'

resultados = {'ok': 0, 'errores': 0, 'avisos': 0}


def check(nombre, condicion, mensaje_error='', es_critico=True):
    if condicion:
        print(f'  {PASS} {nombre}')
        resultados['ok'] += 1
    elif es_critico:
        print(f'  {FAIL} {nombre} - {mensaje_error}')
        resultados['errores'] += 1
    else:
        print(f'  {WARN} {nombre} - {mensaje_error}')
        resultados['avisos'] += 1


def main():
    print()
    print('=' * 60)
    print('  Royal Plastic POS - Verificacion del Sistema')
    print('=' * 60)
    print()

    # ------------------------------------------------------------------
    # 1. Estructura de archivos
    # ------------------------------------------------------------------
    print('[1/7] Estructura de archivos...')
    check('manage.py', (BASE_DIR / 'manage.py').exists(), 'No encontrado')
    check('config/settings.py', (BASE_DIR / 'config' / 'settings.py').exists(), 'No encontrado')
    check('config/settings_production.py',
          (BASE_DIR / 'config' / 'settings_production.py').exists(),
          'Copie este archivo desde deploy_package')
    check('server.py', (BASE_DIR / 'server.py').exists(), 'No encontrado')
    check('Carpeta logs/', (BASE_DIR / 'logs').exists(), 'Ejecute instalar.bat', False)
    check('Carpeta backups/', (BASE_DIR / 'backups').exists(), 'Ejecute instalar.bat', False)
    check('Carpeta staticfiles/', (BASE_DIR / 'staticfiles').exists(),
          'Ejecute collectstatic', False)
    print()

    # ------------------------------------------------------------------
    # 2. Dependencias Python
    # ------------------------------------------------------------------
    print('[2/7] Dependencias de Python...')
    deps = {
        'django': 'Django',
        'psycopg2': 'psycopg2 (driver PostgreSQL)',
        'waitress': 'Waitress (servidor WSGI)',
        'whitenoise': 'WhiteNoise (archivos estaticos)',
    }
    for mod, nombre in deps.items():
        try:
            __import__(mod)
            check(nombre, True)
        except ImportError:
            check(nombre, False, f'pip install {mod}')

    # Dependencias opcionales
    optional = {
        'escpos': 'python-escpos (impresora termica)',
        'reportlab': 'ReportLab (generacion PDF)',
    }
    for mod, nombre in optional.items():
        try:
            __import__(mod)
            check(nombre, True)
        except ImportError:
            check(nombre, False, f'pip install {mod}', es_critico=False)
    print()

    # ------------------------------------------------------------------
    # 3. Django setup
    # ------------------------------------------------------------------
    print('[3/7] Configuracion Django...')
    try:
        import django
        django.setup()
        check('Django inicializado', True)

        from django.conf import settings
        check('DEBUG = False', not settings.DEBUG,
              'DEBUG esta en True, cambie a settings_production')
        check('SECRET_KEY configurada',
              'CAMBIAR' not in settings.SECRET_KEY,
              'Genere una SECRET_KEY unica', es_critico=True)
        check('ALLOWED_HOSTS configurado',
              len(settings.ALLOWED_HOSTS) > 0,
              'Configure ALLOWED_HOSTS')
    except Exception as e:
        check('Django setup', False, str(e))
        print('\n  No se puede continuar sin Django. Corrija el error anterior.\n')
        return
    print()

    # ------------------------------------------------------------------
    # 4. Base de datos
    # ------------------------------------------------------------------
    print('[4/7] Base de datos PostgreSQL...')
    try:
        from django.db import connection
        connection.ensure_connection()
        check('Conexion a PostgreSQL', True)

        cursor = connection.cursor()
        cursor.execute('SELECT version();')
        version = cursor.fetchone()[0]
        print(f'        Version: {version[:60]}...')
    except Exception as e:
        check('Conexion a PostgreSQL', False, str(e))

    # Verificar migraciones
    try:
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command('showmigrations', '--plan', stdout=out)
        pendientes = [l for l in out.getvalue().split('\n')
                      if l.strip().startswith('[ ]')]
        check(f'Migraciones ({len(pendientes)} pendientes)',
              len(pendientes) == 0,
              f'Ejecute: python manage.py migrate', es_critico=len(pendientes) > 0)
    except Exception as e:
        check('Migraciones', False, str(e), es_critico=False)

    # Verificar tablas core
    try:
        from django.apps import apps
        modelos_core = ['Producto', 'Lote', 'Venta', 'Pago']
        for modelo_name in modelos_core:
            encontrado = False
            for app_config in apps.get_app_configs():
                try:
                    modelo = app_config.get_model(modelo_name)
                    modelo.objects.count()
                    encontrado = True
                    break
                except (LookupError, Exception):
                    continue
            check(f'Tabla {modelo_name}', encontrado,
                  f'Modelo no encontrado o tabla no creada', es_critico=False)
    except Exception:
        pass
    print()

    # ------------------------------------------------------------------
    # 5. Archivos estaticos
    # ------------------------------------------------------------------
    print('[5/7] Archivos estaticos...')
    staticfiles_dir = BASE_DIR / 'staticfiles'
    if staticfiles_dir.exists():
        file_count = sum(1 for _ in staticfiles_dir.rglob('*') if _.is_file())
        check(f'Staticfiles ({file_count} archivos)',
              file_count > 0,
              'Ejecute collectstatic')
    else:
        check('Carpeta staticfiles', False,
              'Ejecute: python manage.py collectstatic')
    print()

    # ------------------------------------------------------------------
    # 6. Impresoras (Windows)
    # ------------------------------------------------------------------
    print('[6/7] Impresoras...')
    if sys.platform == 'win32':
        try:
            import win32print
            printers = [p[2] for p in win32print.EnumPrinters(
                win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            )]

            termica = os.environ.get('PRINTER_TERMICA', '2C-POS80-01')
            zebra = os.environ.get('PRINTER_ZEBRA', 'ZDesigner LP 2824')

            check(f'Impresora termica ({termica})',
                  any(termica.lower() in p.lower() for p in printers),
                  f'No encontrada. Impresoras disponibles: {", ".join(printers[:5])}',
                  es_critico=False)
            check(f'Impresora etiquetas ({zebra})',
                  any(zebra.lower() in p.lower() for p in printers),
                  f'No encontrada.',
                  es_critico=False)
        except ImportError:
            check('win32print', False,
                  'pip install pywin32 (necesario para impresoras)', es_critico=False)
    else:
        print(f'  {WARN} Verificacion de impresoras solo disponible en Windows')
        resultados['avisos'] += 1
    print()

    # ------------------------------------------------------------------
    # 7. Red / Firewall
    # ------------------------------------------------------------------
    print('[7/7] Configuracion de red...')
    import socket
    port = int(os.environ.get('SERVER_PORT', '8080'))
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('localhost', port))
        sock.close()
        if result == 0:
            check(f'Puerto {port} (servidor corriendo)', True)
        else:
            check(f'Puerto {port} disponible', True)
    except Exception:
        check(f'Puerto {port}', False, 'No se pudo verificar', es_critico=False)

    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    print(f'        Hostname: {hostname}')
    print(f'        IP Local: {local_ip}')
    print()

    # ------------------------------------------------------------------
    # Resumen
    # ------------------------------------------------------------------
    print('=' * 60)
    print(f'  RESULTADOS: {resultados["ok"]} OK | '
          f'{resultados["errores"]} Errores | '
          f'{resultados["avisos"]} Avisos')
    print('=' * 60)

    if resultados['errores'] == 0:
        print()
        print('  El sistema esta listo para produccion.')
    else:
        print()
        print('  Corrija los errores antes de poner en produccion.')
    print()


if __name__ == '__main__':
    main()
