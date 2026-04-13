"""
Script de Diagnóstico - Sistema de Impresión
Sistema POS FIFO 

Este script verifica que todos los componentes del sistema de impresión
estén correctamente instalados y funcionando.

Uso:
    python manage.py shell < utils/diagnostics/test_printer.py

O desde Django shell:
    >>> from utils.diagnostics.test_printer import diagnostico_completo
    >>> diagnostico_completo()

Autor: Sistema POS FIFO
Versión: 1.0
"""

import sys
import os
from pathlib import Path


def print_header(titulo):
    """Imprime un header formateado"""
    print("\n" + "=" * 70)
    print(f"  {titulo}")
    print("=" * 70)


def print_test(nombre, resultado, mensaje=""):
    """Imprime resultado de un test"""
    status = "✓" if resultado else "✗"
    color = "\033[92m" if resultado else "\033[91m"
    reset = "\033[0m"
    
    print(f"{color}{status}{reset} {nombre}")
    if mensaje:
        print(f"  → {mensaje}")


def test_imports():
    """Verifica que las librerías estén instaladas"""
    print_header("TEST 1: VERIFICAR DEPENDENCIAS")
    
    resultados = {}
    
    # Django
    try:
        import django
        print_test("Django", True, f"Versión: {django.get_version()}")
        resultados['django'] = True
    except ImportError as e:
        print_test("Django", False, str(e))
        resultados['django'] = False
    
    # python-escpos
    try:
        import escpos
        from escpos.printer import Win, Usb
        print_test("python-escpos", True, "Instalado correctamente")
        resultados['escpos'] = True
    except ImportError as e:
        print_test("python-escpos", False, str(e))
        resultados['escpos'] = False
    
    # Pillow
    try:
        from PIL import Image
        print_test("Pillow (PIL)", True, "Instalado correctamente")
        resultados['pillow'] = True
    except ImportError as e:
        print_test("Pillow (PIL)", False, str(e))
        resultados['pillow'] = False
    
    # qrcode
    try:
        import qrcode
        print_test("qrcode", True, "Instalado correctamente")
        resultados['qrcode'] = True
    except ImportError as e:
        print_test("qrcode", False, str(e))
        resultados['qrcode'] = False
    
    # pywin32 (solo en Windows)
    if sys.platform == 'win32':
        try:
            import win32print
            print_test("pywin32", True, "Instalado correctamente")
            resultados['pywin32'] = True
        except ImportError as e:
            print_test("pywin32", False, str(e))
            resultados['pywin32'] = False
    else:
        print_test("pywin32", True, "No requerido en este sistema")
        resultados['pywin32'] = True
    
    return all(resultados.values())


def test_configuracion():
    """Verifica la configuración en settings.py"""
    print_header("TEST 2: VERIFICAR CONFIGURACIÓN")
    
    try:
        from django.conf import settings
        
        # THERMAL_PRINTER
        if hasattr(settings, 'THERMAL_PRINTER'):
            config = settings.THERMAL_PRINTER
            print_test("THERMAL_PRINTER definido", True)
            
            # Verificar campos requeridos
            campos_req = ['ENABLED', 'PRINTER_NAME', 'AUTO_CUT', 'CHARSET', 'PAPER_WIDTH']
            for campo in campos_req:
                existe = campo in config
                print_test(f"  - {campo}", existe, config.get(campo, "NO DEFINIDO"))
            
        else:
            print_test("THERMAL_PRINTER definido", False, 
                      "Falta agregar configuración en settings.py")
            return False
        
        # BUSINESS_INFO
        if hasattr(settings, 'BUSINESS_INFO'):
            info = settings.BUSINESS_INFO
            print_test("BUSINESS_INFO definido", True)
            
            pendientes = []
            if '[PENDIENTE' in str(info.get('RNC', '')):
                pendientes.append('RNC')
            if '[PENDIENTE' in str(info.get('ADDRESS', '')):
                pendientes.append('ADDRESS')
            
            if pendientes:
                print_test("  - Campos pendientes", False, 
                          f"Actualizar: {', '.join(pendientes)}")
            else:
                print_test("  - Todos los campos configurados", True)
        else:
            print_test("BUSINESS_INFO definido", False)
            return False
        
        # QR_CONFIG
        if hasattr(settings, 'QR_CONFIG'):
            print_test("QR_CONFIG definido", True)
        else:
            print_test("QR_CONFIG definido", False)
        
        return True
        
    except Exception as e:
        print_test("Error en configuración", False, str(e))
        return False


def test_archivos():
    """Verifica que los archivos estén en su lugar"""
    print_header("TEST 3: VERIFICAR ARCHIVOS")
    
    archivos = {
        'utils/impresoras/termica.py': 'Driver de impresora térmica',
        'utils/impresoras/manager.py': 'Print Manager',
        'utils/impresoras/views.py': 'Vistas de impresión',
        'utils/impresoras/urls.py': 'URLs de impresión',
        'static/js/printer.js': 'Módulo JavaScript',
        'static/img/logo-royal.jpeg': 'Logo de Royal Plastic',
    }
    
    base_dir = Path.cwd()
    todos_existen = True
    
    for ruta, descripcion in archivos.items():
        archivo = base_dir / ruta
        existe = archivo.exists()
        print_test(descripcion, existe, ruta)
        if not existe:
            todos_existen = False
    
    return todos_existen


def test_impresoras_windows():
    """Lista impresoras disponibles en Windows"""
    print_header("TEST 4: IMPRESORAS EN WINDOWS")
    
    if sys.platform != 'win32':
        print_test("Sistema Windows", False, "Este test solo funciona en Windows")
        return False
    
    try:
        import win32print
        
        printers = win32print.EnumPrinters(2)
        
        if not printers:
            print_test("Impresoras detectadas", False, "No se encontraron impresoras")
            return False
        
        print(f"\n  Se encontraron {len(printers)} impresora(s):\n")
        
        from django.conf import settings
        printer_name = settings.THERMAL_PRINTER.get('PRINTER_NAME', '2Connect POS')
        encontrada = False
        
        for printer_info in printers:
            nombre = printer_info[2]
            print(f"    • {nombre}")
            
            if nombre == printer_name:
                encontrada = True
                print(f"      ← CONFIGURADA EN SETTINGS.PY")
        
        print()
        print_test("Impresora configurada encontrada", encontrada, 
                  f"Buscando: '{printer_name}'")
        
        return encontrada
        
    except Exception as e:
        print_test("Error detectando impresoras", False, str(e))
        return False


def test_conexion_impresora():
    """Intenta conectar con la impresora"""
    print_header("TEST 5: CONECTAR CON IMPRESORA")
    
    try:
        from utils.impresoras.termica import ThermalPrinter2Connect
        
        printer = ThermalPrinter2Connect()
        resultado = printer.connect()
        
        if resultado:
            print_test("Conexión exitosa", True, "Impresora respondió correctamente")
            printer.disconnect()
            return True
        else:
            print_test("Conexión fallida", False, "No se pudo establecer conexión")
            return False
            
    except Exception as e:
        print_test("Error conectando", False, str(e))
        return False


def test_impresion_prueba():
    """Imprime una página de prueba"""
    print_header("TEST 6: IMPRESIÓN DE PRUEBA")
    
    respuesta = input("\n¿Desea imprimir una página de prueba? (s/n): ")
    
    if respuesta.lower() != 's':
        print_test("Prueba omitida", True, "Usuario canceló")
        return True
    
    try:
        from utils.impresoras.manager import print_manager
        
        print("\n  Enviando página de prueba a la impresora...")
        resultado = print_manager.test_printer()
        
        if resultado['success']:
            print_test("Impresión exitosa", True, resultado['mensaje'])
            print("\n  👉 Revise la impresora para verificar el ticket impreso")
            return True
        else:
            print_test("Impresión fallida", False, resultado['mensaje'])
            return False
            
    except Exception as e:
        print_test("Error imprimiendo", False, str(e))
        return False


def test_base_datos():
    """Verifica que el modelo de auditoría esté listo"""
    print_header("TEST 7: BASE DE DATOS Y AUDITORÍA")
    
    try:
        from apps.auditoria.models import Auditoria
        
        # Verificar que existe la tabla
        count = Auditoria.objects.count()
        print_test("Modelo Auditoria", True, f"{count} registros en BD")
        
        # Verificar acciones de impresión
        acciones = dict(Auditoria.ACCIONES)
        
        tiene_impresion = 'IMPRESION_TICKET' in acciones
        print_test("Acción IMPRESION_TICKET", tiene_impresion)
        
        tiene_error = 'ERROR_IMPRESION' in acciones
        print_test("Acción ERROR_IMPRESION", tiene_error)
        
        return tiene_impresion and tiene_error
        
    except Exception as e:
        print_test("Error verificando BD", False, str(e))
        return False


def test_permisos():
    """Verifica que el permiso de reimpresión exista"""
    print_header("TEST 8: PERMISOS")
    
    try:
        from django.contrib.auth.models import Permission
        
        permiso = Permission.objects.filter(
            codename='reimprimir_ticket'
        ).first()
        
        if permiso:
            print_test("Permiso reimprimir_ticket", True, f"ID: {permiso.id}")
            
            # Contar usuarios con el permiso
            usuarios_con_permiso = permiso.user_set.count()
            grupos_con_permiso = permiso.group_set.count()
            
            print(f"  → {usuarios_con_permiso} usuario(s) tienen el permiso")
            print(f"  → {grupos_con_permiso} grupo(s) tienen el permiso")
            
            return True
        else:
            print_test("Permiso reimprimir_ticket", False, 
                      "Falta crear migración o aplicarla")
            return False
            
    except Exception as e:
        print_test("Error verificando permisos", False, str(e))
        return False


def diagnostico_completo():
    """Ejecuta todos los tests de diagnóstico"""
    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 68 + "║")
    print("║" + "  DIAGNÓSTICO DEL SISTEMA DE IMPRESIÓN".center(68) + "║")
    print("║" + "  Royal Plastic - Sistema POS FIFO".center(68) + "║")
    print("║" + " " * 68 + "║")
    print("╚" + "=" * 68 + "╝")
    
    resultados = {}
    
    # Ejecutar tests
    resultados['imports'] = test_imports()
    resultados['config'] = test_configuracion()
    resultados['archivos'] = test_archivos()
    resultados['windows'] = test_impresoras_windows()
    resultados['conexion'] = test_conexion_impresora()
    resultados['impresion'] = test_impresion_prueba()
    resultados['bd'] = test_base_datos()
    resultados['permisos'] = test_permisos()
    
    # Resumen final
    print_header("RESUMEN FINAL")
    
    total = len(resultados)
    exitosos = sum(1 for v in resultados.values() if v)
    
    print(f"\n  Tests exitosos: {exitosos}/{total}")
    print(f"  Tasa de éxito: {exitosos/total*100:.1f}%\n")
    
    if exitosos == total:
        print("  ✅ SISTEMA COMPLETAMENTE FUNCIONAL")
        print("  El sistema de impresión está listo para usar en producción.\n")
    elif exitosos >= total * 0.75:
        print("  ⚠️  SISTEMA PARCIALMENTE FUNCIONAL")
        print("  Revise los tests fallidos y complete la instalación.\n")
    else:
        print("  ❌ SISTEMA NO FUNCIONAL")
        print("  Varios componentes fallan. Revisar guía de instalación.\n")
    
    # Mostrar tests fallidos
    fallidos = [nombre for nombre, resultado in resultados.items() if not resultado]
    if fallidos:
        print("  Tests fallidos:")
        for test in fallidos:
            print(f"    • {test}")
        print()
    
    print("=" * 70)
    print()
    
    return exitosos == total


if __name__ == '__main__':
    # Esto permite ejecutar el script directamente
    diagnostico_completo()
