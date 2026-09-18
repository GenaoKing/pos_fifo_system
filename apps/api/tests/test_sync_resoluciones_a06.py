"""Contrato A06 del retorno cloud -> POS de resoluciones CT-04."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.negocios.models import Negocio
from apps.sucursales.models import Sucursal
from apps.sync.models import MutacionMaestro, ResolucionConflictoMaestro


User = get_user_model()


class ResolucionesMaestroSyncA06Tests(TestCase):
    url = '/api/v1/sync/mutaciones-maestro/resoluciones/'

    def setUp(self):
        self.negocio_a = Negocio.objects.create(nombre='Negocio sync A06 A', slug='sync-a06-a')
        self.negocio_b = Negocio.objects.create(nombre='Negocio sync A06 B', slug='sync-a06-b')
        self.servicio_a = User.objects.create_user(
            'svc_sync_a06_a', 'svc-sync-a06-a@example.test', 'x', rol='CAJERA',
        )
        self.servicio_b = User.objects.create_user(
            'svc_sync_a06_b', 'svc-sync-a06-b@example.test', 'x', rol='CAJERA',
        )
        self.sucursal_a = Sucursal.objects.create(
            negocio=self.negocio_a, codigo='SYNC-A06-A', nombre='Sync A06 A', activa=True,
            usuario_servicio=self.servicio_a,
        )
        self.sucursal_b = Sucursal.objects.create(
            negocio=self.negocio_b, codigo='SYNC-A06-B', nombre='Sync A06 B', activa=True,
            usuario_servicio=self.servicio_b,
        )
        self.token_a = Token.objects.create(user=self.servicio_a)

    def _api_a(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {self.token_a.key}')
        return client

    @staticmethod
    def _mutacion(sucursal, entidad_id):
        return MutacionMaestro.objects.create(
            entidad=MutacionMaestro.Entidad.PRODUCTO,
            entidad_id=entidad_id,
            operacion=MutacionMaestro.Operacion.ACTUALIZAR,
            revision_base='2026-09-17T12:00:00+00:00',
            delta={'nombre': {'before': 'Antes', 'after': 'Después'}},
            estado=MutacionMaestro.Estado.CONFLICTO,
            sucursal=sucursal,
            sucursal_codigo=sucursal.codigo,
            tenant_key=sucursal.negocio.slug,
            cloud_entidad_id=entidad_id,
            cloud_revision='2026-09-17T12:05:00+00:00',
        )

    @classmethod
    def _resolucion(cls, sucursal, entidad_id):
        mutacion = cls._mutacion(sucursal, entidad_id)
        return ResolucionConflictoMaestro.objects.create(
            mutacion=mutacion,
            accion=ResolucionConflictoMaestro.Accion.CONSERVAR_CLOUD,
            motivo='Se conserva la versión aprobada del catálogo cloud.',
            actor_username='admin_a06',
            cloud_revision_observada=mutacion.cloud_revision,
            cloud_revision_resultante='2026-09-17T12:06:00+00:00',
            cloud_entidad_id=entidad_id,
        )

    def test_token_recibe_solo_resoluciones_de_su_sucursal_con_schema_tipado(self):
        propia = self._resolucion(self.sucursal_a, 101)
        self._resolucion(self.sucursal_b, 202)

        respuesta = self._api_a().get(self.url)

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data['schema_version'], 'master.conflict-resolution-sync.v1')
        self.assertIsNone(respuesta.data['next'])
        self.assertEqual(len(respuesta.data['results']), 1)
        fila = respuesta.data['results'][0]
        self.assertEqual(fila['schema_version'], 'master.conflict-resolution-sync.v1')
        self.assertEqual(fila['id'], propia.pk)
        self.assertEqual(fila['mutacion_id'], str(propia.mutacion.mutacion_id))
        self.assertEqual(fila['accion'], ResolucionConflictoMaestro.Accion.CONSERVAR_CLOUD)
        self.assertEqual(fila['cloud_entidad_id'], 101)
        self.assertIn('fecha_modificacion', fila)

    def test_keyset_y_limites_no_exponen_filas_ajenas(self):
        primera = self._resolucion(self.sucursal_a, 301)
        segunda = self._resolucion(self.sucursal_a, 302)
        self._resolucion(self.sucursal_b, 303)
        momento = timezone.now() - timedelta(minutes=1)
        ResolucionConflictoMaestro.objects.filter(pk__in=[primera.pk, segunda.pk]).update(
            resuelto_at=momento,
        )

        pagina_1 = self._api_a().get(self.url, {'page_size': 1})
        pagina_2 = self._api_a().get(
            self.url,
            {
                'page_size': 1,
                'desde': pagina_1.data['results'][0]['fecha_modificacion'],
                'desde_id': pagina_1.data['results'][0]['id'],
            },
        )
        invalida = self._api_a().get(self.url, {'page_size': 101})

        self.assertEqual(pagina_1.status_code, 200)
        self.assertEqual(pagina_1.data['results'][0]['id'], primera.pk)
        self.assertEqual(pagina_1.data['next'], 'keyset')
        self.assertEqual(pagina_2.status_code, 200)
        self.assertEqual(pagina_2.data['results'][0]['id'], segunda.pk)
        self.assertIsNone(pagina_2.data['next'])
        self.assertEqual(invalida.status_code, 400)
