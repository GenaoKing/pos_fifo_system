"""
CXC-IDEMP-CONC: N reintentos de cobro con la misma clave producen EXACTAMENTE un
efecto financiero. Complementa `IdempotenciaCobroTests` (2 llamadas) en
`test_auditoria_cxc.py` con la prueba de N reintentos y el respaldo de BD.
"""
from decimal import Decimal

from django.db import IntegrityError, transaction

from apps.cuentas_por_cobrar.models import PagoCxC
from apps.cuentas_por_cobrar.services import registrar_pago_cxc_service
from apps.cuentas_por_cobrar.tests.test_auditoria_cxc import CxCTestCase


class CobroIdempotenciaNReintentosTests(CxCTestCase):
    def test_n_reintentos_misma_clave_un_solo_abono(self):
        venta = self._vender_credito()
        cuenta = venta.cuenta_por_cobrar
        saldo_inicial = cuenta.saldo

        pagos = [
            registrar_pago_cxc_service(
                cuenta_id=cuenta.id, usuario=self.admin, metodo='EFECTIVO',
                monto=Decimal('50.00'), clave_idempotencia='op-conc-001',
            )
            for _ in range(6)
        ]

        # Las 6 llamadas devuelven el MISMO pago.
        self.assertEqual(len({p.pk for p in pagos}), 1)
        self.assertEqual(
            cuenta.pagos_cxc.filter(estado=PagoCxC.ESTADO_APLICADO).count(), 1
        )

        # Un solo efecto financiero: el saldo bajo exactamente 50, una vez.
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.saldo, saldo_inicial - Decimal('50.00'))

    def test_constraint_db_rechaza_clave_duplicada(self):
        venta = self._vender_credito()
        cuenta = venta.cuenta_por_cobrar

        registrar_pago_cxc_service(
            cuenta_id=cuenta.id, usuario=self.admin, metodo='EFECTIVO',
            monto=Decimal('10.00'), clave_idempotencia='op-dup-001',
        )

        # Una insercion directa que salte el chequeo previo del service igual es
        # rechazada por la unica parcial: el respaldo real ante la carrera.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PagoCxC.objects.create(
                    cuenta=cuenta, metodo='EFECTIVO', monto=Decimal('10.00'),
                    registrado_por=self.admin, clave_idempotencia='op-dup-001',
                )
