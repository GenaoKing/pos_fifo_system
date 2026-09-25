"""RBAC mutations keep the technical tenant in their CT-01 audit event."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.auditoria.models import Auditoria
from apps.permisos import testing
from apps.permisos.catalogo import sembrar_catalogo
from apps.permisos.models import Permiso
from apps.permisos.services import crear_o_reactivar_asignacion, crear_rol
from apps.tenancy.context import reset_current_tenant, set_current_tenant


class TenantAuditContextTests(TestCase):
    def test_global_assignment_and_role_mutation_use_technical_tenant(self):
        sembrar_catalogo(Permiso)
        negocio = testing.crear_negocio('POS FIFO Staging Demo')
        self.assertNotEqual(negocio.slug, 'staging_demo')
        User = get_user_model()
        actor = User.objects.create_user(
            username='operator_qa', email='operator_qa@example.test',
            password='x', rol='SYSADMIN', is_superuser=True,
        )
        usuario = User.objects.create_user(
            username='qa_santiago', email='qa_santiago@example.test',
            password='x', negocio=negocio,
        )
        rol = testing.crear_rol(negocio, 'Lector QA', ['productos.ver'])

        tokens = set_current_tenant('staging_demo', 'default')
        try:
            asignacion, creada = crear_o_reactivar_asignacion(
                actor=actor, usuario=usuario, rol=rol, sucursal=None,
                using='default',
            )
            self.assertTrue(creada)
            self.assertIsNone(asignacion.sucursal_id)
            self.assertEqual(
                Auditoria.objects.get(
                    accion='permisos.asignacion.creada', object_id=asignacion.pk,
                ).tenant_key,
                'staging_demo',
            )

            nuevo_rol = crear_rol(
                actor=actor, negocio=negocio, nombre='Supervisor QA',
                using='default',
            )
            self.assertEqual(
                Auditoria.objects.get(
                    accion='permisos.rol.creado', object_id=nuevo_rol.pk,
                ).tenant_key,
                'staging_demo',
            )
        finally:
            reset_current_tenant(tokens)
