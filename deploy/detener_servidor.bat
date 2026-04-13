@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
title POS FIFO System - Detener Servidor

REM ============================================================================
REM POS FIFO System - Detener Servidor v3
REM ============================================================================

set "PROJECT_DIR=%~dp0.."
call "%PROJECT_DIR%\deploy\env_cliente.bat" >nul 2>&1

echo.
echo  Deteniendo POS FIFO System...
echo.

REM --- Si esta como servicio NSSM ---
set "NSSM_PATH=%~dp0nssm.exe"
if exist "%NSSM_PATH%" (
    "%NSSM_PATH%" stop POSFifoSystem >nul 2>&1
    if !errorlevel! equ 0 (
        echo   [OK] Servicio detenido via NSSM.
        goto :fin
    )
)

REM --- Buscar proceso en el puerto configurado ---
if "%SERVER_PORT%"=="" set SERVER_PORT=8080

for /f "tokens=5" %%p in ('netstat -aon ^| findstr ":%SERVER_PORT% " ^| findstr "LISTENING"') do (
    taskkill /pid %%p /f >nul 2>&1
    echo   [OK] Proceso PID %%p detenido.
)

echo   [OK] Servidor detenido.

:fin
echo.
pause
endlocal
