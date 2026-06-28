from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from utils.impresoras.manager import PrintManager


class FakeTicketPrinter:
    printed = []

    def print_ticket(self, venta_data):
        self.printed.append(deepcopy(venta_data))


class TicketCopiesTests(SimpleTestCase):
    def setUp(self):
        FakeTicketPrinter.printed = []
        self.manager = PrintManager()
        self.manager.printer_class = FakeTicketPrinter
        self.venta = SimpleNamespace(numero_venta='VENTA-0001')
        self.usuario = SimpleNamespace(username='cajero')
        self.venta_data = {
            'numero_venta': 'VENTA-0001',
            'fecha': '20/06/2026 10:00 AM',
            'cajero': 'cajero',
            'items': [],
            'subtotal': 100.0,
            'descuento_total': 0.0,
            'total': 100.0,
            'pagos': [],
            'tiene_efectivo': True,
        }

    @patch('utils.impresoras.manager._is_printing_enabled', return_value=True)
    @patch('utils.impresoras.manager._get_cantidad_copias_ticket', return_value=2)
    def test_print_ticket_venta_imprime_cliente_y_archivo_interno(self, *_):
        with patch.object(self.manager, '_prepare_venta_data', return_value=self.venta_data), \
                patch.object(self.manager, '_registrar_auditoria_impresion') as registrar:
            resultado = self.manager.print_ticket_venta(self.venta, self.usuario)

        self.assertTrue(resultado['success'])
        self.assertEqual(resultado['copias'], 2)
        self.assertEqual(len(FakeTicketPrinter.printed), 2)
        self.assertEqual(FakeTicketPrinter.printed[0]['etiqueta_copia'], 'COPIA CLIENTE')
        self.assertEqual(FakeTicketPrinter.printed[1]['etiqueta_copia'], 'COPIA ARCHIVO INTERNO')
        self.assertTrue(FakeTicketPrinter.printed[0]['tiene_efectivo'])
        self.assertFalse(FakeTicketPrinter.printed[1]['tiene_efectivo'])

        registrar.assert_called_once()
        self.assertEqual(registrar.call_args.kwargs['copias_solicitadas'], 2)
        self.assertEqual(registrar.call_args.kwargs['copias_impresas'], 2)

    @patch('utils.impresoras.manager._is_printing_enabled', return_value=True)
    @patch('utils.impresoras.manager._get_cantidad_copias_ticket', return_value=2)
    def test_reimpresion_no_duplica_copias_configuradas(self, *_):
        self.venta.refresh_from_db = Mock()

        with patch.object(self.manager, '_prepare_venta_data', return_value=self.venta_data), \
                patch.object(self.manager, '_registrar_auditoria_impresion'):
            resultado = self.manager.print_ticket_venta(self.venta, self.usuario, reimpresion=True)

        self.assertTrue(resultado['success'])
        self.assertEqual(resultado['copias'], 1)
        self.assertEqual(len(FakeTicketPrinter.printed), 1)
        self.assertNotIn('etiqueta_copia', FakeTicketPrinter.printed[0])
        self.venta.refresh_from_db.assert_called_once()
