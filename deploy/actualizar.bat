@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
title POS FIFO System - Actualizador (update in-place)

REM ============================================================================
REM POS FIFO System - Actualizacion de una instalacion EXISTENTE v1
REM
REM A diferencia de instalar.bat (install fresco), este script ACTUALIZA un POS
REM que ya esta corriendo, preservando su base de datos, su env_cliente.bat,
REM media/, logs/, backups/ y su venv.
REM
REM COMO SE USA (importante):
REM   1. Este script vive DENTRO del PAQUETE NUEVO (la carpeta del dist).
REM   2. Copie el paquete nuevo a una carpeta STAGING (ej: C:\pos_update\)
REM      DISTINTA del install vivo (para no auto-sobrescribirse).
REM   3. Ejecute como Administrador:  C:\pos_update\deploy\actualizar.bat
REM   4. El script preguntara la ruta del install vivo (default C:\pos_fifo_system).
REM
REM Pasos: detener servicios -> backup BD -> copiar codigo nuevo -> pip ->
REM        migrate -> collectstatic -> seeds idempotentes -> reiniciar servicios.
REM
REM PREREQUISITO: haber ensayado migrate sobre una COPIA de la BD real antes.
REM ============================================================================

echo.
echo  ============================================================
echo    POS FIFO System - Actualizador de instalacion existente
echo  ============================================================
echo.

REM --- Verificar administrador ---
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Ejecute como Administrador: clic derecho, Ejecutar como administrador.
    pause
    exit /b 1
)

REM --- SRC = paquete nuevo (staging, donde vive este .bat) ---
set "SRC_DIR=%~dp0.."
for %%I in ("%SRC_DIR%") do set "SRC_DIR=%%~fI"

REM --- DST = install vivo (argumento o pregunta) ---
set "DST_DIR=%~1"
if "%DST_DIR%"=="" (
    set "DEFAULT_DST=C:\pos_fifo_system"
    REM `%DEFAULT_DST%` se expande en tiempo de PARSEO del bloque, antes de
    REM que el `set` de arriba corra -- el prompt mostraba "[]" vacio en vez
    REM del default (cosmetico: el default se seguia aplicando bien abajo).
    set /p "DST_DIR=  Ruta del POS instalado [!DEFAULT_DST!]: "
    if "!DST_DIR!"=="" set "DST_DIR=!DEFAULT_DST!"
)
for %%I in ("%DST_DIR%") do set "DST_DIR=%%~fI"

REM --- Validaciones de rutas ---
if /i "%SRC_DIR%"=="%DST_DIR%" (
    echo [ERROR] El paquete nuevo STAGING no puede ser la misma carpeta que el
    echo         install vivo. Copielo a una carpeta aparte, ej: C:\pos_update
    pause
    exit /b 1
)
if not exist "%DST_DIR%\manage.py" (
    echo [ERROR] No parece un install valido: no existe "%DST_DIR%\manage.py"
    pause
    exit /b 1
)
if not exist "%DST_DIR%\deploy\env_cliente.bat" (
    echo [ERROR] Falta "%DST_DIR%\deploy\env_cliente.bat" - config del cliente.
    echo         Si es una instalacion nueva use deploy\instalar.bat, no este script.
    pause
    exit /b 1
)
if not exist "%DST_DIR%\venv\Scripts\activate.bat" (
    echo [ERROR] Falta el entorno virtual en "%DST_DIR%\venv".
    pause
    exit /b 1
)

REM --- Cargar configuracion del cliente (DB, secret, sucursal, sync) ---
set PGCLIENTENCODING=UTF8
set PYTHONUTF8=1

REM Cargar los valores para el resto de este script, del formato que ya
REM exista (el .env si una corrida anterior ya convirtio, si no el .bat de
REM siempre). La CONVERSION real de .bat a .env pasa mas abajo, DESPUES de la
REM FASE 4 (dependencias): el comando que la hace (`migrar_env_cliente`) vive
REM en el codigo NUEVO -- que todavia no esta copiado en este punto, y ademas
REM necesita `python-dotenv` instalado para poder importar `config.settings`.
REM Intentarlo aqui siempre fallaba con "Unknown command: 'migrar_env_cliente'"
REM en la primera actualizacion de cualquier cliente en formato .bat --
REM exactamente el caso de Royal Plast (reproducido 2026-08-24): la
REM conversion prometida por el runbook nunca ocurria en la primera pasada.
if exist "%DST_DIR%\deploy\env_cliente.env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%DST_DIR%\deploy\env_cliente.env") do (
        if not "%%A"=="" set "%%A=%%B"
    )
) else (
    call "%DST_DIR%\deploy\env_cliente.bat"
)

echo.
echo  Se va a ACTUALIZAR:
echo    Paquete nuevo (origen):  %SRC_DIR%
echo    Install vivo (destino):  %DST_DIR%
echo    Base de datos:           %DB_NAME%  (usuario %DB_USER%)
echo    Sucursal:                %SUCURSAL_CODIGO%
echo.
echo  Se hara un BACKUP de la BD antes de migrar. Asegurese de que ya ensayo
echo  la migracion sobre una copia de la BD real.
echo.
set /p "CONFIRM=  Escriba SI para continuar: "
if /i not "%CONFIRM%"=="SI" (
    echo Cancelado por el usuario.
    pause
    exit /b 1
)
echo.

set "NSSM_PATH=%DST_DIR%\deploy\nssm.exe"
if not exist "%NSSM_PATH%" set "NSSM_PATH=%SRC_DIR%\deploy\nssm.exe"

REM ============================================================================
REM FASE 1: Detener servicios
REM ============================================================================
echo [FASE 1/8] Deteniendo servicios...
if exist "%NSSM_PATH%" (
    "%NSSM_PATH%" stop POSFifoSync >nul 2>&1
    "%NSSM_PATH%" stop POSFifoSystem >nul 2>&1
) else (
    sc stop POSFifoSync >nul 2>&1
    sc stop POSFifoSystem >nul 2>&1
)
echo   [OK] Servicios detenidos (los que existieran).
echo.

REM ============================================================================
REM FASE 2: Backup de la base de datos (punto de rollback)
REM ============================================================================
echo [FASE 2/8] Backup de la base de datos...
if not exist "%DST_DIR%\backups" mkdir "%DST_DIR%\backups"
REM Recortar %DATE%/%TIME% por posicion asume un formato que depende del
REM locale de Windows. En es-DO, %DATE% trae el dia de la semana adelante
REM ("sab. 22/08/2026"), asi que el recorte se desalinea y produce timestamps
REM con "/" adentro -- pg_dump no puede crear ese archivo, y como el script
REM aborta si el backup falla, la actualizacion nunca pasaba de esta fase
REM (reproducido en Royal Plast, 2026-08-24). PowerShell Get-Date -Format no
REM depende del locale.
for /f "usebackq tokens=*" %%T in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"`) do set "STAMP=%%T"
if not defined STAMP set "STAMP=%RANDOM%"
set "BACKUP_FILE=%DST_DIR%\backups\%DB_NAME%_PRE_UPDATE_%STAMP%.dump"
set "PGPASSWORD=%DB_PASSWORD%"
pg_dump -U %DB_USER% -h %DB_HOST% -p %DB_PORT% -F c -b -f "%BACKUP_FILE%" %DB_NAME%
if %errorlevel% neq 0 (
    echo   [ERROR] Fallo el backup. ABORTANDO antes de tocar nada.
    echo           Verifique credenciales/PostgreSQL e intente de nuevo.
    pause
    exit /b 1
)
echo   [OK] Backup creado: %BACKUP_FILE%
echo        (Rollback: pg_restore -c -U %DB_USER% -d %DB_NAME% "%BACKUP_FILE%")
echo.

REM ============================================================================
REM FASE 3: Copiar codigo nuevo (preserva env_cliente.bat, media, logs, venv)
REM ============================================================================
echo [FASE 3/8] Copiando codigo nuevo sobre el install vivo...
robocopy "%SRC_DIR%\apps"      "%DST_DIR%\apps"      /e /xd __pycache__ /xf *.pyc >nul
robocopy "%SRC_DIR%\config"    "%DST_DIR%\config"    /e /xd __pycache__ /xf *.pyc >nul
robocopy "%SRC_DIR%\templates" "%DST_DIR%\templates" /e >nul
robocopy "%SRC_DIR%\static"    "%DST_DIR%\static"    /e >nul
if exist "%SRC_DIR%\utils" robocopy "%SRC_DIR%\utils" "%DST_DIR%\utils" /e /xd __pycache__ /xf *.pyc >nul
REM deploy: copiar scripts nuevos PERO sin pisar el env_cliente.bat del cliente
robocopy "%SRC_DIR%\deploy"    "%DST_DIR%\deploy"    /e /xf env_cliente.bat >nul
for %%f in (manage.py server.py requirements.txt) do (
    if exist "%SRC_DIR%\%%f" copy /y "%SRC_DIR%\%%f" "%DST_DIR%\" >nul
)
echo   [OK] Codigo actualizado (env_cliente.bat, media, logs, backups y venv intactos).
echo.

REM --- A partir de aqui trabajamos en el install vivo ---
cd /d "%DST_DIR%"
call "%DST_DIR%\venv\Scripts\activate.bat"

REM ============================================================================
REM FASE 4: Dependencias
REM ============================================================================
echo [FASE 4/8] Actualizando dependencias de Python...
python -m pip install --upgrade pip >nul 2>&1
pip install -r "%DST_DIR%\requirements.txt"
if %errorlevel% neq 0 (
    echo   [ERROR] Fallo pip install. Revise los errores arriba.
    echo           El codigo ya fue copiado pero la BD NO se ha migrado aun.
    pause
    exit /b 1
)
echo   [OK] Dependencias actualizadas.
echo.

REM --- Convertir env_cliente.bat -> .env, ahora que el codigo nuevo esta
REM     copiado (FASE 3) y sus dependencias instaladas (esta fase). Las
REM     instalaciones anteriores a la Fase 4 tienen su configuracion real en
REM     env_cliente.bat; se convierte automaticamente, nadie tiene que
REM     reescribirla a mano.
if not exist "%DST_DIR%\deploy\env_cliente.env" (
    if exist "%DST_DIR%\deploy\env_cliente.bat" (
        echo   Convirtiendo env_cliente.bat al nuevo formato .env...
        python manage.py migrar_env_cliente --settings=config.settings_production
        if !errorlevel! neq 0 (
            REM No se detiene la actualizacion: las variables de entorno que
            REM este script ya cargo del .bat siguen activas para el resto de
            REM esta corrida. Pero el .env que va a leer el SERVICIO (una vez
            REM registrado) puede haber quedado incompleto -- el comando ya
            REM lo imprimio arriba con el nombre exacto de la variable.
            echo.
            echo   [AVISO CRITICO] migrar_env_cliente termino con error -- ver el
            echo                   detalle arriba de esta linea: seguramente falta
            echo                   agregar a mano una variable critica al .env antes
            echo                   de registrar los servicios.
            echo.
        )
        if not exist "%DST_DIR%\deploy\env_cliente.env" (
            echo   [AVISO] La conversion no genero env_cliente.env todavia.
            echo           La instalacion sigue funcionando por las variables de
            echo           entorno que ya cargo este script del .bat.
        ) else (
            echo   [OK] env_cliente.env generado.
        )
    )
)
echo.

REM ============================================================================
REM FASE 5: Migraciones
REM ============================================================================
echo [FASE 5/8] Aplicando migraciones...
python manage.py migrate --settings=config.settings_production
if %errorlevel% neq 0 (
    echo   [ERROR] Fallaron las migraciones.
    echo           Para revertir: detenga, restaure el backup con pg_restore y
    echo           vuelva a poner el codigo anterior.
    pause
    exit /b 1
)
echo   [OK] Migraciones aplicadas.
echo.

REM ============================================================================
REM FASE 6: Archivos estaticos
REM ============================================================================
echo [FASE 6/8] Recolectando archivos estaticos...
python manage.py collectstatic --noinput --settings=config.settings_production >nul 2>&1
echo   [OK] Estaticos listos.
echo.

REM ============================================================================
REM FASE 7: Seeds idempotentes (RBAC, modulos, sucursal local)
REM ============================================================================
echo [FASE 7/8] Inicializando RBAC / modulos / sucursal (idempotente)...
if "%NEGOCIO_NOMBRE%"=="" set "NEGOCIO_NOMBRE=Royal Plast"

echo   - crear_sucursal (%SUCURSAL_CODIGO%)
python manage.py crear_sucursal --codigo "%SUCURSAL_CODIGO%" --nombre "%NEGOCIO_NOMBRE%" --settings=config.settings_production
echo   - bootstrap_negocio
python manage.py bootstrap_negocio --nombre "%NEGOCIO_NOMBRE%" --settings=config.settings_production
echo   - sync_permisos
python manage.py sync_permisos --settings=config.settings_production
echo   - bootstrap_suscripciones
python manage.py bootstrap_suscripciones --settings=config.settings_production
echo   - sync_modulos
python manage.py sync_modulos --settings=config.settings_production

echo   Verificando el sistema...
python manage.py check --settings=config.settings_production

REM --- Diagnostico de la instalacion ---
REM Atrapa aqui lo que antes se descubria semanas despues: variables truncadas,
REM migraciones pendientes y modulos vendibles apagados por falta de suscripcion
REM (esto ultimo puede dejar el POS sin imprimir tickets, en silencio).
python manage.py verificar_instalacion --settings=config.settings_production
echo.

REM ============================================================================
REM FASE 8: Reiniciar servicios
REM ============================================================================
echo [FASE 8/8] Reiniciando servicios...
if exist "%NSSM_PATH%" (
    "%NSSM_PATH%" start POSFifoSystem >nul 2>&1
    if !errorlevel! neq 0 (
        echo   [AVISO] No se pudo iniciar POSFifoSystem por NSSM.
        echo           Si aun no existe el servicio, ejecute deploy\registrar_servicio.bat
        REM La causa real casi nunca es "el servicio no existe" -- ese mensaje
        REM generico mandaba a re-registrar cuando el problema real estaba en
        REM el stdout del proceso (tipico: env_check.py aborta el arranque
        REM por una variable critica invalida). Reproducido en Royal Plast
        REM (2026-08-24): el servicio SI existia, pero server.py se negaba a
        REM levantar por un DJANGO_SECRET_KEY corto, y este mensaje generico
        REM no lo delataba.
        if exist "%DST_DIR%\logs\service_stdout.log" (
            echo.
            echo   Ultimas lineas de logs\service_stdout.log:
            powershell -NoProfile -Command "Get-Content -Tail 15 -Path '%DST_DIR%\logs\service_stdout.log' | ForEach-Object { '    ' + $_ }"
        )
        if exist "%DST_DIR%\logs\service_stderr.log" (
            echo.
            echo   Ultimas lineas de logs\service_stderr.log:
            powershell -NoProfile -Command "Get-Content -Tail 15 -Path '%DST_DIR%\logs\service_stderr.log' | ForEach-Object { '    ' + $_ }"
        )
        echo.
    ) else (
        echo   [OK] Servicio web POSFifoSystem iniciado.
    )
) else (
    sc start POSFifoSystem >nul 2>&1
)

REM --- Sync: solo si ya esta configurado (token + SYNC_ENABLED=true) ---
if /i "%SYNC_ENABLED%"=="true" (
    if not "%CLOUD_API_TOKEN%"=="PEGAR-TOKEN-DE-vincular_sucursal_token" (
        echo   - Sync configurado: registrando/levantando POSFifoSync...
        call "%DST_DIR%\deploy\registrar_sync_servicio.bat"
    )
)
echo.

echo  ============================================================
echo    ACTUALIZACION COMPLETADA
echo  ============================================================
echo.
echo  Backup pre-update: %BACKUP_FILE%
echo.
echo  Verifique el POS en: http://localhost:%SERVER_PORT%
echo.
if /i not "%SYNC_ENABLED%"=="true" (
    echo  SYNC AUN NO ACTIVADO. Para encenderlo:
    echo    1. En el cloud: python manage.py vincular_sucursal_token --sucursal %SUCURSAL_CODIGO%
    echo    2. Edite deploy\env_cliente.bat: SYNC_ENABLED=true, CLOUD_API_URL, CLOUD_API_TOKEN
    echo    3. Ejecute como admin: deploy\registrar_sync_servicio.bat
    echo    Ver: deploy\ACTUALIZACION_ROYAL_PLAST.md
)
echo.
pause
endlocal
