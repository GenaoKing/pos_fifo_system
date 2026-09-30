# Producción migrada — 2026-09-30

API y portal están publicados y accesibles. Este documento es evidencia
posterior al despliegue; su commit documental no sustituye el SHA del artefacto.
El responsable autorizó el pase completo, incluidos los cambios locales probados,
e informó que los POS físicos estaban apagados. Su instalación no se modificó.

## Versiones desplegadas

| Componente | Identidad |
| --- | --- |
| Backend, main y staging | `3d5b2fecabe0e986d27af1c5af09eab4d840b6eb` |
| Imagen API y jobs | `sha256:f9d8edf6b48bcdade36ba157ae5a246e1a8817555afda281f32b27c0ca27a85c` |
| Portal, main y staging | `9c8d4915eb29797782e251e9eb98d4960efc2d3b` |

Backend: [dispatch productivo 36673262736](https://github.com/GenaoKing/pos_fifo_system/actions/runs/36673262736),
SUCCESS. Se promovió el digest ya validado en staging, sin reconstruir en prod.
Portal: [CI 36711625834](https://github.com/GenaoKing/pos-cloud-dashboard/actions/runs/36711625834)
y [deploy 36711625898](https://github.com/GenaoKing/pos-cloud-dashboard/actions/runs/36711625898), SUCCESS.

API: `https://posfifo-prod-api.greenglacier-6158bae1.canadacentral.azurecontainerapps.io`.
Portal: `https://red-bay-07331a710.7.azurestaticapps.net`.
El bundle `index-DpwajSXF.js` identifica `9c8d491` y la API productiva, sin
URLs dev/staging; inicio y rutas SPA respondieron 200.

## Ventana real y disponibilidad

Los horarios siguientes son **reales**, no la ventana inicialmente prevista:

- Dev quedó congelado a las **01:06:59**, hora de Santo Domingo.
- Prod quedó congelado a las **01:08:26**; a las 01:08:34 se verificaron cero
  sesiones sobre las bases afectadas antes del respaldo definitivo.
- El job de migraciones comenzó a las **01:38:44**. Su estado `Succeeded`
  quedó observado a las **01:40:23** y el health interno pasó a las **01:41:06**.
- El workflow conservó ingress cerrado, conforme a su diseño. La reapertura
  manual se completó a las **07:48:51** para prod y **07:50:10** para dev.

Por tanto, producción permaneció en mantenimiento aproximadamente **6 h 40 min**;
no debe describirse como disponible desde que terminó el workflow. Se conserva
la cronología completa en los artefactos privados y en GitHub Actions.
Dev conservó exactamente su imagen previa; se cerró temporalmente porque el
tenant demo comparte `tnt_demo` con producción. Staging usa bases separadas.

## Bases y preservación de datos

Se tomaron y verificaron cinco dumps frozen, **3.375.702 bytes** en total:
control plane y los cuatro tenants activos. Se comprobaron SHA-256, índices de
restore, inventario, nombres de destino y ausencia de escritores. No se
restauraron backups sobre producción.

- Materializadas las tablas históricas faltantes de sucursales y auditoría del
  control plane con los comandos explícitos y sus ledgers. Se preservó el
  historial de migraciones.
- `posfifo-prod-migrate-i4fs1ig` terminó SUCCESS y su log confirmó **4/4 tenants
  OK**. Cada una de las cinco bases quedó con **155 migraciones registradas**;
  las bases tenant también pasaron el control de tablas físicas.
- Conteos de registros financieros y sumas de ventas, pagos y CxC: **sin cambios
  por la migración**, comparados antes de reabrir tráfico.
- Se consolidó un placeholder CONTADO duplicado de Royal Plast siguiendo la
  regla existente. No se eliminaron ventas, cuentas, pagos ni hechos financieros.
- Catálogos: cuatro permisos faltantes agregados en cada tenant RP, RP demo y
  SK; demo ya los tenía. Planes, overrides, roles y asignaciones se compararon
  antes/después del sembrado y se conservaron.

La recuperación se ensayó en otro PostgreSQL 16 aislado: cinco bases preservadas
por renombrado y cinco restauradas, con conteos, migraciones, importes y owners
verificados. El helper bloqueó restauraciones tras reapertura antes de acceder
a las bases. En producción no se ejecutó ese restore.

## Comprobaciones operativas

- Staging y main: CI aprobada; **1.693 Django, 72 e-CF y cinco casos del gate
  físico entre tenants** en la corrida staging del SHA final.
- Ambos health de prod: HTTP 200, BD `ok` y SHA exacto.
- **16/16 GET autenticados**: perfil, productos, ingresos y configuración push
  en cuatro tenants. Se usaron JWT efímeros para memberships reales verificadas,
  sin refresh ni impersonación; no es una prueba de contraseña ni de navegador.
- Un 401 inicial del probe fue reproducido como desfase de un segundo en `iat`
  del equipo emisor. El mismo JWT respondió 200 tras 1,2 segundos. Se corrigió
  únicamente el probe para usar la hora de health; no se alteró autenticación,
  credenciales ni permisos del servidor.
- CORS del portal y autorización del preflight OPTIONS: PASS.
- Terraform canónico, sin overlay: **exit 0, sin cambios**. API Single, mínimo
  efectivo 0 restaurado; no se mantiene la réplica temporal exigida por el gate.
- Notificaciones: infraestructura y VAPID preparados, job Manual, Push apagado,
  motores apagados y **cero ejecuciones** del job productivo.
- **Observación de 60 minutos: en curso**, iniciada alrededor de las 08:00.
  El resultado se incorpora al finalizar; no se declara cumplida por anticipado.

## Paquete Windows y pendientes explícitos

Paquete del mismo SHA backend, con todas las mejoras locales aprobadas:
`pos_fifo_system_C06.1_3d5b2fecabe0.zip`, **37.326.921 bytes**.
SHA-256: `d57fda8e1c260b9dc1baef06538c5ccd332af76ed28f8525457863ecf43edb94`.

Se verificaron CRC, hashes, origen por git archive, lock y 36 wheels. Además se
instaló realmente offline en un venv descartable: pip check, imports, compilación
y checks estáticos aprobados, sin conexiones a BD ni cambios de servicios.
Se conserva el manifiesto original del constructor y su acta de verificación
separada. No se afirma instalación o aceptación física en RP/SK.

El usuario decidió **conservar los metadatos actuales y registrar la conciliación
pendiente**: nombres del demo compartido, diferencias de RNC entre configuración
y control, y plan vacío en control frente a Empresarial operativo. No se
cambiaron los datos fiscales ni las suscripciones para ocultar ese diagnóstico.

Quedan para el operador: instalar el paquete en los clientes con sus backups y
verificar sus ciclos reales al encenderlos. El smoke HTTP no simula esas pruebas
físicas ni cambia `SyncToken.ultimo_uso` para aparentar sucursales conectadas.

## Evidencia y custodia

Directorio privado: `C:/Proyectos/_release_prod/20260930/`.
Contiene inventario, backups frozen y hashes, recovery plan ensayado, logs de
reparación, evidencias GitHub/Log Analytics, comparación de integridad,
pruebas HTTP/portal, plan Terraform sin cambios y paquete Windows.
El export local de la clave VAPID privada se retiró después de verificar Key
Vault; el material sensible no se incluyó en Git ni en el ZIP.

Se retiraron los tres worktrees restantes de esta ejecución una vez comprobada
su integración en main, archivando 55 archivos ignorados con SHA-256 verificado.
Quedan únicamente los checkouts principales backend/frontend. Se eliminaron
cinco bases locales descartables y los contenedores de ensayo, conservando
backups y evidencias; no se eliminaron bases ni recursos productivos.

Preparación y causas de las correcciones:
[RELEASE_PROD_2026-09-30.md](RELEASE_PROD_2026-09-30.md).
