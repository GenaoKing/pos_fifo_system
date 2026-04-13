@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
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
    "%NSSM_PATH%" set %SERVICE_NAME% AppEnvironmentExtra ^
        DJANGO_SETTINGS_MODULE=config.settings_production ^
        DJANGO_SECRET_KEY=%DJANGO_SECRET_KEY% ^
        DB_NAME=%DB_NAME% ^
        DB_USER=%DB_USER% ^
        DB_PASSWORD=%DB_PASSWORD% ^
        DB_HOST=%DB_HOST% ^
        DB_PORT=%DB_PORT% ^
        SERVER_IP=%SERVER_IP% ^
        SERVER_PORT=%SERVER_PORT% ^
        PGCLIENTENCODING=UTF8 ^
        PYTHONUTF8=1

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
    echo     nssm edit %SERVICE_NAME%       - Editar config (GUI)
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
