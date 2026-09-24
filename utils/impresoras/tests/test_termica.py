from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from utils.impresoras import termica


class _FakePrinter:
    def __init__(self):
        self.profile = SimpleNamespace(media={})
        self.charcodes = []
        self.raw_commands = []
        self.open_calls = 0
        self.set_calls = []
        self.texts = []
        self.cut_calls = 0

    def _raw(self, command):
        self.raw_commands.append(command)

    def open(self):
        self.open_calls += 1

    def charcode(self, code_page):
        self.charcodes.append(code_page)

    def set(self, *args, **kwargs):
        self.set_calls.append((args, kwargs))

    def text(self, text):
        self.texts.append(text)

    def cut(self):
        self.cut_calls += 1


class ThermalPrinterEncodingTests(SimpleTestCase):
    def _printer_for_test_page(self, code_page="CP850"):
        printer = termica.ThermalPrinter2Connect.__new__(
            termica.ThermalPrinter2Connect
        )
        printer.config = {
            "CODE_PAGE": code_page,
            "PAPER_WIDTH": 48,
            "AUTO_CUT": True,
        }
        printer.business_info = {"NAME": "LABORATORIO"}
        printer.printer = _FakePrinter()
        printer.connect = Mock(return_value=True)
        printer.disconnect = Mock()
        return printer

    def test_test_page_sends_real_latin_characters_and_configured_code_page(self):
        printer = self._printer_for_test_page(code_page="CP850")

        self.assertTrue(printer.print_test_page())

        self.assertIn("Encoding: CP850\n\n", printer.printer.texts)
        self.assertIn("ñ Ñ á é í ó ú ü\n", printer.printer.texts)
        self.assertIn("¿ ¡ RD$\n\n", printer.printer.texts)
        self.assertNotIn("n N a e i o u\n", printer.printer.texts)
        self.assertEqual(printer.printer.cut_calls, 1)
        printer.disconnect.assert_called_once_with()

    def test_connect_selects_the_configured_code_page(self):
        printer = termica.ThermalPrinter2Connect.__new__(
            termica.ThermalPrinter2Connect
        )
        printer.config = {
            "ENABLED": True,
            "PRINTER_NAME": "POS-80C",
            "CODE_PAGE": "CP1252",
        }
        fake_printer = _FakePrinter()

        with patch.object(termica, "Win", return_value=fake_printer):
            self.assertTrue(printer.connect())

        self.assertEqual(fake_printer.charcodes, ["CP1252"])
        self.assertEqual(fake_printer.open_calls, 1)
        self.assertEqual(fake_printer.raw_commands, [b"\x1b@"])

    def test_disconnect_closes_raw_job_without_sending_post_cut_commands(self):
        printer = termica.ThermalPrinter2Connect.__new__(
            termica.ThermalPrinter2Connect
        )
        raw_printer = Mock()
        printer.printer = raw_printer

        printer.disconnect()

        raw_printer.set.assert_not_called()
        raw_printer.close.assert_called_once_with()
        self.assertIsNone(printer.printer)

    def test_long_item_name_wraps_without_discarding_characters(self):
        printer = self._printer_for_test_page()
        product_name = (
            "Café molido selección especial con azúcar y canela para laboratorio"
        )

        printer._print_items([
            {
                "producto": product_name,
                "cantidad": 1,
                "precio_unit": 10.0,
                "subtotal": 10.0,
            }
        ])

        expected_lines = termica._wrap_ticket_text(product_name, width=40)
        for line in expected_lines:
            self.assertIn(f"{line}\n", printer.printer.texts)
        self.assertEqual(" ".join(expected_lines), product_name)

    def test_total_line_identifies_dominican_currency(self):
        printer = self._printer_for_test_page()

        printer._print_total_line("TOTAL:", 1250.5)

        self.assertTrue(printer.printer.texts[-1].endswith("RD$1250.50\n"))
