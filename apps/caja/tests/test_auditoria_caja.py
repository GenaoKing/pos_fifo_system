"""
apps/caja/tests/test_auditoria_caja.py

Regresion de los hallazgos de `docs/exploracion/AUDITORIA_CODIGO_APPS_CAJA.md`.
"""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.caja.models import Caja, MovimientoCaja, TurnoCaja, turno_abierto_de
from apps.inventario.models import Compra, DetalleCompra
from apps.permisos import testing as permisos_testing
from apps.permisos.testing import PERMISOS_VENTA
from apps.permisos.models import AutorizacionOverride
from apps.productos.models import Categoria, Producto
from apps.sucursales.models import Sucursal
from apps.ventas.models import Pago
from apps.ventas.services import procesar_venta_service

User = get_user_model()


class CajaTestCase(TestCase):
    def setUp(self):
        cache.clear()

        self.admin = User.objects.create_user(
            username='admin_caja_aud', email='admin_caja_aud@test.local',
            password='pass', rol='ADMIN', activo=True,
        )
        self.cajera = User.objects.create_user(
            username='cajera_caja_aud', email='cajera_caja_aud@test.local',
            password='pass', rol='CAJERA', activo=True,
        )
        permisos_testing.habilitar_cajero(
            self.cajera, permisos=[*PERMISOS_VENTA, 'caja.operar'],
        )

        self.caja = Caja.objects.create(nombre='Caja Auditoria', activa=True)

        self.categoria = Categoria.objects.create(nombre='Caja Auditoria')
        self.producto = Producto.objects.create(
            sku='CAJA-AUD-001', codigo_barras='CAJA-AUD-001',
            nombre='Producto caja', descripcion='', categoria=self.categoria,
            precio_venta=Decimal('100.00'), stock_minimo=1, activo=True,
            estado='nuevo', marca='', atributos={},
        )
        compra = Compra.objects.create(
            usuario=self.admin, proveedor='Proveedor Caja',
            numero_factura='FAC-CAJA-AUD', total=Decimal('400.00'),
        )
        DetalleCompra.objects.create(
            compra=compra, producto=self.producto, cantidad=20,
            costo_unitario=Decimal('20.00'), subtotal=Decimal('400.00'),
        )

    def tearDown(self):
        cache.clear()

    def _abrir_turno(self, usuario=None, fondo='100.00'):
        return TurnoCaja.objects.create(
            caja=self.caja, usuario=usuario or self.cajera,
            fondo_apertura=Decimal(fondo),
        )

    def _vender_efectivo(self, usuario=None, cantidad=1):
        return procesar_venta_service(
            usuario=usuario or self.cajera,
            datos={
                'carrito': [{
                    'id': self.producto.id, 'cantidad': cantidad,
                    'precio_venta': '100.00', 'descuento': '0.00',
                }],
                'metodo_pago': 'efectivo',
                'total': str(Decimal('100.00') * cantidad),
            },
        )


class PertenenciaAlTurnoTests(CajaTestCase):
    """CAJA-002: cada cobro en efectivo sabe a que turno pertenece."""

    def test_una_venta_con_turno_abierto_ata_su_pago_al_turno(self):
        turno = self._abrir_turno()

        venta = self._vender_efectivo()

        pago = venta.pagos.get()
        self.assertEqual(pago.turno_caja_id, turno.id)

    def test_sin_turno_abierto_el_pago_queda_sin_turno(self):
        """
        Sigue siendo posible cobrar sin caja abierta (canales sin arqueo). Lo
        que cambia es que ahora se distingue: antes se atribuia igual por
        coincidencia de usuario y fecha.
        """
        venta = self._vender_efectivo()

        self.assertIsNone(venta.pagos.get().turno_caja_id)

    def test_el_esperado_usa_el_vinculo_exacto(self):
        turno = self._abrir_turno(fondo='100.00')
        self._vender_efectivo(cantidad=2)   # 200 en efectivo

        desglose = turno.calcular_esperado()

        self.assertEqual(desglose['efectivo_ventas'], Decimal('200.00'))
        self.assertEqual(desglose['esperado'], Decimal('300.00'))

    def test_el_esperado_no_suma_el_efectivo_de_otro_turno(self):
        """
        La heuristica por usuario+fecha atribuia por coincidencia temporal.
        Con dos turnos del mismo cajero, cada uno recibe SOLO lo suyo.
        """
        primero = self._abrir_turno()
        self._vender_efectivo()               # 100 -> primero
        primero.cerrar(
            monto_contado=Decimal('200.00'), cerrado_por=self.cajera, notas='',
        )

        segundo = self._abrir_turno()
        self._vender_efectivo(cantidad=3)     # 300 -> segundo

        self.assertEqual(
            primero.calcular_esperado()['efectivo_ventas'], Decimal('100.00'),
        )
        self.assertEqual(
            segundo.calcular_esperado()['efectivo_ventas'], Decimal('300.00'),
        )

    def test_el_helper_resuelve_solo_turnos_abiertos(self):
        turno = self._abrir_turno()
        self.assertEqual(turno_abierto_de(self.cajera), turno)

        turno.cerrar(
            monto_contado=Decimal('100.00'), cerrado_por=self.cajera, notas='',
        )
        self.assertIsNone(turno_abierto_de(self.cajera))


class AutorizacionDeMovimientoTests(CajaTestCase):
    """CAJA-001: el retiro ya no se autoriza con un `admin_id` adivinable."""

    def setUp(self):
        super().setUp()
        permisos_testing.habilitar_cajero(
            self.admin, permisos=['caja.administrar', 'caja.operar'],
        )
        self.turno = self._abrir_turno()
        self.client.force_login(self.cajera)

    def _registrar(self, **over):
        cuerpo = {'tipo': 'RETIRO', 'monto': '50.00', 'descripcion': 'Retiro'}
        cuerpo.update(over)
        return self.client.post(
            reverse('caja:api_movimiento'),
            data=json.dumps(cuerpo), content_type='application/json',
        )

    def test_el_id_crudo_de_un_admin_ya_no_autoriza(self):
        resp = self._registrar(admin_id=self.admin.id)

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(MovimientoCaja.objects.count(), 0)

    def test_sin_autorizacion_no_hay_retiro(self):
        resp = self._registrar()

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(MovimientoCaja.objects.count(), 0)

    def test_con_un_token_valido_el_retiro_procede(self):
        _, token = AutorizacionOverride.emitir(
            operacion=AutorizacionOverride.OP_CAJA_RETIRO,
            autorizado_por=self.admin,
            solicitado_por=self.cajera,
            monto_maximo=Decimal('100.00'),
            motivo='Retiro para deposito bancario',
        )

        resp = self._registrar(override_token=token)

        self.assertEqual(resp.status_code, 200, resp.content)
        movimiento = MovimientoCaja.objects.get()
        self.assertEqual(movimiento.autorizado_por, self.admin)
        self.assertIn('deposito bancario', movimiento.descripcion)

    def test_un_token_por_menos_monto_no_cubre_el_retiro(self):
        _, token = AutorizacionOverride.emitir(
            operacion=AutorizacionOverride.OP_CAJA_RETIRO,
            autorizado_por=self.admin, solicitado_por=self.cajera,
            monto_maximo=Decimal('10.00'), motivo='Retiro chico',
        )

        resp = self._registrar(override_token=token, monto='50.00')

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(MovimientoCaja.objects.count(), 0)


class ValidacionDeImportesTests(CajaTestCase):
    """CAJA-006: apertura y conteo final no aceptan negativos."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.cajera)

    def test_fondo_de_apertura_negativo_es_rechazado(self):
        resp = self.client.post(
            reverse('caja:api_abrir'),
            data=json.dumps({'caja_id': self.caja.id, 'fondo_apertura': '-50.00'}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(TurnoCaja.objects.count(), 0)

    def test_monto_contado_negativo_es_rechazado(self):
        self._abrir_turno()

        resp = self.client.post(
            reverse('caja:api_cerrar'),
            data=json.dumps({'monto_contado': '-10.00'}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            TurnoCaja.objects.filter(estado='ABIERTO').count(), 1,
        )

    def test_json_invalido_es_400_no_500(self):
        resp = self.client.post(
            reverse('caja:api_abrir'),
            data='no soy json', content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)


class AlcanceYPermisosTests(CajaTestCase):
    """CAJA-003 y CAJA-004."""

    def test_un_usuario_sin_caja_operar_no_entra(self):
        pelado = User.objects.create_user(
            username='sin_caja', email='sin_caja@test.local',
            password='pass', rol='CAJERA', activo=True,
        )
        permisos_testing.habilitar_cajero(pelado, permisos=['ventas.crear'])
        self.client.force_login(pelado)

        resp = self.client.post(
            reverse('caja:api_abrir'),
            data=json.dumps({'caja_id': self.caja.id, 'fondo_apertura': '100.00'}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(TurnoCaja.objects.count(), 0)

    def test_el_cajero_por_defecto_si_puede_operar(self):
        """El gate no puede ser un muro: `caja.operar` va en el rol default."""
        from apps.permisos.catalogo import PERMISOS_CAJERO_DEFAULT

        self.assertIn('caja.operar', PERMISOS_CAJERO_DEFAULT)

    def test_no_se_puede_abrir_una_caja_de_otra_sucursal(self):
        otra = Sucursal.objects.create(codigo='OTRA-001', nombre='Otra', activa=True)
        caja_ajena = Caja.objects.create(
            nombre='Caja Ajena', activa=True, sucursal=otra,
        )
        propia = Sucursal.objects.create(codigo='MIA-001', nombre='Mia', activa=True)
        self.caja.sucursal = propia
        self.caja.save(update_fields=['sucursal'])

        self.client.force_login(self.cajera)
        with self.settings(SUCURSAL_CODIGO='MIA-001'):
            cache.clear()
            resp = self.client.post(
                reverse('caja:api_abrir'),
                data=json.dumps({
                    'caja_id': caja_ajena.id, 'fondo_apertura': '100.00',
                }),
                content_type='application/json',
            )

        self.assertIn(resp.status_code, (400, 404))
        self.assertFalse(TurnoCaja.objects.filter(caja=caja_ajena).exists())


class IdentidadDeCajaTests(CajaTestCase):
    """CAJA-008: la caja se identifica por algo que no cambia."""

    def test_cada_caja_tiene_identidad_estable(self):
        otra = Caja.objects.create(nombre='Otra', activa=True)

        self.assertIsNotNone(self.caja.origen_id)
        self.assertNotEqual(self.caja.origen_id, otra.origen_id)

    def test_la_identidad_sobrevive_al_renombre(self):
        identidad = self.caja.origen_id

        self.caja.nombre = 'Caja Renombrada'
        self.caja.save(update_fields=['nombre'])
        self.caja.refresh_from_db()

        self.assertEqual(self.caja.origen_id, identidad)

    def test_los_eventos_de_sync_transportan_la_identidad(self):
        from apps.sync import serializers

        turno = self._abrir_turno()
        payload = serializers.serializar_apertura_caja(turno)

        self.assertEqual(payload['caja_origen_id'], str(self.caja.origen_id))

    def test_el_receptor_resuelve_por_identidad_pese_al_renombre(self):
        """
        El escenario demostrado: apertura con un nombre, cierre con otro. Antes
        el cierre creaba OTRA caja y otro turno; el viejo quedaba abierto.
        """
        from apps.api.views.sync import _obtener_caja

        sucursal = Sucursal.objects.create(
            codigo='SYNC-001', nombre='Sync', activa=True,
        )
        caja = Caja.objects.create(
            nombre='Caja nombre viejo', activa=True, sucursal=sucursal,
        )

        resuelta = _obtener_caja(sucursal, 'Caja nombre nuevo', str(caja.origen_id))

        self.assertEqual(resuelta.pk, caja.pk)
        self.assertEqual(Caja.objects.filter(sucursal=sucursal).count(), 1)
        # El nombre se actualiza como atributo.
        self.assertEqual(resuelta.nombre, 'Caja nombre nuevo')


class TurnoCerradoTests(CajaTestCase):
    """CAJA-009: un movimiento atrasado no se cuelga de un turno cerrado."""

    def test_no_devuelve_un_turno_cerrado(self):
        from apps.api.views.sync import _buscar_turno_abierto

        sucursal = Sucursal.objects.create(
            codigo='CERR-001', nombre='Cerrada', activa=True,
        )
        caja = Caja.objects.create(nombre='Caja Cerr', activa=True, sucursal=sucursal)
        turno = TurnoCaja.objects.create(
            caja=caja, usuario=self.cajera, fondo_apertura=Decimal('100.00'),
        )
        turno.cerrar(
            monto_contado=Decimal('100.00'), cerrado_por=self.cajera, notas='',
        )

        encontrado = _buscar_turno_abierto(
            sucursal, caja.nombre, turno.fecha_apertura.isoformat(),
            str(caja.origen_id),
        )

        self.assertIsNone(encontrado)


class AdminInmutableTests(CajaTestCase):
    """CAJA-007: la historia de efectivo no se edita desde el admin."""

    def test_turnos_y_movimientos_son_de_solo_lectura(self):
        from django.contrib import admin as django_admin

        from apps.caja.admin import MovimientoCajaAdmin, TurnoCajaAdmin

        for clase, modelo in (
            (TurnoCajaAdmin, TurnoCaja),
            (MovimientoCajaAdmin, MovimientoCaja),
        ):
            with self.subTest(admin=clase.__name__):
                instancia = clase(modelo, django_admin.site)
                self.assertFalse(instancia.has_add_permission(None))
                self.assertFalse(instancia.has_change_permission(None))
                self.assertFalse(instancia.has_delete_permission(None))


class TurnoDestinoTests(CajaTestCase):
    """CAJA-010: un `turno_id` explicito no puede terminar en otro turno."""

    def setUp(self):
        super().setUp()
        permisos_testing.habilitar_cajero(
            self.admin, permisos=['caja.administrar', 'caja.operar'],
        )
        self.turno_cajera = self._abrir_turno(usuario=self.cajera)
        self.otra_caja = Caja.objects.create(nombre='Caja Admin', activa=True)
        self.turno_admin = TurnoCaja.objects.create(
            caja=self.otra_caja, usuario=self.admin,
            fondo_apertura=Decimal('500.00'),
        )

    def _gasto(self, usuario, **over):
        self.client.force_login(usuario)
        cuerpo = {'tipo': 'GASTO', 'monto': '30.00', 'descripcion': 'Gasto'}
        cuerpo.update(over)
        return self.client.post(
            reverse('caja:api_movimiento'),
            data=json.dumps(cuerpo), content_type='application/json',
        )

    def test_el_admin_con_turno_propio_registra_donde_pidio(self):
        """
        Antes el turno propio ganaba siempre: el gasto aterrizaba en el turno
        del admin y la respuesta decia "listo" sin mencionar el desvio.
        """
        resp = self._gasto(self.admin, turno_id=self.turno_cajera.id)

        self.assertEqual(resp.status_code, 200, resp.content)
        movimiento = MovimientoCaja.objects.get()
        self.assertEqual(movimiento.turno_id, self.turno_cajera.id)

    def test_sin_turno_id_el_admin_usa_el_suyo(self):
        resp = self._gasto(self.admin)

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(MovimientoCaja.objects.get().turno_id, self.turno_admin.id)

    def test_un_cajero_no_puede_apuntar_al_turno_de_otro(self):
        resp = self._gasto(self.cajera, turno_id=self.turno_admin.id)

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(MovimientoCaja.objects.count(), 0)

    def test_un_turno_fuera_de_alcance_da_404_no_500(self):
        ajena = Sucursal.objects.create(codigo='AJE-001', nombre='Ajena', activa=True)
        propia = Sucursal.objects.create(codigo='PRO-001', nombre='Propia', activa=True)
        caja_ajena = Caja.objects.create(
            nombre='Caja Lejos', activa=True, sucursal=ajena,
        )
        forastero = User.objects.create_user(
            username='forastero_caja', email='forastero@test.local',
            password='pass', rol='CAJERA', activo=True,
        )
        turno_ajeno = TurnoCaja.objects.create(
            caja=caja_ajena, usuario=forastero, fondo_apertura=Decimal('10.00'),
        )
        self.otra_caja.sucursal = propia
        self.otra_caja.save(update_fields=['sucursal'])

        with self.settings(SUCURSAL_CODIGO='PRO-001'):
            cache.clear()
            resp = self._gasto(self.admin, turno_id=turno_ajeno.id)

        self.assertEqual(resp.status_code, 404)
        self.assertEqual(MovimientoCaja.objects.count(), 0)


class UnSoloModeloDeAdminTests(CajaTestCase):
    """CAJA-011: la UI y el servidor responden lo mismo a "es admin?"."""

    def test_la_pagina_publica_la_decision_del_servidor(self):
        permisos_testing.habilitar_cajero(
            self.admin, permisos=['caja.administrar', 'caja.operar'],
        )
        self.client.force_login(self.admin)

        resp = self.client.get(reverse('caja:index'))

        self.assertTrue(resp.context['puede_administrar_caja'])

    def test_un_supervisor_con_el_permiso_rbac_si_administra(self):
        """
        El caso que la plantilla no sabia ver. Un supervisor con
        `caja.administrar` por rol custom NO tiene `rol = 'ADMIN'`: la UI lo
        trataba como cajero y le abria el soft-login pidiendole credenciales
        de otro, cuando el servidor ya lo auto-autorizaba. Ahora ambos lados
        responden lo mismo.
        """
        supervisor = User.objects.create_user(
            username='supervisor_caja', email='supervisor@test.local',
            password='pass', rol='CAJERA', activo=True,
        )
        permisos_testing.habilitar_cajero(
            supervisor, permisos=['caja.administrar', 'caja.operar'],
        )
        self.client.force_login(supervisor)

        self.assertNotEqual(supervisor.rol, 'ADMIN')

        resp = self.client.get(reverse('caja:index'))

        self.assertTrue(resp.context['puede_administrar_caja'])

    def test_un_cajero_comun_no_administra(self):
        self.client.force_login(self.cajera)

        resp = self.client.get(reverse('caja:index'))

        self.assertFalse(resp.context['puede_administrar_caja'])

    def test_la_plantilla_ya_no_decide_por_el_campo_legacy(self):
        import pathlib

        from django.conf import settings

        plantilla = pathlib.Path(settings.BASE_DIR) / 'templates' / 'caja' / 'index.html'
        fuente = plantilla.read_text(encoding='utf-8')

        self.assertNotIn("'{{ request.user.rol }}' === 'ADMIN'", fuente)
        self.assertIn('puede_administrar_caja', fuente)


class CeroNoEsAusenciaTests(CajaTestCase):
    """CAJA-013: un turno que cerro en cero se ve como cerrado en cero."""

    def test_el_detalle_reporta_los_ceros(self):
        permisos_testing.habilitar_cajero(
            self.admin, permisos=['caja.administrar', 'caja.operar'],
        )
        turno = self._abrir_turno(usuario=self.admin, fondo='0.00')
        turno.cerrar(
            monto_contado=Decimal('0.00'), cerrado_por=self.admin, notas='',
        )
        self.client.force_login(self.admin)

        resp = self.client.get(
            reverse('caja:api_detalle', args=[turno.id]),
        )

        datos = resp.json()['turno']
        self.assertEqual(datos['esperado'], '0.00')
        self.assertEqual(datos['contado'], '0.00')
        self.assertEqual(datos['diferencia'], '0.00')

    def test_un_turno_abierto_si_reporta_ausencia(self):
        """El contraste: NULL sigue siendo None. Cero y "sin dato" se separan."""
        turno = self._abrir_turno()
        self.client.force_login(self.cajera)

        datos = self.client.get(
            reverse('caja:api_detalle', args=[turno.id]),
        ).json()['turno']

        self.assertIsNone(datos['contado'])

    def test_el_detalle_de_otra_sucursal_no_se_lee(self):
        ajena = Sucursal.objects.create(codigo='DET-001', nombre='Det', activa=True)
        propia = Sucursal.objects.create(codigo='DET-002', nombre='Det2', activa=True)
        caja_ajena = Caja.objects.create(nombre='Caja Det', activa=True, sucursal=ajena)
        forastero = User.objects.create_user(
            username='forastero_det', email='forastero_det@test.local',
            password='pass', rol='CAJERA', activo=True,
        )
        turno_ajeno = TurnoCaja.objects.create(
            caja=caja_ajena, usuario=forastero, fondo_apertura=Decimal('10.00'),
        )
        self.caja.sucursal = propia
        self.caja.save(update_fields=['sucursal'])
        self.client.force_login(self.cajera)

        with self.settings(SUCURSAL_CODIGO='DET-002'):
            cache.clear()
            resp = self.client.get(
                reverse('caja:api_detalle', args=[turno_ajeno.id]),
            )

        self.assertEqual(resp.status_code, 404)
