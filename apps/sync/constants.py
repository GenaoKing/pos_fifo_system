"""
apps/sync/constants.py

UNICA fuente de verdad para los tipos de eventos de sincronizacion.

Cuando agregues un tipo nuevo:
    1. Agregalo a TIPOS_EVENTO abajo
    2. Define el serializer en apps/sync/serializers.py
    3. Define el helper en apps/sync/events.py
    4. Emite el hecho dentro de la misma transaction.atomic() del negocio
    5. Registralo en apps/sync/registry.py si es re-serializable
    6. Define el handler en apps/api/views/sync.py HANDLERS

El assert en apps/api/views/sync.py detecta si olvidaste el paso 6.
El procedimiento completo vive en docs/runbooks/EXTENDER_NOTIFICACIONES.md.
"""

TIPOS_EVENTO = [
    ('VENTA_CREADA', 'Venta creada'),
    ('VENTA_ANULADA', 'Venta anulada'),
    ('APERTURA_CAJA', 'Apertura de caja'),
    ('MOVIMIENTO_CAJA', 'Movimiento de caja (retiro/gasto/ingreso)'),
    ('CIERRE_CAJA', 'Cierre de caja'),
    ('AJUSTE_INVENTARIO', 'Ajuste de inventario'),
    ('COMPRA_REGISTRADA', 'Compra registrada'),
    ('INVENTARIO_MOVIMIENTO_REGISTRADO', 'Movimiento de inventario registrado'),
    ('INVENTARIO_SNAPSHOT', 'Snapshot de inventario'),
    ('COTIZACION_CREADA', 'Cotizacion creada'),
    ('COTIZACION_CONVERTIDA', 'Cotizacion convertida'),
    ('CXC_CREADA', 'Cuenta por cobrar creada'),
    ('CXC_PAGO_REGISTRADO', 'Pago de cuenta por cobrar registrado'),
    ('CXC_PAGO_ANULADO', 'Pago de cuenta por cobrar anulado'),
    ('CXC_ANULADA', 'Cuenta por cobrar anulada'),
]

# Solo los codigos (util para validacion con ChoiceField)
TIPOS_EVENTO_CODIGOS = [codigo for codigo, _ in TIPOS_EVENTO]
