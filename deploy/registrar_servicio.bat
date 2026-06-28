@echo off
chcp 65001 >nul 2>&1
setlocal
title POS FIFO System - Registrar Servicio Windows

REM ============================================================================
REM POS FIFO System - Registrar como Servicio de Windows (NSSM) v3
REM Ejecutar como Administrador
REM ============================================================================

echo.
echo  ============================================================
echo    Registrar POS FIFO System como Servicio de Windows
echo  ============================================================
echo.

REM --- Verificar permisos de administrador ---
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Ejecute como Administrador.
    pause
    exit /b 1
)

set "PROJECT_DIR=%~dp0.."
cd /d "%PROJECT_DIR%"
call "%PROJECT_DIR%\deploy\env_cliente.bat"

REM --- Defaults seguros para variables opcionales ---
REM Si NSSM recibe VAR= vacio, Django ve la variable definida y no usa su
REM default. Esto rompe los int(...) de settings/server.py en versiones ya
REM instaladas. Por eso rellenamos aqui los opcionales numericos/booleanos.
if not defined DJANGO_DEBUG set "DJANGO_DEBUG=false"
if not defined SERVER_IP set "SERVER_IP=127.0.0.1"
if not defined SERVER_PORT set "SERVER_PORT=8080"
if not defined SERVER_THREADS set "SERVER_THREADS=4"
if not defined THERMAL_PRINTER_ENABLED set "THERMAL_PRINTER_ENABLED=true"
if not defined THERMAL_CHARSET set "THERMAL_CHARSET=CP850"
if not defined THERMAL_CASH_DRAWER set "THERMAL_CASH_DRAWER=true"
if not defined THERMAL_CASH_DRAWER_PIN set "THERMAL_CASH_DRAWER_PIN=2"
if not defined THERMAL_PAPER_WIDTH set "THERMAL_PAPER_WIDTH=48"
if not defined THERMAL_LOGO_WIDTH set "THERMAL_LOGO_WIDTH=200"

REM --- Normalizar nombres de impresoras ---
REM Si alguien escribio set THERMAL_PRINTER_NAME="2connect pos", cmd guarda las
REM comillas como parte del valor. Las quitamos antes de darselo a NSSM.
if defined PRINTER_TERMICA set "PRINTER_TERMICA=%PRINTER_TERMICA:"=%"
if defined PRINTER_ZEBRA set "PRINTER_ZEBRA=%PRINTER_ZEBRA:"=%"
if defined THERMAL_PRINTER_NAME set "THERMAL_PRINTER_NAME=%THERMAL_PRINTER_NAME:"=%"
if defined ZEBRA_PRINTER_NAME set "ZEBRA_PRINTER_NAME=%ZEBRA_PRINTER_NAME:"=%"
if not defined THERMAL_PRINTER_NAME if defined PRINTER_TERMICA set "THERMAL_PRINTER_NAME=%PRINTER_TERMICA%"
if not defined ZEBRA_PRINTER_NAME if defined PRINTER_ZEBRA set "ZEBRA_PRINTER_NAME=%PRINTER_ZEBRA%"

set SERVICE_NAME=POSFifoSystem
set "NSSM_PATH=%PROJECT_DIR%\deploy\nssm.exe"

REM ============================================================================
REM Opcion 1: Con NSSM (recomendado)
REM ============================================================================
if exist "%NSSM_PATH%" (
    echo [INFO] Usando NSSM para registrar servicio...

    REM --- Remover servicio previo si existe ---
    "%NSSM_PATH%" stop %SERVICE_NAME% >nul 2>&1
    "%NSSM_PATH%" remove %SERVICE_NAME% confirm >nul 2>&1

    REM --- Instalar servicio ---
    "%NSSM_PATH%" install %SERVICE_NAME% "%PROJECT_DIR%\venv\Scripts\python.exe" "%PROJECT_DIR%\server.py"
    "%NSSM_PATH%" set %SERVICE_NAME% AppDirectory "%PROJECT_DIR%"
    "%NSSM_PATH%" set %SERVICE_NAME% DisplayName "POS FIFO System"
    "%NSSM_PATH%" set %SERVICE_NAME% Description "Sistema Punto de Venta con Inventario FIFO"
    "%NSSM_PATH%" set %SERVICE_NAME% Start SERVICE_AUTO_START
    "%NSSM_PATH%" set %SERVICE_NAME% AppStdout "%PROJECT_DIR%\logs\service_stdout.log"
    "%NSSM_PATH%" set %SERVICE_NAME% AppStderr "%PROJECT_DIR%\logs\service_stderr.log"
    "%NSSM_PATH%" set %SERVICE_NAME% AppRotateFiles 1
    "%NSSM_PATH%" set %SERVICE_NAME% AppRotateBytes 5242880

    REM --- Variables de entorno para el servicio ---
    REM Cada par va ENTRECOMILLADO: protege valores con espacios (ej. nombre de
    REM impresora "2connect pos") y caracteres especiales de cmd (& ( ) ^) en el
    REM SECRET_KEY. Sin comillas, un espacio parte el argumento y NSSM rechaza el
    REM token sin "=" ("environment should comprise strings of the form key=value").
    "%NSSM_PATH%" set %SERVICE_NAME% AppEnvironmentExtra ^
        "DJANGO_SETTINGS_MODULE=config.settings_production" ^
        "DJANGO_DEBUG=%DJANGO_DEBUG%" ^
        "DJANGO_SECRET_KEY=%DJANGO_SECRET_KEY%" ^
        "DJANGO_ALLOWED_HOSTS=%DJANGO_ALLOWED_HOSTS%" ^
        "DB_NAME=%DB_NAME%" ^
        "DB_USER=%DB_USER%" ^
        "DB_PASSWORD=%DB_PASSWORD%" ^
        "DB_HOST=%DB_HOST%" ^
        "DB_PORT=%DB_PORT%" ^
        "SUCURSAL_CODIGO=%SUCURSAL_CODIGO%" ^
        "SYNC_ENABLED=%SYNC_ENABLED%" ^
        "CLOUD_API_URL=%CLOUD_API_URL%" ^
        "CLOUD_API_TOKEN=%CLOUD_API_TOKEN%" ^
        "SYNC_INTERVAL=%SYNC_INTERVAL%" ^
        "SYNC_BATCH_SIZE=%SYNC_BATCH_SIZE%" ^
        "SYNC_MAX_RETRIES=%SYNC_MAX_RETRIES%" ^
        "SYNC_HTTP_TIMEOUT=%SYNC_HTTP_TIMEOUT%" ^
        "SERVER_IP=%SERVER_IP%" ^
        "SERVER_PORT=%SERVER_PORT%" ^
        "SERVER_THREADS=%SERVER_THREADS%" ^
        "EXTRA_HOSTS=%EXTRA_HOSTS%" ^
        "PRINTER_TERMICA=%PRINTER_TERMICA%" ^
        "PRINTER_ZEBRA=%PRINTER_ZEBRA%" ^
        "THERMAL_PRINTER_ENABLED=%THERMAL_PRINTER_ENABLED%" ^
        "THERMAL_PRINTER_NAME=%THERMAL_PRINTER_NAME%" ^
        "THERMAL_USB_VENDOR_ID=%THERMAL_USB_VENDOR_ID%" ^
        "THERMAL_USB_PRODUCT_ID=%THERMAL_USB_PRODUCT_ID%" ^
        "THERMAL_CHARSET=%THERMAL_CHARSET%" ^
        "THERMAL_CASH_DRAWER=%THERMAL_CASH_DRAWER%" ^
        "THERMAL_CASH_DRAWER_PIN=%THERMAL_CASH_DRAWER_PIN%" ^
        "THERMAL_PAPER_WIDTH=%THERMAL_PAPER_WIDTH%" ^
        "THERMAL_LOGO_WIDTH=%THERMAL_LOGO_WIDTH%" ^
        "ZEBRA_PRINTER_NAME=%ZEBRA_PRINTER_NAME%" ^
        "PGCLIENTENCODING=UTF8" ^
        "PYTHONUTF8=1"

    REM --- Iniciar servicio ---
    "%NSSM_PATH%" start %SERVICE_NAME%

    echo.
    echo   [OK] Servicio "%SERVICE_NAME%" registrado e iniciado.
    echo.
    echo   Comandos utiles:
    echo     nssm start %SERVICE_NAME%      - Iniciar
    echo     nssm stop %SERVICE_NAME%       - Detener
    echo     nssm restart %SERVICE_NAME%    - Reiniciar
    echo     nssm status %SERVICE_NAME%     - Ver estado
    echo     nssm edit %SERVICE_NAME%       - Editar config en GUI
    echo     nssm remove %SERVICE_NAME%     - Eliminar servicio

    goto :fin
)

REM ============================================================================
REM Opcion 2: Task Scheduler (si no hay NSSM)
REM ============================================================================
echo [INFO] NSSM no encontrado en deploy\nssm.exe
echo [INFO] Usando Task Scheduler como alternativa...
echo.

schtasks /create ^
    /tn "POSFifoSystem_AutoStart" ^
    /tr "\"%PROJECT_DIR%\deploy\iniciar_servidor.bat\"" ^
    /sc onlogon ^
    /rl highest ^
    /f

if %errorlevel% equ 0 (
    echo   [OK] Tarea programada creada: POSFifoSystem_AutoStart
    echo        El servidor se iniciara automaticamente al iniciar sesion.
) else (
    echo   [ERROR] No se pudo crear la tarea programada.
)

echo.
echo   [NOTA] Para mejor control, descargue NSSM de https://nssm.cc/download
echo          Coloque nssm.exe en la carpeta deploy\ y ejecute este script de nuevo.

:fin
echo.
pause
endlocal
