"""
RPT-005: el cierre diario es MANUAL (no hay servicio automatico). El comando
`generar_cierre_diario` es la operacion documentada; aca se prueba de punta a
punta que produce un BORRADOR y que `--finalizar` lo congela en FINAL.

El PDF se mockea: su generacion depende de fuentes/rutas del host y es accesoria
al hecho contable (el comando ya degrada con aviso si el PDF falla, RPT-005).
"""
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.reportes.models import BORRADOR, FINAL, CierreCaja

PDF_PATH = 'apps.reportes.management.commands.generar_cierre_diario.PDFGenerator.generar_cierre_caja'


class ComandoCierreDiarioTests(TestCase):
    @patch(PDF_PATH, return_value='reportes/fake.pdf')
    def test_comando_genera_un_borrador(self, _pdf):
        call_command('generar_cierre_diario')
        cierre = CierreCaja.objects.latest('id')
        self.assertEqual(cierre.estado, BORRADOR)

    @patch(PDF_PATH, return_value='reportes/fake.pdf')
    def test_finalizar_congela_el_cierre(self, _pdf):
        call_command('generar_cierre_diario', '--finalizar')
        cierre = CierreCaja.objects.latest('id')
        self.assertEqual(cierre.estado, FINAL)

    @patch(PDF_PATH, return_value='reportes/fake.pdf')
    def test_recalcula_el_borrador_en_la_siguiente_corrida(self, _pdf):
        call_command('generar_cierre_diario')
        primero = CierreCaja.objects.latest('id')
        call_command('generar_cierre_diario')
        # Sin --finalizar, la segunda corrida recalcula el MISMO borrador (RPT-004),
        # no crea otro.
        self.assertEqual(CierreCaja.objects.count(), 1)
        segundo = CierreCaja.objects.latest('id')
        self.assertEqual(primero.id, segundo.id)
        self.assertEqual(segundo.estado, BORRADOR)
