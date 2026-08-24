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
REM Resolver el ".." a una ruta canonica: sin esto, cada ruta construida con
REM %PROJECT_DIR% (POS_ENV_FILE, logs, AppDirectory...) queda con
REM "\deploy\..\deploy\" literal. Windows lo resuelve igual, pero ensucia
REM cualquier diagnostico que imprima esas rutas (Task Scheduler, nssm dump).
for %%I in ("%PROJECT_DIR%") do set "PROJECT_DIR=%%~fI"
cd /d "%PROJECT_DIR%"
REM La configuracion la lee la aplicacion desde env_cliente.env.

REM --- Verificar que exista la configuracion (igual que registrar_servicio.bat) ---
REM Sin esto, el servicio se registra apuntando a un env_cliente.env que
REM todavia no existe: nssm arranca el proceso igual, `POS_ENV_FILE` no
REM resuelve nada, y el daemon muere en loop con "SYNC_ENABLED=False en
REM settings" -- aparece como RUNNING porque nssm lo relanza sin parar.
REM Reproducido en Royal Plast (2026-08-24): la FASE 8 de actualizar.bat
REM llamaba a este script antes de que el .env existiera.
if not exist "%PROJECT_DIR%\deploy\env_cliente.env" (
    echo [ERROR] Falta deploy\env_cliente.env
    echo         Copie env_cliente.env.template y complete los valores, o
    echo         convierta un env_cliente.bat existente con:
    echo             python manage.py migrar_env_cliente
    pause
    exit /b 1
)

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
    REM --- Variables de entorno: solo DOS ---
    REM La configuracion vive en deploy\env_cliente.env y la lee la aplicacion.
    REM Ver el comentario equivalente en registrar_servicio.bat.
    "%NSSM_PATH%" set %SERVICE_NAME% AppEnvironmentExtra ^
        "DJANGO_SETTINGS_MODULE=config.settings_production" ^
        "POS_ENV_FILE=%PROJECT_DIR%\deploy\env_cliente.env"

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
