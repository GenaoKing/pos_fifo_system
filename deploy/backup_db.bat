@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion

REM ============================================================================
REM Royal Plastic POS - Backup Automatico de Base de Datos
REM ============================================================================

set "PROJECT_DIR=%~dp0.."
cd /d "%PROJECT_DIR%"

REM --- Cargar configuracion ---
call "%PROJECT_DIR%\deploy\env_cliente.bat"

REM --- Crear carpeta de backups ---
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"

REM --- Generar nombre con fecha y hora ---
for /f "tokens=1-3 delims=/" %%a in ('date /t') do set "FECHA=%%c%%b%%a"
for /f "tokens=1-2 delims=: " %%a in ('time /t') do set "HORA=%%a%%b"
set "FECHA=%date:~6,4%%date:~3,2%%date:~0,2%"
set "HORA=%time:~0,2%%time:~3,2%"
set "HORA=%HORA: =0%"

set "BACKUP_FILE=%BACKUP_DIR%\%DB_NAME%_%FECHA%_%HORA%.sql"

REM --- Ejecutar backup ---
echo [%date% %time%] Iniciando backup de %DB_NAME%...

set "PGPASSWORD=%DB_PASSWORD%"
pg_dump -U %DB_USER% -h %DB_HOST% -p %DB_PORT% -F c -b -v -f "%BACKUP_FILE%" %DB_NAME% 2>"%BACKUP_DIR%\backup_log.txt"

if %errorlevel% equ 0 (
    echo [OK] Backup creado: %BACKUP_FILE%

    REM --- Comprimir con PowerShell ---
    powershell -Command "Compress-Archive -Path '%BACKUP_FILE%' -DestinationPath '%BACKUP_FILE%.zip' -Force" 2>nul
    if exist "%BACKUP_FILE%.zip" (
        del "%BACKUP_FILE%"
        echo [OK] Comprimido: %BACKUP_FILE%.zip
    )

    REM --- Limpiar backups antiguos (mantener ultimos 30) ---
    echo [INFO] Limpiando backups antiguos...
    for /f "skip=30 delims=" %%f in ('dir /b /o-d "%BACKUP_DIR%\*.zip" 2^>nul') do (
        del "%BACKUP_DIR%\%%f"
        echo   Eliminado: %%f
    )

    echo [OK] Backup completado exitosamente.
) else (
    echo [ERROR] Fallo el backup. Revise %BACKUP_DIR%\backup_log.txt
)

echo [%date% %time%] Proceso finalizado.
echo.

endlocal
