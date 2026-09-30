# A09: PC de QA contra staging — 2026-09-25

**Acceso Django Admin local — 2026-09-26:** a pedido del responsable,
`qa_santiago` fue promovido **solo en la BD local** `pos_stage_qa` de `ADMIN`
a `SYSADMIN`, `is_staff=True`, `is_superuser=True`. Se usó el guardado auditado
de `UsuarioAdmin` y quedó un evento `usuarios.usuario.actualizado`; no se
alteró la Identity del portal ni la BD cloud. Antes se hizo un dump custom
validado con `pg_restore --list` en
`C:\Proyectos\pos_fifo_staging_qa\before_sysadmin_20260926.dump`, SHA-256
`CE17BFCAC6BF51894BFAA7580483AABAE16023E8C7B48473749DE4E384A039D7`,
con ACL restringida. El login HTTP real de QA devolvió 302 y `/admin/` 200.
El único `staff` activo de la instalación es `qa_santiago`.

El gate en `apps/usuarios/admin_site.py` exige ahora rol `SYSADMIN` además de
cuenta activa e `is_staff`. El archivo corregido se copió al POS QA y pasó
`manage.py check`, pero la sesión no tenía privilegio Windows para reiniciar
`POSFifoStagingQA` (corre como LocalSystem). **Para que el proceso web cargue
el gate nuevo**, ejecutar en PowerShell como Administrador
`Restart-Service POSFifoStagingQA`; no hace falta reiniciar el sync. Hasta ese
reinicio el proceso conserva el gate anterior en memoria, aunque hoy no hay
otro usuario `staff` activo. Esta instalación mantiene la base de código
`5f3e89b` con este archivo corregido puntualmente; no equiparar el conjunto
con un paquete íntegro posterior.

**Actualización 2026-09-26:** el 403/500 y el token 401 descritos abajo
corresponden al estado **anterior** a la promoción. PR #31/#32 pasaron a
`staging@8213ba5` por PR #33; la asignación QA y las pantallas del portal se
verificaron. El estado posterior, los hallazgos abiertos y las instrucciones de
recurrencia están en [incidentes del despliegue](A09-incidentes-deployment-2026-09-26.md).

Instalación aislada en `C:\Proyectos\pos_fifo_staging_qa\pos_fifo_system`,
extraída del paquete Windows construido desde `5f3e89b1fcb2`. Base PostgreSQL
local nueva `pos_stage_qa`; no se usó una base de Royal Plast, SK Performance ni
producción. Los servicios Windows `POSFifoStagingQA` y
`POSFifoStagingQASync` están registrados con arranque automático y leen solo
`DJANGO_SETTINGS_MODULE` y `POS_ENV_FILE` del NSSM. El servidor escucha en
`127.0.0.1:8088` y `/api/v1/health/` respondió `status=ok, db=ok`.

En el tenant cloud `staging_demo` se creó la sucursal `QA-PC-01`, su cuenta
humana `qa_santiago` con asignación Administrador limitada a esa sucursal,
su usuario de servicio y un token exclusivo. El comando
`vincular_sucursal_token` anterior dejó el hash sin registrar en el control
plane: el primer pull respondió 401; el registro manual del hash permitió la
descarga de 273 productos, un cliente, roles y métodos de crédito. El fix
versionado está en el PR #32. Se creó configuración cloud propia para QA y se
apagó e-CF **solo** en `QA-PC-01` mediante `SucursalModuloOverride`, para que
la configuración efectiva no exija un emisor fiscal local. El pull de
configuración avanzó sin bloqueo.

Dos asignaciones globales heredadas para `admin` y
`sucursal_service_STG-01` quedaron diferidas porque esos usuarios no existen
en una PC nueva. Se dieron de alta únicamente en esta base QA como cuentas
inactivas y con contraseña inutilizable; la cola de diferidos quedó en cero y
el último ciclo fue `EXITOSO`. `verificar_sync` informó `sin perdida detectada`
con la cola de eventos vacía. Aún falta una venta y un abono hechos por el
usuario para acreditar el push funcional. El endpoint de prueba térmica,
invocado por HTTP a través del servicio Windows, devolvió éxito con
`POS-80C`; falta confirmación visual del papel.

El login local de `qa_santiago` abrió reportes, productos y caja. La misma
Identity inicia sesión en el portal staging, pero la asignación acotada a
`QA-PC-01` recibe 403 en las vistas que exigen permisos globales. Al intentar
dar el rol global por la API se reprodujo HTTP 500: CT-01 recibía el slug
comercial en vez del `tenant_key` técnico. El PR #31 lo corrige y ya está en
`develop@9aad9f4`; falta promoverlo a staging y repetir la asignación para
completar el acceso portal de QA. No se aplicó ninguna asignación después del
500. Un intento de ejecutar el servicio corregido desde la PC falló por
timeout al puerto PostgreSQL de Azure antes de acceder a la base; la API de
staging respondió 200 y se usará para el alta cuando el fix esté desplegado.

El respaldo base local previo al QA manual está en
`C:\Proyectos\pos_fifo_staging_qa\baseline_before_manual_qa.dump`, SHA-256
`30F6F7B8B26703FBFDD6FCF8664AE1534F4568EFEF49D444F75D272583721F3C`;
`pg_restore --list` pasó y el ACL limita acceso a Santiago, SYSTEM y
Administrators. La guía de uso local sin credenciales está en
`C:\Proyectos\pos_fifo_staging_qa\QA_STAGING.md`.

El monitor remoto de 24 horas sigue abierto; a las 15:28 UTC había 47 muestras
con cinco fallos de conexión/timeout observados desde esta PC. No se atribuyen
todavía al servicio cloud ni se declara cerrado G2. Producción y tiendas
cliente permanecen intactas.
