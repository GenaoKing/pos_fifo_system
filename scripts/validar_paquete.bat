@echo off
chcp 65001 >nul 2>&1
setlocal
title Validar paquete (pre-release)

REM ============================================================================
REM Validacion PRE-empaquetado (dev-only; scripts\ NO se empaqueta).
REM Llamado por deploy\preparar_paquete.bat antes de construir el dist.
REM
REM   1) Lint de .bat: ningun echo con parentesis dentro de bloques if(...).
REM   2) Check en venv LIMPIO: pip install -r requirements.txt + manage.py check,
REM      lo unico que atrapa dependencias faltantes en requirements.txt (bug #6).
REM
REM Uso:  scripts\validar_paquete.bat [--skip-venv]
REM Exit: 0 si todo pasa, 1 si algo falla.
REM ============================================================================

set "PROJECT_DIR=%~dp0.."
set "FAIL=0"

echo [1/2] Lint de archivos .bat...
python "%~dp0lint_bat.py" "%PROJECT_DIR%\deploy"
if errorlevel 1 set "FAIL=1"

if /i "%~1"=="--skip-venv" (
    echo [2/2] Check en venv limpio OMITIDO por --skip-venv.
    goto :fin
)

echo [2/2] Check en venv limpio: creando venv temporal...
set "VENV=%TEMP%\pos_validate_venv"
if exist "%VENV%" rmdir /s /q "%VENV%"
python -m venv "%VENV%"
if errorlevel 1 (
    echo   [ERROR] No se pudo crear el venv temporal.
    set "FAIL=1"
    goto :fin
)
call "%VENV%\Scripts\activate.bat"
echo   Instalando requirements.txt en el venv limpio...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -q -r "%PROJECT_DIR%\requirements.txt"
if errorlevel 1 (
    echo   [ERROR] pip install fallo. Revisa requirements.txt.
    set "FAIL=1"
)
set DJANGO_DEBUG=false
python "%PROJECT_DIR%\manage.py" check --settings=config.settings_production
if errorlevel 1 (
    echo   [ERROR] manage.py check fallo en venv limpio. Falta una dependencia en requirements.txt.
    set "FAIL=1"
)
rmdir /s /q "%VENV%" 2>nul

:fin
echo.
if "%FAIL%"=="1" (
    echo [ERROR] Validacion del paquete FALLO. Corrige antes de empaquetar.
    exit /b 1
)
echo [OK] Validacion del paquete paso.
exit /b 0
