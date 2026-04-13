@echo off
chcp 65001 >nul 2>&1
setlocal
title POS FIFO System - Preparar para Despliegue

REM ============================================================================
REM POS FIFO System - Preparar Paquete de Despliegue v3
REM Ejecutar en la PC de DESARROLLO antes de copiar al cliente.
REM Genera la carpeta "dist\" lista para copiar.
REM ============================================================================

echo.
echo  ============================================================
echo    Preparar paquete para despliegue al cliente
echo  ============================================================
echo.

set "PROJECT_DIR=%~dp0.."
set "DIST_DIR=%PROJECT_DIR%\dist\pos_fifo_system"
cd /d "%PROJECT_DIR%"

REM --- Limpiar dist anterior ---
if exist "%PROJECT_DIR%\dist" rmdir /s /q "%PROJECT_DIR%\dist"
mkdir "%DIST_DIR%"

REM --- Compilar Tailwind CSS para produccion ---
echo [1/5] Compilando Tailwind CSS (produccion)...
if exist "%PROJECT_DIR%\package.json" (
    call npx tailwindcss -i ./static/css/styles.css -o ./static/css/output.css --minify 2>nul
    if %errorlevel% equ 0 (
        echo   [OK] CSS compilado y minificado
    ) else (
        echo   [AVISO] No se pudo compilar Tailwind. Copiando CSS existente...
    )
) else (
    echo   [AVISO] No hay package.json, copiando CSS existente
)

REM --- Collectstatic ---
echo [2/5] Recolectando archivos estaticos...
set DJANGO_SETTINGS_MODULE=config.settings_production
python manage.py collectstatic --noinput >nul 2>&1
echo   [OK] Staticfiles listos

REM --- Copiar archivos del proyecto (excluyendo innecesarios) ---
echo [3/5] Copiando archivos del proyecto...

REM Solo las carpetas necesarias (NO: .git, .vscode, node_modules, diagnostic, media, staticfiles, venv)
robocopy "%PROJECT_DIR%\apps" "%DIST_DIR%\apps" /e /xd __pycache__ /xf *.pyc >nul
robocopy "%PROJECT_DIR%\config" "%DIST_DIR%\config" /e /xd __pycache__ /xf *.pyc >nul
robocopy "%PROJECT_DIR%\templates" "%DIST_DIR%\templates" /e >nul
robocopy "%PROJECT_DIR%\static" "%DIST_DIR%\static" /e >nul
robocopy "%PROJECT_DIR%\utils" "%DIST_DIR%\utils" /e /xd __pycache__ /xf *.pyc >nul
robocopy "%PROJECT_DIR%\deploy" "%DIST_DIR%\deploy" /e /xf env_cliente.bat >nul

REM Archivos raiz (solo produccion, NO: package.json, tailwind.config.js, environment.yml)
for %%f in (manage.py requirements.txt server.py) do (
    if exist "%PROJECT_DIR%\%%f" copy "%PROJECT_DIR%\%%f" "%DIST_DIR%\" >nul
)

REM Crear carpetas vacias necesarias
for %%d in (logs backups media) do (
    mkdir "%DIST_DIR%\%%d" 2>nul
)

echo   [OK] Archivos copiados (sin __pycache__, sin .pyc, sin venv)

REM --- Verificar paquete ---
echo [4/5] Verificando paquete...
set /a FILES=0
for /r "%DIST_DIR%" %%f in (*) do set /a FILES+=1
echo   [OK] Paquete contiene %FILES% archivos

REM --- Generar manifiesto ---
echo [5/5] Generando manifiesto...
dir /s /b "%DIST_DIR%" > "%PROJECT_DIR%\dist\MANIFIESTO.txt"
echo   [OK] Manifiesto en dist\MANIFIESTO.txt

echo.
echo  ============================================================
echo    PAQUETE LISTO EN: dist\pos_fifo_system\
echo  ============================================================
echo.
echo  Instrucciones:
echo    1. Copie la carpeta dist\pos_fifo_system\ a una USB
echo    2. En la PC del cliente, copie a C:\pos_fifo_system\
echo    3. Ejecute como admin: deploy\instalar.bat
echo.

pause
endlocal
