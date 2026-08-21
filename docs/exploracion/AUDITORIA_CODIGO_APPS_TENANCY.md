# Auditoría profunda de código - `apps/tenancy`

Fecha: 2026-08-20  
Revisión de cierre: `3f22385`  
Modo: lectura, pruebas y documentación; no se aplicaron correcciones funcionales.

> **Estado (2026-08-21): MITIGADO.** Los 18 hallazgos se verificaron contra el
> código y los 18 resultaron reales. 17 están corregidos con pruebas de
> regresión; TEN-016 (matriz PostgreSQL multi-DB en CI) queda abierto por ser
> infraestructura de CI, no código. Ver
> [Estado de mitigación](#estado-de-mitigación) al final del documento.
> **Incluye 2 migraciones y acciones operativas de rotación de credenciales.**

## Resumen ejecutivo

`apps/tenancy` es hoy la frontera de aislamiento más sensible del sistema: decide
qué base de datos, usuario operativo y namespace de media corresponden a cada
request. El proyecto declara DB-per-tenant activo en producción y documenta a
Royal Plast y SK Performance operando sobre esta arquitectura. Por eso un fallo
en este módulo no queda contenido dentro de una pantalla: puede afectar acceso,
atribución, datos o recuperación de varios negocios.

La base técnica tiene controles valiosos: usa `ContextVar`, limpia contexto al
inicio y final de cada request, falla en voz alta para la mayoría de modelos de
negocio sin tenant, valida tenants/identities/usuarios activos, conserva el token
sync plano fuera del control plane y usa parámetros/identificadores seguros al
crear bases PostgreSQL. Sin embargo, los límites de autorización y operación no
están cerrados de extremo a extremo.

Los riesgos más urgentes son:

- Revocar una `Membership` o bajar un usuario de ADMIN a CAJERA no invalida su
  JWT. El access token anterior sigue autenticando y el mismo refresh puede
  seguir emitiendo access tokens.
- Los refresh tokens duran siete días en cloud, rotan sin blacklist y no existe
  logout server-side. El token original se puede reutilizar.
- Los dos comandos de onboarding aceptan una contraseña administrativa conocida
  por defecto; al reejecutarlos restablecen la contraseña local y la global. El
  bootstrap también imprime siempre el token sync plano.
- `tenant_key`, `db_name` y `media_prefix` son editables/eliminables desde admin.
  Cambiar `db_name` no reemplaza el `DatabaseWrapper` ya cacheado, por lo que
  workers distintos pueden quedar conectados a bases distintas.
- `media_prefix` no es único ni se valida como namespace no vacío. Dos tenants
  pueden producir exactamente el mismo path de Blob/media.
- `bootstrap_tenant` marca/reactiva el tenant antes de crear/verificar/migrar su
  base. Un fallo posterior deja un registro activo y parcialmente reconfigurado.
- `normalizar_import_tenant` no es atómico: su guard multi-sucursal se evalúa
  después de modificar negocio, sucursal y usuarios; el control plane se escribe
  todavía más tarde y en otra transacción implícita.
- La impersonación global y, en general, las mutaciones API bajo tenancy se
  excluyen expresamente del middleware de auditoría. No queda una traza durable
  que preserve al `Identity` global como actor real.
- El login no tiene rate limit efectivo.
- `backup_tenant` no genera ningún backup aunque documentos vivos lo presentan
  como el comando de backup por tenant.

Se documentan **18 hallazgos**:

| Prioridad | Cantidad | Criterio |
| --- | ---: | --- |
| P1 | 10 | Puede mantener acceso revocado, exponer secretos, mezclar tenants/media, dejar onboarding inconsistente o impedir recuperación confiable. |
| P2 | 7 | Debilita invariantes, migraciones operativas, diagnóstico o cobertura de aislamiento. |
| P3 | 1 | Es deuda de contrato futuro sin consumidor activo hoy. |

La suite existente terminó con **37/37 pruebas aprobadas**. Una batería
adversarial temporal terminó con **7/7 casos aprobados** y una comprobación
adicional con la política cloud de refresh terminó **1/1**; ambos módulos se
eliminaron del workspace. Otra reproducción confirmó el `DatabaseWrapper`
obsoleto. También pasaron `manage.py check` y
`makemigrations tenancy --check --dry-run`.

## Alcance

Se inspeccionaron completamente:

- `apps/tenancy/models.py`
- `apps/tenancy/authentication.py`
- `apps/tenancy/context.py`
- `apps/tenancy/registry.py`
- `apps/tenancy/router.py`
- `apps/tenancy/media.py`
- `apps/tenancy/middleware.py`
- `apps/tenancy/db.py`
- `apps/tenancy/admin.py`
- `apps/tenancy/management/`
- `apps/tenancy/tests/`
- `apps/tenancy/migrations/`

También se trazaron dependencias relevantes en:

- `apps/api/auth_views.py`, `auth_urls.py`, `authentication.py` y
  `permissions.py`
- `apps/auditoria/middleware.py`
- `apps/usuarios/models.py`
- `apps/sucursales/models.py`
- `config/settings.py`, `settings_cloud.py` y `urls.py`
- `infra/azure/modules/container-apps/` y el ambiente `prod`
- `docs/TENANCY_DB_PER_TENANT.md`
- `docs/ROADMAP_TENANCY_DBPERTENANT.md`
- `docs/ROADMAP_PORTAL.md`
- `docs/runbooks/ROYAL_PLAST_IMPORT_DB_PER_TENANT.md`

El núcleo de `apps/tenancy` suma 1,522 líneas Python, sin contar migraciones ni
tests. Sus cuatro archivos de pruebas suman 659 líneas y descubren 37 casos.

La auditoría comenzó y cerró en `3f22385`. Durante la revisión aparecieron
cambios externos sin commit en inventario y sync, coherentes con las correcciones
que se estaban realizando en paralelo. Ningún archivo de `apps/tenancy`, auth,
settings o auditoría citado aquí cambió durante el análisis; no se modificaron ni
revirtieron esos trabajos externos.

## Hallazgos P1

### TEN-001 - La autorización tenant no se revalida después de emitir el JWT

- Severidad: crítica.
- Tipo: revocación / autorización / persistencia de privilegios.
- Evidencia:
  - El login exige una `Membership` activa y un usuario local ADMIN/SYSADMIN
    (`apps/api/auth_views.py:51-76` y `:185-197`).
  - En requests posteriores, `TenantJWTAuthentication` solo vuelve a validar
    `Identity.activo`, `Tenant.activo`, la presencia del claim `username` y que
    el usuario local esté activo (`apps/tenancy/authentication.py:68-106`).
  - No consulta `Membership`, no verifica que siga activa, que conecte ese
    identity/tenant/username ni que el usuario conserve rol de portal.
  - Un token tenant emitido mediante impersonación tampoco comprueba que el
    `Identity` siga siendo global.
- Reproducción validada:
  - Se emitió un JWT para una membership ADMIN, luego se eliminó la membership y
    el usuario local se cambió a CAJERA.
  - El access token previo siguió autenticando al mismo usuario y devolvió rol
    CAJERA, sin rechazar que ya no cumplía el contrato admin-only.
- Impacto:
  - Revocar solo el vínculo con el tenant no revoca la sesión.
  - Un ex-SYSADMIN global puede conservar sesiones ya impersonadas mientras su
    `Identity` permanezca activa.
  - El operador puede creer que retiró acceso y dejar una ventana real de acceso
    hasta el vencimiento/revocación indirecta.
- Recomendación:
  - En cada autenticación tenant, exigir una membership activa que coincida
    exactamente con `identity`, tenant y username, salvo un claim de impersonación
    global explícito cuyo `Identity.is_global` se revalide.
  - Reaplicar el gate de usuario de portal o un permiso dedicado, no el rol
    congelado del token.
  - Incorporar `auth_version`/`session_version` por identity/membership para
    invalidación inmediata y barata.
- Prueba de aceptación sugerida:
  - Eliminar/desactivar membership, retirar `is_global`, cambiar username o bajar
    el rol debe hacer que el access y el siguiente refresh fallen inmediatamente.

### TEN-002 - Refresh reutilizable por siete días y sin logout server-side

- Severidad: crítica.
- Tipo: gestión de sesión / replay de credenciales.
- Evidencia:
  - Cloud configura access de 30 minutos y refresh de 7 días, con rotación
    habilitada pero `BLACKLIST_AFTER_ROTATION=False`
    (`config/settings_cloud.py:167-175`).
  - `/auth/refresh/` usa el `TokenRefreshView` genérico, sin revalidar identity,
    membership, tenant o usuario local (`apps/api/auth_urls.py:14-18`).
  - No hay endpoint de logout ni `token_blacklist`; ambas tareas siguen abiertas
    en `docs/ROADMAP_PORTAL.md:424-430`.
- Reproducción validada:
  - Después de eliminar la membership, el mismo refresh token se intercambió dos
    veces y ambas respuestas fueron HTTP 200.
- Impacto:
  - Cerrar sesión en el frontend solo borra la copia local; una copia robada
    sigue viva.
  - La rotación da apariencia de reemplazo, pero no invalida el refresh anterior.
  - Combinado con TEN-001, la revocación de membership tampoco detiene los nuevos
    access tokens.
- Recomendación:
  - Instalar blacklist, invalidar el refresh anterior al rotar y exponer logout
    server-side idempotente.
  - Usar un serializer de refresh tenant-aware que revalide el estado actual y
    una versión de autorización.
  - Registrar emisión, rotación, logout y rechazo sin registrar el token plano.
- Prueba de aceptación sugerida:
  - El refresh A funciona una sola vez; su replay falla. Logout, revocación de
    membership o pérdida de `is_global` invalidan todos sus descendientes.

### TEN-003 - Onboarding usa una contraseña conocida y reexpone credenciales

- Severidad: crítica.
- Tipo: secretos / credenciales iniciales / idempotencia destructiva.
- Evidencia:
  - `bootstrap_tenant` usa una contraseña administrativa literal si no se pasa
    `--admin-password` (`apps/tenancy/management/commands/bootstrap_tenant.py:31-37`).
  - El mismo default existe en `normalizar_import_tenant`
    (`apps/tenancy/management/commands/normalizar_import_tenant.py:13-23`).
  - Cada reejecución llama `set_password` tanto al usuario operativo como al
    `Identity`, no solo al crear (`bootstrap_tenant.py:207-227` y `:279-296`;
    `normalizar_import_tenant.py:177-191` y `:225-233`).
  - Bootstrap imprime siempre el token sync completo en stdout
    (`bootstrap_tenant.py:117-130`). El normalizador sí lo enmascara salvo opt-in
    (`normalizar_import_tenant.py:49-55`).
  - El runbook de import conserva la credencial literal en un comando y un smoke
    (`docs/runbooks/ROYAL_PLAST_IMPORT_DB_PER_TENANT.md:351-359` y `:384-387`).
- Reproducción validada:
  - Bootstrap sin credenciales creó un `Identity` que aceptó el password por
    defecto y dejó el token sync plano en stdout.
- Impacto:
  - Una ejecución accidental o un rerun “idempotente” puede reemplazar una
    contraseña fuerte por una conocida, bloquear al dueño y abrir acceso.
  - Logs de jobs, CI o terminal pueden retener el token de sucursal reutilizable.
- Recomendación:
  - Hacer `--admin-password` obligatorio para creación o generar un secreto
    aleatorio one-time entregado por canal seguro.
  - No restablecer passwords en reruns salvo un flag explícito de rotación, con
    confirmación y auditoría.
  - Nunca imprimir tokens completos por defecto; almacenar/entregar mediante un
    mecanismo de secretos y rotarlos si ya aparecieron en logs.
  - Revisar y rotar las credenciales que pudieron provisionarse con el default.
- Prueba de aceptación sugerida:
  - Bootstrap sin secreto debe fallar antes de escribir. Un rerun normal conserva
    los hashes y no revela ningún token.

### TEN-004 - La identidad de routing es mutable y la conexión cacheada queda obsoleta

- Severidad: crítica.
- Tipo: aislamiento de base de datos / lifecycle / split-brain.
- Evidencia:
  - El modelo dice que `tenant_key` es estable, pero `save` solo lo transforma y
    todos `tenant_key`, `db_name` y `media_prefix` siguen editables
    (`apps/tenancy/models.py:9-41`).
  - `TenantAdmin` no declara campos readonly ni bloquea delete
    (`apps/tenancy/admin.py:6-10`).
  - El registry actualiza los diccionarios de configuración, pero no cierra ni
    reemplaza el `DatabaseWrapper` que `connections[alias]` ya cacheó
    (`apps/tenancy/registry.py:30-47`).
  - Si recibe una instancia `Tenant`, el registry tampoco exige `activo=True`
    (`registry.py:30-39`).
- Reproducción validada:
  - Se configuró `tenant_old`, se materializó su wrapper, se cambió `db_name` a
    `tenant_new` y se reconfiguró. El diccionario mostró `tenant_new`, pero el
    wrapper cacheado conservó `tenant_old`.
  - La misma reproducción confirmó que una instancia inactiva fue aceptada.
- Impacto:
  - Workers que ya tocaron el alias pueden seguir escribiendo en la base anterior
    mientras procesos nuevos usan la nueva.
  - Cambiar `tenant_key` invalida tokens existentes y puede dejar `db_name` y
    media con el namespace viejo; eliminar la fila no elimina ni respalda la BD.
- Recomendación:
  - Tratar tenant key, alias, db name y media prefix como identificadores
    inmutables después del alta; retirarlos del admin genérico.
  - Implementar cambios mediante una operación de migración explícita, bloqueada,
    auditable y con drenaje/reinicio o cierre garantizado de conexiones.
  - Rechazar instancias inactivas dentro del registry, no solo en sus callers.
- Prueba de aceptación sugerida:
  - Ningún formulario/save ordinario puede cambiar esos campos; una migración
    controlada cierra conexiones y todos los workers convergen a una sola BD.

### TEN-005 - El namespace de media no garantiza aislamiento tenant

- Severidad: crítica.
- Tipo: colisión cross-tenant / integridad y confidencialidad de archivos.
- Evidencia:
  - `db_name` es único, pero `media_prefix` es un `CharField` libre, no único y
    opcional (`apps/tenancy/models.py:15-21`).
  - La ruta de producto/logo confía en ese valor después de una normalización que
    también puede convertir `/`, `.` o segmentos vacíos en un prefijo vacío
    (`apps/tenancy/media.py:6-13` y `:29-61`).
  - Admin permite editar el prefijo sin validación de propiedad
    (`apps/tenancy/admin.py:6-10`).
- Reproducción validada:
  - Se crearon dos tenants con `media_prefix='shared/'`; ambos resolvieron
    `shared/productos/item.jpg`.
- Impacto:
  - Un upload puede sobrescribir/reutilizar el blob de otro negocio o devolver su
    imagen/logo.
  - Un prefijo vacío degrada a rutas globales aunque tenancy esté encendido.
- Recomendación:
  - Constraint único y validación canónica no vacía; preferiblemente derivar el
    namespace de un identificador inmutable que el usuario no edite.
  - Verificar ownership al guardar y al migrar, y auditar colisiones existentes
    antes de aplicar el constraint.
- Prueba de aceptación sugerida:
  - Dos tenants nunca pueden persistir el mismo prefijo; entradas vacías, `.`, `/`
    o prefijos ajenos fallan antes de guardar.

### TEN-006 - Bootstrap publica un tenant activo antes de completar el onboarding

- Severidad: alta-crítica.
- Tipo: consistencia operacional / aprovisionamiento parcial.
- Evidencia:
  - `update_or_create` escribe `activo=True`, reemplaza `db_name` y
    `media_prefix`, y solo después verifica/crea la BD
    (`apps/tenancy/management/commands/bootstrap_tenant.py:83-97`).
  - Migraciones, seed tenant y seed control-plane ocurren en fases separadas, sin
    estado `PROVISIONING/READY/FAILED`, transacción o compensación
    (`bootstrap_tenant.py:99-125`).
  - El seed tenant contiene muchas escrituras secuenciales sin bloque atómico
    (`bootstrap_tenant.py:148-277`).
- Reproducción validada:
  - Un tenant inactivo con DB/prefijo de archivo se pasó al bootstrap. Se simuló
    fallo al verificar la DB: el comando falló, pero la fila quedó activa y con
    DB/prefijo reescritos.
- Impacto:
  - Auth, migraciones y operadores pueden ver un tenant “activo” cuya base no
    existe, no está migrada o quedó sembrada a medias.
  - Reintentar puede además resetear credenciales (TEN-003).
- Recomendación:
  - Crear en estado `PROVISIONING`; validar entradas y precondiciones primero;
    publicar `READY/activo` solo al final.
  - Hacer atómico cada dominio de base y registrar checkpoints/compensaciones para
    los pasos no transaccionales como `CREATE DATABASE` y storage.
  - No reactivar ni reemplazar routing de un tenant existente sin flags y
    precondiciones explícitas.
- Prueba de aceptación sugerida:
  - Inyectar fallo en cada paso y comprobar que el tenant nunca queda enrutable
    como READY, que el rerun retoma de forma segura y que no cambia secretos.

### TEN-007 - La normalización de import puede fallar después de mutar datos

- Severidad: crítica para importaciones productivas.
- Tipo: atomicidad / atribución de datos / split-brain control-tenant.
- Evidencia:
  - Antes del guard multi-sucursal, el comando crea/edita `Negocio`, crea/edita
    la sucursal inicial y asigna negocio a todos los usuarios nulos
    (`apps/tenancy/management/commands/normalizar_import_tenant.py:82-116`).
  - Recién después detecta otras sucursales y filas sin sucursal, y puede lanzar
    `CommandError` (`normalizar_import_tenant.py:118-132`).
  - No hay `transaction.atomic`; después siguen backfills, RBAC, plan, password y
    token (`:134-212`).
  - El control plane se actualiza solo cuando terminó el bloque tenant, en otra
    fase sin transacción coordinada (`:37-55` y `:225-249`).
  - Dry-run no entra al bloque que ejecuta el guard, aunque muestra conteos.
- Reproducción validada:
  - Con dos sucursales y una caja legacy sin sucursal, el guard lanzó error; aun
    así `Negocio.nombre`, `Sucursal.nombre` y `Usuario.negocio` ya habían quedado
    modificados, mientras la caja seguía nula.
- Impacto:
  - Un comando que informa fallo deja el origen parcialmente transformado.
  - Un error en control plane después de cambiar password/token en tenant deja
    credenciales y registros globales fuera de sincronía.
- Recomendación:
  - Mover todas las validaciones y el guard a un preflight real, también ejecutado
    en dry-run, antes de la primera escritura.
  - Usar `transaction.atomic(using=tenant_alias)` para la fase tenant y
    `transaction.atomic(using='default')` para control, con un estado durable de
    saga/checkpoint que permita compensar o reanudar el cruce entre bases.
  - Capturar baseline y postcondiciones exactas para cada tabla/backfill.
- Prueba de aceptación sugerida:
  - Fallar en guard, RBAC, suscripciones, password, token y control plane; cada
    caso debe hacer rollback del dominio o quedar en estado reanudable explícito.

### TEN-008 - Impersonación y mutaciones API no conservan al actor global en auditoría

- Severidad: alta-crítica.
- Tipo: trazabilidad / no repudio / soporte privilegiado.
- Evidencia:
  - `impersonar_tenant` emite un token actuando como un `Usuario` operativo del
    tenant (`apps/api/auth_views.py:98-125`).
  - La autenticación adjunta `identity_id` solo como atributo en memoria al
    usuario (`apps/tenancy/authentication.py:99-106`); no hay evento durable de
    inicio/fin de impersonación.
  - `AuditoriaMiddleware` omite deliberadamente todo request `/api/` cuando
    tenancy está activo, tanto éxitos como excepciones
    (`apps/auditoria/middleware.py:48-58`, `:69-76`, `:89-95` y `:131-137`).
  - No hay integración alternativa de `Identity` en `apps/api`.
- Impacto:
  - Una acción de soporte puede no auditarse o quedar atribuida solo al admin
    local impersonado, no a la persona global que la ejecutó.
  - Incidentes y cambios sensibles no pueden reconstruirse con certeza.
- Recomendación:
  - Crear una sesión de impersonación con ID, actor identity, tenant, usuario
    objetivo, motivo, inicio, vencimiento y cierre.
  - Propagar ese ID/actor a cada auditoría API tenant y registrar mutaciones
    exitosas y fallidas en la BD tenant apropiada o en un log central inmutable.
  - Exigir motivo/ticket y mostrar claramente al operador que está impersonando.
- Prueba de aceptación sugerida:
  - Una edición durante impersonación debe registrar simultáneamente actor global,
    usuario efectivo, tenant, objeto, antes/después, IP y correlación.

### TEN-009 - El endpoint de login no tiene rate limit efectivo

- Severidad: alta, agravada por TEN-003.
- Tipo: fuerza bruta / disponibilidad.
- Evidencia:
  - El único throttle global es `ScopedRateThrottle` y las tasas declaradas son
    `sync`, `maestros` y `reportes`; no existe scope de auth
    (`config/settings.py:413-431`).
  - `PortalTokenObtainPairView` no define `throttle_scope` ni throttle propio
    (`apps/api/auth_views.py:83-95`).
  - El roadmap mantiene rate limiting de login como pendiente
    (`docs/ROADMAP_PORTAL.md:424-430`).
- Reproducción validada:
  - Quince passwords incorrectos consecutivos para el mismo email devolvieron
    quince HTTP 400 y ningún 429.
- Impacto:
  - Permite fuerza bruta y credential stuffing sin freno de aplicación; cada
    intento además ejecuta un hash de password costoso.
- Recomendación:
  - Rate limit por combinación IP/identity normalizada con límites cortos y
    sostenidos, backoff y telemetría; no revelar si el email existe.
  - Diseñar protección compatible con proxies de Azure y una recuperación que no
    permita bloqueo permanente por terceros.
- Prueba de aceptación sugerida:
  - La matriz IP/email debe producir 429/backoff según política, recuperarse al
    vencer la ventana y generar alerta sin diferenciar usuario existente.

### TEN-010 - `backup_tenant` no realiza ni verifica un backup

- Severidad: crítica para continuidad operacional.
- Tipo: backup/restore / contrato documental engañoso.
- Evidencia:
  - El comando solo valida un tenant activo, imprime una advertencia y sugiere un
    `pg_dump`; no ejecuta proceso ni crea archivo
    (`apps/tenancy/management/commands/backup_tenant.py:6-21`).
  - La sugerencia omite host, puerto, usuario, SSL y conexión del settings activo;
    depende del ambiente libpq del operador.
  - La fuente de diseño presenta `backup_tenant` como “pg_dump de
    tnt_royalplast” y afirma backup/export trivial
    (`docs/TENANCY_DB_PER_TENANT.md:18-24` y `:234-246`).
  - El estado maestro reconoce solo siete días de backup del servidor Azure, no
    una restauración por tenant probada (`docs/PROJECT_STATUS.md:27-29`).
- Impacto:
  - Un operador o automatización puede interpretar salida exitosa como backup
    cuando no existe artefacto.
  - La restauración de un solo tenant puede requerir restaurar servidor, extraer
    y reimportar bajo presión, sin RPO/RTO verificado.
- Recomendación:
  - Renombrar de inmediato el helper para que no simule ejecución, o implementar
    backup real con destino explícito, conexión derivada, exit code, tamaño,
    checksum, cifrado, retención y `pg_restore --list`/restore drill.
  - Documentar el camino de recuperación Azure por tenant y probarlo
    periódicamente en una BD aislada.
- Prueba de aceptación sugerida:
  - El comando exitoso debe producir un artefacto verificable y restaurable; si
    no lo produce, debe terminar en error, no código 0.

## Hallazgos P2

### TEN-011 - Las claves lógicas de identidad no tienen la misma unicidad que el login

- Severidad: alta.
- Tipo: identidad ambigua / accountability.
- Evidencia:
  - `Identity.email` usa unicidad normal de base, sensible a mayúsculas en
    PostgreSQL (`apps/tenancy/models.py:72-84`).
  - El login normaliza a minúsculas, busca `email__iexact` y toma `.first()`
    (`apps/api/auth_views.py:37-47`).
  - `Membership` solo es única por `(identity, tenant)`; no por
    `(tenant, username)` (`apps/tenancy/models.py:100-116`).
- Reproducción validada:
  - La base aceptó `Owner@Example.com` y `owner@example.com` simultáneamente.
  - Dos identities distintas pudieron apuntar al mismo `tenant/username=admin`.
- Impacto:
  - El login puede seleccionar una cuenta distinta según orden/estado y dos
    credenciales globales pueden actuar como el mismo usuario operativo.
  - Auditorías basadas solo en `Usuario` pierden la identidad real.
- Recomendación:
  - Índice único sobre email normalizado (`Lower(email)`/citext) y normalización
    en `clean/save`.
  - Constraint único `(tenant, username)` o modelar explícitamente el caso de
    múltiples identities con una política auditable.
  - Limpiar colisiones antes de migrar.

### TEN-012 - La migración de media confía en cualquier destino existente

- Severidad: alta.
- Tipo: integridad de archivos / migración parcial.
- Evidencia:
  - Si el destino existe, el comando no compara tamaño/hash/contenido: lo marca
    `skipped` y aun así actualiza el campo de BD para apuntarle
    (`apps/tenancy/management/commands/migrar_media_tenant.py:107-130`).
  - Upload y actualización ORM no tienen transacción, manifest ni compensación.
  - `_source_path` resuelve el path, pero no verifica formalmente que siga dentro
    del root después de resolver symlinks (`:133-135`).
- Reproducción validada:
  - El origen contenía `expected`; el destino ya contenía `unrelated`. El comando
    conservó `unrelated`, contó `skipped=1` y cambió la BD al destino.
- Impacto:
  - El producto/logo puede terminar mostrando un archivo viejo o ajeno y el
    comando reportar éxito.
  - Un fallo a mitad deja blobs huérfanos o filas parcialmente migradas.
- Recomendación:
  - Manifest por objeto con origen, destino, tamaño y SHA-256; un destino distinto
    debe fallar o usar nombre versionado, nunca aceptarse silenciosamente.
  - Restringir path resuelto al root y registrar estado reanudable por fila.
- Prueba de aceptación sugerida:
  - Matriz destino ausente/igual/distinto, fallo de storage, fallo de DB y rerun;
    solo el contenido verificado puede convertirse en path canónico.

### TEN-013 - Existen rutas que vuelven silenciosamente a `default`

- Severidad: alta si se habilita el escape en cloud.
- Tipo: aislamiento por configuración / fail-open.
- Evidencia:
  - `auth`, `contenttypes`, `usuarios` y `negocios` son dual-home y sin contexto
    siempre usan `default` (`apps/tenancy/router.py:6-8` y `:49-62`).
  - Con `TENANCY_ALLOW_UNSCOPED_OPERATIONS=True`, el resto devuelve `None`; Django
    puede resolver a `default` (`router.py:63-71`).
  - Settings lee esa bandera del ambiente, pero no existe system check que la
    prohíba con `settings_cloud` (`config/settings.py:188-194`).
- Impacto:
  - Código incompleto puede crear usuarios/negocios “compat” en control plane en
    vez de fallar, o cualquier modelo de negocio si se activa el escape.
  - Una variable de emergencia puede desactivar la principal defensa de
    aislamiento sin una alarma de arranque.
- Recomendación:
  - Prohibir `TENANCY_ALLOW_UNSCOPED_OPERATIONS` en cloud/producción mediante
    `ImproperlyConfigured` o system check crítico.
  - Limitar dual-home a operaciones explícitas `.using('default')`; evaluar
    fail-fast para `usuarios/negocios` fuera de admin/migraciones.
- Prueba de aceptación sugerida:
  - Cloud no arranca con escape habilitado y una consulta no scopeada nunca crea
    filas operativas en control plane.

### TEN-014 - Excepciones de infraestructura se convierten en fallback silencioso

- Severidad: media-alta.
- Tipo: observabilidad / fail-open parcial.
- Evidencia:
  - `tenant_media_prefix` captura cualquier `Exception` al leer control plane y
    cae al prefijo derivado del key (`apps/tenancy/media.py:29-42`).
  - `_attach_sucursal` captura cualquier excepción al resolver la sucursal y la
    convierte en `sucursal=None` (`apps/api/authentication.py:104-121`).
- Impacto:
  - Una caída de DB, migración pendiente o bug se presenta como “sin sucursal” o
    usa un namespace distinto del configurado, ocultando causa raíz.
  - En media puede crear archivos bajo un prefijo alternativo; en sync termina en
    403 genérico aunque el token y el registro global sean válidos.
- Recomendación:
  - Capturar solo excepciones esperadas y distinguir configuración ausente de
    infraestructura fallida.
  - Fallar en voz alta en escrituras de media y emitir logs/métricas con tenant y
    correlación; no incluir secretos.

### TEN-015 - `with_tenant` no puede envolver comandos con opciones nombradas

- Severidad: media.
- Tipo: herramienta operativa / contrato incompleto.
- Evidencia:
  - Solo acepta `command_args` posicionales con `nargs='*'`
    (`apps/tenancy/management/commands/with_tenant.py:8-15`).
  - Reenvía esos argumentos a `call_command`, sin mecanismo de opciones
    (`with_tenant.py:16-26`).
  - Activar contexto tampoco cambia un `transaction.atomic()` sin `using`: ese
    bloque seguirá abriéndose sobre `default` aunque el router mande modelos a la
    base tenant.
- Reproducción validada:
  - `with_tenant --tenant __audit_missing__ check --deploy` falló en argparse con
    `unrecognized arguments: --deploy`, antes de validar el tenant.
- Impacto:
  - El wrapper documentado no sirve para muchos comandos reales y puede dar una
    falsa sensación de que también volvió tenant-aware sus transacciones.
- Recomendación:
  - Definir forwarding robusto después de `--`, o retirar el wrapper genérico y
    exigir comandos tenant-aware con opciones tipadas.
  - Documentar y probar explícitamente el alias transaccional.

### TEN-016 - La suite no prueba el aislamiento multi-DB real de forma automatizada

- Severidad: media-alta.
- Tipo: cobertura de regresión.
- Evidencia:
  - Auth global y fallos de login sí se prueban, pero el auth de sucursal mockea
    registry, `TokenAuthentication` y asociación de sucursal
    (`apps/tenancy/tests/test_auth.py:110-147`).
  - Media mockea `TenantCommandMixin.run_in_tenant`
    (`apps/tenancy/tests/test_media.py:67-81`).
  - Bootstrap solo se cubre en dry-run; normalización mockea su núcleo
    (`apps/tenancy/tests/test_models_and_commands.py:34-90` y `:93-172`).
  - Los tests del router comprueban aliases como strings, no dos conexiones con
    datos aislados (`apps/tenancy/tests/test_router.py:22-59`).
- Impacto:
  - Los 37 tests pasan aunque persistan revocación inválida, password default,
    bootstrap parcial, colisión de media y wrapper obsoleto.
  - El smoke multi-DB documentado es histórico/manual, no una barrera de CI.
- Recomendación:
  - Fixture con control plane + dos BDs tenant reales: migrate, seed, login,
    lectura/escritura, transacciones, media y sync A/B.
  - Agregar fault injection para onboarding y revocación, y ejecutar al menos una
    matriz PostgreSQL en CI.

### TEN-017 - Migrar la flota puede dejar versiones mixtas sin un ledger de resultados

- Severidad: media-alta operacional.
- Tipo: rollout de esquema / recuperación.
- Evidencia:
  - `migrate_cloud` migra primero control plane y después todos los tenants
    (`apps/tenancy/management/commands/migrate_cloud.py:12-25`).
  - `migrate_tenants` itera y se detiene ante la primera excepción; solo imprime
    el total final si todos terminan (`migrate_tenants.py:16-40`).
  - No persiste versión objetivo, resultado por tenant, duración, error, intento
    ni estrategia de reanudación/canary.
- Impacto:
  - Un deploy puede dejar control plane nuevo, tenants previos migrados y tenants
    posteriores viejos. El job falla, pero el estado exacto depende del log.
- Recomendación:
  - Registrar ledger por tenant/migración, ejecutar canary, mostrar resumen aun al
    fallar y permitir reanudar solo pendientes.
  - Definir compatibilidad N/N-1 de la app durante el rollout y runbook de
    rollback/forward-fix por tenant.

## Hallazgo P3

### TEN-018 - `Domain` parece operativo, pero no participa en la resolución de tenant

- Severidad: baja hoy; subir antes de habilitar dominios.
- Tipo: modelo futuro / invariantes incompletas.
- Evidencia:
  - `Domain` permite texto libre, varios `is_primary=True` por tenant y solo tiene
    unicidad exacta/case-sensitive del string (`apps/tenancy/models.py:122-134`).
  - Fuera de modelo/admin/migración no hay consumidor que normalice host/puerto o
    seleccione tenant.
  - Subdominios siguen declarados fuera del MVP
    (`docs/TENANCY_DB_PER_TENANT.md:374-380`).
- Impacto:
  - El admin puede dar apariencia de routing configurado cuando el campo no
    cambia ningún request; datos cargados ahora pueden ser ambiguos al activarlo.
- Recomendación:
  - Ocultarlo o marcarlo claramente como inactivo hasta diseñar adopción.
  - Antes de consumirlo: normalización IDNA/lower/puerto, constraint de un primary
    activo por tenant y protección contra hosts reservados.

## Controles que ya están bien encaminados

- El contexto usa `ContextVar`, no globals de proceso, y los context managers
  restauran tokens en `finally` (`apps/tenancy/context.py:7-75`).
- `ClearTenantContextMiddleware` limpia antes y después del request, también ante
  excepciones (`apps/tenancy/middleware.py:4-18`).
- El router falla en voz alta para modelos de tenant sin contexto mientras el
  escape está apagado (`apps/tenancy/router.py:63-72`).
- Auth revalida en cada request que `Identity`, tenant y usuario local estén
  activos; desactivar cualquiera de esos tres sí corta el access
  (`apps/tenancy/authentication.py:75-102`).
- Auth sync resuelve primero el hash en control plane y exige token/tenant activos
  antes de abrir la BD tenant (`apps/api/authentication.py:65-80`).
- El token sync se almacena como SHA-256, no plano, y tiene unicidad por
  tenant/sucursal (`apps/tenancy/models.py:137-157`).
- `create_database` parametriza la existencia y usa `psycopg.sql.Identifier`, por
  lo que el nombre no se concatena como SQL crudo (`apps/tenancy/db.py:4-28`).
- `migrar_media_tenant` es dry-run por defecto, exige `--apply` y conserva el
  nombre real devuelto por el storage (`migrar_media_tenant.py:16-49` y `:118-130`).
- `normalizar_import_tenant` enmascara el token sync por defecto y rechaza email
  vacío/reutilizado en otro tenant activo (`normalizar_import_tenant.py:37-55` y
  `:251-264`).
- Producción configura `enable_db_per_tenant=true` por defecto en Terraform
  (`infra/azure/environments/prod/container_variables.tf:45-49`).

Estos controles reducen fugas accidentales y explican por qué las pruebas de
camino feliz pasan. No sustituyen las invariantes de revocación, lifecycle,
atomicidad y recuperación descritas arriba.

## Validación ejecutada

### Suite existente

Comando:

```powershell
C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py test apps.tenancy.tests.test_auth apps.tenancy.tests.test_media apps.tenancy.tests.test_models_and_commands apps.tenancy.tests.test_router --keepdb --settings=config.settings_development
```

Resultado:

- 37 pruebas ejecutadas.
- 37 aprobadas.
- `System check identified no issues`.
- Tiempo de tests: 4.568 s.

### Reproducciones adversariales temporales

Se crearon módulos de tests temporales, se ejecutaron con el runner Django y se
eliminaron inmediatamente; no forman parte del entregable.

| Caso | Resultado demostrado |
| --- | --- |
| Revocación JWT | Membership eliminada + rol CAJERA: access previo siguió autenticando. |
| Replay refresh | El mismo refresh se usó dos veces y ambas respuestas fueron 200. |
| Bootstrap fallido | Tenant inactivo quedó activo y con DB/prefijo reescritos antes del error. |
| Credencial default | Bootstrap sin password creó Identity con el default conocido y mostró token sync plano. |
| Invariantes globales | Dos emails case-variant, dos identities para el mismo username y dos prefixes iguales fueron aceptados. |
| Normalización parcial | El guard falló, pero negocio, sucursal y usuario ya estaban modificados. |
| Media divergente | Un blob destino diferente se reutilizó y la BD se repuntó hacia él. |
| Rate limit | Quince intentos inválidos no produjeron ningún 429. |
| Registry dinámico | Instancia inactiva aceptada; config nueva + wrapper cacheado con DB vieja. |

El primer módulo temporal agrupó siete tests (algunos demuestran más de una
condición): **7/7 aprobados**, exit code 0, 3.038 s. Un segundo módulo aplicó
explícitamente `ROTATE_REFRESH_TOKENS=True` y
`BLACKLIST_AFTER_ROTATION=False`: el refresh original funcionó dos veces, ambas
respuestas incluyeron un refresh rotado y la prueba terminó **1/1**, exit code 0,
0.051 s. Ambos archivos temporales fueron eliminados.

La prueba del registry se hizo en un proceso aislado sin conexión real y terminó
exit code 0:

```text
inactive_instance_accepted=True
configured_name=tenant_new
cached_wrapper_name=tenant_old
stale_wrapper=True
```

También se validó el contrato de `with_tenant`:

```powershell
python manage.py with_tenant --tenant __audit_missing__ check --deploy --settings=config.settings_development
```

Argparse rechazó `--deploy` como argumento no reconocido antes de consultar el
tenant.

Nota de transparencia: una primera variante inline ejecutó los ocho cuerpos con
resultado verde, pero su teardown trató el alias dinámico de registry como una
conexión de `TestCase` y falló al restaurar wrappers. Una segunda variante mostró
7/7 `OK`, pero el wrapper de script convirtió por error el objeto `TestResult`
verdadero en exit 1. La corrida final se repitió con el runner estándar como
módulo temporal y terminó 7/7, exit 0; registry quedó separado para no alterar el
inventario de conexiones del runner.

### Checks estructurales

```powershell
C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py check --settings=config.settings_development
C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py makemigrations tenancy --check --dry-run --settings=config.settings_development
```

Resultado:

- `System check identified no issues (0 silenced)`.
- `No changes detected in app 'tenancy'`.

## Cobertura que falta antes de corregir

- Access y refresh después de revocar membership, `is_global`, tenant, usuario,
  rol, RBAC, username y versión de autorización.
- Replay, rotación, logout, robo de refresh y sesiones simultáneas.
- Rate limit por IP/identity y comportamiento detrás de proxy Azure.
- Bootstrap real desde cero con dos BDs, y fault injection en create DB,
  migraciones, cada seed y control plane.
- Normalización productiva con rollback por alias y saga entre control/tenant.
- Cambio/rechazo de routing identifiers con varios workers/conexiones persistentes.
- Constraints de email normalizado, tenant/username y media prefix.
- Dos tenants escribiendo el mismo filename sobre el storage real de Azure.
- Migración media con hash, colisión, fallo de upload, fallo de save y rerun.
- Impersonación con auditoría de actor/usuario efectivo antes y después.
- Backup real, checksum, restauración aislada y medición RPO/RTO.
- Migrate canary, fallo en tenant intermedio, reanudación y compatibilidad de
  versión mixta.
- Prueba que cloud no arranca con operaciones no scopeadas habilitadas.

## Orden sugerido de corrección

1. Auditar y rotar credenciales provisionadas con defaults y tokens expuestos;
   retirar el default y limitar login (TEN-003 y TEN-009).
2. Cerrar revocación JWT/refresh, blacklist y logout server-side (TEN-001 y
   TEN-002).
3. Inmutabilizar routing y storage; cerrar conexiones y agregar constraints de
   namespace (TEN-004, TEN-005 y TEN-011).
4. Convertir bootstrap/normalización en workflows con preflight, estado,
   atomicidad por DB y reanudación (TEN-006 y TEN-007).
5. Crear auditoría durable de impersonación y mutaciones API (TEN-008).
6. Implementar y ensayar backup/restore real por tenant (TEN-010).
7. Verificar media por hash y eliminar fallbacks silenciosos (TEN-012 y TEN-014).
8. Endurecer router, herramientas y rollout de migraciones (TEN-013, TEN-015 y
   TEN-017).
9. Incorporar una matriz PostgreSQL multi-DB a CI (TEN-016).
10. Ocultar o completar el contrato de dominios antes de activarlo (TEN-018).

## Trazabilidad del snapshot

Hashes SHA-256 al cierre:

| Archivo | SHA-256 |
| --- | --- |
| `apps/tenancy/models.py` | `25B8415F2F1E248D9433BD92111A3AE8C5BB1116052329B0CDB6BCFE756C054A` |
| `apps/tenancy/authentication.py` | `8FED47863D2CEAE78DDA7B29AD6D8E99D5827C1B22DF4ECC6907D4CCB1404C7B` |
| `apps/tenancy/context.py` | `D32CD553F7DB87BB95EEA28C9543613552C3716C6C028BE45BAAA7EAA12AFAA6` |
| `apps/tenancy/registry.py` | `471A5ACF55FAE77C1494C18327A7473471D58D4BBC9A09CA0E227A33497D98D3` |
| `apps/tenancy/router.py` | `6BA01E97065F16037D705D60946A3DE2DC864AF69071233E8EF4B836D56DC3E7` |
| `apps/tenancy/media.py` | `984C8ED4429EB543C227B5B012A332FD6DE12C00B4E334021E4468063998DE53` |
| `apps/tenancy/management/commands/bootstrap_tenant.py` | `FE2DCCA89ABF3B58F7720CCC38854445F371EB8F2AD78294C4114206E3B2ED50` |
| `apps/tenancy/management/commands/normalizar_import_tenant.py` | `66F1CD64E50AF996F9100417AD25F9784DB0F1718C921EA4A434C30A0DF85FDB` |
| `apps/tenancy/management/commands/migrar_media_tenant.py` | `B5DF5F035C487661B6A9DEF4245ABA29ACEF5BA0FBCA741996802945EA27CE16` |
| `apps/tenancy/management/commands/backup_tenant.py` | `EB97C1F1EFA333EB887AE89BE8805CAD85F9F70DCD7A280398E199C5F5D99173` |
| `apps/api/auth_views.py` | `FADE731AB9089737869CD4BF1A144E058FD60D990B470D3F2EE19677DC769131` |
| `apps/api/authentication.py` | `048EB7A44F63089F402F38059660CC75DF976B4C8C01EB22D60234BED719FEE1` |
| `config/settings_cloud.py` | `92087A4B6235A30E37FD73D428CEEC2D7DFE2B10EF98B60234001BAA5F7344B2` |
| `apps/auditoria/middleware.py` | `0F97B4FCDB39F65B47D5A4B8639354B8BADE58E16F9E73B7E7A115F07DD1467D` |

## Cierre

La arquitectura DB-per-tenant sí contenía una base razonable de aislamiento, pero
su seguridad dependía demasiado de que los tokens, los identificadores y los
comandos nunca cambiaran ni fallaran a mitad. Revocación, onboarding,
impersonación, routing, media y restore ya son estados verificables (ver abajo).

---

# Estado de mitigación

Fecha: 2026-08-21. Verificación previa: se releyó cada hallazgo contra el código
citado. **Los 18 son reales** — ninguno resultó falso positivo ni obsoleto.

## Resumen por hallazgo

| ID | Real | Estado | Dónde quedó la corrección |
|---|---|---|---|
| TEN-001 | Sí | Corregido | `_autorizar_tenant()` revalida en CADA request una `Membership` activa que ate identity + tenant + username, y reaplica el gate de portal contra el rol ACTUAL. La impersonación se distingue con el claim `impersonado` y revalida `Identity.is_global`. |
| TEN-002 | Sí | Corregido | `token_blacklist` instalada (en el control plane), `BLACKLIST_AFTER_ROTATION=True`, endpoint `/auth/logout/` idempotente y `TenantTokenRefreshView` que aplica la MISMA regla que la autenticación. |
| TEN-003 | Sí | Corregido | Sin password por defecto en ninguno de los dos comandos; el alta genera un secreto aleatorio de un solo uso. Los reruns NO tocan credenciales salvo `--rotar-password`. El token sync se enmascara salvo `--mostrar-token`. |
| TEN-004 | Sí | Corregido | `TenantAdmin` congela `tenant_key`/`db_name`/`media_prefix` tras el alta y no permite borrar. El registry rechaza instancias inactivas y **descarta el `DatabaseWrapper` cacheado** al cambiar la configuración. |
| TEN-005 | Sí | Corregido | `media_prefix` único (migración `tenancy.0002`) y nunca vacío. `tenant_media_prefix()` falla si no puede resolver un namespace propio. |
| TEN-006 | Sí | Corregido | El bootstrap crea/actualiza el tenant **inactivo**, aprovisiona, y sólo publica `activo=True` al final. Un fallo restaura el estado previo. La identidad de routing de un tenant existente ya no se reescribe. |
| TEN-007 | Sí | Corregido | El guard multi-sucursal se movió a un preflight que corre antes de la primera escritura y también en dry-run. Fase tenant en `transaction.atomic(using=alias)` y control plane en `atomic(using='default')`. |
| TEN-008 | Sí | Corregido | Modelo `SesionImpersonacion` en el control plane: actor global, tenant, usuario objetivo, motivo obligatorio, IP, inicio, vencimiento y cierre. El logout cierra la sesión. |
| TEN-009 | Sí | Corregido | `LoginRafagaThrottle` + `LoginSostenidoThrottle` sobre `/auth/login/`, con clave combinada IP+email. |
| TEN-010 | Sí | Corregido | `backup_tenant` ejecuta `pg_dump` real, deriva la conexión del settings activo, verifica con `pg_restore --list`, reporta tamaño y SHA-256, y **falla** si no hay artefacto. |
| TEN-011 | Sí | Corregido | `UniqueConstraint(Lower('email'))` en `Identity` (+ normalización en `save`) y `(tenant, username)` único en `Membership`. |
| TEN-012 | Sí | Corregido | La migración de media compara por SHA-256: destino idéntico se reutiliza, destino distinto se sube versionado y se reporta como conflicto. `_source_path` confina la ruta al root tras resolver symlinks. |
| TEN-013 | Sí | Corregido | System check `tenancy.C001`: el escape `TENANCY_ALLOW_UNSCOPED_OPERATIONS` con tenancy activa es CRITICAL y no deja arrancar. |
| TEN-014 | Sí | Corregido | `tenant_media_prefix()` ya no captura `Exception`: un fallo de infraestructura se propaga en vez de degradar a otro namespace. |
| TEN-015 | Sí | Corregido | `with_tenant` reenvía todo lo que sigue a `--`, y avisa explícitamente que el contexto NO vuelve tenant-aware las transacciones del comando destino. |
| TEN-016 | Sí | **Abierto** | Matriz PostgreSQL multi-DB en CI. Es infraestructura de CI, no código; ver pendientes. |
| TEN-017 | Sí | Corregido | `migrate_tenants` lleva un ledger por tenant (estado, duración, error), imprime el resumen aunque falle, y `--continuar-ante-fallo` permite seguir con el resto de la flota. |
| TEN-018 | Sí | Corregido | `Domain` marcado como preparatorio: el admin lo expone de solo lectura para no aparentar un routing que no existe. |

## Acciones OPERATIVAS pendientes (no son código)

La auditoría las pide en el punto 1 de su orden de corrección y **no las puede
hacer una corrección de código**:

1. **Rotar las credenciales provisionadas con el default conocido.** Cualquier
   `Identity` o usuario operativo creado por `bootstrap_tenant` o
   `normalizar_import_tenant` sin `--admin-password` explícito quedó con la
   contraseña literal que estaba publicada en los runbooks. El código ya no la
   acepta, pero las cuentas existentes siguen teniéndola.
2. **Rotar los tokens sync que se imprimieron en claro.** `bootstrap_tenant`
   los mostraba siempre; los logs de CI y de jobs los retienen.
3. **Actualizar `docs/runbooks/ROYAL_PLAST_IMPORT_DB_PER_TENANT.md`**, que
   conserva la credencial literal en un comando y en un smoke.

## Cambios de conducta observables

1. **Revocar una membership ahora corta la sesión al instante**, en el request
   siguiente. Es el objetivo del cambio, pero significa que una revocación
   accidental se siente de inmediato.
2. **El refresh rota con blacklist**: el token anterior deja de servir. Un
   cliente que guarde y reutilice un refresh viejo empezará a recibir 401.
3. **`/auth/impersonate/` exige `motivo`.** Un cliente que no lo mande recibe
   400. El portal React necesita ese campo.
4. **El bootstrap sin `--admin-password` ya no usa una clave conocida**: genera
   una aleatoria y la muestra UNA vez. Los reruns no resetean credenciales.
5. **`backup_tenant` puede fallar** donde antes siempre salía 0. Es el punto: un
   exit 0 sin artefacto era la falla.
6. **El admin de Tenant no permite editar routing ni borrar filas**, y el de
   Domain es de solo lectura.
7. **Cloud no arranca con `TENANCY_ALLOW_UNSCOPED_OPERATIONS=true`.**

## Despliegue: 2 migraciones

1. **`tenancy.0002_alter_tenant_media_prefix_and_more`** — las tres constraints
   de identidad. Corre un **preflight** que rellena prefijos vacíos (seguro) y
   **falla con detalle** si encuentra colisiones reales de `media_prefix`,
   email case-variante o `(tenant, username)`. Es deliberado que no las resuelva
   sola: cambiar un `media_prefix` implica MOVER blobs, y fusionar identidades
   requiere decidir cuál conserva las memberships.
2. **`tenancy.0003_sesionimpersonacion`** — tabla nueva, sin datos que migrar.

Ambas viven en el CONTROL PLANE. La de `token_blacklist` la aporta SimpleJWT y
el router la enruta a `default`.

## Pendiente (no bloqueante)

- **TEN-016: matriz PostgreSQL multi-DB en CI.** Requiere levantar dos bases
  tenant reales en el workflow, migrarlas y correr una batería A/B de
  aislamiento. Es una decisión de infraestructura y costo de CI, no un cambio de
  código; queda para definir con el equipo.
- **Auditoría de mutaciones API bajo tenancy.** `SesionImpersonacion` registra
  QUIÉN entró, por qué y hasta cuándo. Correlacionar cada mutación individual
  con esa sesión exige levantar la exclusión de `/api/` del
  `AuditoriaMiddleware` y decidir en qué base se escribe cada registro; es un
  trabajo propio.
- **Cruce control-plane / tenant en `normalizar_import_tenant`.** Cada dominio
  es atómico, pero el cruce entre las dos bases no puede serlo. Si el control
  plane falla, la base tenant ya quedó normalizada; el comando lo dice
  explícitamente y es idempotente, pero una saga con checkpoints sería más
  fuerte.
- **Drill de restauración.** `backup_tenant` produce y verifica un artefacto,
  pero un backup no está probado hasta restaurarlo. Falta el ensayo periódico en
  una BD aislada y medir RPO/RTO.

## Pruebas

Suite completa, serial: **562 tests, OK.**

Módulo de regresión nuevo: `apps/tenancy/tests/test_auditoria_tenancy.py`
(33 tests) — es la batería adversarial de la auditoría hecha permanente, cubriendo
TEN-001 a TEN-015 y TEN-018.

**Verificación por mutación.** Anulando la revalidación de membership en
`_autorizar_tenant`, los tres tests centrales de TEN-001 fallan:

```
test_eliminar_la_membership_corta_el_acceso
test_desactivar_la_membership_corta_el_acceso
test_cambiar_el_username_corta_el_acceso
```

Es exactamente la reproducción que hizo la auditoría: membership eliminada y el
access token previo seguía autenticando.

Detalle útil al leer estos tests: los que ejercitan el registry desregistran los
alias dinámicos en `tearDown`. Sin eso, `TestCase` intenta restaurar métodos
sobre una conexión que el runner nunca preparó y falla con
`'function' object has no attribute 'wrapped'` — el mismo tropiezo que la
auditoría documentó en su nota de transparencia.
