# Incidente Royal Plast: pull RBAC y acceso portal de David

Fecha: 2026-09-30. Diagnóstico de producción mediante consultas de solo lectura.

## Evidencia

- El POS reportó `tenant_key no coincide con el negocio local` en `roles` y
  `asignaciones`; ambos cursores permanecieron bloqueados. No hay evidencia de
  que esas asignaciones hayan llegado al POS después del cambio de rol.
- En el control plane de producción, `Tenant.tenant_key=royalplast` y
  `Tenant.slug=royal-plast`; en la base de ese tenant,
  `Negocio.slug=royal-plast`. El endpoint RBAC v2 entrega la clave técnica
  `royalplast`, mientras el POS standalone de la versión instalada esperaba
  `Negocio.slug`.
- En producción existe un usuario operativo `David` activo en Royal Plast,
  con email, pero no existe `Identity` con ese email ni `Membership` para
  `royalplast/David`. El login cloud exige ambas y una contraseña propia de la
  Identity. No se consultaron contraseñas ni se intentó iniciar sesión por él.

## Corrección preparada para el POS

El código permite fijar `CLOUD_TENANT_KEY` en el POS standalone, manteniendo el
fallback anterior para otras instalaciones. El contexto técnico de cloud sigue
teniendo prioridad. La configuración de Royal Plast es
`CLOUD_TENANT_KEY=royalplast` en `deploy/env_cliente.env`.

Antes de aplicar el parche físico, comprobar que la instalación parte del paquete
`3d5b2fecabe0`, respaldar los dos archivos y el `.env`, y verificar que el token
cloud corresponde a la sucursal Royal Plast. Copiar solamente
`apps/sync/engine.py` y `config/settings.py` del parche, agregar la variable
anterior al `.env` y reiniciar `POSFifoSync`. Ejecutar un ciclo de sync y
confirmar que los dos pulls terminan sin bloqueo, que los cursores avanzan y que
el rol/asignación esperados aparecen en el POS. No editar el slug comercial ni
forzar cursores. La aplicación física y su verificación no se han ejecutado
desde este diagnóstico.

Secuencia para el operador en una instalación estándar `C:\pos_fifo_system`,
desde PowerShell elevado y con el ZIP extraído en `C:\pos_actualizacion\RP_RBAC_hotfix_20260930`:

```powershell
New-Item -ItemType Directory -Path C:\pos_fifo_system\backups\rbac_hotfix_20260930 | Out-Null
Copy-Item C:\pos_fifo_system\apps\sync\engine.py C:\pos_fifo_system\backups\rbac_hotfix_20260930\engine.py
Copy-Item C:\pos_fifo_system\config\settings.py C:\pos_fifo_system\backups\rbac_hotfix_20260930\settings.py
Copy-Item C:\pos_fifo_system\deploy\env_cliente.env C:\pos_fifo_system\backups\rbac_hotfix_20260930\env_cliente.env
Stop-Service POSFifoSync
Copy-Item C:\pos_actualizacion\RP_RBAC_hotfix_20260930\apps\sync\engine.py C:\pos_fifo_system\apps\sync\engine.py
Copy-Item C:\pos_actualizacion\RP_RBAC_hotfix_20260930\config\settings.py C:\pos_fifo_system\config\settings.py
```

Editar `C:\pos_fifo_system\deploy\env_cliente.env` para dejar exactamente una
línea `CLOUD_TENANT_KEY=royalplast`. Después:

```powershell
Set-Location C:\pos_fifo_system
.\venv\Scripts\python.exe manage.py sincronizar --once --settings=config.settings_production
.\venv\Scripts\python.exe manage.py verificar_sync --settings=config.settings_production
Start-Service POSFifoSync
```

Anotar estado de `roles` y `asignaciones`, y comprobar en el POS la asignación
esperada. Si falla, conservar los logs y restaurar los dos archivos y el `.env`
del respaldo antes de reiniciar el servicio. Los roles no se reparan editando
directamente `VersionMaestro`.

## David

La corrección de RBAC no crea una identidad portal. El usuario autorizó crear
su acceso y entregar la contraseña nueva en el chat. Se repitió el preflight
en producción: David activo, negocio `royal-plast`, email presente, sin
`Identity` con ese email y sin `Membership` para `royalplast/David`. Su rol
`administrador` estaba asignado y activo en cloud. En una transacción del
control plane se creó una `Identity` activa no global con contraseña aleatoria
independiente y una `Membership` activa para `royalplast/David` con rol legacy
`CAJERA`, igual que el alta portal normal. Se verificó el hash de la nueva
contraseña, la membresía y `TenantPortalLoginSerializer.is_valid() == True`.
La contraseña no se guardó en el repositorio. No se ejecutó un login HTTP desde
el navegador de David ni se verificó visualmente su acceso al portal. El usuario
operativo, su contraseña del POS y sus asignaciones RBAC no se modificaron.
