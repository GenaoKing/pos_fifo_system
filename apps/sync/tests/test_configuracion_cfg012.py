"""Consumidor sync de CFG-012: bootstrap explícito solo en el POS local."""
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.configuracion.models import ConfiguracionNegocio
from apps.sucursales.models import Sucursal
from apps.sync.engine import SyncEngine
from apps.sync.models import DiferidoSync, VersionMaestro


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.text = str(payload)

    def json(self):
        return self._payload


@override_settings(
    SYNC_ENABLED=True,
    CLOUD_API_URL='https://cloud.test',
    CLOUD_API_TOKEN='token-test',
    SUCURSAL_CODIGO='CFG-001',
)
class PullConfiguracionBootstrapTests(TestCase):
    def setUp(self):
        cache.clear()
        self.sucursal = Sucursal.objects.create(
            codigo='CFG-001', nombre='Sucursal CFG', activa=True,
        )
        self.engine = SyncEngine()

    def tearDown(self):
        cache.clear()

    @staticmethod
    def _item(**overrides):
        item = {
            'nombre_negocio': 'Cloud inicial',
            'pago_tarjeta': True,
            'fecha_modificacion': timezone.now().isoformat(),
        }
        item.update(overrides)
        return item

    @mock.patch('apps.sync.engine.requests.get')
    def test_primer_pull_crea_la_configuracion_de_la_sucursal(self, mock_get):
        mock_get.return_value = _Response([self._item()])

        resultado = self.engine._pull_configuracion()

        config = ConfiguracionNegocio.objects.get(sucursal=self.sucursal)
        self.assertEqual(resultado['count'], 1)
        self.assertEqual(config.nombre_negocio, 'Cloud inicial')
        self.assertTrue(config.pago_tarjeta)
        self.assertEqual(
            ConfiguracionNegocio.objects.filter(sucursal=self.sucursal).count(), 1,
        )

    @mock.patch('apps.sync.engine.requests.get')
    def test_replay_del_primer_pull_es_idempotente(self, mock_get):
        mock_get.return_value = _Response([self._item()])

        self.engine._pull_configuracion()
        primera = ConfiguracionNegocio.objects.get(sucursal=self.sucursal)
        self.engine._pull_configuracion()

        self.assertEqual(
            ConfiguracionNegocio.objects.filter(sucursal=self.sucursal).count(), 1,
        )
        self.assertEqual(
            ConfiguracionNegocio.objects.get(sucursal=self.sucursal).pk,
            primera.pk,
        )

    @mock.patch('apps.sync.engine.requests.get')
    def test_pull_posterior_actualiza_sin_duplicar(self, mock_get):
        config = ConfiguracionNegocio.bootstrap(sucursal=self.sucursal)
        mock_get.return_value = _Response([self._item(
            nombre_negocio='Cloud actualizado', pago_tarjeta=True,
        )])

        self.engine._pull_configuracion()

        config.refresh_from_db()
        self.assertEqual(config.nombre_negocio, 'Cloud actualizado')
        self.assertTrue(config.pago_tarjeta)
        self.assertEqual(
            ConfiguracionNegocio.objects.filter(sucursal=self.sucursal).count(), 1,
        )

    @mock.patch('apps.sync.engine.requests.get')
    def test_payload_invalido_no_muta_ni_avanza_el_cursor(self, mock_get):
        config = ConfiguracionNegocio.bootstrap(sucursal=self.sucursal)
        config.nombre_negocio = 'Estado local valido'
        config.save(update_fields=['nombre_negocio', 'fecha_modificacion'])
        mock_get.return_value = _Response([self._item(
            nombre_negocio='Nunca debe persistirse',
            pago_efectivo=False,
            pago_transferencia=False,
            pago_tarjeta=False,
        )])

        resultado = self.engine._pull_configuracion()

        config.refresh_from_db()
        cursor = VersionMaestro.objects.get(tabla='configuracion')
        self.assertEqual(config.nombre_negocio, 'Estado local valido')
        self.assertFalse(resultado['ok'])
        self.assertIn('configuracion invalida', resultado['bloqueo'])
        self.assertIsNone(cursor.ultima_version)
        self.assertIsNotNone(cursor.bloqueado_desde)
        self.assertFalse(DiferidoSync.objects.filter(tabla='configuracion').exists())
