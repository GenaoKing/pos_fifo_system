@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
title POS FIFO System - Registrar Servicio de Sincronizacion

REM ============================================================================
REM POS FIFO System - Registrar el daemon de sync como Servicio de Windows v1
REM Servicio: POSFifoSync  (corre `manage.py sincronizar` en loop)
REM Ejecutar como Administrador.
REM
REM Es independiente del servicio web (POSFifoSystem): se puede parar/arrancar
REM el sync sin tocar el POS.
REM ============================================================================

echo.
echo  ============================================================
echo    Registrar Daemon de Sincronizacion (POSFifoSync)
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

REM --- Validar que el sync este configurado ---
if /i not "%SYNC_ENABLED%"=="true" (
    echo [ERROR] SYNC_ENABLED no esta en "true" en deploy\env_cliente.bat
    echo         Configure el sync antes de registrar el servicio.
    pause
    exit /b 1
)
if "%CLOUD_API_TOKEN%"=="PEGAR-TOKEN-DE-vincular_sucursal_token" (
    echo [ERROR] Falta CLOUD_API_TOKEN real en deploy\env_cliente.bat
    pause
    exit /b 1
)

set SERVICE_NAME=POSFifoSync
set "NSSM_PATH=%PROJECT_DIR%\deploy\nssm.exe"

REM ============================================================================
REM Opcion 1: Con NSSM (recomendado)
REM ============================================================================
if exist "%NSSM_PATH%" (
    echo [INFO] Usando NSSM para registrar el servicio de sync...

    REM --- Remover servicio previo si existe ---
    "%NSSM_PATH%" stop %SERVICE_NAME% >nul 2>&1
    "%NSSM_PATH%" remove %SERVICE_NAME% confirm >nul 2>&1

    REM --- Instalar servicio: python manage.py sincronizar (loop continuo) ---
    "%NSSM_PATH%" install %SERVICE_NAME% "%PROJECT_DIR%\venv\Scripts\python.exe" "%PROJECT_DIR%\manage.py" sincronizar --settings=config.settings_production
    "%NSSM_PATH%" set %SERVICE_NAME% AppDirectory "%PROJECT_DIR%"
    "%NSSM_PATH%" set %SERVICE_NAME% DisplayName "POS FIFO System - Sync"
    "%NSSM_PATH%" set %SERVICE_NAME% Description "Daemon de sincronizacion del POS con la nube"
    "%NSSM_PATH%" set %SERVICE_NAME% Start SERVICE_AUTO_START
    "%NSSM_PATH%" set %SERVICE_NAME% AppStdout "%PROJECT_DIR%\logs\sync_service_stdout.log"
    "%NSSM_PATH%" set %SERVICE_NAME% AppStderr "%PROJECT_DIR%\logs\sync_service_stderr.log"
    "%NSSM_PATH%" set %SERVICE_NAME% AppRotateFiles 1
    "%NSSM_PATH%" set %SERVICE_NAME% AppRotateBytes 5242880

    REM --- Variables de entorno para el servicio ---
    REM Cada par ENTRECOMILLADO: protege espacios y caracteres especiales de cmd
    REM (& ( ) ^) en valores como el SECRET_KEY. Sin comillas, NSSM rechaza con
    REM "environment should comprise strings of the form key=value".
    "%NSSM_PATH%" set %SERVICE_NAME% AppEnvironmentExtra ^
        "DJANGO_SETTINGS_MODULE=config.settings_production" ^
        "DJANGO_DEBUG=false" ^
        "DJANGO_SECRET_KEY=%DJANGO_SECRET_KEY%" ^
        "DB_NAME=%DB_NAME%" ^
        "DB_USER=%DB_USER%" ^
        "DB_PASSWORD=%DB_PASSWORD%" ^
        "DB_HOST=%DB_HOST%" ^
        "DB_PORT=%DB_PORT%" ^
        "SUCURSAL_CODIGO=%SUCURSAL_CODIGO%" ^
        "SYNC_ENABLED=true" ^
        "CLOUD_API_URL=%CLOUD_API_URL%" ^
        "CLOUD_API_TOKEN=%CLOUD_API_TOKEN%" ^
        "SYNC_INTERVAL=%SYNC_INTERVAL%" ^
        "SYNC_BATCH_SIZE=%SYNC_BATCH_SIZE%" ^
        "SYNC_MAX_RETRIES=%SYNC_MAX_RETRIES%" ^
        "SYNC_HTTP_TIMEOUT=%SYNC_HTTP_TIMEOUT%" ^
        "PGCLIENTENCODING=UTF8" ^
        "PYTHONUTF8=1"

    "%NSSM_PATH%" start %SERVICE_NAME%

    echo.
    echo   [OK] Servicio "%SERVICE_NAME%" registrado e iniciado.
    echo.
    echo   Comandos utiles:
    echo     nssm start %SERVICE_NAME%      - Iniciar
    echo     nssm stop %SERVICE_NAME%       - Detener
    echo     nssm restart %SERVICE_NAME%    - Reiniciar
    echo     nssm status %SERVICE_NAME%     - Ver estado
    echo     nssm remove %SERVICE_NAME%     - Eliminar servicio
    echo.
    echo   Logs del sync: logs\sync.log  y  logs\sync_service_stderr.log
    goto :fin
)

REM ============================================================================
REM Opcion 2: Task Scheduler (si no hay NSSM)
REM ============================================================================
echo [INFO] NSSM no encontrado en deploy\nssm.exe
echo [INFO] Usando Task Scheduler como alternativa...
echo.

schtasks /create ^
    /tn "POSFifoSync_AutoStart" ^
    /tr "\"%PROJECT_DIR%\deploy\iniciar_sync.bat\"" ^
    /sc onlogon ^
    /rl highest ^
    /f

if %errorlevel% equ 0 (
    echo   [OK] Tarea programada creada: POSFifoSync_AutoStart
    echo        El daemon de sync arrancara al iniciar sesion.
) else (
    echo   [ERROR] No se pudo crear la tarea programada.
)
echo.
echo   [NOTA] Para mejor control (auto-restart, sin sesion abierta), use NSSM:
echo          descargue nssm.exe de https://nssm.cc/download a deploy\ y reejecute.

:fin
echo.
pause
endlocal
