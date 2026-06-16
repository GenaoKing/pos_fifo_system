from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image

from apps.api.serializers.maestros import ProductoSerializer
from apps.common.pdf.standard import business_header, document
from apps.configuracion.models import ConfiguracionNegocio
from apps.productos.models import Categoria, Producto
from apps.tenancy.context import reset_current_tenant, set_current_tenant
from apps.tenancy.management.base import TenantCommandMixin
from apps.tenancy.media import (
    config_logo_upload_to,
    producto_image_upload_to,
    tenant_media_name,
)
from apps.tenancy.models import Tenant


class TenantMediaPathTests(TestCase):
    @override_settings(TENANCY_DB_PER_TENANT_ENABLED=False)
    def test_legacy_paths_when_tenancy_is_disabled(self):
        self.assertEqual(tenant_media_name('productos', 'vaso.jpg'), 'productos/vaso.jpg')
        self.assertEqual(config_logo_upload_to(None, 'logo.png'), 'config/logo.png')

    @override_settings(TENANCY_DB_PER_TENANT_ENABLED=True)
    def test_paths_use_active_tenant_prefix(self):
        Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        tokens = set_current_tenant('demo', 'tnt_demo')
        try:
            self.assertEqual(producto_image_upload_to(None, 'vaso.jpg'), 'demo/productos/vaso.jpg')
            self.assertEqual(config_logo_upload_to(None, 'logo.png'), 'demo/config/logo.png')
        finally:
            reset_current_tenant(tokens)

    @override_settings(TENANCY_DB_PER_TENANT_ENABLED=True)
    def test_already_prefixed_name_is_not_prefixed_twice(self):
        Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        tokens = set_current_tenant('demo', 'tnt_demo')
        try:
            self.assertEqual(
                producto_image_upload_to(None, 'demo/productos/vaso.jpg'),
                'demo/productos/vaso.jpg',
            )
        finally:
            reset_current_tenant(tokens)


class MigrarMediaTenantCommandTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(tenant_key='demo', slug='demo', nombre='Demo')
        self.categoria = Categoria.objects.create(nombre='Vasos')
        self.producto = Producto.objects.create(
            sku='P-001',
            codigo_barras='P-001',
            nombre='Vaso',
            categoria=self.categoria,
            precio_venta='10.00',
            imagen='productos/vaso.jpg',
        )

    def _run_command(self, source_root, *args):
        out = StringIO()
        with patch.object(
            TenantCommandMixin,
            'run_in_tenant',
            autospec=True,
            side_effect=lambda _self, _tenant, callback: callback(),
        ):
            call_command(
                'migrar_media_tenant',
                *args,
                tenant='demo',
                source_media_root=str(source_root),
                stdout=out,
            )
        return out.getvalue()

    def test_dry_run_does_not_update_db_or_copy_file(self):
        with TemporaryDirectory() as source_dir, TemporaryDirectory() as media_dir:
            source = Path(source_dir)
            (source / 'productos').mkdir()
            (source / 'productos' / 'vaso.jpg').write_bytes(b'img')

            with override_settings(MEDIA_ROOT=media_dir):
                output = self._run_command(source)
                self.producto.refresh_from_db()

            self.assertIn('DRY-RUN', output)
            self.assertEqual(self.producto.imagen.name, 'productos/vaso.jpg')
            self.assertFalse((Path(media_dir) / 'demo' / 'productos' / 'vaso.jpg').exists())

    def test_apply_copies_file_and_updates_field_idempotently(self):
        with TemporaryDirectory() as source_dir, TemporaryDirectory() as media_dir:
            source = Path(source_dir)
            (source / 'productos').mkdir()
            (source / 'productos' / 'vaso.jpg').write_bytes(b'img')

            with override_settings(MEDIA_ROOT=media_dir):
                output = self._run_command(source, '--apply')
                self.producto.refresh_from_db()
                second_output = self._run_command(source, '--apply')

            self.assertIn('uploaded: 1', output)
            self.assertIn('updated: 1', output)
            self.assertEqual(self.producto.imagen.name, 'demo/productos/vaso.jpg')
            self.assertTrue((Path(media_dir) / 'demo' / 'productos' / 'vaso.jpg').exists())
            self.assertIn('already_prefixed: 1', second_output)

    def test_missing_file_is_reported_without_updating_field(self):
        with TemporaryDirectory() as source_dir:
            output = self._run_command(Path(source_dir), '--apply')
            self.producto.refresh_from_db()

        self.assertIn('missing: 1', output)
        self.assertEqual(self.producto.imagen.name, 'productos/vaso.jpg')


class TenantMediaSerializerTests(TestCase):
    def test_producto_serializer_uses_prefixed_image_url(self):
        categoria = Categoria.objects.create(nombre='Envases')
        producto = Producto.objects.create(
            sku='P-002',
            codigo_barras='P-002',
            nombre='Envase',
            categoria=categoria,
            precio_venta='25.00',
            imagen='demo/productos/envase.jpg',
        )

        data = ProductoSerializer(producto).data

        self.assertIn('demo/productos/envase.jpg', data['imagen_url'])


class RemoteLogoPdfTests(SimpleTestCase):
    def _png_bytes(self):
        buffer = BytesIO()
        Image.new('RGB', (12, 12), color='white').save(buffer, format='PNG')
        return buffer.getvalue()

    def test_business_header_reads_logo_without_local_path(self):
        logo = SimpleUploadedFile('logo.png', self._png_bytes(), content_type='image/png')

        class Config:
            nombre_negocio = 'Demo'
            rnc = ''
            telefono = ''
            direccion = ''

        Config.logo = logo

        buffer = BytesIO()
        doc = document(buffer)
        doc.build(business_header(Config()))

        self.assertTrue(buffer.getvalue().startswith(b'%PDF'))

    def test_business_header_reads_logo_when_storage_has_no_path(self):
        # Regresion: un FieldFile sobre AzureStorage lanza NotImplementedError en
        # .path; _logo_source debe caer al modo lectura por bytes, no propagar.
        from apps.common.pdf.standard import _logo_source

        png = self._png_bytes()

        class _BlobBackedLogo:
            name = 'demo/config/logo.png'

            def __bool__(self):
                return True

            @property
            def path(self):
                raise NotImplementedError("This backend doesn't support absolute paths.")

            def open(self, mode='rb'):
                return self

            def read(self):
                return png

            def close(self):
                pass

        class Config:
            nombre_negocio = 'Demo'
            rnc = ''
            telefono = ''
            direccion = ''
            logo = _BlobBackedLogo()

        source = _logo_source(Config())
        self.assertIsInstance(source, BytesIO)

        buffer = BytesIO()
        doc = document(buffer)
        doc.build(business_header(Config()))
        self.assertTrue(buffer.getvalue().startswith(b'%PDF'))
