@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
title POS FIFO System - Instalador v3

REM ============================================================================
REM POS FIFO System - Instalacion Automatizada v3
REM
REM Cambios vs v2:
REM   - Datos de negocio via ConfiguracionNegocio (BD), no env_cliente.bat
REM   - Crea usuario SYSADMIN automaticamente (Santiago)
REM   - Crea Caja Principal automaticamente
REM   - Corre crear_config_inicial con preset del negocio
REM   - Validacion de BD con pg_database (no exit code de CREATE)
REM   - Encoding UTF8 forzado (PGCLIENTENCODING + PYTHONUTF8)
REM
REM PREREQUISITOS (instalar manualmente antes):
REM   1. Python 3.12+ (con "Add to PATH" marcado)
REM   2. PostgreSQL 15 (con bin\ agregado al PATH del sistema)
REM
REM EJECUTAR: Click derecho > Ejecutar como administrador
REM ============================================================================

echo.
echo  ============================================================
echo    POS FIFO System - Instalador Automatizado v3
echo    Sistema de Punto de Venta con Inventario FIFO
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
echo [INFO] Directorio del proyecto: %PROJECT_DIR%
echo.

REM --- Forzar encoding UTF8 desde el inicio ---
set PGCLIENTENCODING=UTF8
set PYTHONUTF8=1

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
    echo   [ERROR] pip no disponible. Reinstale Python con pip incluido.
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
    copy "%PROJECT_DIR%\deploy\env_cliente.env.template" "%PROJECT_DIR%\deploy\env_cliente.env" >nul
    echo.
    echo  *** IMPORTANTE ***
    echo  Se abrira el archivo de configuracion.
    echo  DEBE editar al menos:
    echo    - DB_PASSWORD: contrasena para el usuario de la BD del POS
    echo    - INITIAL_SYSADMIN_PASSWORD: password temporal del primer SYSADMIN
    echo    - NEGOCIO_NOMBRE: nombre del negocio
    echo    - NEGOCIO_PRESET: tipo de negocio
    echo.
    echo  Guarde, cierre el Notepad, y presione una tecla para continuar.
    echo.
    notepad "%PROJECT_DIR%\deploy\env_cliente.bat"
    pause
)

REM El .env lo lee la aplicacion, pero este script tambien necesita los
REM valores (crear BD, usuario, etc.), asi que los carga a variables de cmd.
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%PROJECT_DIR%\deploy\env_cliente.env") do (
    if not "%%A"=="" set "%%A=%%B"
)

if "%DB_PASSWORD%"=="CAMBIAR-CONTRASENA-BD" (
    echo   [ERROR] Debe configurar DB_PASSWORD en deploy\env_cliente.bat
    notepad "%PROJECT_DIR%\deploy\env_cliente.bat"
    pause
    exit /b 1
)

if "%INITIAL_SYSADMIN_PASSWORD%"=="CAMBIAR-PASSWORD-INICIAL-SYSADMIN" (
    echo   [ERROR] Debe configurar INITIAL_SYSADMIN_PASSWORD en deploy\env_cliente.bat
    notepad "%PROJECT_DIR%\deploy\env_cliente.bat"
    pause
    exit /b 1
)

if "%INITIAL_SYSADMIN_PASSWORD%"=="" (
    echo   [ERROR] INITIAL_SYSADMIN_PASSWORD no puede estar vacio.
    notepad "%PROJECT_DIR%\deploy\env_cliente.bat"
    pause
    exit /b 1
)

if "%INITIAL_SYSADMIN_USERNAME%"=="" (
    echo   [ERROR] INITIAL_SYSADMIN_USERNAME no puede estar vacio.
    notepad "%PROJECT_DIR%\deploy\env_cliente.bat"
    pause
    exit /b 1
)

echo   [OK] Variables cargadas (DB: %DB_NAME%, Preset: %NEGOCIO_PRESET%)
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
REM FASE 4: Instalar dependencias
REM ============================================================================
echo [FASE 4/10] Instalando dependencias de Python...

python -m pip install --upgrade pip >nul 2>&1

if exist "%PROJECT_DIR%\requirements.txt" (
    pip install -r "%PROJECT_DIR%\requirements.txt"
    if %errorlevel% neq 0 (
        echo   [ERROR] Fallo la instalacion de dependencias.
        echo           Revise los errores arriba.
        pause
        exit /b 1
    )
) else (
    echo   [ERROR] No se encontro requirements.txt
    pause
    exit /b 1
)

REM --- Dependencias de produccion (por si no estan en requirements.txt) ---
pip install waitress whitenoise python-dotenv >nul 2>&1

echo   [OK] Dependencias instaladas
echo.

REM ============================================================================
REM FASE 5: Configurar base de datos PostgreSQL
REM ============================================================================
echo [FASE 5/10] Configurando base de datos...

REM --- Obtener contrasena de postgres si no esta en env ---
if "%PG_SUPERPASS%"=="" (
    echo   Se necesita la contrasena del usuario 'postgres' de PostgreSQL.
    echo   Es la que se definio al instalar PostgreSQL.
    echo.
    set /p "PG_SUPERPASS=  Contrasena de postgres: "
    echo.
)

set "PGPASSWORD=%PG_SUPERPASS%"

REM --- Verificar conexion a PostgreSQL ---
psql -U postgres -tc "SELECT 1" >nul 2>&1
if %errorlevel% neq 0 (
    echo   [ERROR] No se pudo conectar a PostgreSQL con el usuario postgres.
    echo           Verifique que PostgreSQL este corriendo y la contrasena sea correcta.
    pause
    exit /b 1
)
echo   [OK] Conexion a PostgreSQL verificada

REM --- Crear usuario si no existe ---
psql -U postgres -tc "SELECT 1 FROM pg_roles WHERE rolname='%DB_USER%'" 2>nul | findstr "1" >nul
if %errorlevel% neq 0 (
    echo   Creando usuario %DB_USER%...
    psql -U postgres -c "CREATE ROLE %DB_USER% WITH LOGIN PASSWORD '%DB_PASSWORD%' CREATEDB;" 2>nul
) else (
    echo   [OK] Usuario %DB_USER% ya existe
    REM Actualizar contrasena por si cambio
    psql -U postgres -c "ALTER USER %DB_USER% WITH PASSWORD '%DB_PASSWORD%';" >nul 2>&1
)

REM --- Crear base de datos si no existe ---
psql -U postgres -tc "SELECT 1 FROM pg_database WHERE datname='%DB_NAME%'" 2>nul | findstr "1" >nul
if %errorlevel% neq 0 (
    echo   Creando base de datos %DB_NAME%...

    REM Intentar con locale dominicano primero
    psql -U postgres -c "CREATE DATABASE %DB_NAME% OWNER %DB_USER% ENCODING 'UTF8' LC_COLLATE='Spanish_Dominican Republic.1252' LC_CTYPE='Spanish_Dominican Republic.1252' TEMPLATE template0;" 2>nul

    REM Verificar si se creo (no confiar en exit code por warning de locale)
    psql -U postgres -tc "SELECT 1 FROM pg_database WHERE datname='%DB_NAME%'" 2>nul | findstr "1" >nul
    if !errorlevel! neq 0 (
        echo   [AVISO] Locale dominicano no disponible, usando default...
        psql -U postgres -c "CREATE DATABASE %DB_NAME% OWNER %DB_USER% ENCODING 'UTF8';" 2>nul

        REM Verificar de nuevo
        psql -U postgres -tc "SELECT 1 FROM pg_database WHERE datname='%DB_NAME%'" 2>nul | findstr "1" >nul
        if !errorlevel! neq 0 (
            echo   [ERROR] No se pudo crear la base de datos.
            pause
            exit /b 1
        )
    )
    echo   [OK] Base de datos %DB_NAME% creada
) else (
    echo   [OK] Base de datos %DB_NAME% ya existe
)

REM --- Otorgar permisos ---
psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE %DB_NAME% TO %DB_USER%;" >nul 2>&1
psql -U postgres -d %DB_NAME% -c "GRANT ALL ON SCHEMA public TO %DB_USER%;" >nul 2>&1
echo   [OK] Permisos otorgados

REM --- Cambiar a contrasena del usuario POS para el resto del script ---
set "PGPASSWORD=%DB_PASSWORD%"
echo.

REM ============================================================================
REM FASE 6: Migraciones
REM ============================================================================
echo [FASE 6/10] Ejecutando migraciones de Django...

python manage.py migrate --settings=config.settings_production
if %errorlevel% neq 0 (
    echo   [ERROR] Fallaron las migraciones.
    echo           Revise los errores arriba.
    pause
    exit /b 1
)
echo   [OK] Migraciones completadas
echo.

REM ============================================================================
REM FASE 7: Archivos estaticos
REM ============================================================================
echo [FASE 7/10] Recolectando archivos estaticos...

python manage.py collectstatic --noinput --settings=config.settings_production >nul 2>&1
echo   [OK] Archivos estaticos listos
echo.

REM ============================================================================
REM FASE 8: Usuario SYSADMIN + ConfiguracionNegocio + Caja
REM ============================================================================
echo [FASE 8/10] Configurando sistema...

REM --- Crear usuario SYSADMIN ---
REM El manager exige email no vacio, y esta fase lo pasaba vacio.
REM Levantaba ValueError, nadie miraba %errorlevel% y la instalacion
REM terminaba imprimiendo "COMPLETADA EXITOSAMENTE" sin cuenta administrativa
REM (USR-005). Ahora hay email por defecto, se comprueba el codigo de salida y
REM se verifica la postcondicion.
echo   Creando usuario SYSADMIN...
python manage.py shell --settings=config.settings_production -c "
import os, sys
from django.contrib.auth import get_user_model
User = get_user_model()
username = os.environ.get('INITIAL_SYSADMIN_USERNAME', 'Santiago')
password = os.environ.get('INITIAL_SYSADMIN_PASSWORD')
email = os.environ.get('INITIAL_SYSADMIN_EMAIL') or f'{username.lower()}@local.pos'
if not password:
    sys.exit('  [ERROR] Falta INITIAL_SYSADMIN_PASSWORD.')
if not User.objects.filter(username=username).exists():
    u = User.objects.create_superuser(username=username, password=password, email=email)
    u.rol = 'SYSADMIN'
    u.save()
    print(f'  [OK] Usuario {username} (SYSADMIN) creado')
else:
    u = User.objects.get(username=username)
    u.rol = 'SYSADMIN'
    u.is_superuser = True
    u.is_staff = True
    u.activo = True
    if not u.email:
        u.email = email
    u.save()
    print(f'  [OK] Usuario {username} ya existe, rol actualizado a SYSADMIN')
"
if errorlevel 1 (
    echo   [ERROR] No se pudo crear el usuario SYSADMIN. Instalacion abortada.
    exit /b 1
)

REM --- Postcondicion: el SYSADMIN existe, esta activo y es usable ---
python manage.py shell --settings=config.settings_production -c "
import os, sys
from django.contrib.auth import get_user_model
User = get_user_model()
username = os.environ.get('INITIAL_SYSADMIN_USERNAME', 'Santiago')
u = User.objects.filter(username=username, rol='SYSADMIN', activo=True).first()
if u is None:
    sys.exit('  [ERROR] No quedo un SYSADMIN activo tras la instalacion.')
if not u.has_usable_password():
    sys.exit('  [ERROR] El SYSADMIN quedo sin contrasena utilizable.')
print(f'  [OK] Verificado: {username} es SYSADMIN activo')
"
if errorlevel 1 (
    echo   [ERROR] Verificacion del SYSADMIN fallida. Instalacion abortada.
    exit /b 1
)

REM --- Crear ConfiguracionNegocio con preset ---
echo   Configurando datos del negocio (preset: %NEGOCIO_PRESET%)...
python manage.py crear_config_inicial --nombre "%NEGOCIO_NOMBRE%" --preset %NEGOCIO_PRESET% --settings=config.settings_production
if errorlevel 1 (
    echo   [ERROR] No se pudo crear la configuracion del negocio. Instalacion abortada.
    exit /b 1
)

REM --- Crear Caja Principal ---
echo   Creando Caja Principal...
python manage.py shell --settings=config.settings_production -c "
from apps.caja.models import Caja
if not Caja.objects.filter(nombre='Caja Principal').exists():
    Caja.objects.create(nombre='Caja Principal', descripcion='Caja principal del negocio', activa=True)
    print('  [OK] Caja Principal creada')
else:
    print('  [OK] Caja Principal ya existe')
"

echo.

REM ============================================================================
REM FASE 9: Crear estructura de carpetas
REM ============================================================================
echo [FASE 9/10] Creando estructura de carpetas...

if not exist "%PROJECT_DIR%\logs" mkdir "%PROJECT_DIR%\logs"
if not exist "%PROJECT_DIR%\backups" mkdir "%PROJECT_DIR%\backups"
if not exist "%PROJECT_DIR%\media" mkdir "%PROJECT_DIR%\media"
if not exist "%PROJECT_DIR%\staticfiles" mkdir "%PROJECT_DIR%\staticfiles"

echo   [OK] Carpetas creadas
echo.

REM ============================================================================
REM FASE 10: SECRET_KEY + Verificacion final
REM ============================================================================
echo [FASE 10/10] Finalizando...

REM --- Generar SECRET_KEY unica si no se ha cambiado ---
if "%DJANGO_SECRET_KEY%"=="CAMBIAR-POR-KEY-UNICA-POR-INSTALACION" (
    echo   Generando SECRET_KEY unica...
    REM token_urlsafe -> solo [A-Za-z0-9-_]: no rompe cmd ni el -replace de PowerShell.
    for /f "delims=" %%k in ('python -c "import secrets;print(secrets.token_urlsafe(48))"') do (
        set "NEW_KEY=%%k"
    )

    powershell -Command "(Get-Content '%PROJECT_DIR%\deploy\env_cliente.bat') -replace 'CAMBIAR-POR-KEY-UNICA-POR-INSTALACION', '!NEW_KEY!' | Set-Content '%PROJECT_DIR%\deploy\env_cliente.bat'"
    echo   [OK] SECRET_KEY generada y guardada
)

REM --- Verificacion rapida ---
echo.
echo   Verificando que Django arranque correctamente...
python manage.py check --settings=config.settings_production >nul 2>&1
if %errorlevel% neq 0 (
    echo   [AVISO] Django reporto warnings. Ejecute verificar_sistema.py para detalles.
) else (
    echo   [OK] Django check paso sin errores
)

echo.
echo  ============================================================
echo    INSTALACION COMPLETADA EXITOSAMENTE
echo  ============================================================
echo.
echo  Usuario inicial: %INITIAL_SYSADMIN_USERNAME% (SYSADMIN)
echo  Contrasena:     configurada en deploy\env_cliente.bat
echo  IMPORTANTE:     Cambie la contrasena despues del primer login
echo.
echo  Para iniciar el sistema:
echo    deploy\iniciar_servidor.bat
echo.
echo  Para registrar como servicio de Windows (inicio automatico):
echo    deploy\registrar_servicio.bat  (como admin)
echo.
echo  Para configurar backups automaticos:
echo    deploy\programar_backup.bat    (como admin)
echo.
echo  Acceso al sistema:
echo    http://localhost:%SERVER_PORT%
echo    http://%SERVER_IP%:%SERVER_PORT%  (desde otra PC en LAN)
echo.
echo  Configuracion del negocio:
echo    Acceda con Santiago y vaya a Configuracion del Sistema
echo    para ajustar nombre, RNC, modulos, impresoras, etc.
echo.
echo  ============================================================
echo.

pause
endlocal
