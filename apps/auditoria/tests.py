"""
Script de prueba para verificar la instalación de auditoría
Ejecutar con: python manage.py shell < test_auditoria.py
"""

print("=" * 60)
print("🧪 TEST DE AUDITORÍA - Sistema POS FIFO")
print("=" * 60)

# Importaciones
try:
    from apps.auditoria.models import Auditoria, get_client_ip
    from apps.usuarios.models import Usuario
    from django.utils import timezone
    print("✅ Importaciones exitosas")
except Exception as e:
    print(f"❌ Error en importaciones: {e}")
    exit(1)

# Test 1: Crear registro de auditoría simple
print("\n--- Test 1: Crear registro simple ---")
try:
    usuario = Usuario.objects.first()
    if not usuario:
        print("❌ No hay usuarios en la BD. Crear al menos uno.")
        exit(1)
    
    audit = Auditoria.registrar(
        accion=Auditoria.TipoAccion.LOGIN,
        descripcion=f"Test de login - {usuario.username}",
        usuario=usuario,
        ip_address="127.0.0.1",
        nivel_importancia='MEDIA'
    )
    print(f"✅ Registro creado: {audit}")
except Exception as e:
    print(f"❌ Error: {e}")

# Test 2: Registrar con método específico
print("\n--- Test 2: Método registrar_login ---")
try:
    audit = Auditoria.registrar_login(
        usuario=usuario,
        ip_address="192.168.1.100",
        user_agent="Mozilla/5.0 Test",
        exito=True
    )
    print(f"✅ Login registrado: {audit}")
except Exception as e:
    print(f"❌ Error: {e}")

# Test 3: Registrar error
print("\n--- Test 3: Registrar error ---")
try:
    audit = Auditoria.registrar_error(
        descripcion="Error de prueba en el sistema",
        usuario=usuario,
        detalle_error="Este es un error de prueba",
        nivel_importancia='ALTA'
    )
    print(f"✅ Error registrado: {audit}")
except Exception as e:
    print(f"❌ Error: {e}")

# Test 4: Consultar registros
print("\n--- Test 4: Consultar registros ---")
try:
    total = Auditoria.objects.count()
    print(f"✅ Total registros en BD: {total}")
    
    if total > 0:
        ultimo = Auditoria.objects.first()
        print(f"   Último registro: {ultimo}")
        print(f"   - Usuario: {ultimo.usuario}")
        print(f"   - Acción: {ultimo.get_accion_display()}")
        print(f"   - Fecha: {ultimo.fecha_hora}")
        print(f"   - Nivel: {ultimo.get_nivel_importancia_display()}")
except Exception as e:
    print(f"❌ Error: {e}")

# Test 5: Consultar acciones de usuario
print("\n--- Test 5: Acciones del usuario ---")
try:
    acciones = Auditoria.obtener_acciones_usuario(usuario, limite=5)
    print(f"✅ Últimas {acciones.count()} acciones del usuario {usuario.username}:")
    for acc in acciones:
        print(f"   - {acc.fecha_hora.strftime('%H:%M:%S')} | {acc.get_accion_display()}")
except Exception as e:
    print(f"❌ Error: {e}")

# Test 6: Verificar índices
print("\n--- Test 6: Verificar modelo ---")
try:
    print("✅ Campos disponibles:")
    for field in Auditoria._meta.get_fields():
        print(f"   - {field.name}")
    
    print("\n✅ Tipos de acción disponibles:")
    for choice in Auditoria.TipoAccion.choices[:5]:
        print(f"   - {choice[0]}: {choice[1]}")
    print(f"   ... y {len(Auditoria.TipoAccion.choices) - 5} más")
except Exception as e:
    print(f"❌ Error: {e}")

# Resumen
print("\n" + "=" * 60)
print("📊 RESUMEN")
print("=" * 60)
print(f"Total de registros de auditoría: {Auditoria.objects.count()}")
print(f"Usuarios en sistema: {Usuario.objects.count()}")
print(f"Acciones críticas: {Auditoria.objects.filter(nivel_importancia='CRITICA').count()}")
print(f"Errores registrados: {Auditoria.objects.filter(exito=False).count()}")

print("\n✅ ¡Todos los tests completados!")
print("=" * 60)