"""Contrato A05.2a: cola durable local de Producto y Categoria."""
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.auditoria.models import Auditoria
from apps.auditoria.services import AuditContractError
from apps.configuracion.models import ConfiguracionNegocio
from apps.negocios.models import Negocio
from apps.permisos import testing as permisos_testing
from apps.productos.models import Categoria, Producto, productos_vendibles
from apps.productos.services import (
    PermisoMutacionMaestroDenegado,
    cambiar_estado_categoria_local,
    cambiar_estado_producto_local,
    crear_categoria_local,
    crear_producto_local,
    editar_categoria_local,
    editar_producto_local,
)
from apps.sucursales.models import Sucursal
from apps.sync.models import EventoSync, MutacionMaestro


User = get_user_model()


@override_settings(SUCURSAL_CODIGO='A052')
class MutacionesMaestroA052aTests(TestCase):
    def setUp(self):
        cache.clear()
        self.negocio = Negocio.objects.create(nombre='Negocio A052', slug='negocio-a052')
        self.sucursal = Sucursal.objects.create(
            negocio=self.negocio, codigo='A052', nombre='Sucursal A052',
        )
        ConfiguracionNegocio.objects.create(
            sucursal=self.sucursal, nombre_negocio='Catalogo A05.2a',
        )
        self.categoria = Categoria.objects.create(nombre='Base A052', activa=True)

    def tearDown(self):
        cache.clear()

    def _actor(self, *permisos):
        actor = User.objects.create_user(
            username=f'actor_{User.objects.count()}',
            email=f'actor_{User.objects.count()}@a052.test',
            password='Prueba123', rol='CAJERA', activo=True,
        )
        permisos_testing.habilitar_cajero(
            actor,
            negocio=self.negocio,
            sucursal=self.sucursal,
            permisos=list(permisos),
        )
        return actor

    def _datos_producto(self, **extra):
        datos = {
            'nombre': 'Producto A052',
            'descripcion': 'Descripcion inicial',
            'categoria_id': self.categoria.id,
            'precio_venta': Decimal('25.50'),
            'stock_minimo': 2,
            'atributos': {'color': 'azul'},
            'estado': 'nuevo',
            'marca': 'A05',
        }
        datos.update(extra)
        return datos

    def _crear_producto(self, actor=None, mutacion_id=None, **extra):
        actor = actor or self._actor('productos.crear')
        return crear_producto_local(
            actor=actor,
            sucursal=self.sucursal,
            datos=self._datos_producto(**extra),
            mutacion_id=mutacion_id,
        )

    def test_crear_persiste_maestro_auditoria_y_cola_con_trazabilidad(self):
        actor = self._actor('productos.crear')
        identificador = uuid.uuid4()

        resultado = self._crear_producto(actor, identificador)

        producto = resultado.entidad
        cola = resultado.mutacion
        self.assertFalse(resultado.repetida)
        self.assertEqual(cola.mutacion_id, identificador)
        self.assertEqual(cola.entidad, MutacionMaestro.Entidad.PRODUCTO)
        self.assertEqual(cola.entidad_id, producto.id)
        self.assertEqual(cola.operacion, MutacionMaestro.Operacion.CREAR)
        self.assertEqual(cola.estado, MutacionMaestro.Estado.PENDIENTE)
        self.assertEqual(cola.actor_id, actor.id)
        self.assertEqual(cola.actor_username, actor.username)
        self.assertEqual(cola.sucursal_id, self.sucursal.id)
        self.assertEqual(cola.sucursal_codigo, self.sucursal.codigo)
        self.assertEqual(cola.tenant_key, self.negocio.slug)
        self.assertEqual(cola.revision_base, '')
        self.assertEqual(cola.delta['nombre']['after'], producto.nombre)
        self.assertEqual(EventoSync.objects.count(), 0)

        auditoria = Auditoria.objects.get(event_id=cola.auditoria_event_id)
        self.assertEqual(auditoria.schema_version, Auditoria.SCHEMA_V1)
        self.assertIsNotNone(auditoria.actor_ref)
        self.assertEqual(auditoria.sucursal_id, self.sucursal.id)
        self.assertEqual(auditoria.idempotencia_key, str(identificador))
        self.assertEqual(auditoria.metadata['master_mutation_id'], str(identificador))

    def test_replay_del_mismo_uuid_no_duplica_maestro_auditoria_ni_cola(self):
        actor = self._actor('productos.crear')
        identificador = uuid.uuid4()

        primero = self._crear_producto(actor, identificador)
        segundo = self._crear_producto(actor, identificador, nombre='No debe crearse')

        self.assertFalse(primero.repetida)
        self.assertTrue(segundo.repetida)
        self.assertEqual(segundo.entidad.pk, primero.entidad.pk)
        self.assertEqual(Producto.objects.count(), 1)
        self.assertEqual(MutacionMaestro.objects.count(), 1)
        self.assertEqual(Auditoria.objects.filter(schema_version=Auditoria.SCHEMA_V1).count(), 1)

    def test_fallo_de_auditoria_revierte_maestro_y_cola(self):
        actor = self._actor('productos.crear')
        with patch(
            'apps.productos.services.registrar_mutacion',
            side_effect=AuditContractError('AUDIT_WRITE_FAILED', 'fallo inducido'),
        ):
            with self.assertRaises(AuditContractError):
                self._crear_producto(actor, uuid.uuid4(), nombre='Debe revertir')

        self.assertFalse(Producto.objects.filter(nombre='Debe revertir').exists())
        self.assertEqual(MutacionMaestro.objects.count(), 0)
        self.assertEqual(Auditoria.objects.filter(schema_version=Auditoria.SCHEMA_V1).count(), 0)

    def test_edicion_guarda_revision_base_delta_y_conserva_sku_a051(self):
        actor = self._actor('productos.crear', 'productos.editar')
        creado = self._crear_producto(actor)
        producto = creado.entidad
        sku = producto.sku
        revision_base = producto.fecha_modificacion.isoformat()

        resultado = editar_producto_local(
            actor=actor,
            sucursal=self.sucursal,
            producto_id=producto.id,
            datos=self._datos_producto(
                nombre='Producto editado A052',
                precio_venta=Decimal('30.00'),
                codigo_barras=producto.codigo_barras,
            ),
            mutacion_id=uuid.uuid4(),
        )

        producto.refresh_from_db()
        self.assertEqual(producto.sku, sku)
        self.assertEqual(resultado.mutacion.revision_base, revision_base)
        self.assertEqual(resultado.mutacion.delta['precio_venta'], {
            'before': '25.50', 'after': '30.00',
        })
        self.assertEqual(resultado.mutacion.delta['nombre']['after'], 'Producto editado A052')

    def test_producto_pendiente_sigue_vendible_y_conflicto_lo_bloquea_sin_borrarlo(self):
        actor = self._actor('productos.crear')
        resultado = self._crear_producto(actor)
        producto = resultado.entidad

        self.assertTrue(productos_vendibles().filter(pk=producto.pk).exists())
        self.assertTrue(producto.es_vendible)

        resultado.mutacion.marcar_conflicto('CAS_REVISION_MISMATCH')

        producto.refresh_from_db()
        self.assertTrue(Producto.objects.filter(pk=producto.pk).exists())
        self.assertFalse(productos_vendibles().filter(pk=producto.pk).exists())
        self.assertFalse(producto.es_vendible)
        self.assertEqual(resultado.mutacion.estado, MutacionMaestro.Estado.CONFLICTO)

    def test_conflicto_de_categoria_bloquea_sus_productos_y_cola_categoria_es_durable(self):
        actor = self._actor('productos.crear', 'categorias.editar')
        producto = self._crear_producto(actor).entidad
        categoria_mutacion = editar_categoria_local(
            actor=actor,
            sucursal=self.sucursal,
            categoria_id=self.categoria.id,
            datos={
                'nombre': self.categoria.nombre,
                'descripcion': self.categoria.descripcion,
                'activa': True,
            },
            mutacion_id=uuid.uuid4(),
        ).mutacion
        categoria_mutacion.marcar_conflicto('CAS_CATEGORIA')

        self.assertFalse(productos_vendibles().filter(pk=producto.pk).exists())
        self.assertEqual(categoria_mutacion.entidad, MutacionMaestro.Entidad.CATEGORIA)
        self.assertEqual(categoria_mutacion.sucursal_id, self.sucursal.id)

    def test_servicio_rechaza_escritura_directa_de_actor_sin_permiso(self):
        actor = self._actor('ventas.crear')

        with self.assertRaises(PermisoMutacionMaestroDenegado):
            self._crear_producto(actor)

        self.assertEqual(Producto.objects.count(), 0)
        self.assertEqual(MutacionMaestro.objects.count(), 0)

    def test_cambio_de_estado_producto_es_auditado_y_encolado(self):
        actor = self._actor('productos.crear', 'productos.eliminar')
        producto = self._crear_producto(actor).entidad

        resultado = cambiar_estado_producto_local(
            actor=actor,
            sucursal=self.sucursal,
            producto_id=producto.id,
            mutacion_id=uuid.uuid4(),
        )

        producto.refresh_from_db()
        self.assertFalse(producto.activo)
        self.assertEqual(resultado.mutacion.operacion, MutacionMaestro.Operacion.DESACTIVAR)
        self.assertEqual(resultado.mutacion.delta['activo'], {'before': True, 'after': False})
        self.assertEqual(resultado.mutacion.actor_id, actor.id)

    def test_categoria_creada_se_encola_con_actor_sucursal_y_replay(self):
        actor = self._actor('categorias.crear')
        identificador = uuid.uuid4()
        primero = crear_categoria_local(
            actor=actor,
            sucursal=self.sucursal,
            datos={'nombre': 'Nueva A052', 'descripcion': 'Nueva'},
            mutacion_id=identificador,
        )
        segundo = crear_categoria_local(
            actor=actor,
            sucursal=self.sucursal,
            datos={'nombre': 'No duplicar'},
            mutacion_id=identificador,
        )

        self.assertEqual(primero.mutacion.entidad, MutacionMaestro.Entidad.CATEGORIA)
        self.assertEqual(primero.mutacion.actor_id, actor.id)
        self.assertEqual(primero.mutacion.sucursal_id, self.sucursal.id)
        self.assertTrue(segundo.repetida)
        self.assertEqual(Categoria.objects.filter(nombre='Nueva A052').count(), 1)

    def test_cambio_de_estado_categoria_es_auditado_y_encolado(self):
        actor = self._actor('categorias.eliminar')

        resultado = cambiar_estado_categoria_local(
            actor=actor,
            sucursal=self.sucursal,
            categoria_id=self.categoria.id,
            mutacion_id=uuid.uuid4(),
        )

        self.categoria.refresh_from_db()
        self.assertFalse(self.categoria.activa)
        self.assertEqual(resultado.mutacion.entidad, MutacionMaestro.Entidad.CATEGORIA)
        self.assertEqual(resultado.mutacion.operacion, MutacionMaestro.Operacion.DESACTIVAR)
        self.assertEqual(resultado.mutacion.delta['activa'], {'before': True, 'after': False})
        self.assertTrue(Auditoria.objects.filter(event_id=resultado.mutacion.auditoria_event_id).exists())
