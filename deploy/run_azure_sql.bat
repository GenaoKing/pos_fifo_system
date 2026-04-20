@echo off
REM ============================================================================
REM  run_azure_sql.bat — Iniciar Django con Azure SQL Database
REM ============================================================================
REM  USO:
REM    deploy\run_azure_sql.bat migrate          — Correr migraciones
REM    deploy\run_azure_sql.bat runserver         — Levantar servidor dev
REM    deploy\run_azure_sql.bat crear_config      — Crear config inicial
REM    deploy\run_azure_sql.bat test_latency      — Prueba de latencia
REM    deploy\run_azure_sql.bat shell             — Django shell
REM ============================================================================
REM  PREREQUISITOS:
REM    1. ODBC Driver 18 for SQL Server instalado
REM    2. pip install -r requirements_cloud.txt
REM ============================================================================

REM --- Cargar variables de entorno ---
if exist deploy\env_azure_sql_local.bat (
    call deploy\env_azure_sql_local.bat
) else (
    echo [ERROR] No se encontro deploy\env_azure_sql_local.bat
    echo         Copie deploy\env_azure_sql.bat como deploy\env_azure_sql_local.bat
    echo         y configure sus credenciales Azure.
    pause
    exit /b 1
)

REM --- Validar que las credenciales estan configuradas ---
if "%AZURE_SQL_DB_HOST%"=="TU_SERVIDOR.database.windows.net" (
    echo [ERROR] Las credenciales no han sido configuradas.
    echo         Edite deploy\env_azure_sql_local.bat con sus datos reales.
    pause
    exit /b 1
)

REM --- Verificar ODBC Driver ---
reg query "HKLM\SOFTWARE\ODBC\ODBCINST.INI\ODBC Driver 18 for SQL Server" >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] ODBC Driver 18 for SQL Server no esta instalado.
    echo         Descargar: https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server
    pause
    exit /b 1
)

REM --- Ejecutar comando ---
if "%1"=="" (
    echo Uso: deploy\run_azure_sql.bat [migrate^|runserver^|crear_config^|test_latency^|shell]
    exit /b 0
)

if "%1"=="migrate" (
    echo [AZURE SQL] Ejecutando migraciones...
    echo            NOTA: Revise errores de compatibilidad ORM y documentelos
    echo            en docs\FASE1_AZURE_SQL_COMPAT.md
    python manage.py migrate --settings=config.settings_azure_sql
    goto :done
)

if "%1"=="runserver" (
    echo [AZURE SQL] Iniciando servidor en puerto 8003...
    python manage.py runserver 0.0.0.0:8003 --settings=config.settings_azure_sql
    goto :done
)

if "%1"=="crear_config" (
    echo [AZURE SQL] Creando configuracion inicial...
    python manage.py crear_config_inicial --settings=config.settings_azure_sql
    goto :done
)

if "%1"=="test_latency" (
    echo [AZURE SQL] Ejecutando pruebas de latencia...
    python deploy\test_cloud_latency.py
    goto :done
)

if "%1"=="shell" (
    echo [AZURE SQL] Abriendo Django shell...
    python manage.py shell --settings=config.settings_azure_sql
    goto :done
)

REM --- Comando personalizado ---
echo [AZURE SQL] Ejecutando: python manage.py %* --settings=config.settings_azure_sql
python manage.py %* --settings=config.settings_azure_sql

:done
