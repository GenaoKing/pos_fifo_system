"""
apps/ventas/tests.py
Test completo: Crear venta procesando FIFO
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

# 1. Setup inicial
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

# 2. Crear inventario (2 compras = 2 lotes)
print("\n2. Creando inventario con 2 lotes...")

compra1 = Compra.objects.create(
    proveedor='Distribuidor A',
    fecha_compra=datetime.now() - timedelta(days=7),
    total=Decimal('400.00'),
    usuario=usuario
)
DetalleCompra.objects.create(
    compra=compra1,
    producto=producto,
    cantidad=100,
    costo_unitario=Decimal('0.80')
)
print(f"Lote 1: 100 unidades @ $0.80")

compra2 = Compra.objects.create(
    proveedor='Distribuidor B',
    fecha_compra=datetime.now() - timedelta(days=3),
    total=Decimal('280.00'),
    usuario=usuario
)
DetalleCompra.objects.create(
    compra=compra2,
    producto=producto,
    cantidad=80,
    costo_unitario=Decimal('0.85')
)
print(f"Lote 2: 80 unidades @ $0.85")

stock_inicial = obtener_stock_disponible(producto.id)
print(f"\nStock total: {stock_inicial} unidades")

# 3. Crear venta
print("\n3. Creando VENTA...")
venta = Venta.objects.create(
    cajero=usuario,
    total=Decimal('0.00')  # Se calculará después
)
print(f"Venta creada: {venta.numero_venta}")

# 4. Agregar producto a la venta (120 unidades)
print("\n4. Vendiendo 120 unidades del producto...")
cantidad_vender = 120

# Procesar FIFO
resultado_fifo = procesar_venta_fifo(
    producto_id=producto.id,
    cantidad_solicitada=cantidad_vender,
    venta_id=venta.id,
    usuario=usuario
)

print(f"\nResultado FIFO:")
print(f"  - Cantidad vendida: {resultado_fifo['cantidad_vendida']}")
print(f"  - Costo FIFO: ${resultado_fifo['costo_fifo']}")
print(f"  - Lotes consumidos: {resultado_fifo['lotes_consumidos']}")
print(f"  - Stock completo: {resultado_fifo['tiene_stock_completo']}")

# Crear detalle de venta
detalle = DetalleVenta.objects.create(
    venta=venta,
    producto=producto,
    cantidad=cantidad_vender,
    precio_unitario=producto.precio_venta,
    descuento_monto=Decimal('5.00'),  # Descuento de $5
    costo_fifo=resultado_fifo['costo_fifo']
)

print(f"\nDetalle creado:")
print(f"  - Subtotal: ${detalle.subtotal}")
print(f"  - Descuento: ${detalle.descuento_monto} ({detalle.descuento_porcentaje}%)")
print(f"  - Total línea: ${detalle.total_linea}")
print(f"  - Costo FIFO: ${detalle.costo_fifo}")
print(f"  - Margen bruto: ${detalle.get_margen_bruto()}")

# 5. Registrar pago
print("\n5. Registrando pago...")
total_pagar = detalle.total_linea

# Pago mixto: Efectivo + Transferencia
pago1 = Pago.objects.create(
    venta=venta,
    metodo='EFECTIVO',
    monto=Decimal('100.00')
)
pago2 = Pago.objects.create(
    venta=venta,
    metodo='TRANSFERENCIA',
    monto=total_pagar - Decimal('100.00')
)

print(f"Pagos registrados:")
print(f"  - Efectivo: ${pago1.monto}")
print(f"  - Transferencia: ${pago2.monto}")
print(f"  - Total pagado: ${pago1.monto + pago2.monto}")

# 6. Calcular totales de venta
venta.calcular_totales()
venta.save()

print(f"\nVenta completada:")
print(f"  - Número: {venta.numero_venta}")
print(f"  - Subtotal: ${venta.subtotal}")
print(f"  - Descuento: ${venta.descuento_total}")
print(f"  - Total: ${venta.total}")
print(f"  - Estado: {venta.estado}")

# 7. Verificar stock final
print("\n6. Verificando stock después de venta...")
lotes = Lote.objects.filter(producto=producto).order_by('fecha_compra')
for lote in lotes:
    print(f"  {lote.numero_lote}: {lote.cantidad_actual}/{lote.cantidad_inicial}")

stock_final = obtener_stock_disponible(producto.id)
print(f"\nStock final: {stock_final} unidades")
print(f"Vendido: {cantidad_vender} unidades")
print(f"Cálculo: {stock_inicial} - {cantidad_vender} = {stock_final}")

# 8. Verificar consumo FIFO correcto
print("\n7. Verificando consumo FIFO...")
lote1 = lotes[0]
lote2 = lotes[1]

print(f"Lote 1 (más antiguo): {lote1.cantidad_actual}/100")
print(f"  - Debe estar en 0 (consumido completamente)")
print(f"  - CORRECTO" if lote1.cantidad_actual == 0 else "  - ❌ ERROR")

print(f"\nLote 2 (más reciente): {lote2.cantidad_actual}/80")
print(f"  - Debe tener 60 unidades (80 - 20 consumidas)")
print(f"  - CORRECTO" if lote2.cantidad_actual == 60 else "  - ❌ ERROR")

# 9. Verificar costo FIFO
print("\n8. Verificando costo FIFO...")
costo_esperado = (100 * Decimal('0.80')) + (20 * Decimal('0.85'))
print(f"Costo calculado: ${resultado_fifo['costo_fifo']}")
print(f"Costo esperado: ${costo_esperado}")
print(f"  (100 × $0.80 + 20 × $0.85 = ${costo_esperado})")
print(f"  - CORRECTO" if resultado_fifo['costo_fifo'] == costo_esperado else "  - ❌ ERROR")

# 10. Calcular utilidad
print("\n9. Cálculo de utilidad...")
ingreso = detalle.total_linea
costo = detalle.costo_fifo
utilidad = ingreso - costo
margen_porcentaje = (utilidad / ingreso * 100) if ingreso > 0 else 0

print(f"Ingreso (precio venta - descuento): ${ingreso}")
print(f"Costo (FIFO): ${costo}")
print(f"Utilidad bruta: ${utilidad}")
print(f"Margen: {margen_porcentaje:.2f}%")

print("\n" + "="*60)
print("PRUEBA DE VENTA COMPLETADA")
print("="*60)

# Resumen final
print("\nRESUMEN:")
print(f"  - Venta: {venta.numero_venta}")
print(f"  - Total: ${venta.total}")
print(f"  - Unidades vendidas: {cantidad_vender}")
print(f"  - Stock restante: {stock_final}")
print(f"  - FIFO funcionando:")
print(f"  - Margen de utilidad: {margen_porcentaje:.2f}%")