"""A06: baja lógica y estado operativo efectivo de maestros."""
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from apps.api.serializers.maestros import ProductoWriteSerializer
from apps.configuracion.models import ConfiguracionNegocio
from apps.negocios.models import Negocio
from apps.permisos import testing as permisos_testing
from apps.productos.models import Categoria, Producto, productos_vendibles
from apps.productos.services import (
    cambiar_estado_categoria_local,
    cambiar_estado_producto_local,
)
from apps.sucursales.models import Sucursal
from apps.sync.models import MutacionMaestro


User = get_user_model()


@override_settings(SUCURSAL_CODIGO='A06-EST')
class EstadoOperativoA06Tests(TestCase):
    def setUp(self):
        self.negocio = Negocio.objects.create(nombre='Negocio estado A06', slug='estado-a06')
        self.sucursal = Sucursal.objects.create(
            negocio=self.negocio, codigo='A06-EST', nombre='Sucursal estado A06', activa=True,
        )
        ConfiguracionNegocio.objects.create(
            sucursal=self.sucursal, nombre_negocio='Estado operativo A06',
        )
        self.actor = User.objects.create_user(
            'operador_estado_a06', 'operador-estado-a06@example.test', 'x', rol='CAJERA',
        )
        permisos_testing.habilitar_cajero(
            self.actor,
            negocio=self.negocio,
            sucursal=self.sucursal,
            permisos=['productos.ver', 'productos.eliminar', 'categorias.eliminar'],
        )
        self.categoria = Categoria.objects.create(nombre='Categoría estado A06', activa=True)
        self.producto = Producto.objects.create(
            sku='A06-EST-001', codigo_barras='A06-EST-001', nombre='Producto estado A06',
            descripcion='', categoria=self.categoria, precio_venta=Decimal('25.00'),
            stock_minimo=1, activo=True, atributos={}, estado='nuevo', marca='',
        )

    def test_baja_producto_exige_motivo_y_reactivacion_limpia_su_metadato(self):
        with self.assertRaises(ValidationError):
            cambiar_estado_producto_local(
                actor=self.actor, sucursal=self.sucursal, producto_id=self.producto.pk,
                activo=False, mutacion_id=uuid.uuid4(),
            )
        self.producto.refresh_from_db()
        self.assertTrue(self.producto.activo)

        baja = cambiar_estado_producto_local(
            actor=self.actor, sucursal=self.sucursal, producto_id=self.producto.pk,
            activo=False, motivo='Producto discontinuado por el proveedor.', mutacion_id=uuid.uuid4(),
        )
        self.producto.refresh_from_db()
        self.assertFalse(self.producto.activo)
        self.assertEqual(self.producto.motivo_inactivacion, 'Producto discontinuado por el proveedor.')
        self.assertIsNotNone(self.producto.inactivado_at)
        self.assertEqual(
            baja.mutacion.delta['motivo_inactivacion']['after'],
            'Producto discontinuado por el proveedor.',
        )
        self.assertFalse(productos_vendibles().filter(pk=self.producto.pk).exists())

        cambiar_estado_producto_local(
            actor=self.actor, sucursal=self.sucursal, producto_id=self.producto.pk,
            activo=True, mutacion_id=uuid.uuid4(),
        )
        self.producto.refresh_from_db()
        self.assertTrue(self.producto.activo)
        self.assertEqual(self.producto.motivo_inactivacion, '')
        self.assertIsNone(self.producto.inactivado_at)
        self.assertTrue(productos_vendibles().filter(pk=self.producto.pk).exists())

    def test_baja_categoria_no_muta_flag_individual_y_reactivacion_no_lo_resucita(self):
        cambiar_estado_producto_local(
            actor=self.actor, sucursal=self.sucursal, producto_id=self.producto.pk,
            activo=False, motivo='El producto se retiró por separado.', mutacion_id=uuid.uuid4(),
        )
        cambiar_estado_categoria_local(
            actor=self.actor, sucursal=self.sucursal, categoria_id=self.categoria.pk,
            activa=False, motivo='Categoría temporalmente fuera de catálogo.', mutacion_id=uuid.uuid4(),
        )
        self.categoria.refresh_from_db()
        self.producto.refresh_from_db()
        self.assertFalse(self.categoria.activa)
        self.assertEqual(self.categoria.motivo_inactivacion, 'Categoría temporalmente fuera de catálogo.')
        self.assertFalse(self.producto.activo)
        self.assertFalse(self.producto.es_vendible)

        cambiar_estado_categoria_local(
            actor=self.actor, sucursal=self.sucursal, categoria_id=self.categoria.pk,
            activa=True, mutacion_id=uuid.uuid4(),
        )
        self.categoria.refresh_from_db()
        self.producto.refresh_from_db()
        self.assertTrue(self.categoria.activa)
        self.assertFalse(self.producto.activo)
        self.assertFalse(self.producto.es_vendible)

    def test_serializer_portal_rechaza_baja_sin_motivo(self):
        serializer = ProductoWriteSerializer(
            self.producto, data={'activo': False}, partial=True,
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('motivo_inactivacion', serializer.errors)

        serializer = ProductoWriteSerializer(
            self.producto,
            data={
                'activo': False,
                'motivo_inactivacion': 'Baja autorizada desde portal.',
            },
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.producto.refresh_from_db()
        self.assertFalse(self.producto.activo)
        self.assertEqual(self.producto.motivo_inactivacion, 'Baja autorizada desde portal.')

    def test_conflicto_resuelto_no_sigue_ocultando_producto(self):
        mutacion = MutacionMaestro.objects.create(
            entidad=MutacionMaestro.Entidad.PRODUCTO,
            entidad_id=self.producto.pk,
            operacion=MutacionMaestro.Operacion.ACTUALIZAR,
            revision_base='', delta={}, estado=MutacionMaestro.Estado.CONFLICTO,
            actor=self.actor, actor_username=self.actor.username, sucursal=self.sucursal,
            sucursal_codigo=self.sucursal.codigo, tenant_key=self.negocio.slug,
            cloud_entidad_id=1, cloud_revision='2026-09-17T00:00:00+00:00',
        )
        self.assertFalse(productos_vendibles().filter(pk=self.producto.pk).exists())

        from apps.sync.models import ResolucionConflictoMaestro

        ResolucionConflictoMaestro.objects.create(
            mutacion=mutacion,
            accion=ResolucionConflictoMaestro.Accion.CONSERVAR_CLOUD,
            motivo='Cloud conserva la última lista.',
            actor=self.actor,
            actor_username=self.actor.username,
            cloud_revision_observada=mutacion.cloud_revision,
            cloud_revision_resultante=mutacion.cloud_revision,
            cloud_entidad_id=1,
        )
        self.assertTrue(productos_vendibles().filter(pk=self.producto.pk).exists())

    def test_pos_lista_conflicto_local_y_omite_el_que_ya_volvio_por_sync(self):
        pendiente = MutacionMaestro.objects.create(
            entidad=MutacionMaestro.Entidad.PRODUCTO,
            entidad_id=self.producto.pk,
            operacion=MutacionMaestro.Operacion.ACTUALIZAR,
            revision_base='', delta={'nombre': {'before': 'Antes', 'after': 'Después'}},
            estado=MutacionMaestro.Estado.CONFLICTO,
            actor=self.actor, actor_username=self.actor.username, sucursal=self.sucursal,
            sucursal_codigo=self.sucursal.codigo, tenant_key=self.negocio.slug,
            cloud_entidad_id=1, cloud_revision='2026-09-17T00:00:00+00:00',
            codigo_resultado='MASTER_REVISION_CONFLICT',
            conflicto_detalle='El cloud cambió antes de aplicar la propuesta.',
        )
        self.client.force_login(self.actor)

        respuesta = self.client.get('/productos/conflictos/')

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, str(pendiente.mutacion_id))
        self.assertContains(respuesta, 'portal conectado')

        from apps.sync.models import ResolucionConflictoMaestro

        ResolucionConflictoMaestro.objects.create(
            mutacion=pendiente,
            accion=ResolucionConflictoMaestro.Accion.CONSERVAR_CLOUD,
            motivo='El cloud conserva el catálogo aprobado.',
            actor=self.actor,
            actor_username=self.actor.username,
            cloud_revision_observada=pendiente.cloud_revision,
            cloud_revision_resultante=pendiente.cloud_revision,
            cloud_entidad_id=1,
        )
        respuesta = self.client.get('/productos/conflictos/')
        self.assertNotContains(respuesta, str(pendiente.mutacion_id))
