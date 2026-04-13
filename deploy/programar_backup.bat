@echo off
chcp 65001 >nul 2>&1
title POS FIFO System - Programar Backup Automatico

REM ============================================================================
REM POS FIFO System - Programar Backup Diario Automatico v3
REM Ejecutar como Administrador
REM ============================================================================

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Ejecute como Administrador.
    pause
    exit /b 1
)

set "PROJECT_DIR=%~dp0.."

echo.
echo  Programando backup diario automatico...
echo  Hora: 11:00 PM
echo.

schtasks /create ^
    /tn "POSFifoSystem_BackupDiario" ^
    /tr "\"%PROJECT_DIR%\deploy\backup_db.bat\"" ^
    /sc daily ^
    /st 23:00 ^
    /rl highest ^
    /f

if %errorlevel% equ 0 (
    echo   [OK] Backup diario programado a las 11:00 PM
    echo.
    echo   Para verificar: Panel de Control ^> Herramientas administrativas
    echo                    ^> Programador de tareas
    echo.
    echo   Para ejecutar backup manual: deploy\backup_db.bat
) else (
    echo   [ERROR] No se pudo crear la tarea programada.
)

echo.
pause
