"""
Tests del mapper especifico MSeller `build_mseller_payload()`.

Estos tests protegen el contrato mas fragil de la integracion: el JSON
que MSeller transforma a XML DGII. En el troubleshooting real vimos que
DGII/MSeller son sensibles al orden de campos dentro de Encabezado, IdDoc,
Comprador y Totales; por eso varias aserciones verifican orden de keys,
no solo presencia de datos.
"""
from decimal import Decimal

import pytest
from django.core.cache import cache

from apps.facturacion_electronica.integrations.mseller_payload import (
    build_mseller_payload,
)
from apps.facturacion_electronica.services.venta_to_ecf import venta_a_ecf_data

from .factories import (
    ClienteConRNCFactory,
    ClientePersonalSinRNCFactory,
    crear_venta_con_detalles,
)


pytestmark = pytest.mark.django_db


def _inyectar_emisor(ecf_data: dict, **overrides) -> dict:
    """
    Agrega al dict neutro la seccion emisor que normalmente inyecta
    MSellerEmisor. Mantiene los valores de tipo 31 validados en TesteCF.
    """
    emisor = {
        'rnc': '131822096',
        'razon_social': 'Tabacalera Genao SRL',
        'nombre_comercial': 'Tabacalera Genao',
        'direccion': 'Calle Test 123',
        'fecha_vencimiento_secuencia': '31-12-2028',
        'indicador_envio_diferido': None,
        'tipo_ingresos': '01',
        'tipo_pago': 1,
        'fecha_limite_pago': None,
    }
    emisor.update(overrides)
    ecf_data['emisor'] = emisor
    return ecf_data


class TestPayloadTipo32:
    """Factura de consumo: comprador opcional y payload minimo."""

    def test_tipo_32_sin_comprador_omite_bloque_y_respeta_orden(self, config_negocio):
        venta = crear_venta_con_detalles(cliente=None, items=[
            {'precio_unitario': Decimal('118.00'), 'cantidad': 1},
        ])
        ecf_data = _inyectar_emisor(venta_a_ecf_data(venta, tipo_ecf='32'))

        payload = build_mseller_payload(ecf_data, encf='E320000000001')
        ecf = payload['ECF']
        encabezado = ecf['Encabezado']

        assert list(encabezado.keys()) == ['Version', 'IdDoc', 'Emisor', 'Totales']
        assert 'Comprador' not in encabezado
        assert list(ecf.keys()) == ['Encabezado', 'DetallesItems']

        id_doc = encabezado['IdDoc']
        assert list(id_doc.keys()) == [
            'TipoeCF',
            'eNCF',
            'IndicadorEnvioDiferido',
            'IndicadorMontoGravado',
            'TipoIngresos',
            'TipoPago',
        ]
        assert id_doc == {
            'TipoeCF': '32',
            'eNCF': 'E320000000001',
            'IndicadorEnvioDiferido': 0,
            'IndicadorMontoGravado': 0,
            'TipoIngresos': '01',
            'TipoPago': 1,
        }

    def test_tipo_32_totales_omite_campos_cero_que_no_aplican(self, config_negocio):
        venta = crear_venta_con_detalles(items=[
            {'precio_unitario': Decimal('118.00'), 'cantidad': 1},
        ])
        ecf_data = _inyectar_emisor(venta_a_ecf_data(venta, tipo_ecf='32'))

        totales = build_mseller_payload(
            ecf_data, encf='E320000000001'
        )['ECF']['Encabezado']['Totales']

        assert list(totales.keys()) == [
            'MontoGravadoTotal',
            'MontoGravadoI1',
            'ITBIS1',
            'TotalITBIS',
            'TotalITBIS1',
            'MontoTotal',
        ]
        assert totales['MontoGravadoTotal'] == 100.0
        assert totales['MontoGravadoI1'] == 100.0
        assert totales['ITBIS1'] == 18
        assert totales['TotalITBIS'] == 18.0
        assert totales['TotalITBIS1'] == 18.0
        assert totales['MontoTotal'] == 118.0
        assert 'MontoGravadoI2' not in totales
        assert 'MontoExento' not in totales

    def test_items_convierte_decimals_a_numeros_json(self, config_negocio):
        venta = crear_venta_con_detalles(items=[{
            'precio_unitario': Decimal('118.00'),
            'cantidad': 2,
            'descuento_monto': Decimal('11.80'),
        }])
        ecf_data = _inyectar_emisor(venta_a_ecf_data(venta, tipo_ecf='32'))

        item = build_mseller_payload(
            ecf_data, encf='E320000000001'
        )['ECF']['DetallesItems']['Item'][0]

        assert item.pop('NombreItem').startswith('Producto Test ')
        assert item == {
            'NumeroLinea': 1,
            'IndicadorFacturacion': 1,
            'IndicadorBienoServicio': 1,
            'CantidadItem': 2.0,
            'PrecioUnitarioItem': 100.0,
            'MontoItem': 190.0,
            'DescuentoMonto': 10.0,
        }


class TestPayloadTipo31:
    """Credito fiscal: comprador obligatorio y orden sensible."""

    def test_tipo_31_orden_de_encabezado_iddoc_comprador_y_totales(self, config_negocio):
        cliente = ClienteConRNCFactory(
            nombre='Empresa Acme SRL',
            cedula_rnc='131-12345-6',
            direccion='Av. Independencia 100',
        )
        venta = crear_venta_con_detalles(cliente=cliente, items=[
            {'precio_unitario': Decimal('118.00'), 'cantidad': 1},
        ])
        ecf_data = _inyectar_emisor(venta_a_ecf_data(venta, tipo_ecf='31'))

        payload = build_mseller_payload(ecf_data, encf='E310000000013')
        ecf = payload['ECF']
        encabezado = ecf['Encabezado']

        assert list(encabezado.keys()) == [
            'Version',
            'IdDoc',
            'Emisor',
            'Comprador',
            'Totales',
        ]
        assert list(encabezado['IdDoc'].keys()) == [
            'TipoeCF',
            'eNCF',
            'FechaVencimientoSecuencia',
            'IndicadorEnvioDiferido',
            'IndicadorMontoGravado',
            'TipoIngresos',
            'TipoPago',
        ]
        assert encabezado['IdDoc'] == {
            'TipoeCF': '31',
            'eNCF': 'E310000000013',
            'FechaVencimientoSecuencia': '31-12-2028',
            'IndicadorEnvioDiferido': 1,
            'IndicadorMontoGravado': 0,
            'TipoIngresos': '01',
            'TipoPago': 1,
        }
        assert list(encabezado['Comprador'].keys()) == [
            'RNCComprador',
            'RazonSocialComprador',
            'DireccionComprador',
        ]
        assert encabezado['Comprador'] == {
            'RNCComprador': '131123456',
            'RazonSocialComprador': 'Empresa Acme SRL',
            'DireccionComprador': 'Av. Independencia 100',
        }

        assert list(ecf.keys()) == ['Encabezado', 'DetallesItems']
        assert 'Paginacion' not in ecf
        assert 'TotalPaginas' not in ecf
        assert 'FechaHoraFirma' not in ecf

    def test_tipo_31_credito_agrega_fecha_limite_pago_al_final(self, config_negocio):
        cliente = ClienteConRNCFactory()
        venta = crear_venta_con_detalles(cliente=cliente, condicion_pago='CREDITO')
        ecf_data = _inyectar_emisor(
            venta_a_ecf_data(venta, tipo_ecf='31'),
            tipo_pago=2,
            fecha_limite_pago='31-12-2028',
        )

        id_doc = build_mseller_payload(
            ecf_data, encf='E310000000014'
        )['ECF']['Encabezado']['IdDoc']

        assert list(id_doc.keys()) == [
            'TipoeCF',
            'eNCF',
            'FechaVencimientoSecuencia',
            'IndicadorEnvioDiferido',
            'IndicadorMontoGravado',
            'TipoIngresos',
            'TipoPago',
            'FechaLimitePago',
        ]
        assert id_doc['TipoPago'] == 2
        assert id_doc['FechaLimitePago'] == '31-12-2028'

    def test_tipo_31_prefiere_tipo_pago_de_metadata_sobre_emisor(self, config_negocio):
        cliente = ClienteConRNCFactory()
        venta = crear_venta_con_detalles(cliente=cliente)
        ecf_data = _inyectar_emisor(
            venta_a_ecf_data(venta, tipo_ecf='31'),
            tipo_pago=1,
            fecha_limite_pago=None,
        )
        ecf_data['metadata']['tipo_pago'] = 2
        ecf_data['metadata']['fecha_limite_pago'] = '15-07-2026'

        id_doc = build_mseller_payload(
            ecf_data, encf='E310000000015'
        )['ECF']['Encabezado']['IdDoc']

        assert id_doc['TipoPago'] == 2
        assert id_doc['FechaLimitePago'] == '15-07-2026'


class TestPayloadTipo34:
    """Nota de credito: referencia al e-CF original."""

    def test_tipo_34_incluye_informacion_referencia(self, config_negocio):
        cliente = ClienteConRNCFactory(cedula_rnc='131123456')
        venta = crear_venta_con_detalles(cliente=cliente)
        ecf_data = _inyectar_emisor(
            venta_a_ecf_data(
                venta,
                tipo_ecf='34',
                motivo_nc='Anulacion total',
                encf_referencia='E320000000099',
                codigo_modificacion_nc=1,
            )
        )

        payload = build_mseller_payload(ecf_data, encf='E340000000001')
        ecf = payload['ECF']

        assert list(ecf.keys()) == [
            'Encabezado',
            'DetallesItems',
            'InformacionReferencia',
        ]
        assert ecf['Encabezado']['IdDoc']['IndicadorNotaCredito'] == '0'
        assert ecf['InformacionReferencia'] == {
            'NCFModificado': 'E320000000099',
            'CodigoModificacion': 1,
            'RazonModificacion': 'Anulacion total',
            'FechaNCFModificado': ecf_data['metadata']['fecha_emision'].strftime('%d-%m-%Y'),
            'RNCOtroContribuyente': '131123456',
        }


class TestPayloadEdgeCases:
    """Contratos defensivos del builder MSeller."""

    def test_emisor_none_levanta_value_error(self, config_negocio):
        venta = crear_venta_con_detalles()
        ecf_data = venta_a_ecf_data(venta, tipo_ecf='32')

        with pytest.raises(ValueError, match='emisor'):
            build_mseller_payload(ecf_data, encf='E320000000001')

    def test_monto_exento_solo_se_envia_si_aplica(self, config_negocio):
        config_negocio.itbis_porcentaje_global = Decimal('0.00')
        config_negocio.save()
        cache.clear()

        cliente = ClientePersonalSinRNCFactory()
        venta = crear_venta_con_detalles(cliente=cliente, items=[
            {'precio_unitario': Decimal('100.00'), 'cantidad': 2},
        ])
        ecf_data = _inyectar_emisor(venta_a_ecf_data(venta, tipo_ecf='32'))

        totales = build_mseller_payload(
            ecf_data, encf='E320000000001'
        )['ECF']['Encabezado']['Totales']

        assert list(totales.keys()) == ['MontoExento', 'MontoTotal']
        assert totales['MontoExento'] == 200.0
        assert totales['MontoTotal'] == 200.0
