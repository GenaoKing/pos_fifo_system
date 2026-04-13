@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
title POS FIFO System - Servidor

REM ============================================================================
REM POS FIFO System - Iniciar Servidor de Produccion v3
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
    echo         Ejecute primero deploy\instalar.bat
    pause
    exit /b 1
)

REM --- Activar entorno virtual ---
if exist "%PROJECT_DIR%\venv\Scripts\activate.bat" (
    call "%PROJECT_DIR%\venv\Scripts\activate.bat"
) else (
    echo [ERROR] Entorno virtual no encontrado.
    echo         Ejecute primero deploy\instalar.bat
    pause
    exit /b 1
)

REM --- Verificar que PostgreSQL este corriendo ---
set PG_FOUND=0
for %%v in (17 16 15 14) do (
    sc query postgresql-x64-%%v >nul 2>&1
    if !errorlevel! equ 0 set PG_FOUND=1
)
if %PG_FOUND% equ 0 (
    echo [AVISO] No se detecto servicio PostgreSQL activo.
    echo         Verifique que PostgreSQL este corriendo.
)

echo.
echo  Iniciando POS FIFO System...
echo  Acceda al sistema en: http://localhost:%SERVER_PORT%
echo  Presione Ctrl+C para detener
echo.

python "%PROJECT_DIR%\server.py"

pause
endlocal
