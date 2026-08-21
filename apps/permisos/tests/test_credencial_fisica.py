"""
apps/permisos/tests/test_credencial_fisica.py

Credencial fisica (carnet/tarjeta) como forma alternativa de credencial para
emitir un `AutorizacionOverride`.

Las dos invariantes que importan:

1. El codigo crudo NUNCA se persiste. Un dump de la BD no permite fabricar
   carnets.
2. Pasar el carnet autoriza una operacion puntual; NO abre sesion ni cambia el
   usuario del turno.
"""
import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.permisos import testing as permisos_testing
from apps.permisos import throttling
from apps.permisos.models import AutorizacionOverride, CredencialFisica

CODIGO = 'CARNET-SUPERVISOR-0042'


class CredencialFisicaTestCase(TestCase):

    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.supervisor = User.objects.create_user(
            username='supervisor_carnet',
            email='supervisor_carnet@test.local',
            password='pass',
            rol='ADMIN',
            activo=True,
        )
        self.credencial = CredencialFisica.registrar(
            usuario=self.supervisor,
            codigo=CODIGO,
            etiqueta='Carnet supervisor',
        )

    def tearDown(self):
        cache.clear()


class AlmacenamientoTests(CredencialFisicaTestCase):

    def test_el_codigo_crudo_no_se_persiste(self):
        fila = CredencialFisica.objects.values().get(pk=self.credencial.pk)

        self.assertNotIn(CODIGO, json.dumps(fila, default=str))
        self.assertEqual(len(self.credencial.codigo_hash), 64)

    def test_un_codigo_corto_se_rechaza(self):
        """Un codigo de 3 caracteres se adivina por fuerza bruta."""
        with self.assertRaises(ValueError):
            CredencialFisica.registrar(usuario=self.supervisor, codigo='123')

    def test_dos_credenciales_no_pueden_compartir_codigo(self):
        from django.db import IntegrityError

        with self.assertRaises(IntegrityError):
            CredencialFisica.registrar(usuario=self.supervisor, codigo=CODIGO)


class ResolucionTests(CredencialFisicaTestCase):

    def test_resuelve_al_usuario(self):
        self.assertEqual(CredencialFisica.resolver(CODIGO), self.supervisor)

    def test_el_lector_puede_agregar_espacios_o_salto_de_linea(self):
        """Un keyboard wedge suele cerrar la lectura con CR/LF."""
        self.assertEqual(CredencialFisica.resolver(f'  {CODIGO}\r\n'), self.supervisor)

    def test_codigo_desconocido_no_resuelve(self):
        self.assertIsNone(CredencialFisica.resolver('CARNET-QUE-NO-EXISTE'))

    def test_codigo_vacio_no_resuelve(self):
        self.assertIsNone(CredencialFisica.resolver(''))
        self.assertIsNone(CredencialFisica.resolver(None))

    def test_credencial_dada_de_baja_no_resuelve(self):
        """Una tarjeta reportada como perdida deja de servir sin tocar al usuario."""
        self.credencial.dar_de_baja()

        self.assertIsNone(CredencialFisica.resolver(CODIGO))
        self.credencial.refresh_from_db()
        self.assertIsNotNone(self.credencial.fecha_baja)

    def test_usuario_inactivo_no_resuelve(self):
        """
        El cajero que se va no autoriza nada aunque su carnet siga circulando.

        `activo` es el flag real de este modelo: `Usuario` extiende
        `AbstractBaseUser`, donde `is_active` es un atributo de clase y no un
        campo. `resolver` chequea los dos igual, por si algun dia cambia.
        """
        self.supervisor.activo = False
        self.supervisor.save(update_fields=['activo'])

        self.assertIsNone(CredencialFisica.resolver(CODIGO))


class EndpointCredencialTests(CredencialFisicaTestCase):
    """El carnet emite el token igual que usuario+contrasena."""

    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.cajera = User.objects.create_user(
            username='cajera_carnet',
            email='cajera_carnet@test.local',
            password='pass',
            rol='CAJERA',
            activo=True,
        )
        negocio = permisos_testing.crear_negocio('Negocio Carnet')
        permisos_testing.habilitar_cajero(
            self.cajera, negocio=negocio, permisos=['ventas.crear'],
        )
        permisos_testing.asignar(
            self.supervisor,
            permisos_testing.crear_rol(
                negocio, 'Supervisor Carnet', ['ventas.autorizar_descuento'],
            ),
        )
        self.client.force_login(self.cajera)

    def _pedir(self, **over):
        cuerpo = {
            'credencial': CODIGO,
            'operacion': 'ventas.descuento',
            'monto': '50.00',
        }
        cuerpo.update(over)
        return self.client.post(
            reverse('caja:api_validar_admin'),
            data=json.dumps(cuerpo),
            content_type='application/json',
        )

    def test_el_carnet_emite_un_token(self):
        resp = self._pedir()

        datos = resp.json()
        self.assertTrue(datos['valido'], resp.content)
        autorizacion = AutorizacionOverride.objects.get()
        self.assertEqual(autorizacion.autorizado_por, self.supervisor)
        self.assertEqual(autorizacion.solicitado_por, self.cajera)

    def test_pasar_el_carnet_no_cambia_la_sesion(self):
        """Autoriza una operacion; no es un login."""
        self._pedir()

        self.assertEqual(int(self.client.session['_auth_user_id']), self.cajera.pk)

    def test_carnet_desconocido_no_emite_nada(self):
        resp = self._pedir(credencial='CARNET-FALSIFICADO-99')

        self.assertFalse(resp.json()['valido'])
        self.assertFalse(AutorizacionOverride.objects.exists())

    def test_carnet_de_alguien_sin_permiso_no_emite_nada(self):
        """El carnet identifica; el permiso es lo que autoriza."""
        CredencialFisica.registrar(
            usuario=self.cajera, codigo='CARNET-CAJERA-0001',
        )

        resp = self._pedir(credencial='CARNET-CAJERA-0001')

        self.assertFalse(resp.json()['valido'])
        self.assertFalse(AutorizacionOverride.objects.exists())

    def test_carnet_dado_de_baja_no_emite_nada(self):
        self.credencial.dar_de_baja()

        resp = self._pedir()

        self.assertFalse(resp.json()['valido'])
        self.assertFalse(AutorizacionOverride.objects.exists())


class ThrottlingTests(EndpointCredencialTests):
    """
    Sin freno, el cajero al que se le quiere poner el control puede iterar
    codigos desde la consola del navegador hasta dar con uno valido.
    """

    def test_tras_demasiados_fallos_el_endpoint_corta(self):
        for _ in range(throttling.INTENTOS_MAX):
            self._pedir(credencial='CARNET-FALSIFICADO-99')

        resp = self._pedir(credencial='CARNET-FALSIFICADO-99')
        self.assertEqual(resp.status_code, 429)

    def test_el_freno_aplica_aunque_despues_use_el_carnet_bueno(self):
        """Si no, bastaria con alternar para reiniciar el presupuesto."""
        for _ in range(throttling.INTENTOS_MAX):
            self._pedir(credencial='CARNET-FALSIFICADO-99')

        resp = self._pedir()
        self.assertEqual(resp.status_code, 429)
        self.assertFalse(AutorizacionOverride.objects.exists())

    def test_un_exito_limpia_el_historial_de_fallos(self):
        for _ in range(throttling.INTENTOS_MAX - 1):
            self._pedir(credencial='CARNET-FALSIFICADO-99')

        self.assertTrue(self._pedir().json()['valido'])

        # El contador quedo en cero: vuelve a haber presupuesto completo.
        for _ in range(throttling.INTENTOS_MAX - 1):
            self._pedir(credencial='CARNET-FALSIFICADO-99')
        self.assertTrue(self._pedir().json()['valido'])


class MotivoPorOperacionTests(CredencialFisicaTestCase):
    """
    Regresion del aflojamiento del motivo.

    `emitir()` exige motivo por diseno: sin el, la traza dice QUIEN aprobo pero
    no POR QUE. Se afloja SOLO para descuentos. Este test existe para que nadie
    lo extienda al resto por descuido.
    """

    def test_descuento_admite_motivo_vacio(self):
        autorizacion, _ = AutorizacionOverride.emitir(
            operacion=AutorizacionOverride.OP_VENTA_DESCUENTO,
            autorizado_por=self.supervisor,
            motivo='',
        )
        self.assertEqual(autorizacion.motivo, '')

    def test_retiro_de_caja_sigue_exigiendo_motivo(self):
        with self.assertRaises(ValueError):
            AutorizacionOverride.emitir(
                operacion=AutorizacionOverride.OP_CAJA_RETIRO,
                autorizado_por=self.supervisor,
                motivo='',
            )

    def test_exceso_de_credito_sigue_exigiendo_motivo(self):
        with self.assertRaises(ValueError):
            AutorizacionOverride.emitir(
                operacion=AutorizacionOverride.OP_CREDITO_EXCEDER_LIMITE,
                autorizado_por=self.supervisor,
                motivo='',
            )
