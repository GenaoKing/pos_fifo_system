"""USR-014: atribucion de IP consistente entre portal y auditoria."""

from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.api.auth_views import get_client_ip
from apps.auditoria.models import get_client_ip as audit_client_ip


class PortalProxyIpTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(AUDITORIA_CONFIAR_EN_PROXY=False)
    def test_impersonacion_ignora_xff_sin_proxy_declarado(self):
        request = self.factory.post(
            '/', HTTP_X_FORWARDED_FOR='198.51.100.10', REMOTE_ADDR='10.0.0.9',
        )

        self.assertEqual(get_client_ip(request), '10.0.0.9')
        self.assertEqual(get_client_ip(request), audit_client_ip(request))

    @override_settings(AUDITORIA_CONFIAR_EN_PROXY=True)
    def test_impersonacion_usa_solo_el_extremo_agregado_por_proxy(self):
        request = self.factory.post(
            '/',
            HTTP_X_FORWARDED_FOR='198.51.100.10, 10.0.0.7',
            REMOTE_ADDR='10.0.0.9',
        )

        self.assertEqual(get_client_ip(request), '10.0.0.7')
        self.assertEqual(get_client_ip(request), audit_client_ip(request))
