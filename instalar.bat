@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
title Royal Plastic POS - Instalador v2

REM ============================================================================
REM Royal Plastic POS - Instalacion Automatizada v2
REM
REM PREREQUISITOS (instalar manualmente antes de ejecutar):
REM   1. Python 3.12+ (con "Add to PATH" marcado)
REM   2. PostgreSQL 15 (con bin\ agregado al PATH)
REM   3. Driver impresora 2Connect (opcional, se configura despues)
REM
REM EJECUTAR: Click derecho > Ejecutar como administrador
REM ============================================================================

echo.
echo  ============================================================
echo    Royal Plastic POS - Instalador Automatizado v2
echo  ============================================================
echo.

REM --- Verificar administrador ---
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Este script debe ejecutarse como Administrador.
    echo         Click derecho - Ejecutar como administrador
    pause
    exit /b 1
)

set "PROJECT_DIR=%~dp0.."
cd /d "%PROJECT_DIR%"
echo [INFO] Directorio: %PROJECT_DIR%
echo.

REM ============================================================================
REM FASE 1: Verificar prerequisitos
REM ============================================================================
echo [FASE 1/10] Verificando prerequisitos...
echo.

set ERRORS=0

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] Python no encontrado. Instale Python 3.12+ desde python.org
    echo           Marque "Add Python to PATH" durante la instalacion.
    set /a ERRORS+=1
) else (
    for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do (
        echo   [OK] Python %%v
    )
)

where psql >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] psql no encontrado. Instale PostgreSQL 15+ y agregue bin\ al PATH.
    echo           Ejemplo: C:\Program Files\PostgreSQL\15\bin
    set /a ERRORS+=1
) else (
    echo   [OK] PostgreSQL encontrado
)

python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] pip no disponible.
    set /a ERRORS+=1
) else (
    echo   [OK] pip disponible
)

if %ERRORS% gtr 0 (
    echo.
    echo [ERROR] Corrija los %ERRORS% errores antes de continuar.
    pause
    exit /b 1
)
echo.

REM ============================================================================
REM FASE 2: Configuracion del cliente
REM ============================================================================
echo [FASE 2/10] Configuracion del cliente...

if not exist "%PROJECT_DIR%\deploy\env_cliente.bat" (
    echo   Creando env_cliente.bat desde template...
    copy "%PROJECT_DIR%\deploy\env_cliente.bat.template" "%PROJECT_DIR%\deploy\env_cliente.bat" >nul
    echo.
    echo  *** IMPORTANTE ***
    echo  Se abrio el archivo de configuracion del cliente.
    echo  Revise y ajuste los valores (contrasena DB, IP, etc.)
    echo  Guarde, cierre el Notepad, y presione una tecla para continuar.
    echo.
    notepad "%PROJECT_DIR%\deploy\env_cliente.bat"
    pause
)

call "%PROJECT_DIR%\deploy\env_cliente.bat"
echo   [OK] Variables cargadas (DB: %DB_NAME%, IP: %SERVER_IP%)
echo.

REM ============================================================================
REM FASE 3: Entorno virtual
REM ============================================================================
echo [FASE 3/10] Entorno virtual de Python...

if not exist "%PROJECT_DIR%\venv" (
    python -m venv "%PROJECT_DIR%\venv"
    if %errorlevel% neq 0 (
        echo   [ERROR] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
    echo   [OK] Entorno virtual creado
) else (
    echo   [OK] Entorno virtual ya existe
)

call "%PROJECT_DIR%\venv\Scripts\activate.bat"
echo   [OK] Activado
echo.

REM ============================================================================
REM FASE 4: Dependencias
REM ============================================================================
echo [FASE 4/10] Instalando dependencias...
echo   Esto puede tomar unos minutos...
echo.

python -m pip install --upgrade pip >nul 2>&1

pip install -r "%PROJECT_DIR%\requirements.txt"
if %errorlevel% neq 0 (
    echo.
    echo   [ERROR] Fallo la instalacion de dependencias.
    echo   Revise los mensajes de error arriba.
    pause
    exit /b 1
)

echo.
echo   [OK] Todas las dependencias instaladas
echo.

REM ============================================================================
REM FASE 5: Base de datos PostgreSQL
REM ============================================================================
echo [FASE 5/10] Configurando base de datos...
echo.

REM Pedir contrasena de postgres si no la tenemos
echo   Se necesita la contrasena del usuario "postgres" de PostgreSQL.
echo   (Es la que se definio al instalar PostgreSQL)
echo.
set /p PG_PASS="   Contrasena de postgres: "
echo.

set "PGPASSWORD=%PG_PASS%"

REM Verificar conexion a PostgreSQL
psql -U postgres -c "SELECT 1;" >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] No se pudo conectar a PostgreSQL con la contrasena proporcionada.
    echo           Verifique que PostgreSQL este corriendo y la contrasena sea correcta.
    pause
    exit /b 1
)
echo   [OK] Conexion a PostgreSQL verificada

REM Crear usuario
psql -U postgres -tc "SELECT 1 FROM pg_roles WHERE rolname='%DB_USER%'" 2>nul | findstr "1" >nul
if %errorlevel% neq 0 (
    psql -U postgres -c "CREATE USER %DB_USER% WITH PASSWORD '%DB_PASSWORD%';" 2>nul
    echo   [OK] Usuario %DB_USER% creado
) else (
    psql -U postgres -c "ALTER USER %DB_USER% WITH PASSWORD '%DB_PASSWORD%';" 2>nul
    echo   [OK] Usuario %DB_USER% ya existia, contrasena actualizada
)

REM Crear base de datos
psql -U postgres -tc "SELECT 1 FROM pg_database WHERE datname='%DB_NAME%'" 2>nul | findstr "1" >nul
if %errorlevel% neq 0 (
    psql -U postgres -c "CREATE DATABASE %DB_NAME% OWNER %DB_USER% ENCODING 'UTF8';" 2>nul
    if %errorlevel% neq 0 (
        echo   [ERROR] No se pudo crear la base de datos.
        pause
        exit /b 1
    )
    echo   [OK] Base de datos %DB_NAME% creada
) else (
    echo   [OK] Base de datos %DB_NAME% ya existe
)

REM Permisos
psql -U postgres -d %DB_NAME% -c "GRANT ALL ON SCHEMA public TO %DB_USER%;" >nul 2>&1
psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE %DB_NAME% TO %DB_USER%;" >nul 2>&1
echo   [OK] Permisos otorgados
echo.

REM ============================================================================
REM FASE 6: Migraciones
REM ============================================================================
echo [FASE 6/10] Ejecutando migraciones...

python manage.py migrate --settings=config.settings_production
if %errorlevel% neq 0 (
    echo.
    echo   [ERROR] Fallaron las migraciones. Revise los mensajes arriba.
    pause
    exit /b 1
)
echo.
echo   [OK] Migraciones completadas
echo.

REM ============================================================================
REM FASE 7: Archivos estaticos
REM ============================================================================
echo [FASE 7/10] Recolectando archivos estaticos...

python manage.py collectstatic --noinput --settings=config.settings_production >nul 2>&1
if %errorlevel% neq 0 (
    echo   [AVISO] collectstatic reporto advertencias (puede ser normal)
) else (
    echo   [OK] Archivos estaticos listos
)
echo.

REM ============================================================================
REM FASE 8: Superusuario
REM ============================================================================
echo [FASE 8/10] Crear usuario administrador...

python manage.py shell --settings=config.settings_production -c "from django.contrib.auth import get_user_model; User = get_user_model(); exit(0 if User.objects.filter(is_superuser=True).exists() else 1)" 2>nul
if %errorlevel% neq 0 (
    echo   Creando usuario administrador del sistema.
    echo   Complete los datos que se le soliciten:
    echo.
    python manage.py createsuperuser --settings=config.settings_production
) else (
    echo   [OK] Ya existe un usuario administrador
)
echo.

REM ============================================================================
REM FASE 9: Carpetas y SECRET_KEY
REM ============================================================================
echo [FASE 9/10] Configuracion final...

if not exist "%PROJECT_DIR%\logs" mkdir "%PROJECT_DIR%\logs"
if not exist "%PROJECT_DIR%\backups" mkdir "%PROJECT_DIR%\backups"
if not exist "%PROJECT_DIR%\media" mkdir "%PROJECT_DIR%\media"
echo   [OK] Carpetas creadas

REM Generar SECRET_KEY unica si no se ha configurado
echo %DJANGO_SECRET_KEY% | findstr "CAMBIAR" >nul
if %errorlevel% equ 0 (
    echo   Generando SECRET_KEY unica...
    for /f "delims=" %%k in ('python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"') do (
        set "NEW_KEY=%%k"
    )
    powershell -Command "(Get-Content '%PROJECT_DIR%\deploy\env_cliente.bat') -replace 'CAMBIAR-POR-KEY-UNICA-POR-INSTALACION', '!NEW_KEY!' | Set-Content '%PROJECT_DIR%\deploy\env_cliente.bat'" 2>nul
    echo   [OK] SECRET_KEY generada y guardada
) else (
    echo   [OK] SECRET_KEY ya configurada
)
echo.

REM ============================================================================
REM FASE 10: Verificacion
REM ============================================================================
echo [FASE 10/10] Verificacion rapida del sistema...
echo.

echo   Iniciando servidor de prueba...
echo   Abra el navegador en: http://localhost:%SERVER_PORT%
echo.
echo   Si el sistema carga correctamente, cierre esta ventana (Ctrl+C)
echo   y pase a configurar el servidor de produccion con:
echo     deploy\iniciar_servidor.bat
echo.

set DJANGO_SETTINGS_MODULE=config.settings_production
python manage.py runserver 0.0.0.0:%SERVER_PORT%

pause
endlocal
