import json
from unittest.mock import patch

from django.db import OperationalError
from django.test import SimpleTestCase, override_settings
from django.urls import resolve
from rest_framework.test import APIRequestFactory

from apps.api.views.health import health_check, health_live


class HealthCheckTests(SimpleTestCase):
    url = '/api/v1/health/'

    def setUp(self):
        self.factory = APIRequestFactory()

    @override_settings(
        APP_VERSION='test-version',
        GIT_COMMIT_SHA='test-sha',
        CLOUD_ENVIRONMENT='test',
    )
    @patch('apps.api.views.health.connection')
    def test_health_publico_con_db_ok(self, connection_mock):
        request = self.factory.get(self.url)
        response = health_check(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'ok')
        self.assertEqual(response.data['db'], 'ok')
        self.assertEqual(response.data['version'], 'test-version')
        self.assertEqual(response.data['commit'], 'test-sha')
        self.assertEqual(response.data['environment'], 'test')
        connection_mock.cursor.return_value.__enter__.return_value.execute.assert_called_once_with('SELECT 1')

    @patch('apps.api.views.health.connection')
    def test_health_responde_503_si_db_falla(self, connection_mock):
        connection_mock.cursor.side_effect = OperationalError('db unavailable')

        request = self.factory.get(self.url)
        response = health_check(request)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data['status'], 'degraded')
        self.assertEqual(response.data['db'], 'error')


class HealthLiveTests(SimpleTestCase):
    @override_settings(
        APP_VERSION='release-version',
        GIT_COMMIT_SHA='a' * 40,
        CLOUD_ENVIRONMENT='prod',
    )
    @patch('apps.api.views.health.connection')
    def test_live_returns_runtime_identity_without_database(self, connection_mock):
        connection_mock.cursor.side_effect = AssertionError('No DB for liveness')
        request = APIRequestFactory().get('/api/v1/health/live/')
        response = health_live(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {
            'status': 'ok', 'app': 'ok', 'environment': 'prod',
            'version': 'release-version', 'commit': 'a' * 40,
        })
        connection_mock.cursor.assert_not_called()

    def test_live_url_uses_the_lightweight_view(self):
        self.assertIs(resolve('/api/v1/health/live/').func, health_live)
