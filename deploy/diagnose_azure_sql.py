"""
diagnose_azure_sql.py — Diagnostico de conexion Azure SQL
==========================================================
Uso:
    call deploy\env_azure_sql_local.bat
    python deploy\diagnose_azure_sql.py

Verifica paso a paso: DNS, puerto, ODBC driver, y conexion directa.
"""

import os
import sys
import socket
import subprocess


def check_env_vars():
    """Verificar que las variables de entorno estan configuradas."""
    print("\n[1/5] Variables de entorno")
    required = ['AZURE_SQL_DB_HOST', 'AZURE_SQL_DB_NAME', 'AZURE_SQL_DB_USER', 'AZURE_SQL_DB_PASSWORD']
    all_ok = True
    for var in required:
        val = os.environ.get(var, '')
        if not val or 'TU_' in val:
            print(f"      [FALTA] {var}")
            all_ok = False
        else:
            # Ocultar password
            display = val if var != 'AZURE_SQL_DB_PASSWORD' else '***' + val[-3:]
            print(f"      [OK]    {var} = {display}")
    return all_ok


def check_dns():
    """Resolver el hostname del servidor Azure SQL."""
    print("\n[2/5] Resolucion DNS")
    host = os.environ.get('AZURE_SQL_DB_HOST', '')
    if not host:
        print("      [ERROR] AZURE_SQL_DB_HOST no configurado")
        return False
    try:
        ip = socket.gethostbyname(host)
        print(f"      [OK]    {host} -> {ip}")
        return True
    except socket.gaierror as e:
        print(f"      [ERROR] No se pudo resolver {host}: {e}")
        print(f"      Verifique que el nombre del servidor es correcto")
        return False


def check_port():
    """Verificar que el puerto 1433 esta accesible (no bloqueado por firewall)."""
    print("\n[3/5] Conectividad TCP al puerto 1433")
    host = os.environ.get('AZURE_SQL_DB_HOST', '')
    port = int(os.environ.get('AZURE_SQL_DB_PORT', '1433'))
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    try:
        result = sock.connect_ex((host, port))
        if result == 0:
            print(f"      [OK]    Puerto {port} accesible en {host}")
            return True
        else:
            print(f"      [ERROR] Puerto {port} NO accesible (codigo: {result})")
            print(f"      CAUSA PROBABLE: Firewall de Azure SQL no permite tu IP")
            print(f"      SOLUCION:")
            print(f"        1. Azure Portal > SQL Server (no SQL Database) > Networking")
            print(f"        2. En 'Public network access': seleccionar 'Selected networks'")
            print(f"        3. En 'Firewall rules': click 'Add your client IPv4 address'")
            print(f"        4. Guardar cambios")
            print(f"        5. Tambien marcar 'Allow Azure services...' si no esta marcado")
            return False
    except socket.timeout:
        print(f"      [ERROR] Timeout conectando al puerto {port}")
        print(f"      CAUSA PROBABLE: Firewall de Azure SQL bloqueando la conexion")
        print(f"      SOLUCION: Mismos pasos de arriba — agregar IP al firewall")
        return False
    except Exception as e:
        print(f"      [ERROR] {e}")
        return False
    finally:
        sock.close()


def check_odbc_driver():
    """Verificar que el ODBC Driver 18 esta instalado."""
    print("\n[4/5] ODBC Driver")
    try:
        import pyodbc
        drivers = pyodbc.drivers()
        target = 'ODBC Driver 18 for SQL Server'
        if target in drivers:
            print(f"      [OK]    {target} encontrado")
            return True
        else:
            print(f"      [ERROR] {target} NO encontrado")
            print(f"      Drivers disponibles: {drivers}")
            # Verificar si hay otra version
            sql_drivers = [d for d in drivers if 'SQL Server' in d]
            if sql_drivers:
                print(f"      Drivers SQL disponibles: {sql_drivers}")
                print(f"      Podrias actualizar settings_azure_sql.py para usar: {sql_drivers[0]}")
            return False
    except ImportError:
        print("      [ERROR] pyodbc no instalado. Ejecutar: pip install pyodbc")
        return False


def check_direct_connection():
    """Intentar conexion directa con pyodbc (sin Django)."""
    print("\n[5/5] Conexion directa pyodbc")
    try:
        import pyodbc
    except ImportError:
        print("      [SKIP] pyodbc no disponible")
        return False
    
    host = os.environ.get('AZURE_SQL_DB_HOST', '')
    db = os.environ.get('AZURE_SQL_DB_NAME', '')
    user = os.environ.get('AZURE_SQL_DB_USER', '')
    pwd = os.environ.get('AZURE_SQL_DB_PASSWORD', '')
    
    conn_str = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={host},1433;"
        f"DATABASE={db};"
        f"UID={user};"
        f"PWD={pwd};"
        f"Encrypt=yes;"
        f"TrustServerCertificate=no;"
        f"Connection Timeout=15;"
    )
    
    print(f"      Intentando conexion a {host}...")
    try:
        conn = pyodbc.connect(conn_str, timeout=15)
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        print(f"      [OK]    Conexion exitosa! SELECT 1 = {result[0]}")
        
        # Info adicional
        cursor.execute("SELECT @@VERSION")
        version = cursor.fetchone()[0]
        print(f"      [INFO]  {version[:80]}...")
        
        conn.close()
        return True
    except pyodbc.Error as e:
        error_code = e.args[0] if e.args else 'unknown'
        error_msg = e.args[1] if len(e.args) > 1 else str(e)
        print(f"      [ERROR] Codigo: {error_code}")
        print(f"      {error_msg[:200]}")
        
        if '08001' in str(error_code):
            print(f"\n      DIAGNOSTICO: Error de red/firewall")
            print(f"      -> Tu IP no esta en las reglas de firewall de Azure SQL Server")
        elif '28000' in str(error_code):
            print(f"\n      DIAGNOSTICO: Credenciales incorrectas")
            print(f"      -> Verificar usuario y password en env_azure_sql_local.bat")
        elif '42000' in str(error_code):
            print(f"\n      DIAGNOSTICO: Base de datos no existe")
            print(f"      -> Verificar que la BD '{db}' existe en Azure Portal")
        
        return False


def get_public_ip():
    """Obtener la IP publica actual para agregarla al firewall."""
    print("\n[INFO] Tu IP publica actual:")
    try:
        # Usar un servicio simple para obtener la IP
        import urllib.request
        ip = urllib.request.urlopen('https://api.ipify.org', timeout=5).read().decode()
        print(f"       {ip}")
        print(f"       Esta es la IP que debes agregar al firewall de Azure SQL Server")
        return ip
    except Exception:
        print(f"       No se pudo determinar (sin acceso a internet?)")
        return None


def main():
    print("=" * 60)
    print("  DIAGNOSTICO DE CONEXION — AZURE SQL DATABASE")
    print("=" * 60)
    
    # Paso 1: Variables de entorno
    if not check_env_vars():
        print("\n[RESULTADO] Configura las variables de entorno primero.")
        return
    
    # Paso 2: DNS
    dns_ok = check_dns()
    if not dns_ok:
        print("\n[RESULTADO] El hostname no resuelve. Verifica el nombre del servidor.")
        return
    
    # Paso 3: Puerto/Firewall
    port_ok = check_port()
    
    # Paso 4: ODBC Driver
    odbc_ok = check_odbc_driver()
    
    # Si el puerto fallo, mostrar IP y salir
    if not port_ok:
        get_public_ip()
        print("\n" + "=" * 60)
        print("  RESULTADO: FIREWALL BLOQUEANDO CONEXION")
        print("=" * 60)
        print("  Pasos para resolver:")
        print("  1. Azure Portal > SQL Servers > tu servidor > Networking")
        print("  2. 'Public network access' = Selected networks")
        print("  3. Firewall rules > 'Add your client IPv4 address'")
        print("  4. Marcar 'Allow Azure services...'")
        print("  5. Click 'Save'")
        print("  6. Esperar 1-2 minutos y re-ejecutar este script")
        print("=" * 60)
        return
    
    if not odbc_ok:
        print("\n[RESULTADO] Instala el ODBC Driver 18 primero.")
        return
    
    # Paso 5: Conexion directa
    conn_ok = check_direct_connection()
    
    if conn_ok:
        print("\n" + "=" * 60)
        print("  RESULTADO: CONEXION EXITOSA")
        print("  Ahora puedes ejecutar: deploy\\run_azure_sql.bat migrate")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("  RESULTADO: CONEXION FALLO")
        print("  Revisa los errores arriba para el diagnostico")
        print("=" * 60)


if __name__ == '__main__':
    main()