from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.productos.models import Categoria, Producto


class IdentidadProductoA05Tests(TestCase):
    def setUp(self):
        self.categoria = Categoria.objects.create(nombre='A05')
        self.producto = Producto.objects.create(
            sku='A05-001',
            nombre='Producto A05',
            categoria=self.categoria,
            precio_venta='10.00',
        )

    def test_sku_no_cambia_por_guardado_directo(self):
        self.producto.sku = 'A05-ALTERADO'

        with self.assertRaisesMessage(
            ValidationError,
            'El SKU es inmutable despues de crear el producto.',
        ):
            self.producto.save()

        self.producto.refresh_from_db()
        self.assertEqual(self.producto.sku, 'A05-001')

    def test_sku_no_cambia_por_update_masivo(self):
        with self.assertRaisesMessage(
            ValidationError,
            'Campos inmutables de Producto: sku',
        ):
            Producto.objects.filter(pk=self.producto.pk).update(sku='OTRO')

        self.producto.refresh_from_db()
        self.assertEqual(self.producto.sku, 'A05-001')

    def test_identidad_cloud_adopta_null_y_luego_es_inmutable(self):
        self.producto.origen_cloud_id = 501
        self.producto.save(update_fields=['origen_cloud_id'])

        self.producto.origen_cloud_id = 502
        with self.assertRaisesMessage(
            ValidationError,
            'La identidad cloud es inmutable una vez adoptada.',
        ):
            self.producto.save(update_fields=['origen_cloud_id'])

        self.producto.refresh_from_db()
        self.assertEqual(self.producto.origen_cloud_id, 501)

    def test_bd_no_admite_identidad_cloud_ambigua(self):
        self.producto.origen_cloud_id = 503
        self.producto.save(update_fields=['origen_cloud_id'])

        with self.assertRaises(IntegrityError), transaction.atomic():
            Producto.objects.create(
                sku='A05-002',
                nombre='Otra fila',
                categoria=self.categoria,
                precio_venta='20.00',
                origen_cloud_id=503,
            )
