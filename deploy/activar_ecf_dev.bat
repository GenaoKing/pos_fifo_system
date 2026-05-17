@echo off
REM ============================================================================
REM POS FIFO System - Activar entorno e-CF para desarrollo / testing
REM ============================================================================
REM Este .bat configura las variables de entorno necesarias para que el
REM modulo facturacion_electronica pueda autenticar contra MSeller TesteCF.
REM
REM Uso:
REM   deploy\activar_ecf_dev.bat runserver 8000
REM   deploy\activar_ecf_dev.bat shell
REM   deploy\activar_ecf_dev.bat ecf_procesar_pendientes --dry-run
REM   deploy\activar_ecf_dev.bat ecf_procesar_pendientes --solo-emitir
REM   deploy\activar_ecf_dev.bat ecf_procesar_pendientes --ecf-id 42
REM
REM Estas vars se referencian (no se hardcodean) desde Emisor.config_proveedor
REM via los campos email_env, password_env, api_key_env. Si cambias el sufijo
REM (_ROYAL por otro), actualiza tambien el JSON del Emisor en admin.
REM
REM IMPORTANTE:
REM   - Las credenciales son del entorno TesteCF (sandbox de pruebas).
REM     NO usar las de produccion (eCF) hasta cerrar testing.
REM   - El API Key se genera en https://ecf.mseller.app/ → Cuenta → API Keys.
REM   - El email/password son los de la cuenta MSeller del cliente.
REM ============================================================================

REM --- Encoding (previene errores en Windows espanol) ---
set PGCLIENTENCODING=UTF8
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

REM --- Sucursal (mismo valor que activar_sync_sucursal.bat) ---
set SUCURSAL_CODIGO=SD-001

REM ============================================================================
REM CREDENCIALES MSELLER - Cliente piloto (Royal Plast)
REM ============================================================================
REM Sufijo _ROYAL coincide con los nombres que el Emisor.config_proveedor
REM debe tener en el JSON: {"email_env":"MSELLER_EMAIL_ROYAL", ...}
REM
REM Si cambias el sufijo (otro cliente), actualizar tambien admin del Emisor.
REM ============================================================================

REM --- PEGA AQUI EL EMAIL de la cuenta MSeller TesteCF ---
set MSELLER_EMAIL_ROYAL=tabacaleragenao@gmail.com

REM --- PEGA AQUI EL PASSWORD de la cuenta MSeller TesteCF ---
set MSELLER_PASSWORD_ROYAL=Tabacalera01

REM --- PEGA AQUI EL API KEY generado en panel MSeller TesteCF ---
set MSELLER_API_KEY_ROYAL=8e56ec24-6f4b-4b6a-a06c-3cbfd61b11ab

REM ============================================================================
REM Validacion minima de que las credenciales fueron editadas
REM ============================================================================
if "%MSELLER_EMAIL_ROYAL%"=="PEGAR-EMAIL-CUENTA-MSELLER-AQUI" (
    echo.
    echo [ERROR] Debes editar este .bat y completar las credenciales MSeller.
    echo.
    echo Pasos:
    echo   1. Login en https://ecf.mseller.app/
    echo   2. Verificar que la cuenta tenga acceso al entorno TesteCF
    echo   3. Generar API Key en seccion de Cuenta o Integraciones
    echo   4. Pegar email, password y api key en este .bat
    echo.
    pause
    exit /b 1
)

if "%MSELLER_API_KEY_ROYAL%"=="PEGAR-API-KEY-AQUI" (
    echo.
    echo [ERROR] Falta el API Key de MSeller. Generarlo en el panel y editarlo aqui.
    echo.
    pause
    exit /b 1
)

echo [OK] Entorno e-CF configurado
echo     Sucursal: %SUCURSAL_CODIGO%
echo     Email:    %MSELLER_EMAIL_ROYAL%
echo     Entorno:  TesteCF (sandbox)
echo.

REM ============================================================================
REM Ejecutar el comando que paso el usuario
REM ============================================================================
if "%~1"=="" (
    echo Uso: activar_ecf_dev.bat ^<comando^> [args]
    echo   Ejemplos:
    echo     activar_ecf_dev.bat runserver 8000
    echo     activar_ecf_dev.bat shell
    echo     activar_ecf_dev.bat ecf_procesar_pendientes --dry-run
    echo     activar_ecf_dev.bat ecf_procesar_pendientes --ecf-id 42
    exit /b 1
)

python manage.py %*