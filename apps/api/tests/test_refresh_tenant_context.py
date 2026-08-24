"""
BUG-E (docs/BUGS.md): `/api/v1/auth/refresh/` devolvia 500 en vez de 401
cuando el usuario del token no resolvia.

Causa raiz: `TokenRefreshSerializer.validate()` (la base de simplejwt)
resuelve el usuario del token con `Usuario.objects.get(id=...)` ANTES de que
`TenantTokenRefreshSerializer` estableciera el tenant. Bajo DB-per-tenant,
`Usuario` es dual-home: sin contexto activo esa consulta cae al control
plane, donde el usuario no existe, y el `DoesNotExist` subia sin capturar.

Estos tests no necesitan una BD de tenant real conectada: `configure_tenant_database`
solo REGISTRA el alias (un dict en `settings.DATABASES`), no abre conexion.
Aislar la llamada a la base de simplejwt con mocks prueba exactamente el
mecanismo del bug sin la complejidad de levantar una segunda base fisica.
"""
from unittest.mock import patch

from django.core.exceptions import ObjectDoesNotExist
from django.test import TestCase
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.api.auth_views import TenantTokenRefreshSerializer
from apps.tenancy.models import Identity, Membership, Tenant


def _desregistrar_alias(prefijo='tnt_'):
    from django.conf import settings
    from django.db import connections

    for alias in [a for a in list(connections.databases) if a.startswith(prefijo)]:
        connections.databases.pop(alias, None)
        settings.DATABASES.pop(alias, None)
        contenedor = getattr(connections, '_connections', None)
        if contenedor is not None and hasattr(contenedor, alias):
            delattr(contenedor, alias)


class RefreshResuelveUsuarioContraElTenantCorrectoTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            tenant_key='refresh-ctx', slug='refresh-ctx', nombre='Refresh Ctx',
            db_name='tnt_refresh_ctx',
        )
        self.identity = Identity.objects.create(email='admin@refresh-ctx.local')
        Membership.objects.create(
            identity=self.identity, tenant=self.tenant, username='admin', rol='ADMIN',
        )

    def tearDown(self):
        _desregistrar_alias()

    def _token_de_tenant(self, user_id=999):
        token = RefreshToken()
        token['identity_id'] = self.identity.pk
        token['tenant_key'] = self.tenant.tenant_key
        token['username'] = 'admin'
        token['user_id'] = user_id
        return token

    def test_activa_el_tenant_antes_de_llamar_a_la_base_de_simplejwt(self):
        """
        El orden es la esencia del fix: sin el tenant activo, la base de
        simplejwt busca en el control plane y falla. Este test prueba que
        `tenant_context` ya esta abierto en el momento en que se invoca la
        validacion base.
        """
        token = self._token_de_tenant()
        contexto_activo_durante_la_llamada = {}

        def fake_validate_base(self_serializer, attrs):
            from apps.tenancy.context import get_current_tenant_key
            contexto_activo_durante_la_llamada['tenant_key'] = get_current_tenant_key()
            return {'access': 'fake-access-token'}

        with patch(
            'rest_framework_simplejwt.serializers.TokenRefreshSerializer.validate',
            fake_validate_base,
        ), patch('apps.api.auth_views._autorizar_tenant', return_value=None):
            serializer = TenantTokenRefreshSerializer(data={'refresh': str(token)})
            self.assertTrue(serializer.is_valid(), serializer.errors)

        self.assertEqual(
            contexto_activo_durante_la_llamada['tenant_key'], self.tenant.tenant_key,
            'el tenant debia estar activo DURANTE la resolucion del usuario, no despues',
        )

    def test_usuario_del_token_ya_no_existe_da_401_no_500(self):
        """
        Regresion directa del sintoma: antes esto burbujeaba
        `Usuario.DoesNotExist` sin capturar y DRF lo convertia en 500.
        """
        from apps.usuarios.models import Usuario

        token = self._token_de_tenant()

        def fake_validate_base(self_serializer, attrs):
            raise Usuario.DoesNotExist('Usuario matching query does not exist.')

        with patch(
            'rest_framework_simplejwt.serializers.TokenRefreshSerializer.validate',
            fake_validate_base,
        ):
            serializer = TenantTokenRefreshSerializer(data={'refresh': str(token)})
            with self.assertRaises(InvalidToken):
                serializer.is_valid(raise_exception=True)

    def test_ObjectDoesNotExist_generico_tambien_se_traduce_a_401(self):
        """El catch es por la clase base, no por el nombre concreto del modelo."""
        token = self._token_de_tenant()

        def fake_validate_base(self_serializer, attrs):
            raise ObjectDoesNotExist('cualquier modelo dual-home')

        with patch(
            'rest_framework_simplejwt.serializers.TokenRefreshSerializer.validate',
            fake_validate_base,
        ):
            serializer = TenantTokenRefreshSerializer(data={'refresh': str(token)})
            with self.assertRaises(InvalidToken):
                serializer.is_valid(raise_exception=True)

    def test_tenant_inactivo_da_401_sin_tocar_la_base_de_simplejwt(self):
        self.tenant.activo = False
        self.tenant.save(update_fields=['activo'])
        token = self._token_de_tenant()

        with patch(
            'rest_framework_simplejwt.serializers.TokenRefreshSerializer.validate',
        ) as mock_validate:
            serializer = TenantTokenRefreshSerializer(data={'refresh': str(token)})
            with self.assertRaises(InvalidToken):
                serializer.is_valid(raise_exception=True)

        mock_validate.assert_not_called()

    def test_token_legacy_sin_identity_id_no_activa_ningun_tenant(self):
        """Compatibilidad: un token sin tenancy se comporta exactamente igual que antes."""
        token = RefreshToken()
        token['user_id'] = 1

        def fake_validate_base(self_serializer, attrs):
            from apps.tenancy.context import get_current_tenant_key
            self.assertIsNone(get_current_tenant_key())
            return {'access': 'fake-access-token'}

        with patch(
            'rest_framework_simplejwt.serializers.TokenRefreshSerializer.validate',
            fake_validate_base,
        ):
            serializer = TenantTokenRefreshSerializer(data={'refresh': str(token)})
            self.assertTrue(serializer.is_valid(), serializer.errors)
