@echo off
chcp 65001 >nul 2>&1
title Royal Plastic POS - Detener Servidor

REM ============================================================================
REM Royal Plastic POS - Detener Servidor
REM ============================================================================

echo.
echo  Deteniendo Royal Plastic POS...
echo.

REM --- Si esta como servicio NSSM ---
set "NSSM_PATH=%~dp0nssm.exe"
if exist "%NSSM_PATH%" (
    "%NSSM_PATH%" stop RoyalPlasticPOS >nul 2>&1
    if %errorlevel% equ 0 (
        echo   [OK] Servicio detenido via NSSM.
        goto :fin
    )
)

REM --- Si corre como proceso Python ---
tasklist /fi "WINDOWTITLE eq Royal Plastic POS*" 2>nul | findstr "cmd.exe" >nul
if %errorlevel% equ 0 (
    taskkill /fi "WINDOWTITLE eq Royal Plastic POS*" /f >nul 2>&1
)

REM --- Buscar proceso waitress/python en el puerto ---
for /f "tokens=5" %%p in ('netstat -aon ^| findstr ":%SERVER_PORT% " ^| findstr "LISTENING"') do (
    taskkill /pid %%p /f >nul 2>&1
    echo   [OK] Proceso PID %%p detenido.
)

echo   [OK] Servidor detenido.

:fin
echo.
pause
