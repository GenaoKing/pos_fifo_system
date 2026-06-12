import os
import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.configuracion.models import ConfiguracionNegocio
from apps.reportes.models import CierreCaja


class CierreCajaPdfTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media_root)
        self.override.enable()

        User = get_user_model()
        self.user = User.objects.create_user(
            username='admin_cierre_pdf',
            email='admin_cierre_pdf@test.local',
            password='pass',
            rol='ADMIN',
            activo=True,
        )
        ConfiguracionNegocio.objects.create(nombre_negocio='Royal PDF')
        self.cierre = CierreCaja.objects.create(
            fecha=timezone.localdate(),
            cantidad_ventas=3,
            total_ventas=Decimal('1500.00'),
            total_descuentos=Decimal('50.00'),
            total_efectivo=Decimal('700.00'),
            total_transferencia=Decimal('300.00'),
            total_tarjeta=Decimal('400.00'),
            total_cobros_cxc=Decimal('100.00'),
            cantidad_anulaciones=1,
            total_anulaciones=Decimal('25.00'),
            resumen_cajeros={
                'admin_cierre_pdf': {
                    'cantidad': 3,
                    'total': '1500.00',
                }
            },
            generado_por=self.user,
        )

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def test_descargar_pdf_cierre_genera_archivo_y_attachment(self):
        self.client.force_login(self.user)
        res = self.client.get(f'/reportes/pdf/cierre/{self.cierre.id}/')

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertIn('attachment', res['Content-Disposition'])
        contenido = b''.join(res.streaming_content)
        self.assertTrue(contenido.startswith(b'%PDF'))

        self.cierre.refresh_from_db()
        self.assertTrue(self.cierre.archivo_pdf)
        self.assertTrue(os.path.exists(self.cierre.archivo_pdf))
