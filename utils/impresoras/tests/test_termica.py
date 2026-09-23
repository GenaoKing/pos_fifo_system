from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from utils.impresoras import termica


class _FakePrinter:
    def __init__(self):
        self.profile = SimpleNamespace(media={})
        self.charcodes = []
        self.raw_commands = []
        self.set_calls = []
        self.texts = []
        self.cut_calls = 0

    def _raw(self, command):
        self.raw_commands.append(command)

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
