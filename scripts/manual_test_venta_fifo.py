"""
Script de prueba manual: venta completa con FIFO.
Ejecutar desde la raíz del proyecto con el entorno activado:
    python scripts/manual_test_venta_fifo.py
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
from apps.inventario.fifo_logic import procesar_venta_fifo, obtener_stock_disponible
from apps.ventas.models import Venta, DetalleVenta, Pago

User = get_user_model()

print("="*60)
print("PRUEBA: VENTA COMPLETA CON FIFO")
print("="*60)

# Setup
print("\n1. Setup - Usuario, Categoría, Producto")
usuario, _ = User.objects.get_or_create(
    username='cajero_test',
    defaults={'email': 'cajero@test.com', 'is_staff': True}
)
if _:
    usuario.set_password('test123')
    usuario.save()

categoria, _ = Categoria.objects.get_or_create(
    nombre='Snacks',
    defaults={'descripcion': 'Snacks y botanas'}
)

producto, _ = Producto.objects.get_or_create(
    sku='SNK-001',
    defaults={
        'categoria': categoria,
        'codigo_barras': '7501234567891',
        'nombre': 'Papas Lays 45g',
        'precio_venta': Decimal('1.50'),
        'stock_minimo': 20
    }
)
print(f"Producto: {producto.nombre} - Precio: ${producto.precio_venta}")

# Inventario
print("\n2. Creando inventario con 2 lotes...")
compra1 = Compra.objects.create(
    proveedor='Distribuidor A',
    fecha_compra=datetime.now() - timedelta(days=7),
    total=Decimal('400.00'),
    usuario=usuario
)
DetalleCompra.objects.create(compra=compra1, producto=producto, cantidad=100, costo_unitario=Decimal('0.80'))
print(f"Lote 1: 100 unidades @ $0.80")

compra2 = Compra.objects.create(
    proveedor='Distribuidor B',
    fecha_compra=datetime.now() - timedelta(days=3),
    total=Decimal('280.00'),
    usuario=usuario
)
DetalleCompra.objects.create(compra=compra2, producto=producto, cantidad=80, costo_unitario=Decimal('0.85'))
print(f"Lote 2: 80 unidades @ $0.85")

stock_inicial = obtener_stock_disponible(producto.id)
print(f"\nStock total: {stock_inicial} unidades")

# Venta
print("\n3. Creando VENTA de 120 unidades...")
venta = Venta.objects.create(cajero=usuario, total=Decimal('0.00'))
resultado_fifo = procesar_venta_fifo(
    producto_id=producto.id, cantidad_solicitada=120, venta_id=venta.id, usuario=usuario
)

detalle = DetalleVenta.objects.create(
    venta=venta, producto=producto, cantidad=120,
    precio_unitario=producto.precio_venta,
    descuento_monto=Decimal('5.00'),
    costo_fifo=resultado_fifo['costo_fifo']
)

pago1 = Pago.objects.create(venta=venta, metodo='EFECTIVO', monto=Decimal('100.00'))
pago2 = Pago.objects.create(venta=venta, metodo='TRANSFERENCIA', monto=detalle.total_linea - Decimal('100.00'))

venta.calcular_totales()
venta.save()

print(f"Venta: {venta.numero_venta} | Total: ${venta.total}")

stock_final = obtener_stock_disponible(producto.id)
print(f"\nStock final: {stock_final} unidades (inicial={stock_inicial}, vendido=120)")

lotes = Lote.objects.filter(producto=producto).order_by('fecha_compra')
lote1, lote2 = lotes[0], lotes[1]
print(f"Lote 1 (más antiguo): {lote1.cantidad_actual}/100 — debe ser 0")
print(f"Lote 2 (más reciente): {lote2.cantidad_actual}/80 — debe ser 60")

costo_esperado = (100 * Decimal('0.80')) + (20 * Decimal('0.85'))
print(f"\nCosto FIFO: ${resultado_fifo['costo_fifo']} (esperado: ${costo_esperado})")

print("\n" + "="*60)
print("PRUEBA DE VENTA COMPLETADA")
print("="*60)
