@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
title POS FIFO System - Daemon de Sincronizacion

REM ============================================================================
REM POS FIFO System - Iniciar Daemon de Sincronizacion con la nube
REM
REM Corre el motor de sync (apps/sync) que:
REM   - empuja ventas/eventos locales al cloud (cola EventoSync)
REM   - baja maestros (categorias/productos/clientes) desde el cloud
REM
REM Uso:
REM   deploy\iniciar_sync.bat            -> loop continuo (Ctrl+C para detener)
REM   deploy\iniciar_sync.bat --once     -> una sola pasada (util para probar)
REM
REM Requiere que en deploy\env_cliente.bat este configurado:
REM   SYNC_ENABLED=true, CLOUD_API_URL, CLOUD_API_TOKEN, SUCURSAL_CODIGO
REM ============================================================================

set "PROJECT_DIR=%~dp0.."
cd /d "%PROJECT_DIR%"

REM --- Forzar encoding ---
set PGCLIENTENCODING=UTF8
set PYTHONUTF8=1

REM --- Cargar configuracion ---
if exist "%PROJECT_DIR%\deploy\env_cliente.bat" (
    call "%PROJECT_DIR%\deploy\env_cliente.bat"
) else (
    echo [ERROR] No se encontro deploy\env_cliente.bat
    echo         Ejecute primero deploy\instalar.bat o deploy\actualizar.bat
    pause
    exit /b 1
)

REM --- Activar entorno virtual ---
if exist "%PROJECT_DIR%\venv\Scripts\activate.bat" (
    call "%PROJECT_DIR%\venv\Scripts\activate.bat"
) else (
    echo [ERROR] Entorno virtual no encontrado. Ejecute primero la instalacion.
    pause
    exit /b 1
)

REM --- Validaciones minimas ---
if /i not "%SYNC_ENABLED%"=="true" (
    echo [ERROR] SYNC_ENABLED no esta en "true" en deploy\env_cliente.bat
    echo         El sync esta deshabilitado. Active SYNC_ENABLED=true para sincronizar.
    pause
    exit /b 1
)

if "%CLOUD_API_TOKEN%"=="PEGAR-TOKEN-DE-vincular_sucursal_token" (
    echo [ERROR] Falta CLOUD_API_TOKEN real en deploy\env_cliente.bat
    echo         Genere el token en el cloud: python manage.py vincular_sucursal_token --sucursal %SUCURSAL_CODIGO%
    pause
    exit /b 1
)

echo.
echo  Daemon de sincronizacion
echo    Sucursal: %SUCURSAL_CODIGO%
echo    Cloud:    %CLOUD_API_URL%
echo    Intervalo: %SYNC_INTERVAL%s
echo.

REM --- Ejecutar (pasa cualquier argumento extra: --once, --only-push, etc.) ---
python manage.py sincronizar --settings=config.settings_production %*

pause
endlocal
