"""
POS - Servidor de Produccion (Waitress)
Ejecutar con: python server.py
O a traves de: deploy/iniciar_servidor.bat
"""
import os
import sys
import logging
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings_production')


def env_int(name, default):
    raw = os.environ.get(name, '')
    if not raw.strip():
        return default
    return int(raw)


def get_server_config():
    host = os.environ.get('SERVER_IP', '0.0.0.0')
    port = env_int('SERVER_PORT', 8080)
    threads = env_int('SERVER_THREADS', 4)
    return host, port, threads


def main():
    print()
    print('=' * 60)
    print('  POS - Servidor de Produccion')
    print('=' * 60)
    print()

    try:
        import django
        django.setup()
    except Exception as e:
        print(f'[ERROR] No se pudo inicializar Django: {e}')
        sys.exit(1)

    # Verificar conexion a base de datos
    try:
        from django.db import connection
        connection.ensure_connection()
        print('  [OK] Conexion a PostgreSQL establecida')
    except Exception as e:
        print(f'  [ERROR] No se pudo conectar a PostgreSQL: {e}')
        print('  Verifique que PostgreSQL este corriendo y los datos en env_cliente.bat')
        sys.exit(1)

    # Verificar migraciones pendientes
    try:
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command('showmigrations', '--plan', stdout=out)
        pending = [l for l in out.getvalue().split('\n') if l.strip().startswith('[ ]')]
        if pending:
            print(f'  [AVISO] Hay {len(pending)} migraciones pendientes.')
            print('  Ejecute: python manage.py migrate --settings=config.settings_production')
    except Exception:
        pass

    # Iniciar Waitress
    try:
        from waitress import serve
        from config.wsgi import application
    except ImportError:
        print('[ERROR] Waitress no instalado. Ejecute: pip install waitress')
        sys.exit(1)

    host, port, threads = get_server_config()

    print()
    print(f'  Servidor:  http://{host}:{port}')
    print(f'  Local:     http://localhost:{port}')
    print(f'  Threads:   {threads}')
    print()
    print('  Presione Ctrl+C para detener el servidor')
    print('=' * 60)
    print()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )

    serve(
        application,
        host=host,
        port=port,
        threads=threads,
        url_scheme='http',
        channel_timeout=120,
        recv_bytes=65536,
        send_bytes=65536,
        cleanup_interval=30,
        connection_limit=100,
    )


if __name__ == '__main__':
    main()
