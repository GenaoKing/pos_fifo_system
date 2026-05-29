"""
Script de prueba manual del sistema FIFO de inventario.
Ejecutar desde la raíz del proyecto con el entorno activado:
    python scripts/manual_test_fifo_inventario.py
NO usar con manage.py test — no es un TestCase de Django.
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from decimal import Decimal
from datetime import datetime, timedelta
from django.contrib.auth import get_user_model
from apps.productos.models import Producto, Categoria
from apps.inventario.models import Compra, DetalleCompra, Lote
from apps.inventario.fifo_logic import (
    obtener_stock_disponible,
    procesar_venta_fifo,
    calcular_valuacion_fifo,
    anular_venta_devolver_stock
)

User = get_user_model()

print("="*60)
print("PRUEBA DEL SISTEMA FIFO")
print("="*60)

# 1. Crear usuario de prueba
print("\n1. Creando usuario...")
try:
    usuario = User.objects.create_user(
        username='admin_test',
        password='test123',
        email='example@example.com',
        is_staff=True
    )
    print(f"Usuario creado: {usuario.username}")
except:
    usuario = User.objects.get(username='admin_test')
    print(f"Usuario ya existe: {usuario.username}")

# 2. Crear categoría
print("\n2. Creando categoría...")
categoria, created = Categoria.objects.get_or_create(
    nombre='Bebidas Test',
    defaults={'descripcion': 'Categoría de prueba'}
)
print(f"Categoría: {categoria.nombre}")

# 3. Crear producto
print("\n3. Creando producto...")
producto, created = Producto.objects.get_or_create(
    sku='TEST-001',
    defaults={
        'categoria': categoria,
        'codigo_barras': '7501234567890',
        'nombre': 'Coca-Cola 600ml TEST',
        'precio_venta': Decimal('2.00'),
        'stock_minimo': 20
    }
)
print(f"Producto: {producto.nombre} (SKU: {producto.sku})")

# 4. Primera compra - Lote 1
print("\n4. Creando COMPRA 1 (50 unidades @ $1.00)...")
compra1 = Compra.objects.create(
    proveedor='Proveedor A',
    fecha_compra=datetime.now() - timedelta(days=10),
    total=Decimal('50.00'),
    usuario=usuario
)
detalle1 = DetalleCompra.objects.create(
    compra=compra1,
    producto=producto,
    cantidad=50,
    costo_unitario=Decimal('1.00')
)
print(f"Compra: {compra1.numero_compra}")
lote1 = Lote.objects.filter(producto=producto).first()
print(f"Lote auto-creado: {lote1.numero_lote}")
print(f"   - Cantidad: {lote1.cantidad_actual}")
print(f"   - Costo: ${lote1.costo_unitario}")

# 5. Segunda compra - Lote 2
print("\n5. Creando COMPRA 2 (30 unidades @ $1.20)...")
compra2 = Compra.objects.create(
    proveedor='Proveedor B',
    fecha_compra=datetime.now() - timedelta(days=5),
    total=Decimal('36.00'),
    usuario=usuario
)
detalle2 = DetalleCompra.objects.create(
    compra=compra2,
    producto=producto,
    cantidad=30,
    costo_unitario=Decimal('1.20')
)
print(f"Compra: {compra2.numero_compra}")
lote2 = Lote.objects.filter(producto=producto).exclude(id=lote1.id).first()
print(f"Lote auto-creado: {lote2.numero_lote}")
print(f"   - Cantidad: {lote2.cantidad_actual}")
print(f"   - Costo: ${lote2.costo_unitario}")

# 6. Verificar stock total
print("\n6. Verificando stock total...")
stock_total = obtener_stock_disponible(producto.id)
print(f"Stock total: {stock_total} unidades")
print(f"   (50 del Lote 1 + 30 del Lote 2)")

# 7. Verificar valuación
print("\n7. Calculando valuación FIFO...")
valor = calcular_valuacion_fifo(producto.id)
print(f"Valor inventario: ${valor}")
print(f"   (50 × $1.00 + 30 × $1.20 = ${50*1.00 + 30*1.20})")

# 8. Procesar venta FIFO de 60 unidades
print("\n8. PROCESANDO VENTA FIFO de 60 unidades...")
resultado = procesar_venta_fifo(
    producto_id=producto.id,
    cantidad_solicitada=60,
    venta_id=999,
    usuario=usuario
)

print(f"\nResultado:")
print(f"   - Vendido: {resultado['cantidad_vendida']}")
print(f"   - Faltante: {resultado['cantidad_faltante']}")
print(f"   - Stock completo: {resultado['tiene_stock_completo']}")
print(f"   - Costo FIFO: ${resultado['costo_fifo']}")

# 9. Estado de lotes
print("\n9. Estado de lotes después de venta:")
lote1.refresh_from_db()
lote2.refresh_from_db()
print(f"   Lote 1: {lote1.cantidad_actual}/{lote1.cantidad_inicial} (debe ser 0/50)")
print(f"   Lote 2: {lote2.cantidad_actual}/{lote2.cantidad_inicial} (debe ser 20/30)")

# 10. Stock restante
print("\n10. Stock restante:")
stock_restante = obtener_stock_disponible(producto.id)
print(f"Stock actual: {stock_restante} unidades (debe ser 20)")

# 11. Anulación
print("\n11. ANULANDO VENTA...")
resultado_anulacion = anular_venta_devolver_stock(venta_id=999, usuario=usuario)

if resultado_anulacion['success']:
    print(f"Venta anulada exitosamente")
    lote1.refresh_from_db()
    lote2.refresh_from_db()
    print(f"   Lote 1: {lote1.cantidad_actual}/{lote1.cantidad_inicial} (debe ser 50/50)")
    print(f"   Lote 2: {lote2.cantidad_actual}/{lote2.cantidad_inicial} (debe ser 30/30)")

print("\n" + "="*60)
print("PRUEBA COMPLETADA")
print("="*60)
