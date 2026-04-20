@echo off
REM ============================================================================
REM  run_azure_pg.bat — Iniciar Django con Azure PostgreSQL
REM ============================================================================
REM  USO:
REM    deploy\run_azure_pg.bat migrate          — Correr migraciones
REM    deploy\run_azure_pg.bat runserver         — Levantar servidor dev
REM    deploy\run_azure_pg.bat crear_config      — Crear config inicial
REM    deploy\run_azure_pg.bat test_latency      — Prueba de latencia
REM    deploy\run_azure_pg.bat shell             — Django shell
REM ============================================================================

REM --- Cargar variables de entorno ---
if exist deploy\env_azure_pg_local.bat (
    call deploy\env_azure_pg_local.bat
) else (
    echo [ERROR] No se encontro deploy\env_azure_pg_local.bat
    echo         Copie deploy\env_azure_pg.bat como deploy\env_azure_pg_local.bat
    echo         y configure sus credenciales Azure.
    pause
    exit /b 1
)

REM --- Validar que las credenciales estan configuradas ---
if "%AZURE_PG_DB_HOST%"=="TU_SERVIDOR.postgres.database.azure.com" (
    echo [ERROR] Las credenciales no han sido configuradas.
    echo         Edite deploy\env_azure_pg_local.bat con sus datos reales.
    pause
    exit /b 1
)

REM --- Ejecutar comando ---
if "%1"=="" (
    echo Uso: deploy\run_azure_pg.bat [migrate^|runserver^|crear_config^|test_latency^|shell]
    exit /b 0
)

if "%1"=="migrate" (
    echo [AZURE PG] Ejecutando migraciones...
    python manage.py migrate --settings=config.settings_azure_pg
    goto :done
)

if "%1"=="runserver" (
    echo [AZURE PG] Iniciando servidor en puerto 8002...
    python manage.py runserver 0.0.0.0:8002 --settings=config.settings_azure_pg
    goto :done
)

if "%1"=="crear_config" (
    echo [AZURE PG] Creando configuracion inicial...
    python manage.py crear_config_inicial --settings=config.settings_azure_pg
    goto :done
)

if "%1"=="test_latency" (
    echo [AZURE PG] Ejecutando pruebas de latencia...
    python deploy\test_cloud_latency.py
    goto :done
)

if "%1"=="shell" (
    echo [AZURE PG] Abriendo Django shell...
    python manage.py shell --settings=config.settings_azure_pg
    goto :done
)

REM --- Comando personalizado ---
echo [AZURE PG] Ejecutando: python manage.py %* --settings=config.settings_azure_pg
python manage.py %* --settings=config.settings_azure_pg

:done
