"""
Tests de `MSellerEmisor`.

No hacen llamadas HTTP reales. Mockean el cliente MSeller para verificar
que el orquestador:
- inyecta datos del Emisor,
- asigna eNCF local,
- fuerza validar=False en el flujo normal,
- traduce respuestas y errores a DTOs neutros,
- mapea estados de consulta MSeller al vocabulario EstadosECF.
"""
from unittest.mock import MagicMock

import pytest

from apps.facturacion_electronica.interfaces import EstadosECF
from apps.facturacion_electronica.models import ECF
from apps.facturacion_electronica.services.mseller_emisor import MSellerEmisor
from apps.facturacion_electronica.services.mseller_http_client import (
    MSellerAuthError,
    MSellerError,
    MSellerValidationError,
)

from .factories import EmisorFactory, crear_venta_con_detalles


pytestmark = pytest.mark.django_db


@pytest.fixture
def mseller_env(monkeypatch):
    """Variables falsas para que MSellerConfig.from_emisor_config resuelva."""
    monkeypatch.setenv('TEST_MSELLER_EMAIL', 'test@example.com')
    monkeypatch.setenv('TEST_MSELLER_PASSWORD', 'secret')
    monkeypatch.setenv('TEST_MSELLER_API_KEY', 'api-key')


@pytest.fixture
def emisor(mseller_env):
    return EmisorFactory(
        rnc='131822096',
        razon_social='Tabacalera Genao SRL',
        direccion='Calle Test 123',
    )


@pytest.fixture
def mseller_emisor(emisor):
    emisor_impl = MSellerEmisor(emisor)
    emisor_impl.http = MagicMock()
    return emisor_impl


class TestEmitir:
    """Emision normal y errores de proveedor."""

    def test_emitir_inyecta_emisor_envia_payload_y_fuerza_validar_false(
        self,
        mseller_emisor,
        emisor,
        config_negocio,
    ):
        venta = crear_venta_con_detalles()
        ecf_data = mseller_emisor._ecf_data_para_venta(venta, '32')
        ecf_data['emisor'] = None
        mseller_emisor.http.enviar_documento.return_value = {
            'ecf': 'E320000000001',
            'internalTrackId': 'track-1',
            'securityCode': 'ABC123',
            'qr_url': 'https://dgii.test/qr',
        }

        resultado = mseller_emisor.emitir(ecf_data)

        assert resultado.exitoso is True
        assert resultado.estado_inicial == EstadosECF.ENVIADO
        assert resultado.encf == 'E320000000001'
        assert resultado.track_id == 'track-1'
        assert resultado.raw_response['securityCode'] == 'ABC123'

        mseller_emisor.http.enviar_documento.assert_called_once()
        payload_enviado = mseller_emisor.http.enviar_documento.call_args.args[0]
        kwargs = mseller_emisor.http.enviar_documento.call_args.kwargs
        assert kwargs == {'validar': False}

        encabezado = payload_enviado['ECF']['Encabezado']
        assert encabezado['Emisor']['RNCEmisor'] == emisor.rnc
        assert encabezado['Emisor']['RazonSocialEmisor'] == emisor.razon_social
        assert encabezado['IdDoc']['eNCF'] == 'E320000000001'

    def test_emitir_incrementa_secuencia_desde_ultimo_ecf(
        self,
        mseller_emisor,
        emisor,
        config_negocio,
    ):
        venta = crear_venta_con_detalles()
        ECF.objects.create(
            emisor=emisor,
            venta=venta,
            tipo='32',
            encf='E320000000041',
            estado=EstadosECF.APROBADO,
            proveedor_usado='mseller',
        )
        mseller_emisor.http.enviar_documento.return_value = {
            'internalTrackId': 'track-42',
            'securityCode': 'SEC42',
        }

        resultado = mseller_emisor.emitir(mseller_emisor._ecf_data_para_venta(venta, '32'))

        payload = mseller_emisor.http.enviar_documento.call_args.args[0]
        assert payload['ECF']['Encabezado']['IdDoc']['eNCF'] == 'E320000000042'
        assert resultado.encf == 'E320000000042'

    def test_emitir_validation_error_retorna_rechazado(
        self,
        mseller_emisor,
        config_negocio,
    ):
        venta = crear_venta_con_detalles()
        exc = MSellerValidationError(
            'Documento invalido',
            validation_errors=[{'field': 'Totales', 'message': 'No cuadra'}],
        )
        mseller_emisor.http.enviar_documento.side_effect = exc

        resultado = mseller_emisor.emitir(mseller_emisor._ecf_data_para_venta(venta, '32'))

        assert resultado.exitoso is False
        assert resultado.estado_inicial == EstadosECF.RECHAZADO
        assert resultado.encf == 'E320000000001'
        assert resultado.raw_response == {
            'validation_errors': [{'field': 'Totales', 'message': 'No cuadra'}]
        }

    def test_emitir_auth_error_retorna_error_recuperable(
        self,
        mseller_emisor,
        config_negocio,
    ):
        venta = crear_venta_con_detalles()
        mseller_emisor.http.enviar_documento.side_effect = MSellerAuthError(
            'API key invalida',
            status_code=403,
            response_body={'message': 'Forbidden'},
        )

        resultado = mseller_emisor.emitir(mseller_emisor._ecf_data_para_venta(venta, '32'))

        assert resultado.exitoso is False
        assert resultado.estado_inicial == EstadosECF.ERROR
        assert resultado.mensaje == 'Auth error: API key invalida'
        assert resultado.raw_response == {'message': 'Forbidden'}

    def test_emitir_error_transitorio_retorna_error(
        self,
        mseller_emisor,
        config_negocio,
    ):
        venta = crear_venta_con_detalles()
        mseller_emisor.http.enviar_documento.side_effect = MSellerError(
            'Timeout MSeller',
            response_body={'detail': 'timeout'},
        )

        resultado = mseller_emisor.emitir(mseller_emisor._ecf_data_para_venta(venta, '32'))

        assert resultado.exitoso is False
        assert resultado.estado_inicial == EstadosECF.ERROR
        assert resultado.mensaje == 'Timeout MSeller'
        assert resultado.raw_response == {'detail': 'timeout'}


class TestConsultarEstado:
    """Polling de estado remoto y mapeo a EstadosECF."""

    @pytest.mark.parametrize(
        ('status_remoto', 'estado_esperado'),
        [
            ('Aceptado', EstadosECF.APROBADO),
            ('Aceptado Condicional', EstadosECF.APROBADO_CONDICIONAL),
            ('Rechazado', EstadosECF.RECHAZADO),
            ('RECIBIDO', EstadosECF.ENVIADO),
            ('PROCESANDO', EstadosECF.EN_PROCESO),
            ('En Proceso', EstadosECF.EN_PROCESO),
            ('ERROR', EstadosECF.ERROR),
            ('Estado raro de MSeller', EstadosECF.EN_PROCESO),
            (None, EstadosECF.ENVIADO),
        ],
    )
    def test_consultar_estado_mapea_status_mseller(
        self,
        mseller_emisor,
        status_remoto,
        estado_esperado,
    ):
        mseller_emisor.http.consultar_documento.return_value = {
            'status': status_remoto,
            'ncf': 'E320000000001',
            'securityCode': 'ABC123',
            'internalTrackId': 'track-1',
        }

        estado = mseller_emisor.consultar_estado('E320000000001')

        assert estado.estado == estado_esperado
        assert estado.encf == 'E320000000001'
        assert estado.codigo_seguridad == 'ABC123'
        assert estado.track_id == 'track-1'
        assert estado.raw_response['status'] == status_remoto
        mseller_emisor.http.consultar_documento.assert_called_once_with('E320000000001')

    def test_consultar_estado_error_mseller_retorna_error(self, mseller_emisor):
        mseller_emisor.http.consultar_documento.side_effect = MSellerError(
            'MSeller no responde',
            response_body={'message': 'down'},
        )

        estado = mseller_emisor.consultar_estado('E320000000001')

        assert estado.estado == EstadosECF.ERROR
        assert estado.track_id == 'E320000000001'
        assert estado.mensaje == 'MSeller no responde'
        assert estado.raw_response == {'message': 'down'}


class TestNotaCreditoYXML:
    """Metodos complementarios de la interfaz."""

    def test_emitir_nota_credito_usa_venta_original_y_tipo_34(
        self,
        mseller_emisor,
        emisor,
        config_negocio,
        monkeypatch,
    ):
        venta = crear_venta_con_detalles()
        ecf_original = ECF.objects.create(
            emisor=emisor,
            venta=venta,
            tipo='32',
            encf='E320000000099',
            estado=EstadosECF.APROBADO,
            proveedor_usado='mseller',
        )
        emit_mock = MagicMock()
        monkeypatch.setattr(mseller_emisor, 'emitir', emit_mock)

        mseller_emisor.emitir_nota_credito(ecf_original, 'Anulacion total')

        emit_mock.assert_called_once()
        ecf_data = emit_mock.call_args.args[0]
        assert ecf_data['tipo'] == '34'
        assert ecf_data['metadata']['motivo_nc'] == 'Anulacion total'
        assert ecf_data['metadata']['encf_referencia'] == 'E320000000099'
        assert ecf_data['metadata']['codigo_modificacion_nc'] == 1
        assert ecf_data['emisor']['rnc'] == emisor.rnc

    def test_emitir_nota_credito_sin_venta_retorna_error(self, mseller_emisor, emisor):
        ecf_original = ECF.objects.create(
            emisor=emisor,
            venta=None,
            tipo='32',
            encf='E320000000099',
            estado=EstadosECF.APROBADO,
            proveedor_usado='mseller',
        )

        resultado = mseller_emisor.emitir_nota_credito(ecf_original, 'Anulacion total')

        assert resultado.exitoso is False
        assert resultado.estado_inicial == EstadosECF.ERROR
        assert 'no tiene venta' in resultado.mensaje

    def test_descargar_xml_aprobado_documenta_limitacion_mseller(self, mseller_emisor):
        with pytest.raises(NotImplementedError, match='XML firmado'):
            mseller_emisor.descargar_xml_aprobado('track-1')
