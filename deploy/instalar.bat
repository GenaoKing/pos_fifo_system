@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
title Royal Plastic POS - Instalador v1.0

REM ============================================================================
REM Royal Plastic POS - Script de Instalacion Automatizada
REM Ejecutar como Administrador
REM ============================================================================

echo.
echo  ============================================================
echo    Royal Plastic POS - Instalador Automatizado
echo    Sistema de Punto de Venta con Inventario FIFO
echo  ============================================================
echo.

REM --- Verificar que se ejecuta como administrador ---
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Este script debe ejecutarse como Administrador.
    echo         Click derecho ^> Ejecutar como administrador
    pause
    exit /b 1
)

REM --- Directorio base del proyecto ---
set "PROJECT_DIR=%~dp0.."
cd /d "%PROJECT_DIR%"
echo [INFO] Directorio del proyecto: %PROJECT_DIR%
echo.

REM ============================================================================
REM PASO 1: Verificar prerequisitos
REM ============================================================================
echo [PASO 1/8] Verificando prerequisitos...
echo.

REM --- Python ---
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python no encontrado. Instale Python 3.11+ desde python.org
    echo         Marque "Add Python to PATH" durante la instalacion.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYTHON_VER=%%v
echo   [OK] Python %PYTHON_VER% encontrado

REM --- PostgreSQL ---
where psql >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] PostgreSQL no encontrado en PATH.
    echo         Instale PostgreSQL 15+ desde postgresql.org
    echo         Agregue la carpeta bin al PATH del sistema.
    echo         Ejemplo: C:\Program Files\PostgreSQL\15\bin
    pause
    exit /b 1
)
echo   [OK] PostgreSQL encontrado

REM --- pip ---
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] pip no encontrado. Reinstale Python con pip incluido.
    pause
    exit /b 1
)
echo   [OK] pip disponible
echo.

REM ============================================================================
REM PASO 2: Cargar configuracion del cliente
REM ============================================================================
echo [PASO 2/8] Cargando configuracion...

if not exist "%PROJECT_DIR%\deploy\env_cliente.bat" (
    echo [AVISO] No se encontro env_cliente.bat
    echo         Copiando template...
    copy "%PROJECT_DIR%\deploy\env_cliente.bat.template" "%PROJECT_DIR%\deploy\env_cliente.bat" >nul
    echo.
    echo  *** IMPORTANTE ***
    echo  Edite el archivo deploy\env_cliente.bat con los datos del cliente
    echo  y luego ejecute este instalador nuevamente.
    echo.
    notepad "%PROJECT_DIR%\deploy\env_cliente.bat"
    pause
    exit /b 0
)

call "%PROJECT_DIR%\deploy\env_cliente.bat"
echo   [OK] Variables de entorno cargadas
echo.

REM ============================================================================
REM PASO 3: Crear entorno virtual
REM ============================================================================
echo [PASO 3/8] Creando entorno virtual de Python...

if not exist "%PROJECT_DIR%\venv" (
    python -m venv "%PROJECT_DIR%\venv"
    echo   [OK] Entorno virtual creado
) else (
    echo   [OK] Entorno virtual ya existe
)

call "%PROJECT_DIR%\venv\Scripts\activate.bat"
echo   [OK] Entorno virtual activado
echo.

REM ============================================================================
REM PASO 4: Instalar dependencias
REM ============================================================================
echo [PASO 4/8] Instalando dependencias de Python...

python -m pip install --upgrade pip >nul 2>&1

if exist "%PROJECT_DIR%\requirements.txt" (
    pip install -r "%PROJECT_DIR%\requirements.txt"
    if %errorlevel% neq 0 (
        echo [ERROR] Fallo la instalacion de dependencias.
        pause
        exit /b 1
    )
) else (
    echo [ERROR] No se encontro requirements.txt
    pause
    exit /b 1
)

REM --- Dependencias adicionales de produccion ---
pip install waitress whitenoise >nul 2>&1
echo   [OK] Waitress y WhiteNoise instalados
echo.

REM ============================================================================
REM PASO 5: Configurar base de datos PostgreSQL
REM ============================================================================
echo [PASO 5/8] Configurando base de datos...

REM --- Verificar si la base de datos ya existe ---
set "PGPASSWORD=%DB_PASSWORD%"

psql -U postgres -tc "SELECT 1 FROM pg_database WHERE datname='%DB_NAME%'" 2>nul | findstr "1" >nul
if %errorlevel% neq 0 (
    echo   Creando usuario %DB_USER%...
    psql -U postgres -c "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='%DB_USER%') THEN CREATE ROLE %DB_USER% WITH LOGIN PASSWORD '%DB_PASSWORD%'; END IF; END $$;" 2>nul

    echo   Creando base de datos %DB_NAME%...
    psql -U postgres -c "CREATE DATABASE %DB_NAME% OWNER %DB_USER% ENCODING 'UTF8' LC_COLLATE='Spanish_Dominican Republic.1252' LC_CTYPE='Spanish_Dominican Republic.1252' TEMPLATE template0;" 2>nul

    if %errorlevel% neq 0 (
        echo   [AVISO] Intentando con locale por defecto...
        psql -U postgres -c "CREATE DATABASE %DB_NAME% OWNER %DB_USER% ENCODING 'UTF8';" 2>nul
    )

    psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE %DB_NAME% TO %DB_USER%;" 2>nul
    echo   [OK] Base de datos creada
) else (
    echo   [OK] Base de datos ya existe
)

echo.

REM ============================================================================
REM PASO 6: Migraciones y datos iniciales
REM ============================================================================
echo [PASO 6/8] Ejecutando migraciones de Django...

python manage.py migrate --settings=config.settings_production
if %errorlevel% neq 0 (
    echo [ERROR] Fallaron las migraciones.
    pause
    exit /b 1
)
echo   [OK] Migraciones completadas

REM --- Recolectar archivos estaticos ---
echo   Recolectando archivos estaticos...
python manage.py collectstatic --noinput --settings=config.settings_production >nul 2>&1
echo   [OK] Archivos estaticos listos
echo.

REM ============================================================================
REM PASO 7: Crear superusuario
REM ============================================================================
echo [PASO 7/8] Configuracion de usuario administrador...

python manage.py shell --settings=config.settings_production -c "from django.contrib.auth import get_user_model; User = get_user_model(); print('existe') if User.objects.filter(is_superuser=True).exists() else print('no_existe')" 2>nul | findstr "existe" >nul

if %errorlevel% neq 0 (
    echo   Creando usuario administrador...
    echo   Complete los datos que se le soliciten:
    echo.
    python manage.py createsuperuser --settings=config.settings_production
) else (
    echo   [OK] Ya existe un usuario administrador
)
echo.

REM ============================================================================
REM PASO 8: Crear estructura de carpetas
REM ============================================================================
echo [PASO 8/8] Creando estructura de carpetas...

if not exist "%PROJECT_DIR%\logs" mkdir "%PROJECT_DIR%\logs"
if not exist "%PROJECT_DIR%\backups" mkdir "%PROJECT_DIR%\backups"
if not exist "%PROJECT_DIR%\media" mkdir "%PROJECT_DIR%\media"
if not exist "%PROJECT_DIR%\staticfiles" mkdir "%PROJECT_DIR%\staticfiles"

echo   [OK] Carpetas creadas
echo.

REM ============================================================================
REM GENERAR SECRET KEY UNICA
REM ============================================================================
if "%DJANGO_SECRET_KEY%"=="CAMBIAR-POR-KEY-UNICA-POR-INSTALACION" (
    echo [AVISO] Generando SECRET_KEY unica para esta instalacion...
    for /f "delims=" %%k in ('python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"') do (
        set "NEW_KEY=%%k"
    )

    powershell -Command "(Get-Content '%PROJECT_DIR%\deploy\env_cliente.bat') -replace 'CAMBIAR-POR-KEY-UNICA-POR-INSTALACION', '!NEW_KEY!' | Set-Content '%PROJECT_DIR%\deploy\env_cliente.bat'"
    echo   [OK] SECRET_KEY generada y guardada en env_cliente.bat
    echo.
)

REM ============================================================================
REM VERIFICACION FINAL
REM ============================================================================
echo.
echo  ============================================================
echo    INSTALACION COMPLETADA EXITOSAMENTE
echo  ============================================================
echo.
echo  Para iniciar el sistema:
echo    deploy\iniciar_servidor.bat
echo.
echo  Para registrar como servicio de Windows:
echo    deploy\registrar_servicio.bat
echo.
echo  Para configurar backups automaticos:
echo    deploy\programar_backup.bat
echo.
echo  Acceso al sistema:
echo    http://localhost:%SERVER_PORT%
echo    http://%SERVER_IP%:%SERVER_PORT% (desde otra PC)
echo.
echo  ============================================================
echo.

pause
endlocal
