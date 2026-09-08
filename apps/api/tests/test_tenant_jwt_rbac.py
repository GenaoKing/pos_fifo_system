from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase

from apps.tenancy.authentication import TenantJWTAuthentication


class TenantJwtRbacGateTests(SimpleTestCase):
    @patch('apps.permisos.engine.permisos_de_usuario', return_value={'productos.ver'})
    @patch('apps.tenancy.authentication.get_user_model')
    @patch('apps.tenancy.authentication._autorizar_tenant')
    @patch('apps.tenancy.authentication.set_current_tenant', return_value=('a', 'b'))
    @patch('apps.tenancy.authentication.bind_tenant_context_to_request')
    @patch('apps.tenancy.authentication.configure_tenant_database')
    @patch('apps.tenancy.authentication.Identity.objects')
    def test_rol_no_admin_con_rbac_pasa_gate_jwt(
        self, identities, configurar, bind, contexto, autorizar, get_model,
        permisos,
    ):
        identity = Mock(pk=1, is_global=False)
        identities.using.return_value.filter.return_value.first.return_value = identity
        tenant = Mock(tenant_key='demo')
        configurar.return_value = (tenant, 'tenant_demo')
        user = Mock(pk=3, rol='CAJERA', activo=True)
        get_model.return_value.objects.filter.return_value.first.return_value = user
        token = {
            'identity_id': 1, 'tenant_key': 'demo', 'username': 'cajera',
        }
        result = TenantJWTAuthentication().get_user_for_token(token, Mock())
        self.assertIs(result, user)
        permisos.assert_called_once()
