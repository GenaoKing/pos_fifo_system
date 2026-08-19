"""
Tests del comando `verificar_sync` (Fase 0 de docs/ROADMAP_SYNC_CONFIABLE.md).

El comando es el detector de BUG-A: responde "que hechos de negocio ocurrieron
sin que se encolara su evento de sync". Estos tests verifican que detecta la
perdida y que no genera falsos positivos.
"""
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal
from apps.sync.models import EventoSync
from apps.ventas.models import Venta

User = get_user_model()


class VerificarSyncTestsBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            'cajera_vs', 'cajera_vs@test.local', 'x', rol='CAJERA'
        )
        self.sucursal = Sucursal.objects.create(
            codigo='SD-001', nombre='Sucursal SD', activa=True,
            usuario_servicio=self.user,
        )
        self.categoria = Categoria.objects.create(nombre='Plasticos')
        self.producto = Producto.objects.create(
            sku='SKU-VS-1', nombre='Vaso', categoria=self.categoria,
            precio_venta=Decimal('25.00'), stock_minimo=5,
        )

    def _venta(self, numero, con_evento=True):
        venta = Venta.objects.create(
            numero_venta=numero,
            sucursal=self.sucursal,
            usuario=self.user,
            subtotal=Decimal('100.00'),
            total=Decimal('100.00'),
            estado='COMPLETADA',
        )
        if con_evento:
            EventoSync.objects.create(
                sucursal=self.sucursal,
                tipo_evento='VENTA_CREADA',
                objeto_referencia=numero,
                objeto_id_local=venta.pk,
                payload={'numero_venta': numero},
                hash_payload=f'hash-{numero}',
                estado='CONFIRMADO',
            )
        return venta

    def _correr(self, *args):
        out = StringIO()
        call_command('verificar_sync', *args, stdout=out)
        return out.getvalue()

    def _correr_json(self, *args):
        import json
        return json.loads(self._correr('--json', *args))

    @staticmethod
    def _numero(dia=None, seq=1):
        dia = dia or timezone.localdate()
        return f'V-{dia.strftime("%Y%m%d")}-{seq:04d}'


class DeteccionDeEventosFaltantesTests(VerificarSyncTestsBase):
    def test_venta_con_evento_no_se_reporta(self):
        self._venta(self._numero(seq=1), con_evento=True)

        reporte = self._correr_json()

        self.assertEqual(reporte['sin_evento']['ventas']['sin_evento'], 0)
        self.assertEqual(reporte['sin_evento']['ventas']['total_en_ventana'], 1)

    def test_venta_sin_evento_se_reporta_con_su_referencia(self):
        """El caso de BUG-A: la venta existe, el evento nunca se encolo."""
        numero = self._numero(seq=1)
        self._venta(numero, con_evento=False)

        reporte = self._correr_json()
        ventas = reporte['sin_evento']['ventas']

        self.assertEqual(ventas['sin_evento'], 1)
        self.assertIn(numero, ventas['referencias'])
        hoy = timezone.localdate().isoformat()
        self.assertEqual(ventas['por_dia'][hoy], 1)

    def test_mezcla_de_ventas_con_y_sin_evento(self):
        self._venta(self._numero(seq=1), con_evento=True)
        self._venta(self._numero(seq=2), con_evento=False)
        self._venta(self._numero(seq=3), con_evento=False)

        reporte = self._correr_json()

        self.assertEqual(reporte['sin_evento']['ventas']['total_en_ventana'], 3)
        self.assertEqual(reporte['sin_evento']['ventas']['sin_evento'], 2)

    def test_evento_de_otro_tipo_no_cuenta_como_cobertura(self):
        """
        Un INVENTARIO_SNAPSHOT con el mismo objeto_id_local no debe hacer
        pasar por cubierta a una venta sin su VENTA_CREADA.
        """
        numero = self._numero(seq=1)
        venta = self._venta(numero, con_evento=False)
        EventoSync.objects.create(
            sucursal=self.sucursal,
            tipo_evento='INVENTARIO_SNAPSHOT',
            objeto_id_local=venta.pk,
            payload={},
            hash_payload='hash-snapshot',
            estado='CONFIRMADO',
        )

        reporte = self._correr_json()

        self.assertEqual(reporte['sin_evento']['ventas']['sin_evento'], 1)

    def test_ventana_de_dias_excluye_lo_viejo(self):
        vieja = self._venta(self._numero(seq=1), con_evento=False)
        Venta.objects.filter(pk=vieja.pk).update(
            fecha_venta=timezone.now() - timezone.timedelta(days=120)
        )

        reporte = self._correr_json('--dias=30')

        self.assertEqual(reporte['sin_evento']['ventas']['total_en_ventana'], 0)
        self.assertEqual(reporte['sin_evento']['ventas']['sin_evento'], 0)


class HuecosDeNumeracionTests(VerificarSyncTestsBase):
    def test_numeracion_continua_no_reporta_huecos(self):
        for seq in (1, 2, 3):
            self._venta(self._numero(seq=seq))

        reporte = self._correr_json()

        self.assertEqual(reporte['huecos_numeracion'], [])

    def test_detecta_hueco_al_inicio_del_dia(self):
        """
        Patron exacto del hueco de Royal Plast del 2026-06-23: el dia arranca
        en la #8 porque las 7 primeras nunca llegaron.
        """
        for seq in (8, 9, 10):
            self._venta(self._numero(seq=seq))

        reporte = self._correr_json()

        self.assertEqual(len(reporte['huecos_numeracion']), 1)
        hueco = reporte['huecos_numeracion'][0]
        self.assertEqual(hueco['presentes'], 3)
        self.assertEqual(hueco['maximo'], 10)
        self.assertEqual(hueco['faltan'], 7)
        self.assertEqual(hueco['numeros'], [1, 2, 3, 4, 5, 6, 7])

    def test_detecta_hueco_intermedio(self):
        for seq in (1, 2, 5):
            self._venta(self._numero(seq=seq))

        reporte = self._correr_json()

        hueco = reporte['huecos_numeracion'][0]
        self.assertEqual(hueco['numeros'], [3, 4])

    def test_venta_temprana_en_el_borde_de_la_ventana_no_inventa_hueco(self):
        """
        Regresion: si la ventana corta a mitad del dia mas viejo, las ventas de
        esa manana quedan fuera y el detector inventa un hueco. La ventana debe
        arrancar a medianoche local.

        Caso real: --dias=90 corrido a las 12:19 dejo fuera una venta de las
        12:13 y reporto un hueco inexistente en Royal Plast.
        """
        dia_borde = timezone.localdate() - timezone.timedelta(days=90)
        tz = timezone.get_current_timezone()

        for seq, hora in ((1, 8), (2, 14), (3, 18)):
            venta = self._venta(self._numero(dia=dia_borde, seq=seq))
            Venta.objects.filter(pk=venta.pk).update(
                fecha_venta=timezone.make_aware(
                    timezone.datetime.combine(
                        dia_borde, timezone.datetime.min.time()
                    ),
                    tz,
                ) + timezone.timedelta(hours=hora)
            )

        reporte = self._correr_json('--dias=90')

        self.assertEqual(
            reporte['huecos_numeracion'], [],
            'La ventana corto a mitad del dia e invento un hueco.',
        )

    def test_numero_con_formato_no_reconocido_no_rompe(self):
        self._venta('LEGACY-0001')
        self._venta(self._numero(seq=1))

        reporte = self._correr_json()

        self.assertEqual(reporte['huecos_numeracion'], [])


@override_settings(SUCURSAL_CODIGO='SD-001')
class DiagnosticoDeConfiguracionTests(VerificarSyncTestsBase):
    @override_settings(
        SYNC_ENABLED=False,
        CLOUD_API_URL='https://cloud.example',
        CLOUD_API_TOKEN='token-x',
    )
    def test_cloud_configurado_con_sync_apagado_es_critico(self):
        """La bandera roja de BUG-A: hay cloud pero la emision esta apagada."""
        reporte = self._correr_json()
        alertas = reporte['configuracion']['alertas']

        self.assertTrue(
            any(a.startswith('CRITICO') for a in alertas),
            f'Se esperaba alerta CRITICO, se obtuvo: {alertas}',
        )

    @override_settings(SYNC_ENABLED=True, CLOUD_API_URL='', CLOUD_API_TOKEN='')
    def test_sync_encendido_sin_destino_alerta(self):
        reporte = self._correr_json()
        alertas = reporte['configuracion']['alertas']

        self.assertTrue(any('nunca se envian' in a for a in alertas), alertas)

    @override_settings(
        SYNC_ENABLED=True,
        CLOUD_API_URL='https://cloud.example',
        CLOUD_API_TOKEN='token-x',
    )
    def test_configuracion_coherente_no_alerta(self):
        reporte = self._correr_json()

        self.assertEqual(reporte['configuracion']['alertas'], [])
        self.assertEqual(
            reporte['configuracion']['sucursal_resuelta'], str(self.sucursal)
        )

    @override_settings(SUCURSAL_CODIGO='NO-EXISTE', SYNC_ENABLED=True,
                       CLOUD_API_URL='https://cloud.example',
                       CLOUD_API_TOKEN='token-x')
    def test_sucursal_inexistente_alerta(self):
        reporte = self._correr_json()
        alertas = reporte['configuracion']['alertas']

        self.assertTrue(any('no corresponde a ninguna Sucursal' in a for a in alertas), alertas)


class GuardarrailBaseCloudTests(VerificarSyncTestsBase):
    """
    En el cloud, los eventos recibidos se guardan sin `objeto_id_local`, asi que
    el analisis "sin evento" daria todo como faltante. El comando debe avisarlo
    en vez de reportar una perdida masiva falsa.
    """

    def test_base_de_sucursal_no_se_marca_como_cloud(self):
        self._venta(self._numero(seq=1), con_evento=True)

        reporte = self._correr_json()

        self.assertFalse(reporte['parece_base_cloud'])

    def test_eventos_sin_objeto_id_local_se_detectan_como_cloud(self):
        self._venta(self._numero(seq=1), con_evento=False)
        EventoSync.objects.create(
            sucursal=self.sucursal, tipo_evento='VENTA_CREADA',
            objeto_id_local=None,
            payload={}, hash_payload='h-cloud', estado='CONFIRMADO',
        )

        reporte = self._correr_json()

        self.assertTrue(reporte['parece_base_cloud'])
        self.assertIn('lado CLOUD', self._correr())

    def test_cola_vacia_no_se_confunde_con_cloud(self):
        reporte = self._correr_json()

        self.assertFalse(reporte['parece_base_cloud'])


class SaludDeColaTests(VerificarSyncTestsBase):
    @override_settings(SYNC_MAX_RETRIES=3)
    def test_reporta_eventos_atascados_sobre_max_retries(self):
        EventoSync.objects.create(
            sucursal=self.sucursal, tipo_evento='VENTA_CREADA',
            payload={}, hash_payload='h-atascado', estado='ERROR', intentos=5,
        )
        EventoSync.objects.create(
            sucursal=self.sucursal, tipo_evento='VENTA_CREADA',
            payload={}, hash_payload='h-sano', estado='PENDIENTE', intentos=1,
        )

        reporte = self._correr_json()

        self.assertEqual(reporte['cola']['atascados_sobre_max_retries'], 1)
        self.assertEqual(reporte['cola']['por_estado']['ERROR'], 1)
        self.assertEqual(reporte['cola']['por_estado']['PENDIENTE'], 1)

    def test_salida_legible_no_revienta(self):
        """La salida humana es el camino real de uso; no debe lanzar."""
        self._venta(self._numero(seq=2), con_evento=False)

        salida = self._correr('--dias=90')

        self.assertIn('VERIFICACION DE SYNC', salida)
        self.assertIn('HUECOS EN LA NUMERACION', salida)
