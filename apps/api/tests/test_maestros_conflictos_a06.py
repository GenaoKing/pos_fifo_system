"""Contrato A06 para listado y resolución humana de CT-04."""
import threading
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from apps.auditoria.models import Auditoria
from apps.permisos import engine, testing
from apps.permisos.catalogo import sembrar_catalogo
from apps.permisos.models import Permiso
from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal
from apps.sync.models import MutacionMaestro, ResolucionConflictoMaestro


User = get_user_model()


@override_settings(RBAC_LEGACY_ADMIN_BYPASS=False)
class ConflictosMaestrosA06Tests(TestCase):
    lista_url = '/api/v1/maestros/conflictos/'

    def setUp(self):
        sembrar_catalogo(Permiso)
        self.negocio_a = testing.crear_negocio('Negocio conflictos A06 A')
        self.negocio_b = testing.crear_negocio('Negocio conflictos A06 B')
        self.sucursal_a = Sucursal.objects.create(
            negocio=self.negocio_a, codigo='A06-A', nombre='Sucursal A06 A', activa=True,
        )
        self.sucursal_b = Sucursal.objects.create(
            negocio=self.negocio_b, codigo='A06-B', nombre='Sucursal A06 B', activa=True,
        )
        self.usuario = User.objects.create_user(
            'resolver_a06', 'resolver-a06@example.test', 'x',
            rol='CAJERA', negocio=self.negocio_a,
        )
        rol = testing.crear_rol(
            self.negocio_a,
            'Resolver producto A06',
            ['productos.ver', 'productos.editar'],
        )
        testing.asignar(self.usuario, rol, sucursal=self.sucursal_a)
        engine.limpiar_memo()
        self.categoria = Categoria.objects.create(nombre='Categoría A06')
        self.producto = Producto.objects.create(
            sku='A06-SKU', codigo_barras='A06-SKU', nombre='Producto cloud A06',
            descripcion='', categoria=self.categoria,
            precio_venta=Decimal('100.00'), stock_minimo=1, activo=True,
            atributos={}, estado='nuevo', marca='',
        )

    def tearDown(self):
        engine.limpiar_memo()
        super().tearDown()

    def _api(self, usuario=None):
        client = APIClient()
        client.force_authenticate(user=usuario or self.usuario)
        return client

    def _mutacion(self, *, sucursal=None, entidad='PRODUCTO', estado='CONFLICTO',
                  cloud_entidad_id=None, cloud_revision=None, nombre='Local A06'):
        return MutacionMaestro.objects.create(
            mutacion_id=uuid.uuid4(),
            entidad=entidad,
            entidad_id=501,
            operacion=MutacionMaestro.Operacion.ACTUALIZAR,
            revision_base='2000-01-01T00:00:00+00:00',
            delta={
                'nombre': {'before': 'Antes A06', 'after': nombre},
                'precio_venta': {'before': '100.00', 'after': '125.00'},
            },
            estado=estado,
            actor_username='operador_pos_a06',
            sucursal=sucursal or self.sucursal_a,
            sucursal_codigo=(sucursal or self.sucursal_a).codigo,
            tenant_key=(sucursal or self.sucursal_a).negocio.slug,
            cloud_entidad_id=(self.producto.pk if cloud_entidad_id is None else cloud_entidad_id),
            cloud_revision=(cloud_revision or self.producto.fecha_modificacion.isoformat()),
            codigo_resultado='MASTER_REVISION_CONFLICT',
            conflicto_detalle='El maestro cloud cambió antes de aplicar la propuesta.',
        )

    def _resolver(self, mutacion, **extra):
        datos = {
            'schema_version': 'master.conflict-resolution.v1',
            'accion': 'CONSERVAR_CLOUD',
            'motivo': 'La política vigente conserva el catálogo cloud.',
            'cloud_revision_observada': mutacion.cloud_revision,
        }
        datos.update(extra)
        return self._api().post(
            f'{self.lista_url}{mutacion.mutacion_id}/resolver/', datos, format='json',
        )

    def test_lista_contrato_ct04_filtra_por_permiso_y_tenant(self):
        visible = self._mutacion()
        self._mutacion(entidad=MutacionMaestro.Entidad.CATEGORIA)
        self._mutacion(sucursal=self.sucursal_b, nombre='No visible por tenant')

        respuesta = self._api().get(self.lista_url)

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data['schema_version'], 'master.conflict-list.v1')
        self.assertIsNone(respuesta.data['next_cursor'])
        self.assertEqual(len(respuesta.data['items']), 1)
        item = respuesta.data['items'][0]
        self.assertEqual(item['schema_version'], 'master.conflict.v1')
        self.assertEqual(item['mutacion_id'], str(visible.mutacion_id))
        self.assertEqual(item['entidad']['tipo'], 'PRODUCTO')
        self.assertEqual(item['sucursal']['codigo'], self.sucursal_a.codigo)
        self.assertEqual(item['acciones'], ['CONSERVAR_CLOUD', 'APLICAR_LOCAL'])

    def test_lista_cursor_opaco_es_estable_y_page_size_tiene_tope(self):
        primera = self._mutacion(nombre='Primera A06')
        segunda = self._mutacion(nombre='Segunda A06')

        pagina_1 = self._api().get(self.lista_url, {'page_size': 1})
        pagina_2 = self._api().get(
            self.lista_url, {'page_size': 1, 'cursor': pagina_1.data['next_cursor']},
        )
        invalida = self._api().get(self.lista_url, {'cursor': 'no-es-un-cursor'})
        excedida = self._api().get(self.lista_url, {'page_size': 101})

        self.assertEqual(pagina_1.status_code, 200)
        self.assertEqual(pagina_1.data['items'][0]['mutacion_id'], str(primera.mutacion_id))
        self.assertIsNotNone(pagina_1.data['next_cursor'])
        self.assertEqual(pagina_2.status_code, 200)
        self.assertEqual(pagina_2.data['items'][0]['mutacion_id'], str(segunda.mutacion_id))
        self.assertEqual(invalida.status_code, 400)
        self.assertEqual(excedida.status_code, 400)

    def test_conservar_cloud_exige_schema_motivo_cas_y_deja_ledger_auditable(self):
        mutacion = self._mutacion()

        schema_invalido = self._resolver(mutacion, schema_version='master.conflict-resolution.v2')
        motivo_invalido = self._resolver(mutacion, motivo=' ')
        revision_invalida = self._resolver(mutacion, cloud_revision_observada='2000-01-01T00:00:00+00:00')
        respuesta = self._resolver(mutacion)

        self.assertEqual(schema_invalido.status_code, 400)
        self.assertEqual(motivo_invalido.status_code, 400)
        self.assertEqual(revision_invalida.status_code, 409)
        mutacion.refresh_from_db()
        self.assertEqual(mutacion.estado, MutacionMaestro.Estado.CONFLICTO)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.data['schema_version'], 'master.conflict.v1')
        self.assertEqual(respuesta.data['acciones'], [])
        resolucion = ResolucionConflictoMaestro.objects.get(mutacion=mutacion)
        self.assertEqual(resolucion.accion, ResolucionConflictoMaestro.Accion.CONSERVAR_CLOUD)
        self.assertEqual(resolucion.actor_id, self.usuario.pk)
        self.assertTrue(Auditoria.objects.filter(
            accion='sync.maestro.conflicto.resuelto',
            correlacion_id=mutacion.mutacion_id,
            resultado=Auditoria.Resultado.SUCCEEDED,
        ).exists())
        self.assertEqual(self._api().get(self.lista_url).data['items'], [])

    def test_aplicar_local_revalida_cas_y_no_pisa_un_cloud_mas_nuevo(self):
        mutacion = self._mutacion(nombre='Nombre decidido localmente A06')
        self.producto.nombre = 'Cloud más nuevo A06'
        # El escenario requiere una revisión posterior. Dos save() inmediatos
        # pueden recibir el mismo tick del reloj en Windows; fijar el tiempo
        # hace determinista esa precondición sin simular el resolver ni su CAS.
        posterior = self.producto.fecha_modificacion + timedelta(seconds=1)
        with patch('django.utils.timezone.now', return_value=posterior):
            self.producto.save()
        self.assertNotEqual(
            self.producto.fecha_modificacion.isoformat(), mutacion.cloud_revision,
        )

        conflicto = self._resolver(mutacion, accion='APLICAR_LOCAL')

        self.assertEqual(conflicto.status_code, 409)
        self.producto.refresh_from_db()
        mutacion.refresh_from_db()
        self.assertEqual(self.producto.nombre, 'Cloud más nuevo A06')
        self.assertEqual(mutacion.estado, MutacionMaestro.Estado.CONFLICTO)
        self.assertEqual(mutacion.codigo_resultado, 'MASTER_REVISION_CONFLICT')
        self.assertFalse(ResolucionConflictoMaestro.objects.filter(mutacion=mutacion).exists())

    def test_aplicar_local_guarda_nueva_revision_y_cierra_solo_la_propuesta(self):
        mutacion = self._mutacion(nombre='Nombre local aprobado A06')

        respuesta = self._resolver(mutacion, accion='APLICAR_LOCAL')

        self.assertEqual(respuesta.status_code, 200)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.nombre, 'Nombre local aprobado A06')
        resolucion = ResolucionConflictoMaestro.objects.get(mutacion=mutacion)
        self.assertEqual(resolucion.accion, ResolucionConflictoMaestro.Accion.APLICAR_LOCAL)
        self.assertTrue(resolucion.cloud_revision_resultante)
        self.assertEqual(self._api().get(self.lista_url).data['items'], [])

    def test_resolver_exige_permiso_editar_en_sucursal_origen(self):
        lector = User.objects.create_user(
            'lector_a06', 'lector-a06@example.test', 'x',
            rol='CAJERA', negocio=self.negocio_a,
        )
        rol = testing.crear_rol(self.negocio_a, 'Lector producto A06', ['productos.ver'])
        testing.asignar(lector, rol, sucursal=self.sucursal_a)
        engine.limpiar_memo()
        mutacion = self._mutacion()
        datos = {
            'schema_version': 'master.conflict-resolution.v1',
            'accion': 'CONSERVAR_CLOUD',
            'motivo': 'No puede resolver.',
            'cloud_revision_observada': mutacion.cloud_revision,
        }

        respuesta = self._api(lector).post(
            f'{self.lista_url}{mutacion.mutacion_id}/resolver/', datos, format='json',
        )

        self.assertEqual(respuesta.status_code, 403)
        self.assertFalse(ResolucionConflictoMaestro.objects.filter(mutacion=mutacion).exists())


@override_settings(RBAC_LEGACY_ADMIN_BYPASS=False)
class ResolucionConflictoMaestroConcurrenteA06Tests(TransactionTestCase):
    """Dos operadores no pueden aplicar una misma propuesta dos veces."""

    def setUp(self):
        sembrar_catalogo(Permiso)
        self.negocio = testing.crear_negocio('Negocio concurrencia A06')
        self.sucursal = Sucursal.objects.create(
            negocio=self.negocio, codigo='A06-CONC', nombre='Sucursal A06 concurrente',
            activa=True,
        )
        self.usuario = User.objects.create_user(
            'resolver_concurrente_a06', 'resolver-concurrente-a06@example.test', 'x',
            rol='CAJERA', negocio=self.negocio,
        )
        rol = testing.crear_rol(
            self.negocio, 'Resolver concurrente A06',
            ['productos.ver', 'productos.editar'],
        )
        testing.asignar(self.usuario, rol, sucursal=self.sucursal)
        self.categoria = Categoria.objects.create(nombre='Categoría concurrencia A06')
        self.producto = Producto.objects.create(
            sku='A06-CONC', codigo_barras='A06-CONC', nombre='Cloud concurrente A06',
            descripcion='', categoria=self.categoria,
            precio_venta=Decimal('10.00'), stock_minimo=1, activo=True,
            atributos={}, estado='nuevo', marca='',
        )
        self.mutacion = MutacionMaestro.objects.create(
            entidad=MutacionMaestro.Entidad.PRODUCTO,
            entidad_id=501,
            operacion=MutacionMaestro.Operacion.ACTUALIZAR,
            revision_base='2000-01-01T00:00:00+00:00',
            delta={'nombre': {'before': 'Antes', 'after': 'Aplicado una vez A06'}},
            estado=MutacionMaestro.Estado.CONFLICTO,
            actor_username='operador_pos_a06',
            sucursal=self.sucursal,
            sucursal_codigo=self.sucursal.codigo,
            tenant_key=self.negocio.slug,
            cloud_entidad_id=self.producto.pk,
            cloud_revision=self.producto.fecha_modificacion.isoformat(),
            codigo_resultado='MASTER_REVISION_CONFLICT',
            conflicto_detalle='Conflicto para prueba concurrente.',
        )

    def tearDown(self):
        connection.close()
        engine.limpiar_memo()
        super().tearDown()

    def test_aplicar_local_concurrente_crea_una_sola_resolucion(self):
        barrera = threading.Barrier(2, timeout=30)
        respuestas = []
        errores = []
        candado = threading.Lock()
        payload = {
            'schema_version': 'master.conflict-resolution.v1',
            'accion': 'APLICAR_LOCAL',
            'motivo': 'La propuesta local fue revisada por el equipo.',
            'cloud_revision_observada': self.mutacion.cloud_revision,
        }

        def resolver():
            close_old_connections()
            try:
                usuario = User.objects.get(pk=self.usuario.pk)
                client = APIClient()
                client.force_authenticate(user=usuario)
                barrera.wait()
                respuesta = client.post(
                    f'/api/v1/maestros/conflictos/{self.mutacion.mutacion_id}/resolver/',
                    payload,
                    format='json',
                )
                with candado:
                    respuestas.append(respuesta.status_code)
            except Exception as exc:  # pragma: no cover - la aserción acredita la ausencia.
                with candado:
                    errores.append(exc)
            finally:
                close_old_connections()

        hilos = [threading.Thread(target=resolver) for _ in range(2)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=30)

        self.assertEqual(errores, [])
        self.assertEqual(sorted(respuestas), [200, 409])
        self.assertEqual(
            ResolucionConflictoMaestro.objects.filter(mutacion=self.mutacion).count(), 1,
        )
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.nombre, 'Aplicado una vez A06')
