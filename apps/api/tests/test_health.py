from unittest.mock import patch

from django.db import OperationalError
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory

from apps.api.views.health import health_check


class HealthCheckTests(TestCase):
    url = '/api/v1/health/'

    def setUp(self):
        self.factory = APIRequestFactory()

    @override_settings(
        APP_VERSION='test-version',
        GIT_COMMIT_SHA='test-sha',
        CLOUD_ENVIRONMENT='test',
    )
    def test_health_publico_con_db_ok(self):
        request = self.factory.get(self.url)
        response = health_check(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'ok')
        self.assertEqual(response.data['db'], 'ok')
        self.assertEqual(response.data['version'], 'test-version')
        self.assertEqual(response.data['commit'], 'test-sha')
        self.assertEqual(response.data['environment'], 'test')

    @patch('apps.api.views.health.connection.cursor')
    def test_health_responde_503_si_db_falla(self, cursor_mock):
        cursor_mock.side_effect = OperationalError('db unavailable')

        request = self.factory.get(self.url)
        response = health_check(request)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data['status'], 'degraded')
        self.assertEqual(response.data['db'], 'error')
