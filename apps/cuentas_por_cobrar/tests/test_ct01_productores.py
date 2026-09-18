"""
apps/cuentas_por_cobrar/tests/test_ct01_productores.py

C05 punto 6 — los productores de auditoría de CxC usan el contrato CT-01
(`registrar_mutacion`), no el adaptador legacy `Auditoria.registrar`.

Cubre los que mueven dinero/saldos: cuenta creada, abono registrado, abono
anulado y cuenta anulada por anulación de venta. Reutiliza el fixture de
`test_auditoria_cxc` (un cajero, un cliente a crédito y stock).
"""
from decimal import Decimal

from apps.auditoria.models import Auditoria
from apps.cuentas_por_cobrar.models import CuentaPorCobrar, PagoCxC
from apps.cuentas_por_cobrar.services import (
    anular_cuenta_por_venta,
    anular_pago_cxc_service,
    registrar_pago_cxc_service,
)
from apps.ventas.services import anular_venta_service

from .test_auditoria_cxc import CxCTestCase


class ProductoresCT01CxCTests(CxCTestCase):
    def _cuenta(self):
        venta = self._vender_credito()
        return venta, CuentaPorCobrar.objects.get(venta=venta)

    def test_cuenta_creada_emite_evento_ct01(self):
        _venta, cuenta = self._cuenta()
        evento = Auditoria.objects.get(
            accion='cuentas_por_cobrar.cuenta.creada',
            object_id=cuenta.id,
        )
        self.assertEqual(evento.schema_version, 'audit.event.v1')
        self.assertEqual(evento.canal, Auditoria.Canal.POS_LOCAL)
        self.assertEqual(evento.resultado, Auditoria.Resultado.SUCCEEDED)
        self.assertEqual(evento.entity_type, CuentaPorCobrar._meta.label)
        self.assertEqual(evento.datos_nuevos['saldo'], str(cuenta.saldo))

    def test_abono_registrado_emite_evento_ct01_con_idempotencia(self):
        _venta, cuenta = self._cuenta()
        pago = registrar_pago_cxc_service(
            cuenta_id=cuenta.id, usuario=self.cajera,
            metodo='EFECTIVO', monto=Decimal('50.00'),
            clave_idempotencia='op-abono-ct01',
        )
        evento = Auditoria.objects.get(
            accion='cuentas_por_cobrar.abono.registrado',
            object_id=pago.id,
        )
        self.assertEqual(evento.entity_type, PagoCxC._meta.label)
        self.assertEqual(evento.resultado, Auditoria.Resultado.SUCCEEDED)
        self.assertEqual(evento.datos_nuevos['monto'], '50.00')
        # La clave opaca va en `idempotencia_key` (CharField), no en
        # `correlacion_id` (UUIDField): un texto no-UUID reventaría el save.
        self.assertEqual(evento.idempotencia_key, 'op-abono-ct01')
        self.assertIsNone(evento.correlacion_id)

    def test_abono_anulado_emite_evento_ct01(self):
        _venta, cuenta = self._cuenta()
        pago = registrar_pago_cxc_service(
            cuenta_id=cuenta.id, usuario=self.cajera,
            metodo='EFECTIVO', monto=Decimal('50.00'),
        )
        anular_pago_cxc_service(
            pago_id=pago.id, usuario=self.admin, motivo='Reversa de prueba',
        )
        evento = Auditoria.objects.get(
            accion='cuentas_por_cobrar.abono.anulado',
            object_id=pago.id,
        )
        self.assertEqual(evento.resultado, Auditoria.Resultado.SUCCEEDED)
        self.assertEqual(evento.datos_nuevos['estado'], PagoCxC.ESTADO_ANULADO)

    def test_cuenta_anulada_por_venta_emite_evento_ct01(self):
        venta, cuenta = self._cuenta()
        # Sin abonos aplicados, anular la venta anula la cuenta.
        anular_venta_service(
            venta_id=venta.id, usuario=self.admin,
            motivo='Anulacion de prueba CT-01',
        )
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.estado, CuentaPorCobrar.ESTADO_ANULADA)
        evento = Auditoria.objects.get(
            accion='cuentas_por_cobrar.cuenta.anulada',
            object_id=cuenta.id,
        )
        self.assertEqual(evento.resultado, Auditoria.Resultado.SUCCEEDED)
        # Ya no se emite el código legacy EDITAR para esta anulación.
        self.assertFalse(
            Auditoria.objects.filter(
                accion=Auditoria.TipoAccion.EDITAR, object_id=cuenta.id,
            ).exists()
        )
