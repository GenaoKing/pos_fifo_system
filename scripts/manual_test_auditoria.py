"""
Script de prueba manual del sistema de auditoría.
Ejecutar desde la raíz del proyecto con el entorno activado:
    python scripts/manual_test_auditoria.py
NO usar con manage.py test — no es un TestCase de Django.
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

print("=" * 60)
print("TEST DE AUDITORÍA - Sistema POS FIFO")
print("=" * 60)

try:
    from apps.auditoria.models import Auditoria, get_client_ip
    from apps.usuarios.models import Usuario
    from django.utils import timezone
    print("Importaciones exitosas")
except Exception as e:
    print(f"Error en importaciones: {e}")
    exit(1)

# Test 1
print("\n--- Test 1: Crear registro simple ---")
try:
    usuario = Usuario.objects.first()
    if not usuario:
        print("No hay usuarios en la BD. Crear al menos uno.")
        exit(1)

    audit = Auditoria.registrar(
        accion=Auditoria.TipoAccion.LOGIN,
        descripcion=f"Test de login - {usuario.username}",
        usuario=usuario,
        ip_address="127.0.0.1",
        nivel_importancia='MEDIA'
    )
    print(f"Registro creado: {audit}")
except Exception as e:
    print(f"Error: {e}")

# Test 2
print("\n--- Test 2: Método registrar_login ---")
try:
    audit = Auditoria.registrar_login(
        usuario=usuario, ip_address="192.168.1.100",
        user_agent="Mozilla/5.0 Test", exito=True
    )
    print(f"Login registrado: {audit}")
except Exception as e:
    print(f"Error: {e}")

# Test 3
print("\n--- Test 3: Registrar error ---")
try:
    audit = Auditoria.registrar_error(
        descripcion="Error de prueba en el sistema",
        usuario=usuario,
        detalle_error="Este es un error de prueba",
        nivel_importancia='ALTA'
    )
    print(f"Error registrado: {audit}")
except Exception as e:
    print(f"Error: {e}")

# Test 4
print("\n--- Test 4: Consultar registros ---")
try:
    total = Auditoria.objects.count()
    print(f"Total registros en BD: {total}")
except Exception as e:
    print(f"Error: {e}")

print("\n" + "=" * 60)
print(f"Total de registros de auditoría: {Auditoria.objects.count()}")
print(f"Usuarios en sistema: {Usuario.objects.count()}")
print("=" * 60)
