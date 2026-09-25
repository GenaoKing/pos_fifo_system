"""El catalogo del portal deja evidencia CT-01 en la misma transaccion."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.auditoria.models import Auditoria
from apps.productos.models import Categoria, Producto


@override_settings(API_MAESTROS_PERMITE_ESCRITURA_LOCAL_TEST=True)
class AuditoriaCatalogoPortalTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username='admin_catalogo', email='admin_catalogo@example.com',
            password='pass', rol='ADMIN', activo=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.categoria = Categoria.objects.create(nombre='Inicial')

    def test_precio_y_baja_crean_un_evento_por_mutacion(self):
        creado = self.client.post('/api/v1/maestros/productos/', {
            'sku': 'AUD-001', 'nombre': 'Producto auditado',
            'precio_venta': '10.00', 'categoria': self.categoria.pk,
        }, format='json')
        self.assertEqual(creado.status_code, 201)
        producto_id = creado.data['id']

        editado = self.client.patch(
            f'/api/v1/maestros/productos/{producto_id}/',
            {'precio_venta': '12.50'}, format='json',
        )
        self.assertEqual(editado.status_code, 200)
        baja = self.client.delete(
            f'/api/v1/maestros/productos/{producto_id}/',
            {'motivo_inactivacion': 'Fin de prueba'}, format='json',
        )
        self.assertEqual(baja.status_code, 200)

        eventos = list(Auditoria.objects.filter(
            accion__startswith='productos.producto.', object_id=producto_id,
        ).order_by('id'))
        self.assertEqual([e.accion for e in eventos], [
            'productos.producto.creado',
            'productos.producto.precio_modificado',
            'productos.producto.desactivado',
        ])
        self.assertEqual(eventos[0].datos_anteriores, {})
        self.assertEqual(eventos[1].datos_anteriores['precio_venta'], '10.00')
        self.assertEqual(eventos[1].datos_nuevos['precio_venta'], '12.50')
        self.assertEqual(eventos[2].datos_anteriores['activo'], True)
        self.assertEqual(eventos[2].datos_nuevos['activo'], False)
        self.assertTrue(all(e.canal == Auditoria.Canal.PORTAL_API for e in eventos))
        self.assertTrue(all(e.actor_username == self.admin.username for e in eventos))

    def test_categoria_crear_editar_y_desactivar_deja_traza(self):
        creado = self.client.post('/api/v1/maestros/categorias/', {
            'nombre': 'Nueva categoria',
        }, format='json')
        self.assertEqual(creado.status_code, 201)
        categoria_id = creado.data['id']
        editado = self.client.patch(
            f'/api/v1/maestros/categorias/{categoria_id}/',
            {'nombre': 'Categoria renombrada'}, format='json',
        )
        self.assertEqual(editado.status_code, 200)
        baja = self.client.delete(
            f'/api/v1/maestros/categorias/{categoria_id}/',
            {'motivo_inactivacion': 'Fin de prueba'}, format='json',
        )
        self.assertEqual(baja.status_code, 200)

        eventos = list(Auditoria.objects.filter(
            accion__startswith='productos.categoria.', object_id=categoria_id,
        ).order_by('id'))
        self.assertEqual([e.accion for e in eventos], [
            'productos.categoria.creado',
            'productos.categoria.actualizado',
            'productos.categoria.desactivado',
        ])
        self.assertEqual(eventos[1].datos_anteriores['nombre'], 'Nueva categoria')
        self.assertEqual(eventos[1].datos_nuevos['nombre'], 'Categoria renombrada')

    def test_fallo_de_auditoria_revierte_precio(self):
        producto = Producto.objects.create(
            sku='AUD-ROLLBACK', nombre='Producto', categoria=self.categoria,
            precio_venta='10.00',
        )
        with patch('apps.api.views.maestros.registrar_mutacion', side_effect=RuntimeError('audit down')):
            with self.assertRaises(RuntimeError):
                self.client.patch(
                    f'/api/v1/maestros/productos/{producto.pk}/',
                    {'precio_venta': '20.00'}, format='json',
                )

        producto.refresh_from_db()
        self.assertEqual(str(producto.precio_venta), '10.00')
        self.assertFalse(Auditoria.objects.filter(
            accion__startswith='productos.producto.', object_id=producto.pk,
        ).exists())
