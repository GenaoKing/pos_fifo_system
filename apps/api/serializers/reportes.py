"""
apps/api/serializers/reportes.py
Serializers para reportes consolidados (cloud → dashboard).

Estos serializers definen la estructura de respuesta de los endpoints
de reportes. Son solo de salida (read-only) y no están ligados a un
modelo Django — los views construyen los datos con queries de agregación.

TODO: FASE 2 — Los views que usen estos serializers necesitan el modelo
Sucursal para filtrar y agrupar por sucursal.
"""

from rest_framework import serializers


class VentaSucursalResumenSerializer(serializers.Serializer):
    """
    Resumen de ventas del día para una sucursal.
    
    Uso en respuesta de:
        GET /api/v1/reportes/ventas-hoy/
        GET /api/v1/reportes/ventas-hoy/<codigo_sucursal>/
    """
    sucursal_codigo = serializers.CharField()
    sucursal_nombre = serializers.CharField()
    cantidad_ventas = serializers.IntegerField()
    total_ventas = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_descuentos = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_efectivo = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_transferencia = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_tarjeta = serializers.DecimalField(max_digits=14, decimal_places=2)
    cantidad_anulaciones = serializers.IntegerField()
    ultima_sync = serializers.DateTimeField(allow_null=True)


class ComparativoSucursalSerializer(serializers.Serializer):
    """
    Datos para comparativo entre sucursales.
    
    Uso en respuesta de:
        GET /api/v1/reportes/comparativo/
    """
    sucursal_codigo = serializers.CharField()
    sucursal_nombre = serializers.CharField()
    ventas_hoy = serializers.DecimalField(max_digits=14, decimal_places=2)
    ventas_semana = serializers.DecimalField(max_digits=14, decimal_places=2)
    ventas_mes = serializers.DecimalField(max_digits=14, decimal_places=2)
    ticket_promedio = serializers.DecimalField(max_digits=10, decimal_places=2)
    productos_vendidos = serializers.IntegerField()
    estado_sync = serializers.ChoiceField(
        choices=[('verde', 'verde'), ('amarillo', 'amarillo'), ('rojo', 'rojo')]
    )


class InventarioConsolidadoSerializer(serializers.Serializer):
    """
    Inventario consolidado por producto a través de sucursales.
    
    Uso en respuesta de:
        GET /api/v1/reportes/inventario-consolidado/
    """
    producto_id = serializers.IntegerField()
    producto_sku = serializers.CharField()
    producto_nombre = serializers.CharField()
    categoria = serializers.CharField()
    stock_por_sucursal = serializers.DictField(
        child=serializers.IntegerField(),
        help_text='{"SD-001": 150, "STI-001": 80}'
    )
    stock_total = serializers.IntegerField()
    precio_venta = serializers.DecimalField(max_digits=10, decimal_places=2)
    necesita_reposicion = serializers.BooleanField()