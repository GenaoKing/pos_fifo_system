@echo off
setlocal EnableExtensions DisableDelayedExpansion

REM Este archivo antes contenia credenciales de prueba versionadas.
REM Esta deshabilitado a proposito: no uses tokens ni contrasenas en archivos
REM rastreados por Git. Para un smoke autorizado, usa un entorno local no
REM versionado y credenciales efimeras provistas por el operador.
echo [BLOQUEADO] prueba.bat no ejecuta solicitudes autenticadas.
echo Use el runbook y un entorno de laboratorio autorizado.
exit /b 1
