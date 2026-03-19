@echo off
chcp 65001 >nul 2>&1
title Royal Plastic POS - Servidor

REM ============================================================================
REM Royal Plastic POS - Iniciar Servidor de Produccion
REM ============================================================================

set "PROJECT_DIR=%~dp0.."
cd /d "%PROJECT_DIR%"

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
sc query postgresql-x64-15 >nul 2>&1
if %errorlevel% neq 0 (
    sc query postgresql-x64-16 >nul 2>&1
    if %errorlevel% neq 0 (
        echo [AVISO] No se detecto servicio PostgreSQL activo.
        echo         Verifique que PostgreSQL este corriendo.
    )
)

echo.
echo  Iniciando Royal Plastic POS...
echo  Acceda al sistema en: http://localhost:%SERVER_PORT%
echo  Presione Ctrl+C para detener
echo.

python "%PROJECT_DIR%\server.py"

pause
