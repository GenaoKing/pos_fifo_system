"""
PAG-CXC-CAJA: el historial de turnos ya no corta en 50 sin aviso; se pagina y el
operador puede recorrer todo el historial.
"""
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from apps.caja.models import TurnoCaja
from apps.caja.tests.test_auditoria_caja import CajaTestCase


class HistorialPaginacionTests(CajaTestCase):
    def test_historial_paginado(self):
        for _ in range(55):
            TurnoCaja.objects.create(
                caja=self.caja, usuario=self.cajera,
                fondo_apertura=Decimal('100.00'),
                estado='CERRADO', fecha_cierre=timezone.now(),
            )
        self.client.force_login(self.admin)

        p1 = self.client.get(reverse('caja:historial'))
        self.assertEqual(p1.status_code, 200)
        self.assertEqual(len(p1.context['turnos']), 50)
        self.assertEqual(p1.context['page_obj'].paginator.count, 55)

        p2 = self.client.get(reverse('caja:historial') + '?page=2')
        self.assertEqual(len(p2.context['turnos']), 5)
