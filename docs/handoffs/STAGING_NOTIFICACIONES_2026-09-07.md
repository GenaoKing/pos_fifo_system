# Handoff de promocion a staging — notificaciones

Fecha de corte: **2026-09-08**

Base auditada: `origin/staging@1e20a70..origin/develop@0c6cbff`

Produccion: **fuera de alcance**

## Resultado ejecutivo

La promocion de backend no es un parche aislado: `develop` lleva **60 commits,
208 archivos, 22,649 inserciones, 1,246 eliminaciones y 13 migraciones
ejecutables** que staging todavia no tiene. El diff fue revisado con enfasis en
tenancy, RBAC, sync, caja y notificaciones.

No se encontraron operaciones `RemoveField`, `DeleteModel` ni SQL destructivo en
las migraciones pendientes. Las tres transformaciones que ameritan preflight
son compatibles con los datos actuales de staging:

- `clientes.0006`: no hay clientes reales marcados como CONTADO;
- `permisos.0009`: no hay asignaciones de rol duplicadas;
- `auditoria.0005` y `0006`: viajan juntas, por lo que ningun registro se firma
  con el instante incorrecto corregido por `0006`.

La promoción terminó correctamente. El backend staging quedó en
`bb37b2f361fbb643aaef2d12fee47c4c5865b9fc`; el pipeline aplicó las 13
migraciones al control plane y a `tnt_staging_royalplast` antes de actualizar
la API. La suite, el health check y un ciclo real del job terminaron sanos.

El control plane conserva `staging_royalplast` y se aprovisionó un tenant de
prueba realmente aislado: `staging_demo`, base `tnt_staging_demo`, sucursal
`01`. Su motor se activó con corte desde
`2026-09-08 04:12:31.348488+00:00`; no hizo backfill y arrancó sin entregas ni
proyecciones pendientes.

## Migraciones promovidas

| Aplicacion | Migracion | Efecto / verificacion |
|---|---|---|
| auditoria | `0005_auditoria_inmutable_y_actor` | Snapshot/hash; filas previas quedan sin hash. |
| auditoria | `0006_alter_auditoria_fecha_hora` | Debe desplegarse junto con `0005`. |
| clientes | `0006_cliente_contado_singleton` | Consolida genericos; aborta ante un CONTADO real. Preflight: `0`. |
| configuracion | `0010_configuracionnegocio_conteo_ciego_caja` | Campo aditivo con default seguro. |
| notificaciones | `0001_initial` | Tablas nuevas; motor nace apagado. |
| notificaciones | `0002_reglas_default` | Apertura/cierre para Administrador, idempotente. |
| notificaciones | `0003_proyeccion_reintentos` | Estado de proyeccion/reintentos con default compatible. |
| permisos | `0008_permisos_productos_portal_cajera` | Data migration idempotente sobre rol de sistema. |
| permisos | `0009_asignacion_unicidad_efectiva` | Deduplica; revocacion gana. Preflight de duplicados: `0`. |
| permisos | `0010_notificaciones_administrar` | Solo rol Administrador de sistema. |
| productos | `0011_producto_imagen_origen_url_producto_origen_sucursal_and_more` | Tres campos aditivos con defaults inocuos. |
| sync | `0010_logsync_detalle_alter_logsync_tipo` | JSON de diagnostico y nueva opcion de tipo. |
| usuarios | `0004_usuario_negocio_protect` | Borrado de negocio con usuarios pasa a fallar cerrado. |

`apps/notificaciones/migrations/__init__.py` tambien aparece en el diff, pero no
es una migracion ejecutable.

## Preflight de datos y respaldos

Consultas de solo lectura sobre staging:

| Control | Resultado |
|---|---:|
| Tenants activos | 1 |
| Clientes reales marcados CONTADO | 0 |
| Asignaciones de rol duplicadas | 0 |
| Usuarios activos sin negocio | 0 |
| Tamano de `tnt_staging_royalplast` | 14 MB |
| Migraciones pendientes en control plane | 13 |
| Migraciones pendientes en el tenant | 13 |

Respaldos logicos previos a la promocion, conservados localmente en
`backups/staging-pre-notifications-20260907/` y verificados con
`pg_restore --list`:

| Base | Archivo | Bytes | SHA-256 |
|---|---|---:|---|
| control | `pos_fifo_staging-pre-notifications.dump` | 89,448 | `0a6b5f6da388824514572f19e71535dd1cc1ec77002518a6b35b8b756556f12a` |
| tenant | `tnt_staging_royalplast-pre-notifications.dump` | 303,890 | `b9cfdafe09b0525e843847c7e78d2e075c7d12464ed28cc975e6fd47b6928123` |

La integridad estructural de ambos dumps esta verificada. No se hizo un restore
drill completo en otra instancia.

## Contratos que cambian respecto de staging

- JWT cloud revalida permisos RBAC efectivos; deja de exigir exclusivamente
  `ADMIN/SYSADMIN`.
- Logout Django solo acepta POST con CSRF.
- El sync agrega conciliacion y los eventos de caja; `CIERRE_CAJA` acepta
  `schema_version=2` y `resumen_turno`, manteniendo compatibilidad con payloads
  viejos estimados.
- Se agregan bandeja, reglas, catalogo y dispositivos Web Push bajo
  `/api/v1/notificaciones/`.
- Se agregan permisos de fotografia/productos y
  `notificaciones.administrar`; las data migrations no amplian roles custom.
- El cliente CONTADO pasa a ser singleton y `Usuario.negocio` usa `PROTECT`.

El detalle exhaustivo de contratos y migraciones sigue centralizado en
`docs/ESTADO_AUDITORIAS.md` y `docs/BUGS.md`.

## Infraestructura aplicada

El plan de staging del 2026-09-07 dio, y el apply del 2026-09-08 ejecutó,
**6 altas, 1 cambio in-place y 0 destrucciones**:

- job `posfifo-staging-notifications`;
- identidad administrada del job;
- asignaciones RBAC de ACR Pull y lectura de Key Vault;
- Action Group;
- alerta de ausencia de ejecucion exitosa en cinco minutos;
- actualizacion in-place de la API para publicar solamente la configuracion Web
  Push del ambiente.

Staging reutiliza el Container Apps Environment de dev; por eso la alerta debe
consultar el Log Analytics Workspace asociado a ese runtime compartido.

El plan posterior al apply devolvió `No changes`. API y job usan la misma
imagen inmutable `bb37b2f`; el job corre `*/1 * * * *` con `0.5 CPU / 1 GiB`.
Una ejecución manual (`posfifo-staging-notifications-7pl2mg1`) terminó exitosa
y la consulta Kusto sana devolvió cero filas. La misma consulta con un nombre
de job inexistente devolvió una fila, validando la condición negativa.

La privada VAPID exclusiva de staging ya esta en
`posfifostagingkv/web-push-vapid-private-key`. La clave privada no se imprimio ni
se guardo en el repositorio; los archivos temporales de generacion se borraron.

## Gates de promocion

- [x] Preflight de datos sin incompatibilidades.
- [x] Dumps de control plane y tenant creados y listables.
- [x] `manage.py check` limpio.
- [x] `makemigrations --check --dry-run` sin cambios.
- [x] Pruebas enfocadas BUG-I/J: 61 OK.
- [x] Portal: TypeScript/build, ESLint y Vitest (92) OK.
- [x] Terraform `fmt -check`, `init -backend=false` y `validate` en los cuatro roots.
- [x] Suite Django completa sobre base recreada: 1,160 pruebas OK (1,008 s).
- [x] Kusto: job sano devuelve 0 filas y nombre inexistente devuelve 1.
- [x] CI y despliegue dev con API/job en el mismo SHA (`0c6cbff`).
- [x] Action Group dev creado y prueba Azure `Succeeded`.
- [x] Promocion backend y 13 migraciones staging.
- [x] Terraform staging aplicado sin destrucciones y sin drift posterior.
- [x] Portal staging publicado; CI, Vitest, lint y build verdes.
- [x] Action Group staging probado: Azure reportó `Email: Succeeded`.
- [x] Tenant aislado `staging_demo`, sucursal `01` y corte temporal.
- [x] Rig local dedicado conectado a staging con `SYNC_INTERVAL=60`.
- [x] Correo de prueba del Action Group recibido en
  `genaosantiago001@gmail.com` y confirmado por el receptor (2026-09-08).
- [ ] Smoke físico Windows de BUG-I/J y Web Push.
- [ ] Matriz física Windows, Android e iPhone.

Si la matriz falla, desactivar primero el motor del tenant `staging_demo`. Esto detiene
nuevas proyecciones sin borrar bandeja, eventos ni diagnostico.

## Contención durante el aprovisionamiento

El slug inicialmente previsto, `demo`, ya apuntaba a `tnt_demo`, una base
descartable compartida históricamente con producción. Se detuvo ese camino al
detectarlo: el motor `demo` quedó desactivado y, solo en el control plane de
staging, se desactivaron tenant, membresía y token. No se usará `demo` para esta
matriz.

La primera credencial autogenerada de ese bootstrap apareció en la salida del
job. Se rotó inmediatamente mediante Key Vault y dejó de ser válida. Durante
la contención se rotó además la contraseña administrativa operativa de la base
compartida `tnt_demo`; no se tocaron los tenants de clientes. La solución
definitiva para la prueba es `staging_demo`, que sí tiene base propia.

## Smoke automatizado posterior

- Login JWT de `staging_demo` correcto; el Administrador tiene
  `notificaciones.administrar` y recibe configuración VAPID.
- Portal, `/notificaciones`, manifest, service worker e iconos 180/192/512
  responden 200 y el bundle apunta a la API staging, no a dev.
- El token de la sucursal `01` está en Key Vault y autentica
  `/api/v1/sync/status/`; una regla temporal de firewall usada para validarlo se
  eliminó y se verificó ausente.
- La base local `pos_fifo_demo_branch` está migrada y aislada. Un ciclo push/pull
  real contra staging terminó sin fallos ni pérdida; el daemon quedó probado
  con heartbeat, push y pull cada 60 segundos.
