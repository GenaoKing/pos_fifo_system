# Script de inicialización del proyecto POS FIFO
# Ejecutar con: python init_project.py

import os
import sys
import subprocess

def crear_apps():
    """Crear las apps del proyecto si no existen"""
    apps = ['usuarios', 'productos', 'inventario', 'ventas', 'reportes', 'auditoria']
    
    for app in apps:
        app_path = f'apps/{app}'
        if not os.path.exists(app_path):
            print(f'Creando app: {app}')
            subprocess.run([sys.executable, 'manage.py', 'startapp', app, app_path])
        else:
            print(f'App {app} ya existe, saltando...')

def crear_directorios():
    """Crear directorios necesarios"""
    dirs = [
        'static/css',
        'static/js',
        'static/img',
        'templates/base',
        'templates/pos',
        'templates/inventario',
        'templates/reportes',
        'media',
        'utils/impresoras',
    ]
    
    for dir_path in dirs:
        os.makedirs(dir_path, exist_ok=True)
        print(f'Directorio creado: {dir_path}')

def aplicar_migraciones():
    """Aplicar migraciones de Django"""
    print('\n--- Aplicando migraciones ---')
    subprocess.run([sys.executable, 'manage.py', 'makemigrations'])
    subprocess.run([sys.executable, 'manage.py', 'migrate'])

def main():
    print('=== Inicializando Proyecto POS FIFO ===\n')
    
    # Verificar que estamos en el directorio correcto
    if not os.path.exists('manage.py'):
        print('ERROR: Este script debe ejecutarse desde la raíz del proyecto Django')
        sys.exit(1)
    
    # Crear apps
    print('\n1. Creando apps...')
    crear_apps()
    
    # Crear directorios
    print('\n2. Creando estructura de directorios...')
    crear_directorios()
    
    # Aplicar migraciones
    print('\n3. ¿Deseas aplicar las migraciones ahora? (s/n): ', end='')
    respuesta = input().lower()
    if respuesta == 's':
        aplicar_migraciones()
    
    print('\n=== ¡Inicialización completada! ===')
    print('\nPróximos pasos:')
    print('1. python manage.py createsuperuser')
    print('2. python manage.py runserver')
    print('\nAccede a: http://localhost:8000/admin')

if __name__ == '__main__':
    main()
