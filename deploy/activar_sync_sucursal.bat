@echo off
REM ============================================================================
REM POS FIFO System - Activar Sync Engine para SUCURSAL local
REM ============================================================================
REM Este .bat configura las variables de entorno necesarias para que la
REM instancia LOCAL (tu Postgres local en settings.py) haga sync contra la
REM nube (Azure PostgreSQL con run_azure_pg.bat corriendo en otra ventana).
REM
REM Uso:
REM   deploy\activar_sync_sucursal.bat runserver     (levantar sucursal)
REM   deploy\activar_sync_sucursal.bat sincronizar --once   (correr daemon)
REM   deploy\activar_sync_sucursal.bat sync_status   (diagnostico)
REM   deploy\activar_sync_sucursal.bat shell         (shell con sync activo)
REM
REM IMPORTANTE:
REM   - Usa tu settings.py normal (Postgres LOCAL). No toca Azure.
REM   - La instancia Azure (run_azure_pg.bat) debe estar CORRIENDO como
REM     runserver en OTRA ventana para que el sync tenga a quien hablarle.
REM   - CLOUD_API_TOKEN debe ser el token generado por vincular_sucursal_token
REM     en la instancia CLOUD (Azure).
REM ============================================================================

REM --- Encoding (previene errores en Windows espanol) ---
set PGCLIENTENCODING=UTF8
set PYTHONUTF8=1

REM --- Sucursal ---
set SUCURSAL_CODIGO=SD-001

REM --- Cloud API (apunta a la otra ventana corriendo Azure PG) ---
REM Si Azure corre con run_azure_pg.bat runserver por default en 8000,
REM cambia ese a 8001 y deja la sucursal en 8000.
set CLOUD_API_URL=http://localhost:8002

REM --- PEGA AQUI EL TOKEN que te dio vincular_sucursal_token ---
REM Ejemplo (no es un token real):
REM set CLOUD_API_TOKEN=7a3e9b8c4d5f6e2a1b0c9d8e7f6a5b4c3d2e1f0a
set CLOUD_API_TOKEN=46e7ba02adba37c9a6e4eb98bab6ea176f28d4cc

REM --- Sync engine settings ---
set SYNC_ENABLED=true
set SYNC_INTERVAL=30
set SYNC_BATCH_SIZE=50
set SYNC_MAX_RETRIES=10
set SYNC_HTTP_TIMEOUT=10

REM --- Validacion minima ---
if "%CLOUD_API_TOKEN%"=="PEGAR-TOKEN-DE-vincular_sucursal_token-AQUI" (
    echo.
    echo [ERROR] Debes editar este .bat y poner CLOUD_API_TOKEN real.
    echo         Generarlo con: deploy\run_azure_pg.bat vincular_sucursal_token --sucursal SD-001
    echo.
    pause
    exit /b 1
)

echo [OK] Sync engine configurado para sucursal %SUCURSAL_CODIGO%
echo     Cloud: %CLOUD_API_URL%
echo     Sync:  ENABLED (interval=%SYNC_INTERVAL%s batch=%SYNC_BATCH_SIZE%)
echo.

REM --- Ejecutar el comando que paso el usuario ---
REM (%* pasa todos los argumentos: runserver, sincronizar, sync_status, etc.)
if "%~1"=="" (
    echo Uso: activar_sync_sucursal.bat ^<comando^> [args]
    echo   Ejemplos:
    echo     activar_sync_sucursal.bat runserver 8000
    echo     activar_sync_sucursal.bat sincronizar --once
    echo     activar_sync_sucursal.bat sync_status
    exit /b 1
)

python manage.py %*
