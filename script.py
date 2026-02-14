#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from escpos.printer import Usb
import usb.core
import usb.util
import sys
import time

VENDOR_ID = 0x0FE6
PRODUCT_ID = 0x811E


def find_usb_device(vendor_id: int, product_id: int):
    """Encuentra el dispositivo USB con PyUSB."""
    dev = usb.core.find(idVendor=vendor_id, idProduct=product_id)
    return dev


def main():
    dev = find_usb_device(VENDOR_ID, PRODUCT_ID)
    if dev is None:
        print(f"[ERROR] No se encontró el dispositivo USB {VENDOR_ID:#06x}:{PRODUCT_ID:#06x}")
        print("Sugerencias:")
        print("- Verifica el cable/puerto USB")
        print("- En Linux: ejecuta 'lsusb' y confirma el VID/PID")
        print("- Puede requerir permisos (udev) o ejecutar como root")
        sys.exit(1)

    print(f"[OK] Dispositivo encontrado: {VENDOR_ID:#06x}:{PRODUCT_ID:#06x}")

    # Nota:
    # En la mayoría de impresoras ESC/POS USB se usa:
    # interface=0, in_ep=0x82, out_ep=0x01
    # Pero puede variar según el modelo. Si falla, revisa más abajo “Qué hacer si no imprime”.

    try:
        printer = Usb(
            VENDOR_ID,
            PRODUCT_ID,
            interface=0,      # cambia si tu impresora expone otra interfaz
            in_ep=0x82,       # puede variar
            out_ep=0x01,      # puede variar
            timeout=0,        # 0 suele funcionar bien en muchas impresoras
        )

        # --- PRUEBA DE IMPRESIÓN ---
        printer.hw("INIT")
        time.sleep(0.2)

        printer.set(align="center", width=2, height=2, bold=True)
        printer.text("PRUEBA USB\n")

        printer.set(align="left", width=1, height=1, bold=False)
        printer.text("--------------------------------\n")
        printer.text("Impresora: Termica 80mm\n")
        printer.text(f"VID:PID = {VENDOR_ID:#06x}:{PRODUCT_ID:#06x}\n")
        printer.text("python-escpos + USB OK\n")
        printer.text("--------------------------------\n\n")

        # Opcional: QR (si el modelo lo soporta)
        try:
            printer.set(align="center")
            printer.qr("https://example.com/test-usb", size=6)
            printer.text("\n")
        except Exception as e:
            printer.set(align="left")
            printer.text("[Aviso] QR no soportado o fallo al generar QR.\n")
            printer.text(f"Detalle: {e}\n\n")

        printer.set(align="center", bold=True)
        printer.text("FIN DE PRUEBA\n\n")

        # Corte (si tiene cortador)
        try:
            printer.cut()
        except Exception:
            # Algunos modelos no tienen cutter; en esos casos al menos alimentamos papel
            printer.text("\n\n\n")
            printer.control("LF")

        printer.close()
        print("[OK] Trabajo enviado a la impresora (si no ves salida, revisa endpoints/permisos).")

    except Exception as e:
        print("[ERROR] No se pudo inicializar/imprimir por USB con python-escpos.")
        print("Causa:", repr(e))
        print("\nQué probar:")
        print("1) Ejecutar como administrador/root (para descartar permisos USB).")
        print("2) Cambiar interface/in_ep/out_ep (ver abajo).")
        print("3) En Linux, verificar si el kernel tomó el driver (usblp) y liberarlo.")
        sys.exit(2)


if __name__ == "__main__":
    main()
