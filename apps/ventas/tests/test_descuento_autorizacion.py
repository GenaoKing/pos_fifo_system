"""
apps/ventas/tests/test_descuento_autorizacion.py

Gate opcional de descuentos: cuando el negocio lo activa, un descuento por
encima de la tolerancia no se puede cerrar sin la autorizacion puntual de
alguien con `ventas.autorizar_descuento`.

La invariante que protegen estos tests es una sola: **el gate vive en el
servidor**. La UI decide si abre el modal, pero un POST directo — el caso real,
un cajero con la consola del navegador abierta — tiene que chocar contra la
misma regla.
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.auditoria.models import Auditoria
from apps.permisos import testing as permisos_testing
from apps.permisos.models import AutorizacionOverride, Permiso
from apps.ventas.models import Venta
from apps.ventas.services import PermisoDenegadoError, StockInsuficienteError

from .test_ventas_service import VentaServiceTestCase


class DescuentoAutorizacionTestCase(VentaServiceTestCase):
    """Fixture: un supervisor que puede autorizar y helpers de descuento."""

    def setUp(self):
        super().setUp()

        User = get_user_model()
        self.supervisor = User.objects.create_user(
            username='supervisor_descuento',
            email='supervisor_descuento@test.local',
            password='pass',
            rol='ADMIN',
            activo=True,
        )
        permisos_testing.habilitar_cajero(
            self.supervisor,
            permisos=['ventas.autorizar_descuento'],
        )

    # -- helpers ------------------------------------------------------------

    def _activar_gate(self, *, monto='0.00', porcentaje='0.00', motivo='NINGUNO'):
        return self._set_config(
            descuento_requiere_autorizacion=True,
            descuento_tolerancia_monto=Decimal(monto),
            descuento_tolerancia_porcentaje=Decimal(porcentaje),
            descuento_motivo_modo=motivo,
        )

    def _carrito_con_descuento(self, descuento):
        """Un item de 2 x $100 con el descuento indicado. Subtotal = $200."""
        return [{
            'id': self.producto.id,
            'cantidad': 2,
            'precio_venta': '100.00',
            'descuento': str(descuento),
        }]

    def _vender_con_descuento(self, descuento, *, usuario=None, token=None):
        descuento = Decimal(str(descuento))
        extra = {}
        if token is not None:
            extra['descuento_override_token'] = token
        return self._vender(
            usuario=usuario,
            carrito=self._carrito_con_descuento(descuento),
            total=str(Decimal('200.00') - descuento),
            **extra,
        )

    def _emitir_token(self, *, monto='50.00', motivo='Regateo', operacion=None,
                      solicitante=None, minutos=5):
        _, token = AutorizacionOverride.emitir(
            operacion=operacion or AutorizacionOverride.OP_VENTA_DESCUENTO,
            autorizado_por=self.supervisor,
            solicitado_por=solicitante if solicitante is not None else self.cajera,
            monto_maximo=Decimal(monto) if monto is not None else None,
            motivo=motivo,
            minutos=minutos,
        )
        return token


class GateApagadoTests(DescuentoAutorizacionTestCase):
    """
    No-regresion. El gate esta OFF por defecto: toda instalacion que hoy vende
    con descuento tiene que seguir vendiendo igual, sin pedir nada.
    """

    def test_con_el_gate_apagado_el_descuento_no_pide_autorizacion(self):
        venta = self._vender_con_descuento('50.00')

        self.assertEqual(venta.descuento_total, Decimal('50.00'))
        self.assertIsNone(venta.descuento_autorizado_por)
        self.assertEqual(venta.descuento_autorizacion_motivo, '')
        self.assertFalse(AutorizacionOverride.objects.exists())

    def test_el_permiso_de_aplicar_descuento_sigue_gateando(self):
        """El gate nuevo no reemplaza al viejo: son capas distintas."""
        User = get_user_model()
        sin_permiso = User.objects.create_user(
            username='cajera_sin_descuento',
            email='cajera_sin_descuento@test.local',
            password='pass',
            rol='CAJERA',
            activo=True,
        )
        permisos_testing.habilitar_cajero(sin_permiso, permisos=['ventas.crear'])

        with self.assertRaises(PermisoDenegadoError):
            self._vender_con_descuento('50.00', usuario=sin_permiso)


class ToleranciaTests(DescuentoAutorizacionTestCase):
    """
    La regla de tolerancia. El `or` entre monto y porcentaje es deliberado:
    con un `and`, dejar una de las dos en 0 anularia la otra y el negocio no
    podria configurar "hasta RD$100" sin configurar tambien un porcentaje.
    """

    def test_ambas_tolerancias_en_cero_exigen_autorizacion_para_cualquier_descuento(self):
        self._activar_gate(monto='0.00', porcentaje='0.00')

        with self.assertRaises(PermisoDenegadoError):
            self._vender_con_descuento('0.01')

        self.assertFalse(Venta.objects.exists())

    def test_descuento_cero_nunca_pide_autorizacion(self):
        self._activar_gate(monto='0.00', porcentaje='0.00')

        venta = self._vender_con_descuento('0.00')

        self.assertEqual(venta.descuento_total, Decimal('0.00'))

    def test_dentro_de_la_tolerancia_por_monto_pasa_libre(self):
        self._activar_gate(monto='100.00', porcentaje='0.00')

        venta = self._vender_con_descuento('100.00')

        self.assertEqual(venta.descuento_total, Decimal('100.00'))
        self.assertIsNone(venta.descuento_autorizado_por)

    def test_sobre_la_tolerancia_por_monto_exige_autorizacion(self):
        self._activar_gate(monto='100.00', porcentaje='0.00')

        with self.assertRaises(PermisoDenegadoError):
            self._vender_con_descuento('100.01')

    def test_dentro_de_la_tolerancia_por_porcentaje_pasa_libre(self):
        # Subtotal 200, tolerancia 5% => 10.00 libre.
        self._activar_gate(monto='0.00', porcentaje='5.00')

        venta = self._vender_con_descuento('10.00')

        self.assertEqual(venta.descuento_total, Decimal('10.00'))

    def test_sobre_la_tolerancia_por_porcentaje_exige_autorizacion(self):
        self._activar_gate(monto='0.00', porcentaje='5.00')

        with self.assertRaises(PermisoDenegadoError):
            self._vender_con_descuento('10.01')

    def test_alcanza_con_caer_dentro_de_una_de_las_dos_tolerancias(self):
        """Monto=150 y pct=1%: 150 supera el 1% pero cae dentro del monto."""
        self._activar_gate(monto='150.00', porcentaje='1.00')

        venta = self._vender_con_descuento('150.00')

        self.assertEqual(venta.descuento_total, Decimal('150.00'))


class ExencionDelAutorizadorTests(DescuentoAutorizacionTestCase):
    """
    Quien puede autorizar no se autoriza a si mismo.

    Ademas de evitar que el dueno que atiende caja un sabado quede trabado,
    esto hace que la autoautorizacion sea IMPOSIBLE por construccion: el gate
    solo se le aplica a quien no tiene el permiso, asi que nunca hay un camino
    en el que un usuario emita y consuma su propio token.
    """

    def test_quien_puede_autorizar_no_necesita_token(self):
        self._activar_gate(monto='0.00', porcentaje='0.00')

        venta = self._vender_con_descuento('80.00', usuario=self.supervisor)

        self.assertEqual(venta.descuento_total, Decimal('80.00'))
        self.assertIsNone(venta.descuento_autorizado_por)
        self.assertFalse(AutorizacionOverride.objects.exists())


class ConsumoDelTokenTests(DescuentoAutorizacionTestCase):
    """El token es de un solo uso, de vida corta y ligado a monto y operacion."""

    def setUp(self):
        super().setUp()
        self._activar_gate(monto='0.00', porcentaje='0.00')

    def test_token_valido_autoriza_y_queda_registrado_en_la_venta(self):
        token = self._emitir_token(monto='50.00', motivo='Cliente regatea')

        venta = self._vender_con_descuento('50.00', token=token)

        self.assertEqual(venta.descuento_total, Decimal('50.00'))
        self.assertEqual(venta.descuento_autorizado_por, self.supervisor)
        self.assertEqual(venta.descuento_autorizacion_motivo, 'Cliente regatea')

        autorizacion = AutorizacionOverride.objects.get()
        self.assertTrue(autorizacion.consumida)
        self.assertEqual(autorizacion.consumido_referencia, venta.numero_venta)

    def test_sin_token_es_rechazada_antes_de_tocar_inventario(self):
        stock_antes = self.producto.stock_actual

        with self.assertRaises(PermisoDenegadoError):
            self._vender_con_descuento('50.00')

        self.producto.refresh_from_db()
        self.assertEqual(self.producto.stock_actual, stock_antes)
        self.assertFalse(Venta.objects.exists())

    def test_token_de_otra_operacion_no_sirve(self):
        token = self._emitir_token(
            operacion=AutorizacionOverride.OP_CAJA_RETIRO,
            motivo='Retiro de caja',
        )

        with self.assertRaises(PermisoDenegadoError):
            self._vender_con_descuento('50.00', token=token)

        self.assertFalse(Venta.objects.exists())

    def test_token_vencido_no_sirve(self):
        token = self._emitir_token(monto='50.00')
        AutorizacionOverride.objects.update(
            expira=timezone.now() - timedelta(minutes=1)
        )

        with self.assertRaises(PermisoDenegadoError):
            self._vender_con_descuento('50.00', token=token)

    def test_token_ya_usado_no_sirve_para_una_segunda_venta(self):
        token = self._emitir_token(monto='50.00')

        self._vender_con_descuento('50.00', token=token)

        with self.assertRaises(PermisoDenegadoError):
            self._vender_con_descuento('50.00', token=token)

        self.assertEqual(Venta.objects.count(), 1)

    def test_token_por_menos_monto_no_cubre_un_descuento_mayor(self):
        """El supervisor autorizo $50; el cajero intenta descontar $80."""
        token = self._emitir_token(monto='50.00')

        with self.assertRaises(PermisoDenegadoError):
            self._vender_con_descuento('80.00', token=token)

        self.assertFalse(Venta.objects.exists())

    def test_token_de_otro_operador_no_sirve(self):
        token = self._emitir_token(monto='50.00', solicitante=self.supervisor)

        with self.assertRaises(PermisoDenegadoError):
            self._vender_con_descuento('50.00', token=token)

    def test_token_inexistente_no_sirve(self):
        with self.assertRaises(PermisoDenegadoError):
            self._vender_con_descuento('50.00', token='token-inventado')

    def test_una_venta_fallida_no_quema_la_autorizacion(self):
        """
        El consumo va dentro de la transaccion: si la venta revienta despues,
        el rollback libera el token. Si no, el supervisor tendria que volver a
        autorizar por un fallo que no fue suyo.
        """
        token = self._emitir_token(monto='50.00')

        with self.assertRaises(StockInsuficienteError):
            self._vender(
                carrito=[{
                    'id': self.producto.id,
                    'cantidad': 999,          # mas stock del que hay
                    'precio_venta': '100.00',
                    'descuento': '50.00',
                }],
                total='99850.00',
                descuento_override_token=token,
            )

        autorizacion = AutorizacionOverride.objects.get()
        self.assertFalse(autorizacion.consumida)

        # Y sigue sirviendo para la venta buena.
        venta = self._vender_con_descuento('50.00', token=token)
        self.assertEqual(venta.descuento_autorizado_por, self.supervisor)


class AuditoriaDescuentoTests(DescuentoAutorizacionTestCase):
    """
    El control real no es la tarjeta — es que quede escrito quien autorizo que.
    """

    def test_se_registra_una_entrada_de_auditoria(self):
        self._activar_gate(monto='0.00', porcentaje='0.00')
        token = self._emitir_token(monto='50.00', motivo='Cliente frecuente')

        venta = self._vender_con_descuento('50.00', token=token)

        entrada = Auditoria.objects.get(
            accion=Auditoria.TipoAccion.DESCUENTO_AUTORIZADO
        )
        self.assertEqual(entrada.usuario, self.cajera)
        self.assertIn(venta.numero_venta, entrada.descripcion)
        self.assertEqual(entrada.metadata['autorizado_por'], self.supervisor.username)
        self.assertEqual(entrada.metadata['motivo'], 'Cliente frecuente')

    def test_el_descuento_autorizado_viaja_en_el_payload_de_sync(self):
        """
        `AutorizacionOverride` no sincroniza al cloud; `Venta` si. Sin estos
        campos denormalizados el dueno no ve nada en el portal.
        """
        from apps.sync.serializers import serializar_venta

        self._activar_gate(monto='0.00', porcentaje='0.00')
        token = self._emitir_token(monto='50.00', motivo='Cliente frecuente')
        venta = self._vender_con_descuento('50.00', token=token)

        payload = serializar_venta(venta)

        self.assertEqual(payload['descuento_autorizado_por'], self.supervisor.username)
        self.assertEqual(payload['descuento_autorizacion_motivo'], 'Cliente frecuente')

    def test_una_venta_sin_autorizacion_serializa_los_campos_vacios(self):
        from apps.sync.serializers import serializar_venta

        venta = self._vender_con_descuento('0.00')
        payload = serializar_venta(venta)

        self.assertIsNone(payload['descuento_autorizado_por'])
        self.assertEqual(payload['descuento_autorizacion_motivo'], '')


class RenderDelPOSTests(DescuentoAutorizacionTestCase):
    """
    La pagina del POS se renderiza y lleva la config del gate.

    El JS decide si abre el modal con las mismas tolerancias que aplica el
    servidor; si la config no llegara al template, la UI dejaria cerrar ventas
    que el servidor rechaza recien al cobrar, con el cliente esperando.
    """

    def test_la_pagina_del_pos_lleva_la_config_de_descuentos(self):
        self._activar_gate(monto='100.00', porcentaje='5.00')
        self.client.force_login(self.cajera)

        resp = self.client.get(reverse('pos:punto_venta'))

        self.assertEqual(resp.status_code, 200)
        config = resp.context['pos_config_json']['descuento']
        self.assertTrue(config['requiere_autorizacion'])
        self.assertEqual(config['tolerancia_monto'], 100.0)
        self.assertEqual(config['tolerancia_porcentaje'], 5.0)
        # La cajera NO puede autorizar: la UI tiene que pedirle el carnet.
        self.assertFalse(config['usuario_autoriza'])

    def test_a_quien_puede_autorizar_la_ui_no_le_pide_nada(self):
        """Espejo de la exencion del servidor. Si divergen, sobra un modal."""
        self._activar_gate(monto='0.00', porcentaje='0.00')
        self.client.force_login(self.supervisor)

        resp = self.client.get(reverse('pos:punto_venta'))

        self.assertTrue(resp.context['pos_config_json']['descuento']['usuario_autoriza'])


class EndpointDescuentoTests(DescuentoAutorizacionTestCase):
    """El camino real del POS: pedir la autorizacion y usarla en la venta."""

    def setUp(self):
        super().setUp()
        self._activar_gate(monto='0.00', porcentaje='0.00')
        self.client.force_login(self.cajera)

    def _pedir(self, **over):
        cuerpo = {
            'username': self.supervisor.username,
            'password': 'pass',
            'operacion': 'ventas.descuento',
            'monto': '50.00',
        }
        cuerpo.update(over)
        return self.client.post(
            reverse('caja:api_validar_admin'),
            data=json.dumps(cuerpo),
            content_type='application/json',
        )

    def test_el_endpoint_emite_token_y_la_venta_lo_consume(self):
        resp = self._pedir()

        self.assertEqual(resp.status_code, 200, resp.content)
        datos = resp.json()
        self.assertTrue(datos['valido'], datos)

        venta = self._vender_con_descuento('50.00', token=datos['token'])
        self.assertEqual(venta.descuento_autorizado_por, self.supervisor)

    def test_sin_permiso_de_autorizar_no_emite_token(self):
        """La cajera no puede emitirse su propia autorizacion."""
        resp = self._pedir(username=self.cajera.username, password='pass')

        self.assertFalse(resp.json()['valido'])
        self.assertFalse(AutorizacionOverride.objects.exists())

    def test_el_motivo_no_es_obligatorio_por_defecto(self):
        """
        Donde se regatea, casi toda venta lleva descuento: exigir texto libre
        en cada una produce 400 filas que dicen "descuento". Lo decide el
        negocio, y el default es no pedirlo.
        """
        resp = self._pedir(motivo='')

        self.assertTrue(resp.json()['valido'], resp.content)
        self.assertEqual(AutorizacionOverride.objects.get().motivo, '')

    def test_con_modo_obligatorio_el_motivo_se_exige(self):
        self._activar_gate(monto='0.00', porcentaje='0.00', motivo='OBLIGATORIO')

        resp = self._pedir(motivo='')

        self.assertFalse(resp.json()['valido'])
        self.assertFalse(AutorizacionOverride.objects.exists())

    def test_la_vigencia_sale_de_la_configuracion(self):
        self._set_config(descuento_vigencia_minutos=15)

        resp = self._pedir()

        self.assertEqual(resp.json()['expira_en_minutos'], 15)

    def test_las_otras_operaciones_siguen_exigiendo_motivo(self):
        """
        Regresion: aflojar el motivo para descuentos NO debe aflojarlo para
        `caja.retiro` ni `credito.exceder_limite`, donde fue un hallazgo de
        auditoria deliberado.
        """
        # Se amplia el rol que ya tiene: `habilitar_cajero` crea un Negocio
        # nuevo por llamada y el slug chocaria.
        rol = self.supervisor.asignaciones_rol.get().rol
        rol.permisos.add(Permiso.objects.get(codigo='caja.administrar'))
        cache.clear()

        resp = self._pedir(operacion='caja.retiro', motivo='')

        self.assertFalse(resp.json()['valido'])
        self.assertIn('motivo', resp.json()['error'].lower())
