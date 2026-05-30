"""
apps/facturacion_electronica/tests/test_venta_to_ecf.py

Tests del mapper neutro `venta_a_ecf_data()`.

Este mapper es el contrato de la capa agnóstica al proveedor: cualquier
implementación de EmisorECFInterface (MSeller hoy, librería nativa en
Fase 2) consume su salida. Por eso los tests son exhaustivos.

Cobertura:
1. Validación de parámetros (tipo_ecf inválido, tipo 34 incompleto)
2. Construcción del Comprador según tipo y cliente
3. Cálculo de líneas en modo ITBIS incluido vs ITBIS sumado
4. Indicador de facturación según tasa
5. Totalización agregada
6. Redondeo half-up (no banker's rounding)
7. Normalización de RNC
8. Edge cases (venta sin detalles, nombres largos, metadata)
"""
import logging
from datetime import date
from decimal import Decimal

import pytest
from django.core.cache import cache

from apps.cuentas_por_cobrar.models import CuentaPorCobrar, MetodoPlazoCredito
from apps.facturacion_electronica.services.venta_to_ecf import (
    _indicador_facturacion,
    _normalizar_rnc,
    venta_a_ecf_data,
)

from .factories import (
    ClienteConRNCFactory,
    ClienteContadoFactory,
    ClientePersonalSinRNCFactory,
    DetalleVentaFactory,
    ProductoFactory,
    VentaFactory,
    crear_venta_con_detalles,
)


# Todos los tests de este archivo necesitan acceso a BD vía pytest-django.
pytestmark = pytest.mark.django_db


# =============================================================================
# 1. Validación de parámetros
# =============================================================================

class TestValidacionParametros:
    """
    `venta_a_ecf_data()` debe rechazar parámetros mal formados antes de
    tocar la BD o devolver datos. Estos errores son del caller, no del
    proveedor, así que se levantan como ValueError.
    """

    def test_tipo_ecf_99_invalido_levanta_value_error(self, config_negocio):
        venta = crear_venta_con_detalles()
        with pytest.raises(ValueError, match='no soportado'):
            venta_a_ecf_data(venta, tipo_ecf='99')

    def test_tipo_ecf_40_no_implementado_levanta_value_error(self, config_negocio):
        """Tipo 40 existe en DGII pero el POS no lo emite."""
        venta = crear_venta_con_detalles()
        with pytest.raises(ValueError):
            venta_a_ecf_data(venta, tipo_ecf='40')

    def test_tipo_34_sin_motivo_nc_levanta_value_error(self, config_negocio):
        venta = crear_venta_con_detalles()
        with pytest.raises(ValueError, match='motivo_nc'):
            venta_a_ecf_data(
                venta,
                tipo_ecf='34',
                encf_referencia='E320000000001',
                codigo_modificacion_nc=1,
            )

    def test_tipo_34_sin_encf_referencia_levanta_value_error(self, config_negocio):
        venta = crear_venta_con_detalles()
        with pytest.raises(ValueError, match='encf_referencia'):
            venta_a_ecf_data(
                venta,
                tipo_ecf='34',
                motivo_nc='Anulacion',
                codigo_modificacion_nc=1,
            )

    def test_tipo_34_sin_codigo_modificacion_levanta_value_error(self, config_negocio):
        venta = crear_venta_con_detalles()
        with pytest.raises(ValueError, match='codigo_modificacion'):
            venta_a_ecf_data(
                venta,
                tipo_ecf='34',
                motivo_nc='Anulacion',
                encf_referencia='E320000000001',
            )

    def test_tipo_34_codigo_modificacion_5_invalido_levanta_value_error(self, config_negocio):
        """Códigos válidos: 1 (anulación), 2 (texto), 3 (montos)."""
        venta = crear_venta_con_detalles()
        with pytest.raises(ValueError, match='codigo_modificacion'):
            venta_a_ecf_data(
                venta,
                tipo_ecf='34',
                motivo_nc='Anulacion',
                encf_referencia='E320000000001',
                codigo_modificacion_nc=5,
            )

    def test_tipo_34_codigo_modificacion_0_invalido_levanta_value_error(self, config_negocio):
        venta = crear_venta_con_detalles()
        with pytest.raises(ValueError, match='codigo_modificacion'):
            venta_a_ecf_data(
                venta,
                tipo_ecf='34',
                motivo_nc='Anulacion',
                encf_referencia='E320000000001',
                codigo_modificacion_nc=0,
            )


# =============================================================================
# 2. Construcción del Comprador
# =============================================================================

class TestComprador:
    """
    Reglas DGII para el bloque Comprador:
    - Tipo 31 (crédito fiscal): RNC obligatorio, cliente real requerido.
    - Tipo 32 (consumo): Comprador opcional; presente solo si hay cliente
      real; ausente si no hay cliente o si es CONTADO.
    - Tipo 34 (NC): mismas reglas que tipo 32.
    - Cliente CONTADO se trata SIEMPRE como ausencia fiscal, incluso si
      el cliente tiene cedula_rnc seteada por algún motivo.
    """

    # ----- Tipo 31: RNC obligatorio -----

    def test_tipo_31_sin_cliente_levanta_value_error(self, config_negocio):
        venta = crear_venta_con_detalles(cliente=None)
        with pytest.raises(ValueError, match='tipo 31 requiere cliente'):
            venta_a_ecf_data(venta, tipo_ecf='31')

    def test_tipo_31_con_cliente_contado_levanta_value_error(self, config_negocio):
        """CONTADO es ausencia fiscal aunque sea un Cliente persistido."""
        contado = ClienteContadoFactory()
        venta = crear_venta_con_detalles(cliente=contado)
        with pytest.raises(ValueError, match='tipo 31 requiere cliente'):
            venta_a_ecf_data(venta, tipo_ecf='31')

    def test_tipo_31_con_cliente_sin_rnc_levanta_value_error(self, config_negocio):
        cliente = ClientePersonalSinRNCFactory()
        venta = crear_venta_con_detalles(cliente=cliente)
        with pytest.raises(ValueError, match=r'no tiene c.dula/RNC'):
            venta_a_ecf_data(venta, tipo_ecf='31')

    def test_tipo_31_con_cliente_rnc_construye_comprador_correcto(self, config_negocio):
        cliente = ClienteConRNCFactory(
            nombre='Empresa Acme SRL',
            cedula_rnc='131123456',
            direccion='Av. Independencia 100',
        )
        venta = crear_venta_con_detalles(cliente=cliente)
        result = venta_a_ecf_data(venta, tipo_ecf='31')

        assert result['comprador'] is not None
        assert result['comprador']['rnc_o_cedula'] == '131123456'
        assert result['comprador']['razon_social'] == 'Empresa Acme SRL'
        assert result['comprador']['direccion'] == 'Av. Independencia 100'

    def test_tipo_31_normaliza_rnc_con_guiones(self, config_negocio):
        """El RNC se almacena tal como lo tipea el usuario; el mapper lo
        normaliza antes de mandarlo a DGII."""
        cliente = ClienteConRNCFactory(cedula_rnc='131-12345-6')
        venta = crear_venta_con_detalles(cliente=cliente)
        result = venta_a_ecf_data(venta, tipo_ecf='31')

        assert result['comprador']['rnc_o_cedula'] == '131123456'

    # ----- Tipo 32: Comprador opcional -----

    def test_tipo_32_sin_cliente_omite_comprador(self, config_negocio):
        venta = crear_venta_con_detalles(cliente=None)
        result = venta_a_ecf_data(venta, tipo_ecf='32')

        assert result['comprador'] is None

    def test_tipo_32_con_cliente_contado_omite_comprador(self, config_negocio):
        contado = ClienteContadoFactory()
        venta = crear_venta_con_detalles(cliente=contado)
        result = venta_a_ecf_data(venta, tipo_ecf='32')

        assert result['comprador'] is None

    def test_tipo_32_con_cliente_real_incluye_comprador(self, config_negocio):
        cliente = ClienteConRNCFactory(
            nombre='Juan Perez',
            cedula_rnc='40212345678',
        )
        venta = crear_venta_con_detalles(cliente=cliente)
        result = venta_a_ecf_data(venta, tipo_ecf='32')

        assert result['comprador'] is not None
        assert result['comprador']['rnc_o_cedula'] == '40212345678'
        assert result['comprador']['razon_social'] == 'Juan Perez'

    def test_tipo_32_con_cliente_sin_rnc_comprador_rnc_none(self, config_negocio):
        """Para tipo 32, cliente sin RNC se acepta. Comprador presente
        pero con rnc_o_cedula=None — MSeller decide qué hacer."""
        cliente = ClientePersonalSinRNCFactory(nombre='Pedro')
        venta = crear_venta_con_detalles(cliente=cliente)
        result = venta_a_ecf_data(venta, tipo_ecf='32')

        assert result['comprador'] is not None
        assert result['comprador']['rnc_o_cedula'] is None
        assert result['comprador']['razon_social'] == 'Pedro'

    # ----- Tipo 34: mismas reglas que tipo 32 -----

    def test_tipo_34_hereda_reglas_de_tipo_32_para_comprador(self, config_negocio):
        contado = ClienteContadoFactory()
        venta = crear_venta_con_detalles(cliente=contado)
        result = venta_a_ecf_data(
            venta,
            tipo_ecf='34',
            motivo_nc='Anulacion total',
            encf_referencia='E320000000001',
            codigo_modificacion_nc=1,
        )

        assert result['comprador'] is None


# =============================================================================
# 3. Cálculo de líneas (ITBIS incluido vs sumado)
# =============================================================================

class TestCalculoLineas:
    """
    El mapper soporta dos modos según ConfiguracionNegocio:
    - itbis_incluido_en_precio=True: precio del POS YA incluye ITBIS,
      back-calculo con base = precio / (1 + pct/100).
    - itbis_incluido_en_precio=False: precio es base imponible, ITBIS
      se suma encima.
    """

    def test_modo_itbis_incluido_extrae_base_de_precio_pos(self, config_negocio):
        """Precio POS = 118, tasa 18%: base = 100, ITBIS = 18."""
        # config_negocio ya viene con itbis_incluido=True, pct=18
        venta = crear_venta_con_detalles(items=[
            {'precio_unitario': Decimal('118.00'), 'cantidad': 1},
        ])
        result = venta_a_ecf_data(venta, tipo_ecf='32')

        item = result['items'][0]
        assert item['precio_unitario'] == Decimal('100.00')
        assert item['monto_item'] == Decimal('100.00')
        assert result['totales']['monto_gravado_18'] == Decimal('100.00')
        assert result['totales']['total_itbis_18'] == Decimal('18.00')
        assert result['totales']['monto_total'] == Decimal('118.00')

    def test_modo_itbis_sumado_usa_precio_como_base(self, config_negocio):
        """Precio POS = 100, tasa 18%: base = 100, ITBIS = 18, total = 118."""
        config_negocio.itbis_incluido_en_precio = False
        config_negocio.save()
        cache.clear()

        venta = crear_venta_con_detalles(items=[
            {'precio_unitario': Decimal('100.00'), 'cantidad': 1},
        ])
        result = venta_a_ecf_data(venta, tipo_ecf='32')

        item = result['items'][0]
        assert item['precio_unitario'] == Decimal('100.00')
        assert item['monto_item'] == Decimal('100.00')
        assert result['totales']['monto_gravado_18'] == Decimal('100.00')
        assert result['totales']['total_itbis_18'] == Decimal('18.00')
        assert result['totales']['monto_total'] == Decimal('118.00')

    def test_producto_exento_indicador_4_sin_itbis(self, config_negocio):
        """Tasa 0% (exento): indicador 4, sin ITBIS, monto va a monto_exento."""
        config_negocio.itbis_porcentaje_global = Decimal('0.00')
        config_negocio.save()
        cache.clear()

        venta = crear_venta_con_detalles(items=[
            {'precio_unitario': Decimal('100.00'), 'cantidad': 2},
        ])
        result = venta_a_ecf_data(venta, tipo_ecf='32')

        item = result['items'][0]
        assert item['indicador_facturacion'] == 4
        assert result['totales']['monto_exento'] == Decimal('200.00')
        assert result['totales']['total_itbis'] == Decimal('0.00')
        assert result['totales']['monto_gravado_18'] == Decimal('0.00')

    def test_tasa_16_indicador_2(self, config_negocio):
        """Tasa 16% (productos específicos): indicador 2, total_itbis_16."""
        config_negocio.itbis_incluido_en_precio = False
        config_negocio.itbis_porcentaje_global = Decimal('16.00')
        config_negocio.save()
        cache.clear()

        venta = crear_venta_con_detalles(items=[
            {'precio_unitario': Decimal('100.00'), 'cantidad': 1},
        ])
        result = venta_a_ecf_data(venta, tipo_ecf='32')

        item = result['items'][0]
        assert item['indicador_facturacion'] == 2
        assert result['totales']['monto_gravado_16'] == Decimal('100.00')
        assert result['totales']['total_itbis_16'] == Decimal('16.00')
        assert result['totales']['monto_gravado_18'] == Decimal('0.00')

    def test_descuento_modo_itbis_incluido_se_desglosa(self, config_negocio):
        """
        En modo ITBIS incluido, un descuento del POS también incluye ITBIS
        y debe desglosarse: descuento_pos / 1.18 = descuento sin ITBIS.

        Ejemplo: 1 unidad a $118 - $11.80 descuento (ambos con ITBIS).
        Base: precio = 100, descuento = 10, monto_item = 90.
        """
        # config_negocio ya viene con itbis_incluido=True, pct=18
        venta = crear_venta_con_detalles(items=[{
            'precio_unitario': Decimal('118.00'),
            'cantidad': 1,
            'descuento_monto': Decimal('11.80'),
        }])
        result = venta_a_ecf_data(venta, tipo_ecf='32')

        item = result['items'][0]
        assert item['precio_unitario'] == Decimal('100.00')
        assert item['descuento_monto'] == Decimal('10.00')
        assert item['monto_item'] == Decimal('90.00')

    def test_descuento_modo_itbis_sumado_se_aplica_directo(self, config_negocio):
        """
        En modo ITBIS sumado, el descuento se aplica directo sobre la base.

        Ejemplo: 1 unidad a $100 - $10 descuento.
        Base: precio = 100, descuento = 10, monto_item = 90.
        """
        config_negocio.itbis_incluido_en_precio = False
        config_negocio.save()
        cache.clear()

        venta = crear_venta_con_detalles(items=[{
            'precio_unitario': Decimal('100.00'),
            'cantidad': 1,
            'descuento_monto': Decimal('10.00'),
        }])
        result = venta_a_ecf_data(venta, tipo_ecf='32')

        item = result['items'][0]
        assert item['precio_unitario'] == Decimal('100.00')
        assert item['descuento_monto'] == Decimal('10.00')
        assert item['monto_item'] == Decimal('90.00')


# =============================================================================
# 4. Indicador de facturación (función pura)
# =============================================================================

class TestIndicadorFacturacion:
    """`_indicador_facturacion(tasa_decimal)` mapea tasa al código DGII."""

    def test_18_pct_retorna_1(self):
        assert _indicador_facturacion(Decimal('18')) == 1
        assert _indicador_facturacion(Decimal('18.00')) == 1

    def test_16_pct_retorna_2(self):
        assert _indicador_facturacion(Decimal('16')) == 2
        assert _indicador_facturacion(Decimal('16.00')) == 2

    def test_0_pct_retorna_4(self):
        assert _indicador_facturacion(Decimal('0')) == 4
        assert _indicador_facturacion(Decimal('0.00')) == 4

    def test_tasa_no_estandar_retorna_1_con_warning(self, monkeypatch):
        """
        Tasa fuera de {0, 16, 18} cae a 1 (18%) y loguea warning.
 
        Usamos monkeypatch + MagicMock en lugar de caplog porque caplog
        depende de la configuración de propagate/handlers/level del
        logger 'ecf.mapper' en settings.py, y verificar comportamiento
        de logging no debería acoplarse a esa configuración. Con el mock
        verificamos el contrato directamente: el código pidió un logger
        llamado 'ecf.mapper' y le hizo .warning() con el mensaje esperado.
        """
        from unittest.mock import MagicMock
        import logging as _logging
 
        mock_logger = MagicMock()
        original_get_logger = _logging.getLogger
 
        def patched_get_logger(name=None):
            if name == 'ecf.mapper':
                return mock_logger
            return original_get_logger(name)
 
        monkeypatch.setattr(_logging, 'getLogger', patched_get_logger)
 
        result = _indicador_facturacion(Decimal('10'))
 
        assert result == 1
        mock_logger.warning.assert_called_once()
        mensaje = mock_logger.warning.call_args[0][0]
        assert 'no estándar' in mensaje or 'estandar' in mensaje.lower()
 


# =============================================================================
# 5. Totales agregados
# =============================================================================

class TestTotalesAgregados:
    """
    Con varias líneas a tasas distintas, los totales se agregan por tasa
    y la suma final cierra.
    """

    def test_lineas_mismo_pct_se_suman(self, config_negocio):
        config_negocio.itbis_incluido_en_precio = False
        config_negocio.save()
        cache.clear()

        venta = crear_venta_con_detalles(items=[
            {'precio_unitario': Decimal('100.00'), 'cantidad': 1},
            {'precio_unitario': Decimal('200.00'), 'cantidad': 1},
        ])
        result = venta_a_ecf_data(venta, tipo_ecf='32')

        assert result['totales']['monto_gravado_18'] == Decimal('300.00')
        assert result['totales']['total_itbis_18'] == Decimal('54.00')
        assert result['totales']['total_itbis'] == Decimal('54.00')
        assert result['totales']['monto_total'] == Decimal('354.00')

    def test_invariante_total_iguala_suma_de_partes(self, config_negocio):
        """
        Invariante fiscal:
            monto_total = monto_gravado_18 + monto_gravado_16 + monto_exento + total_itbis

        Si esto se rompe, DGII rechaza por inconsistencia interna.
        """
        venta = crear_venta_con_detalles(items=[
            {'precio_unitario': Decimal('118.00'), 'cantidad': 3},
            {'precio_unitario': Decimal('236.00'), 'cantidad': 2},
        ])
        result = venta_a_ecf_data(venta, tipo_ecf='32')
        totales = result['totales']

        suma_partes = (
            totales['monto_gravado_18']
            + totales['monto_gravado_16']
            + totales['monto_exento']
            + totales['total_itbis']
        )
        assert suma_partes == totales['monto_total']

    def test_total_itbis_es_suma_de_18_y_16(self, config_negocio):
        """total_itbis debe ser literalmente total_itbis_18 + total_itbis_16."""
        venta = crear_venta_con_detalles()
        result = venta_a_ecf_data(venta, tipo_ecf='32')
        totales = result['totales']

        assert totales['total_itbis'] == totales['total_itbis_18'] + totales['total_itbis_16']


# =============================================================================
# 6. Redondeo half-up
# =============================================================================

class TestRedondeoHalfUp:
    """
    DGII espera 2 decimales con redondeo half-up. Python por default
    usa banker's rounding (half-even), que da resultados distintos en
    el caso .005. Verificamos que el mapper hace half-up.
    """

    def test_q_redondea_005_a_01_no_a_00(self):
        """
        `_q()` debe usar half-up: 0.005 → 0.01 (no 0.00 como haría
        banker's rounding por default de Python).
 
        Probamos la función pura porque inyectar un valor con 3 decimales
        a través del flujo Venta → DetalleVenta → mapper es imposible:
        DetalleVenta.precio_unitario es DecimalField(decimal_places=2)
        y Django recorta al guardar. Por eso testeamos el invariante
        directamente en la función responsable del redondeo.
 
        Este invariante es crítico: DGII rechaza por desfase de 0.01
        si los cálculos del POS no coinciden con su redondeo half-up.
        """
        from apps.facturacion_electronica.services.venta_to_ecf import _q
 
        # El caso clásico .005 — banker's daría 0.00, half-up da 0.01
        assert _q(Decimal('100.005')) == Decimal('100.01')
        assert _q(Decimal('0.005')) == Decimal('0.01')
 
        # Sanity: valores que NO terminan en .5 redondean normal
        assert _q(Decimal('100.004')) == Decimal('100.00')
        assert _q(Decimal('100.006')) == Decimal('100.01')
        

    def test_division_por_factor_redondea_2_decimales(self, config_negocio):
        """100 / 1.18 = 84.74576... → debe quedar 84.75 con half-up."""
        venta = crear_venta_con_detalles(items=[
            {'precio_unitario': Decimal('100.00'), 'cantidad': 1},
        ])
        result = venta_a_ecf_data(venta, tipo_ecf='32')

        item = result['items'][0]
        assert item['precio_unitario'] == Decimal('84.75')


# =============================================================================
# 7. Normalización de RNC
# =============================================================================

class TestNormalizacionRNC:
    """
    `_normalizar_rnc()` deja solo dígitos. La validación de longitud
    (9 u 11) la hace MSeller, no este mapper.
    """

    def test_rnc_con_guiones_se_limpia(self):
        assert _normalizar_rnc('131-12345-6') == '131123456'

    def test_rnc_con_espacios_se_limpia(self):
        assert _normalizar_rnc(' 40211111111 ') == '40211111111'

    def test_rnc_con_caracteres_mixtos_solo_deja_digitos(self):
        """Caracteres no numéricos arbitrarios se descartan."""
        assert _normalizar_rnc('40A2.11_111-111') == '40211111111'

    def test_rnc_none_devuelve_string_vacio(self):
        assert _normalizar_rnc(None) == ''

    def test_rnc_string_vacio_devuelve_string_vacio(self):
        assert _normalizar_rnc('') == ''

    def test_rnc_ya_limpio_se_devuelve_igual(self):
        assert _normalizar_rnc('131123456') == '131123456'


# =============================================================================
# 8. Edge cases y estructura del resultado
# =============================================================================

class TestEdgeCases:
    """Casos límite y propiedades estructurales del resultado."""

    def test_venta_sin_detalles_levanta_value_error(self, config_negocio):
        """Una Venta sin DetalleVenta no es facturable. Falla explícito
        antes de armar payload, para que el procesador no llegue a MSeller."""
        venta = VentaFactory()  # sin detalles
        with pytest.raises(ValueError, match='no tiene detalles'):
            venta_a_ecf_data(venta, tipo_ecf='32')

    def test_nombre_producto_largo_se_trunca_a_80_chars(self, config_negocio):
        """DGII tiene límite en NombreItem. El mapper trunca defensivamente."""
        nombre_largo = 'A' * 150
        producto = ProductoFactory(nombre=nombre_largo)
        venta = VentaFactory()
        DetalleVentaFactory(venta=venta, producto=producto)

        result = venta_a_ecf_data(venta, tipo_ecf='32')

        assert len(result['items'][0]['nombre_item']) == 80
        assert result['items'][0]['nombre_item'] == 'A' * 80

    def test_metadata_incluye_venta_id_y_numero(self, config_negocio):
        """Metadata propaga referencias a la venta para troubleshooting."""
        venta = crear_venta_con_detalles(numero_venta='V-TEST-METADATA-001')
        result = venta_a_ecf_data(venta, tipo_ecf='32')

        assert result['metadata']['venta_id'] == venta.id
        assert result['metadata']['numero_venta'] == 'V-TEST-METADATA-001'
        assert result['metadata']['fecha_emision'] == venta.fecha_venta.date()

    def test_metadata_tipo_pago_contado_y_credito_desde_venta(self, config_negocio):
        contado = crear_venta_con_detalles()
        contado_data = venta_a_ecf_data(contado, tipo_ecf='32')

        cliente = ClienteConRNCFactory()
        credito = crear_venta_con_detalles(
            cliente=cliente,
            condicion_pago='CREDITO',
            total=Decimal('118.00'),
        )
        metodo = MetodoPlazoCredito.objects.create(
            nombre='Credito fiscal metadata',
            tipo=MetodoPlazoCredito.TIPO_VENCIMIENTO_UNICO,
            dias_vencimiento=30,
        )
        CuentaPorCobrar.objects.create(
            cliente=cliente,
            venta=credito,
            metodo_plazo=metodo,
            total=Decimal('118.00'),
            monto_inicial=Decimal('0.00'),
            saldo=Decimal('118.00'),
            fecha_limite=date(2026, 7, 15),
            creado_por=credito.usuario,
        )
        credito_data = venta_a_ecf_data(credito, tipo_ecf='31')

        assert contado_data['metadata']['tipo_pago'] == 1
        assert contado_data['metadata']['fecha_limite_pago'] is None
        assert credito_data['metadata']['tipo_pago'] == 2
        assert credito_data['metadata']['fecha_limite_pago'] == date(2026, 7, 15)

    def test_nc_tipo_34_propaga_metadata_de_referencia(self, config_negocio):
        """Para NC tipo 34, motivo y eNCF referenciado quedan en metadata."""
        venta = crear_venta_con_detalles()
        result = venta_a_ecf_data(
            venta,
            tipo_ecf='34',
            motivo_nc='Anulacion por cliente',
            encf_referencia='E320000000042',
            codigo_modificacion_nc=1,
        )

        assert result['metadata']['motivo_nc'] == 'Anulacion por cliente'
        assert result['metadata']['encf_referencia'] == 'E320000000042'
        assert result['metadata']['codigo_modificacion_nc'] == 1

    def test_emisor_se_devuelve_como_none_a_proposito(self, config_negocio):
        """
        El mapper devuelve emisor=None. El orquestador del proveedor
        (MSellerEmisor) es quien inyecta los datos del Emisor. Esta
        separación mantiene el mapper agnóstico al proveedor.
        """
        venta = crear_venta_con_detalles()
        result = venta_a_ecf_data(venta, tipo_ecf='32')

        assert result['emisor'] is None

    def test_estructura_del_resultado_tiene_claves_esperadas(self, config_negocio):
        """Sanity check de la forma del dict que el mapper devuelve."""
        venta = crear_venta_con_detalles()
        result = venta_a_ecf_data(venta, tipo_ecf='32')

        # Top-level
        for key in ('tipo', 'emisor', 'comprador', 'items', 'totales', 'metadata'):
            assert key in result, f'Falta clave top-level: {key}'

        # Totales
        totales_esperados = {
            'monto_gravado_18', 'monto_gravado_16', 'monto_exento',
            'total_itbis_18', 'total_itbis_16', 'total_itbis', 'monto_total',
        }
        assert set(result['totales'].keys()) == totales_esperados

        # Items: cada uno con sus claves
        item_keys_esperadas = {
            'numero_linea', 'indicador_facturacion', 'nombre_item',
            'cantidad', 'precio_unitario', 'descuento_monto',
            'monto_item', 'itbis_pct',
        }
        for item in result['items']:
            assert set(item.keys()) == item_keys_esperadas
