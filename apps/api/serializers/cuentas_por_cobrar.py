"""
apps/api/serializers/cuentas_por_cobrar.py
Serializers de LECTURA para la cartera (cuentas por cobrar) en el portal cloud.

Contexto (ver ROADMAP_PORTAL.md -> 5.H / B15 y ROADMAP_CLOUD.md -> "Decision
record: Credito y cuentas por cobrar v1"):

    CxC fluye sucursal -> cloud por eventos (CXC_CREADA, CXC_PAGO_REGISTRADO,
    CXC_ANULADA). El alta de credito, abonos y anulaciones nacen en el POS.
    En el portal CxC es SOLO LECTURA / presentacion.

Por eso aqui NO hay write serializers: el portal nunca escribe cartera.
Los campos denormalizados (cliente_nombre, numero_venta, etc.) evitan que el
frontend tenga que cruzar recursos.
"""

from rest_framework import serializers

from apps.cuentas_por_cobrar.models import CuentaPorCobrar, CuotaCxC, PagoCxC


class CuotaCxCSerializer(serializers.ModelSerializer):
    """Cuota de una cuenta por cobrar (solo lectura)."""

    class Meta:
        model = CuotaCxC
        fields = [
            'id',
            'numero',
            'monto',
            'saldo',
            'fecha_vencimiento',
            'estado',
            'fecha_pago',
        ]
        read_only_fields = fields


class PagoCxCSerializer(serializers.ModelSerializer):
    """Abono aplicado a una cuenta por cobrar (solo lectura)."""

    registrado_por = serializers.CharField(
        source='registrado_por.username',
        read_only=True,
        default=None,
    )
    anulado_por = serializers.CharField(
        source='anulado_por.username',
        read_only=True,
        default=None,
    )

    class Meta:
        model = PagoCxC
        fields = [
            'id',
            'metodo',
            'monto',
            'referencia',
            'fecha_pago',
            'estado',
            'registrado_por',
            'anulado_por',
            'fecha_anulacion',
            'motivo_anulacion',
        ]
        read_only_fields = fields


class CuentaPorCobrarSerializer(serializers.ModelSerializer):
    """
    Cuenta por cobrar para la vista de LISTA del portal.

    Incluye datos denormalizados del cliente, venta, metodo de plazo y
    sucursal para que el frontend pueble la tabla sin requests adicionales.
    `esta_vencida` se calcula contra la fecha de hoy (el campo `estado` solo
    se recalcula por eventos y puede estar desfasado).
    """

    numero_venta = serializers.CharField(source='venta.numero_venta', read_only=True)
    cliente_nombre = serializers.CharField(source='cliente.nombre', read_only=True)
    cliente_cedula_rnc = serializers.CharField(
        source='cliente.cedula_rnc', read_only=True, default=None
    )
    metodo_plazo_nombre = serializers.CharField(
        source='metodo_plazo.nombre', read_only=True
    )
    sucursal_codigo = serializers.CharField(
        source='sucursal.codigo', read_only=True, default=None
    )
    # Deuda real al emitir = capital financiado + interes (property del modelo).
    monto_financiado = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    esta_vencida = serializers.BooleanField(read_only=True)

    class Meta:
        model = CuentaPorCobrar
        fields = [
            'id',
            'numero_venta',
            'cliente_id',
            'cliente_nombre',
            'cliente_cedula_rnc',
            'sucursal_codigo',
            'metodo_plazo_nombre',
            'total',
            'monto_inicial',
            'saldo_original',
            'interes_porcentaje',
            'monto_interes',
            'monto_financiado',
            'saldo',
            'estado',
            'fecha_emision',
            'fecha_limite',
            'esta_vencida',
        ]
        read_only_fields = fields


class CuentaPorCobrarDetalleSerializer(CuentaPorCobrarSerializer):
    """Detalle de una cuenta: agrega las cuotas y los abonos."""

    cuotas = CuotaCxCSerializer(many=True, read_only=True)
    pagos = PagoCxCSerializer(source='pagos_cxc', many=True, read_only=True)

    class Meta(CuentaPorCobrarSerializer.Meta):
        fields = CuentaPorCobrarSerializer.Meta.fields + ['cuotas', 'pagos']
        read_only_fields = fields


class CarteraResumenSerializer(serializers.Serializer):
    """
    Agregados de TODA la cartera (no de la pagina actual).

    Solo se usa para documentar/validar la forma de la respuesta del
    endpoint `resumen/`; los valores se calculan con una sola consulta de
    agregacion en el ViewSet.
    """

    cartera_total = serializers.DecimalField(max_digits=14, decimal_places=2)
    saldo_vencido = serializers.DecimalField(max_digits=14, decimal_places=2)
    cuentas_abiertas = serializers.IntegerField()
    cuentas_vencidas = serializers.IntegerField()
    clientes_con_saldo = serializers.IntegerField()
