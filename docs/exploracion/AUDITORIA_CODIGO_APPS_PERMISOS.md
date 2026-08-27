# Auditoría profunda de código - `apps/permisos`

Fecha: 2026-08-20  
Revisión de cierre: `3f22385`  
Modo: lectura, pruebas y documentación; no se aplicaron correcciones funcionales.

> **Estado (2026-08-27): P1 MITIGADO (10/10).** Los diez hallazgos P1 se
> verificaron contra el código y los diez resultaron reales; los diez están
> corregidos, con pruebas de regresión. **P2 y P3 siguen abiertos** salvo
> PER-011, que se cerró junto con las señales. Ver
> [Estado de mitigación](#estado-de-mitigación) al final.
> **Incluye 1 migración con deduplicación previa, un cambio de contrato del
> motor** (qué significa llamar sin sucursal) **y un cambio de alcance del
> portal** (un ADMIN de tenant ya no administra suscripciones).

## Resumen ejecutivo

`apps/permisos` es la frontera de autorización del POS local, del portal cloud y
de parte de la sincronización cloud → sucursal. La base conceptual es buena: el
catálogo es declarativo, los roles son configurables por negocio, las
asignaciones pueden ser globales o acotadas a una sucursal, el motor niega por
defecto a usuarios ordinarios y existen señales para invalidar el caché. La API
también intenta limitar la administración de RBAC al negocio del solicitante.

Sin embargo, la implementación no conserva esas garantías en todos los caminos.
Los riesgos más urgentes son:

- El caché usa solamente `usuario.pk` y `sucursal.pk`; no incluye tenant ni alias
  de base. En DB-per-tenant, dos usuarios de negocios distintos con los mismos
  PK pueden compartir permisos dentro del mismo worker.
- Producción ejecuta Gunicorn con tres workers y usa el caché local por defecto.
  Una revocación invalida solamente el worker que procesó la escritura; los
  demás pueden conservar el permiso durante cinco minutos.
- Omitir la sucursal no significa “solo asignaciones globales”: significa unir
  las asignaciones de todas las sucursales. Los decoradores HTML, el filtro de
  plantillas y los requests de portal sin token de sucursal omiten el scope.
- `AsignacionRol` no exige que usuario, rol y sucursal pertenezcan al mismo
  negocio. El motor confía en cualquier fila activa que encuentre.
- La API permite asignar un rol a un usuario con `negocio=NULL`, y
  `negocio_actual()` permite a cualquier usuario sin negocio elegir
  `?negocio=<id>` sin comprobar que sea SYSADMIN. Se reprodujo una escalada de
  administración RBAC del negocio A al B.
- La sincronización identifica asignaciones por username + slug de rol + código
  de sucursal, pero la API permite cambiar esos campos. El nuevo registro baja a
  la sucursal y el anterior nunca recibe tombstone. El borrado físico de un rol
  custom produce el mismo efecto.
- La unicidad declarada para asignaciones globales no funciona cuando
  `sucursal=NULL`. Dos filas pueden coexistir y revocar una deja la otra activa.
- `ADMIN` y `SYSADMIN` aprueban cualquier string de permiso, incluso códigos
  inexistentes y `suscripciones.administrar`, descrito como permiso de operador
  SaaS. Un admin tenant puede alcanzar controles comerciales que el modelo de
  permisos presenta como externos al negocio.
- `Usuario.activo` no participa en el motor y los cambios de `rol`, `activo`,
  `negocio` o `is_superuser` no invalidan el caché. Una sesión local ya abierta
  puede seguir autorizada después de desactivar al usuario; un downgrade de
  ADMIN a CAJERA puede conservar el catálogo precargado por cinco minutos.
- Las altas, bajas y ediciones de roles/asignaciones no producen una auditoría
  durable, aunque el modelo de auditoría ya define acciones de asignación y
  revocación de permisos.

Se documentan **21 hallazgos**:

| Prioridad | Cantidad | Criterio |
| --- | ---: | --- |
| P1 | 10 | Puede mezclar permisos entre tenants, mantener privilegios revocados, romper el scope de sucursal o permitir escalada administrativa. |
| P2 | 8 | Debilita consistencia transaccional, sincronización, catálogo, bootstrap, trazabilidad u operación segura. |
| P3 | 3 | Deuda de contratos internos, migraciones, diagnóstico y cobertura preventiva. |

La validación seleccionada terminó con **49/49 pruebas existentes aprobadas**.
Una batería adversarial temporal terminó con **7/7 reproducciones confirmadas**;
el archivo temporal se eliminó del workspace. También pasaron `manage.py check`
y `makemigrations permisos --check --dry-run`.

## Alcance

Se inspeccionaron completamente:

- `apps/permisos/models.py`
- `apps/permisos/engine.py`
- `apps/permisos/signals.py`
- `apps/permisos/catalogo.py`
- `apps/permisos/seed.py`
- `apps/permisos/decorators.py`
- `apps/permisos/templatetags/permisos.py`
- `apps/permisos/admin.py`
- `apps/permisos/management/commands/`
- `apps/permisos/migrations/`
- `apps/permisos/tests/`

También se trazaron las fronteras relevantes en:

- `apps/api/permissions.py`
- `apps/api/views/permisos.py`
- `apps/api/serializers/permisos.py`
- `apps/api/views/sync.py`
- `apps/api/views/suscripciones.py`
- `apps/api/tests/test_rbac_admin.py`
- `apps/api/tests/test_sync_roles.py`
- `apps/sync/engine.py`
- `apps/sync/tests/test_pull_roles.py`
- `apps/negocios/utils.py`
- `apps/usuarios/models.py` y `views.py`
- `apps/tenancy/authentication.py`, `router.py` y `middleware.py`
- `apps/auditoria/models.py` y `middleware.py`
- gates consumidores en ventas, inventario, caja, CxC, reportes, configuración
  e impresión
- `config/settings.py`, `config/settings_cloud.py` y `Dockerfile`
- `infra/azure/environments/prod/`
- `docs/RBAC_PERMISOS.md`, `docs/TENANCY_DB_PER_TENANT.md` y documentación de
  despliegue

El núcleo de `apps/permisos` suma **825 líneas Python**, sin contar migraciones.
Sus cinco archivos de pruebas suman **291 líneas** y contienen **21 casos**. El
catálogo declara **31 permisos**.

La auditoría comenzó y cerró en `3f22385`. `apps/permisos` no tenía cambios
locales al empezar y no fue modificado. Durante la revisión ya existían cambios
externos sin commit en inventario y en `apps/api/views/sync.py`; el diff visible
de sync afectaba el handler de compras, no los endpoints ni el pull de RBAC
citados aquí. No se revirtieron ni alteraron esos trabajos.

## Hallazgos P1

### PER-001 - El caché de permisos no incluye la identidad del tenant o de la base

- Severidad: crítica.
- Tipo: aislamiento DB-per-tenant / autorización / fuga de privilegios.
- Evidencia:
  - La clave es
    `permisos_usuario:v<version>:<usuario_id>:<sucursal_id>`
    (`apps/permisos/engine.py:30-53`).
  - `permisos_de_usuario()` construye esa clave únicamente con PK de usuario y
    sucursal (`apps/permisos/engine.py:75-92`).
  - No incorpora el alias activo, `tenant_key`, negocio, rol ni otra identidad
    estable global.
  - `TenantJWTAuthentication` activa una base tenant distinta antes de cargar al
    usuario (`apps/tenancy/authentication.py:68-106`), pero no cambia el
    namespace del caché.
- Reproducción validada:
  - Dos instancias de usuario con `pk=777`, representando dos bases tenant, se
    resolvieron con resultados diferentes esperados.
  - Tras cachear `{'clientes.ver'}` para la primera, la segunda recibió el mismo
    set y `_resolver_permisos` no volvió a ejecutarse.
- Impacto:
  - Los PK se reinician por base tenant. Un usuario de Royal Plast y otro de SK
    Performance pueden tener el mismo `id`, igual que sus sucursales.
  - Dentro del mismo worker, el último set cacheado para esa combinación puede
    autorizar o denegar acciones en el tenant equivocado durante 300 segundos.
- Recomendación:
  - Incluir en la clave un identificador técnico inmutable del contexto actual:
    `tenant_key` o alias de base validado, además de usuario y sucursal.
  - No derivarlo de `Negocio.pk`, porque ese PK también es local a cada base.
  - Hacer que la ausencia de contexto en modo tenancy sea un error, no el
    namespace `default` implícito.
- Prueba de aceptación sugerida:
  - Dos bases tenant con usuarios y sucursales de PK idénticos deben mantener
    sets distintos al alternar requests repetidamente en un mismo proceso.

### PER-002 - La invalidación local no es coherente con los tres workers de producción

- Severidad: crítica para revocaciones; alta para altas de permiso.
- Tipo: caché distribuido / persistencia temporal de privilegios.
- Evidencia:
  - El motor reconoce que `LocMemCache` solo es consistente con un worker y
    admite una demora máxima de 300 segundos en otros procesos
    (`apps/permisos/engine.py:16-31`).
  - `config/settings_cloud.py` hereda la configuración base y no declara un
    backend compartido (`config/settings_cloud.py:1-14`). Django usa entonces
    el caché local por proceso.
  - La imagen productiva arranca Gunicorn con `--workers 3`
    (`Dockerfile:37`).
  - Limitar Azure a una réplica (`infra/azure/environments/prod/
    terraform.tfvars.example:52-53`) no reduce esos tres procesos a uno.
  - `docs/RBAC_PERMISOS.md:73-74` y el comentario del motor describen Azure como
    single-worker, en contradicción con la imagen desplegable.
- Impacto:
  - Revocar un rol o permiso en el worker A no cambia la versión almacenada en
    B y C.
  - Requests posteriores pueden continuar autorizados hasta expirar el caché.
  - Escalar réplicas amplía el número de caches divergentes.
- Recomendación:
  - Usar Redis u otro backend compartido, con namespace por tenant.
  - Mientras se despliega, considerar un comportamiento sin caché para gates
    sensibles o reducir conscientemente el TTL; esto es contención, no solución
    al aislamiento de PER-001.
- Prueba de aceptación sugerida:
  - Precargar un permiso en varios workers, revocarlo por uno y comprobar que
    todos deniegan en el siguiente request, sin esperar el TTL.

### PER-003 - Omitir la sucursal convierte permisos locales en permisos globales

- Severidad: alta.
- Tipo: scope de sucursal / escalada horizontal.
- Evidencia:
  - `_resolver_permisos()` filtra asignaciones globales + la sucursal pedida
    solamente cuando `sucursal_id is not None`
    (`apps/permisos/engine.py:96-119`).
  - Con `None`, no agrega ningún filtro y une asignaciones de todas las
    sucursales.
  - Los dos decoradores locales llaman `user.tiene_permiso(codigo)` sin
    sucursal (`apps/permisos/decorators.py:20-59`).
  - El filtro `puede` hace lo mismo (`apps/permisos/templatetags/permisos.py:
    19-31`).
  - DRF extrae sucursal solo de `request.sucursal` o `request.auth.sucursal`
    (`apps/api/permissions.py:86-94` y `:108-116`). Un JWT humano del portal no
    trae normalmente token de sucursal.
- Reproducción validada:
  - Un usuario recibió `ventas.anular` únicamente en la sucursal A.
  - `usuario.tiene_permiso('ventas.anular')`, sin scope, devolvió `True`.
- Impacto:
  - En una instalación que contiene más de una sucursal, un rol otorgado para A
    habilita gates locales de B.
  - En el portal, un permiso acotado puede habilitar endpoints consolidados del
    negocio completo.
  - El significado de `None` no es conservador y resulta fácil de usar por
    accidente, precisamente como lo hacen las APIs públicas del módulo.
- Recomendación:
  - Definir un contrato explícito: `None` debe consultar solo asignaciones
    globales o fallar si el consumidor requiere scope.
  - Resolver la sucursal actual en middleware/contexto y pasarla en decoradores,
    templates y servicios.
  - Crear una operación separada y nombrada para “unión de todo el negocio” si
    existe un caso legítimo.
- Prueba de aceptación sugerida:
  - Un permiso solo de A debe ser falso sin scope y en B; verdadero únicamente
    en A. Los mismos casos deben cubrir HTML, JSON, templates y DRF.

### PER-004 - El modelo acepta asignaciones entre negocios y el motor las honra

- Severidad: alta.
- Tipo: integridad multi-tenant / autorización.
- Evidencia:
  - `AsignacionRol` declara FKs independientes a usuario, rol y sucursal, pero
    no implementa `clean()` ni constraints que relacionen sus negocios
    (`apps/permisos/models.py:93-125`).
  - Tampoco exige que negocio, usuario o sucursal estén activos.
  - El resolver filtra solo usuario, asignación activa y rol activo
    (`apps/permisos/engine.py:100-109`).
  - No verifica `rol.negocio == usuario.negocio`, ni
    `sucursal.negocio == rol.negocio`.
  - El admin de Django expone los tres modelos sin validaciones propias
    (`apps/permisos/admin.py:6-29`).
- Reproducción validada:
  - Se construyó una asignación con usuario del negocio B y rol del A.
  - `full_clean()` la aceptó, se guardó y el usuario obtuvo
    `permisos.administrar` del negocio A.
- Impacto:
  - Una importación, comando, admin, shell, carrera o bug de API puede crear una
    fila que el motor convierte inmediatamente en privilegio efectivo.
  - El endpoint de sync filtra por negocio del rol, no del usuario
    (`apps/api/views/sync.py:330-349`), por lo que una fila malformada también
    puede propagarse a un usuario homónimo local.
- Recomendación:
  - Validar el mismo negocio en el modelo/servicio autoritativo, no solo en una
    vista.
  - Limpiar filas existentes antes de agregar constraints viables.
  - Incluir estados activos y negocio en la consulta defensiva del motor.
- Prueba de aceptación sugerida:
  - Cada combinación cross-negocio debe fallar tanto por API como por
    `full_clean()`/servicio, admin, seed y sync.

### PER-005 - Un usuario sin negocio puede seleccionar cualquier negocio y escalar RBAC

- Severidad: crítica en una base multi-negocio; alta como defecto de contrato en
  DB-per-tenant.
- Tipo: escalada horizontal / resolución de tenant.
- Evidencia:
  - `negocio_actual()` documenta que el query param es para SYSADMIN/superuser,
    pero comprueba únicamente que `user.negocio_id` sea nulo
    (`apps/negocios/utils.py:15-36`).
  - La API de asignaciones acepta que el usuario pertenezca al negocio actual o
    tenga `negocio=None` (`apps/api/views/permisos.py:150-166`).
  - Al crear la asignación no vincula ese usuario al negocio
    (`apps/api/views/permisos.py:111-138`).
  - El gate de administración consulta el motor antes de resolver el queryset
    del negocio (`apps/api/views/permisos.py:41-42`).
- Reproducción validada de extremo a extremo:
  1. Un ADMIN del negocio A asignó a un usuario `negocio=NULL` un rol de A con
     `permisos.administrar`; la API respondió 201.
  2. El usuario permaneció con `negocio=NULL`.
  3. Ese usuario pidió `/api/v1/permisos/roles/?negocio=<B>`.
  4. La API respondió 200 y devolvió el rol privado del negocio B.
- Impacto:
  - En modo row-level/local con varios negocios, el usuario puede listar,
    crear, editar y asignar roles en cualquier negocio alcanzable.
  - DB-per-tenant reduce el conjunto visible a la base activa, pero no corrige
    el contrato y deja vulnerable cualquier operación monolítica, importación o
    entorno de soporte.
- Recomendación:
  - Exigir una identidad global comprobable (`is_global_identity`, superuser o
    capacidad explícita del control plane) antes de aceptar `?negocio=`.
  - No permitir asignaciones tenant a usuarios sin negocio, o vincularlos de
    forma transaccional mediante un flujo de alta explícito.
- Prueba de aceptación sugerida:
  - Un usuario ordinario con `negocio=NULL`, aunque tenga una asignación
    malformada, debe recibir 403 al elegir cualquier negocio.

### PER-006 - Cambiar la identidad natural de una asignación no revoca la anterior en sucursales

- Severidad: crítica.
- Tipo: sincronización / revocación persistente.
- Evidencia:
  - El serializer permite escribir `usuario`, `rol` y `sucursal`
    (`apps/api/serializers/permisos.py:47-64`).
  - `perform_update()` valida los valores enviados y guarda el mismo registro
    con la nueva terna (`apps/api/views/permisos.py:140-142`).
  - El payload cloud → local no envía un ID estable de asignación; usa
    `usuario_username`, `rol_slug` y `sucursal_codigo`
    (`apps/api/views/sync.py:339-349`).
  - El pull hace `update_or_create()` por esa terna
    (`apps/sync/engine.py:1006-1059`).
  - No existe una fila/tombstone para la identidad natural anterior.
- Impacto:
  - Cambiar el usuario, el rol o mover una asignación de sucursal crea la nueva
    relación local, pero deja activa la anterior indefinidamente.
  - El portal puede mostrar el estado correcto mientras el POS conserva un
    permiso que aparentemente fue movido o retirado.
- Recomendación:
  - Dar a cada asignación una identidad cloud inmutable que viaje al local.
  - Tratar cambios de la terna como revoke-old + create-new dentro de una sola
    transacción, conservando ambos eventos/tombstones.
  - Como contención, hacer inmutables esos tres campos y exigir soft-delete +
    alta nueva.
- Prueba de aceptación sugerida:
  - Tras mover A→B, el siguiente ciclo debe desactivar A y activar B, incluso si
    la sucursal estuvo offline durante el cambio.

### PER-007 - El borrado físico de roles custom no se propaga a los POS locales

- Severidad: crítica.
- Tipo: sincronización / baja sin tombstone.
- Evidencia:
  - La API protege roles de sistema, pero ejecuta `instance.delete()` para un rol
    custom (`apps/api/views/permisos.py:87-93`).
  - `Rol.negocio` y `AsignacionRol.rol` usan cascade
    (`apps/permisos/models.py:52-57` y `:102-106`), por lo que desaparecen rol y
    asignaciones cloud.
  - El endpoint de roles devuelve solo filas existentes, incrementalmente por
    `fecha_modificacion` (`apps/api/views/sync.py:266-303`).
  - `_pull_roles()` solo hace upsert y nunca reconcilia ausencias
    (`apps/sync/engine.py:939-985`).
  - El endpoint de asignaciones tampoco puede emitir las filas cascaded que ya
    no existen.
- Impacto:
  - El rol y sus asignaciones quedan activos en cada sucursal que los había
    sincronizado.
  - El privilegio puede persistir indefinidamente y sobrevivir reinicios.
- Recomendación:
  - Usar soft-delete versionado para roles o un ledger de tombstones con ID
    estable.
  - Añadir reconciliación completa periódica además del incremental.
- Prueba de aceptación sugerida:
  - Crear y sincronizar un rol, borrarlo con la sucursal offline, reconectar y
    verificar que rol/asignaciones locales queden inactivos.

### PER-008 - La unicidad global con `sucursal=NULL` no protege la revocación

- Severidad: alta.
- Tipo: integridad / concurrencia / baja incompleta.
- Evidencia:
  - La única protección es `unique_together = (usuario, rol, sucursal)`
    (`apps/permisos/models.py:121-125`).
  - PostgreSQL y SQLite permiten múltiples filas cuando la columna incluida es
    `NULL`.
  - La API hace búsqueda `.first()` y luego create, sin lock ni transacción
    (`apps/api/views/permisos.py:111-138`).
  - `bootstrap()` usa `get_or_create(..., sucursal=None)`
    (`apps/permisos/seed.py:101-114`) y el pull usa `update_or_create()`
    (`apps/sync/engine.py:1054-1059`); ninguno corrige duplicados preexistentes.
  - La documentación reconoce la deuda, pero la llama mitigada a nivel app
    (`docs/RBAC_PERMISOS.md:313-315`). La carrera y los otros escritores dejan
    incompleta esa mitigación.
- Reproducción validada:
  - Se crearon dos asignaciones globales idénticas sin error.
  - El soft-delete API de una respondió 204; la segunda siguió activa y el
    usuario conservó `ventas.anular`.
- Impacto:
  - Una revocación visible como exitosa puede no revocar nada efectivamente.
  - `update_or_create()` puede lanzar `MultipleObjectsReturned` y congelar el
    cursor de sync.
- Recomendación:
  - Agregar una constraint efectiva para NULL (`nulls_distinct=False` en la
    versión soportada de PostgreSQL, o constraints parciales separadas).
  - Deduplicar antes de migrar y definir cuál fila/estado gana.
  - Serializar el reactivate-or-create con transacción y lock.
- Prueba de aceptación sugerida:
  - Dos altas concurrentes globales deben terminar en una sola fila; una baja
    debe dejar cero asignaciones efectivas.

### PER-009 - El acceso total legacy ignora catálogo, tenant comercial y errores de código

- Severidad: alta.
- Tipo: mínimo privilegio / separación operador-tenant / fail-open.
- Evidencia:
  - `es_acceso_total()` incluye superuser, `SYSADMIN` y `ADMIN`
    (`apps/permisos/engine.py:56-72`).
  - `tiene_permiso()` retorna `True` antes de comprobar catálogo o asignaciones
    (`apps/permisos/engine.py:122-133`).
  - La propia prueba exige que ADMIN apruebe `codigo.inexistente`
    (`apps/permisos/tests/test_engine.py:48-54`).
  - El catálogo describe `suscripciones.administrar` como capacidad del
    operador SaaS (`apps/permisos/catalogo.py:77-79`).
  - Sus endpoints declaran que ADMIN obtiene esa capacidad por acceso total y
    exponen querysets de planes/suscripciones/overrides
    (`apps/api/views/suscripciones.py:1-13` y `:30-61`).
- Impacto:
  - Un typo en el permiso de un endpoint nuevo no protege frente a ADMIN.
  - El rol configurable “Administrador” es inerte para usuarios legacy ADMIN:
    quitarle permisos en el portal no los restringe.
  - Un administrador del tenant puede alcanzar controles descritos como
    exclusivos del operador y, en una BD tenant, modificar su propia
    suscripción/entitlements.
- Recomendación:
  - Separar claramente superusuario global de administrador tenant.
  - Validar que el código exista; los códigos desconocidos deben denegar y
    alertar.
  - Migrar ADMIN a asignaciones explícitas antes de retirar el bypass, con una
    comprobación previa que evite lockout.
  - Gatear operaciones comerciales mediante principal global/control plane, no
    mediante un permiso que todo ADMIN aprueba automáticamente.
- Prueba de aceptación sugerida:
  - ADMIN tenant debe poder perder un permiso explícitamente y nunca aprobar un
    código inexistente ni capacidades de operador SaaS.

### PER-010 - Desactivar o degradar un usuario no revoca de inmediato sus permisos

- Severidad: crítica para usuario desactivado con sesión viva; alta para cambios
  de rol.
- Tipo: revocación / caché / autenticación local.
- Evidencia:
  - El motor solo comprueba `is_authenticated`, no `Usuario.activo`
    (`apps/permisos/engine.py:75-90` y `:122-133`).
  - Las señales observan Rol, AsignacionRol, Permiso y M2M, pero no Usuario,
    Negocio ni Sucursal (`apps/permisos/signals.py:10-27`).
  - `Usuario` define `activo`, pero no redefine `is_active`; hereda el valor de
    clase `True` de `AbstractBaseUser` (`apps/usuarios/models.py:74-98`).
  - El login local revisa `activo` solo al iniciar sesión
    (`apps/usuarios/views.py:11-32`). Una sesión ya emitida vuelve a cargar al
    usuario mediante el backend estándar, que observa `is_active`, no `activo`.
  - En tenancy cloud sí se filtra `activo=True` al autenticar cada request
    (`apps/tenancy/authentication.py:95-102`), pero eso no corrige POS local ni
    el caché del motor.
- Reproducción validada:
  - Se precargó con `permisos_de_usuario()` el catálogo de un ADMIN.
  - Se cambió su rol legacy a CAJERA y se guardó.
  - `tiene_permiso('permisos.administrar')` siguió devolviendo `True` desde el
    caché, sin asignaciones, porque ninguna señal cambió la versión.
- Impacto:
  - El downgrade conserva privilegios hasta cinco minutos por worker.
  - Un usuario local desactivado puede continuar con la sesión existente; el
    resolver seguiría honrando sus asignaciones incluso después de expirar el
    caché.
  - Cambios de `negocio`, `is_superuser` o estado de sucursal/negocio tienen el
    mismo problema de invalidación o de falta de filtro.
- Recomendación:
  - Unificar `activo` con el contrato `is_active` de Django.
  - Denegar explícitamente usuarios, negocios y sucursales inactivos en la
    frontera adecuada.
  - Invalidar/rotar sesiones y cache al cambiar campos de autorización.
- Prueba de aceptación sugerida:
  - Una sesión abierta debe perder acceso en el primer request posterior a la
    desactivación o downgrade, en cada worker y tenant.

## Hallazgos P2

### PER-011 - Las señales invalidan antes del commit y permiten recachear estado viejo

- Severidad: media-alta; alta en una revocación concurrente.
- Tipo: consistencia transaccional / caché.
- Evidencia:
  - `post_save`, `post_delete` y `m2m_changed` llaman inmediatamente a
    `invalidar_cache()` (`apps/permisos/signals.py:17-27`).
  - No usan `transaction.on_commit()`.
  - La versión cambia aunque la escritura todavía no sea visible para otras
    transacciones.
- Escenario:
  1. La transacción A revoca una asignación; la señal incrementa la versión.
  2. Antes del commit, B usa la versión nueva, pero todavía lee la asignación
     anterior y la cachea.
  3. A hace commit; no queda otra invalidación.
  4. B conserva el permiso anterior durante el TTL.
- Recomendación:
  - Programar la invalidación con `transaction.on_commit()` sobre el alias de la
    escritura.
  - Mantener una estrategia segura para escrituras sin transacción explícita.
- Prueba de aceptación sugerida:
  - Una prueba con dos conexiones y barreras debe demostrar que ninguna lectura
    posterior al commit reutiliza estado pre-commit.

### PER-012 - Cambiar el M2M de un rol no siempre avanza su cursor de sincronización

- Severidad: media-alta.
- Tipo: sincronización incremental / marca de agua.
- Evidencia:
  - `Rol.fecha_modificacion` es `auto_now` y cambia solamente al guardar el rol
    (`apps/permisos/models.py:79-80`).
  - El endpoint incremental filtra roles por ese campo
    (`apps/api/views/sync.py:231-263` y `:285-299`).
  - `sync_permisos` ejecuta `rol.permisos.set(todos)` sin `rol.save()`
    (`apps/permisos/management/commands/sync_permisos.py:19-24`).
  - Otros usos directos de `add/set/remove` tampoco actualizan el timestamp;
    la señal solo invalida caché.
- Reproducción validada:
  - Se agregó un permiso por M2M, se refrescó el rol y
    `fecha_modificacion` permaneció exactamente igual.
- Impacto:
  - Un POS cuyo cursor ya superó esa fecha no recibe el nuevo set de permisos.
  - Cloud y sucursal pueden mostrar/ejecutar configuraciones distintas sin error.
- Recomendación:
  - Actualizar una versión/timestamp del agregado dentro del mismo servicio que
    modifica el M2M.
  - No depender de que cada consumidor recuerde llamar `save()`.
- Prueba de aceptación sugerida:
  - Todo add/remove/clear/set debe producir una versión posterior y aparecer en
    `GET /sync/roles/?desde=<valor anterior>`.

### PER-013 - El catálogo no coincide completamente con el enforcement real

- Severidad: media-alta.
- Tipo: contrato RBAC / cobertura de gates.
- Evidencia:
  - `ventas.reimprimir` existe y se entrega por defecto al Cajero
    (`apps/permisos/catalogo.py:51-55` y `:83-100`).
  - Las vistas reales de reimpresión declaran en comentarios un código diferente
    (`ventas.reimprimir_ticket`), pero heredan solo `LoginRequiredMixin`; no
    consultan ningún permiso (`utils/impresoras/views.py:80-120` y `:257-317`).
  - El enlace global de reimpresión tampoco usa `|puede:`
    (`templates/base.html:93-94`).
  - `ventas.anular` pasa el gate de la vista, pero el servicio vuelve a exigir
    rol legacy ADMIN/SYSADMIN (`apps/ventas/services/anulaciones_service.py:
    185-192`). Un rol custom con el permiso no puede completar la acción.
  - `configuracion.administrar` está en el catálogo
    (`apps/permisos/catalogo.py:47-49`), pero no tiene consumidor productivo.
- Impacto:
  - Quitar `ventas.reimprimir` a un rol no restringe la reimpresión.
  - Asignar `ventas.anular` a un rol custom produce una UI/API que parece
    autorizar y un servicio que rechaza.
  - El catálogo no es una fuente de verdad verificable y puede dar una falsa
    sensación de mínimo privilegio.
- Recomendación:
  - Mantener una matriz catálogo → gate server-side → gate UI → prueba.
  - Usar exactamente el mismo código en vista y servicio; el servicio debe ser
    la autoridad final para operaciones sensibles.
  - Marcar permisos reservados/no implementados o retirarlos hasta que exista el
    consumidor.
- Prueba de aceptación sugerida:
  - Por cada código, una prueba positiva y negativa debe llegar hasta el servicio
    que muta estado; ningún permiso debe quedar huérfano o meramente visual.

### PER-014 - Las mutaciones de RBAC no dejan una auditoría durable

- Severidad: media-alta.
- Tipo: trazabilidad de seguridad / no repudio.
- Evidencia:
  - Crear, editar y borrar roles/asignaciones se implementa directamente en los
    viewsets (`apps/api/views/permisos.py:64-166`).
  - No hay llamadas a `Auditoria.registrar()` en `apps/permisos` ni en esos
    endpoints.
  - El modelo ya define `PERMISO_ASIGNADO` y `PERMISO_REVOCADO`
    (`apps/auditoria/models.py:78-84`).
  - El middleware no incluye rutas `/api/v1/permisos/` en `URLS_CRITICAS` y, con
    tenancy activo, omite expresamente toda ruta `/api/`
    (`apps/auditoria/middleware.py:22-49` y `:69-120`).
- Impacto:
  - No se puede reconstruir quién otorgó, revocó o cambió una capacidad, qué
    valor había antes ni a qué tenant/sucursal pertenecía.
  - Una escalada o error administrativo puede quedar indistinguible de un estado
    legítimo.
- Recomendación:
  - Auditar explícitamente en el servicio transaccional de RBAC, después de
    validar y junto al cambio.
  - Conservar actor global real, usuario objetivo, tenant, sucursal, diff y
    resultado.
- Prueba de aceptación sugerida:
  - Cada create/update/soft-delete/reactivate de rol o asignación debe generar
    exactamente un evento durable con antes/después y actor correcto.

### PER-015 - `sync_permisos` mezcla catálogo, política de rol y sincronización unidireccional

- Severidad: media.
- Tipo: operación / drift de configuración.
- Evidencia:
  - `sembrar_catalogo()` solo hace upsert de las 31 entradas
    (`apps/permisos/catalogo.py:108-126`). No desactiva ni elimina códigos que
    salen del código.
  - El comando vuelve a asignar **todos** los permisos a todo rol de sistema con
    slug `administrador` (`apps/permisos/management/commands/sync_permisos.py:
    16-30`).
  - `crear_roles_default()` declara que reejecutar el bootstrap no pisa
    personalizaciones (`apps/permisos/seed.py:25-47`), mientras el comando sí
    pisa el Administrador.
  - No hay transacción que englobe catálogo y actualización de todos los roles.
- Impacto:
  - Un permiso retirado sigue visible/asignable en la BD y puede continuar en
    roles.
  - Una personalización deliberada del rol Administrador se pierde al desplegar.
  - Un fallo intermedio deja tenants/roles en estados diferentes.
- Recomendación:
  - Separar “sincronizar definición del catálogo” de “aplicar plantilla de rol”.
  - Versionar/deprecar códigos y exigir un plan de migración para retirarlos.
  - Ejecutar cambios por tenant de manera transaccional e informar resultados.
- Prueba de aceptación sugerida:
  - Retirar/deprecar un código debe converger de forma explícita; sincronizar el
    catálogo no debe modificar roles personalizados sin una opción declarada.

### PER-016 - El bootstrap no es completamente idempotente ni seguro en bases multi-negocio

- Severidad: media-alta.
- Tipo: seed / onboarding / integridad.
- Evidencia:
  - Elige silenciosamente el primer `Negocio` por ID
    (`apps/permisos/seed.py:85-94`).
  - Asigna a ese negocio todas las sucursales y usuarios huérfanos
    (`apps/permisos/seed.py:96-97`).
  - Recorre todos sus usuarios y usa `get_or_create()` global
    (`apps/permisos/seed.py:99-114`). Si la fila existe inactiva, `defaults` no
    la reactiva.
  - Slug y alta usan check-then-create sin protección de carrera
    (`apps/permisos/seed.py:15-22`).
  - Catálogo, negocio, FKs, roles y asignaciones no están dentro de una
    transacción común.
  - `_nombre_default()` silencia cualquier excepción y cae a `Mi Negocio`
    (`apps/permisos/management/commands/bootstrap_negocio.py:54-65`).
- Impacto:
  - Reejecutar el supuesto bootstrap idempotente no restaura una asignación
    default previamente desactivada.
  - En una base con varios negocios, los huérfanos pueden vincularse al tenant
    equivocado.
  - Un fallo intermedio deja un bootstrap parcial que la siguiente ejecución no
    necesariamente repara.
- Recomendación:
  - Exigir negocio/tenant explícito cuando exista más de uno.
  - Definir si una fila inactiva debe permanecer revocada o reactivarse y reflejar
    esa semántica en el comando y su nombre.
  - Añadir dry-run, precondiciones, transacción y resumen de cambios.
- Prueba de aceptación sugerida:
  - Cubrir segunda ejecución, fila inactiva, dos negocios, error inyectado a
    mitad y dos ejecuciones concurrentes.

### PER-017 - Las escrituras API de rol/asignación no son atómicas ante concurrencia

- Severidad: media-alta.
- Tipo: carrera / errores 500 / consistencia.
- Evidencia:
  - `_slug_rol_unico()` consulta disponibilidad en un bucle y crea después
    (`apps/api/views/permisos.py:45-53` y `:76-85`). Dos requests pueden elegir
    el mismo slug; uno chocará con la constraint.
  - El reactivate-or-create consulta `.first()` y luego guarda/crea sin
    `atomic()` ni `select_for_update()` (`apps/api/views/permisos.py:111-138`).
  - El serializer desactiva todo validador de unicidad
    (`apps/api/serializers/permisos.py:54-64`).
  - Para scope no nulo, una carrera termina en `IntegrityError`; para NULL puede
    terminar en duplicados por PER-008.
  - PATCH puede cambiar la terna hacia otra ya existente y la API no transforma
    el conflicto en una respuesta de dominio.
- Impacto:
  - Dos clics, reintentos de red o workers concurrentes pueden generar 500,
    duplicados o una reactivación diferente a la esperada.
- Recomendación:
  - Encapsular el comando de dominio en transacción, apoyarse en constraints
    correctas y capturar conflictos como 409/resultado idempotente.
- Prueba de aceptación sugerida:
  - Barreras concurrentes para alta global, alta de sucursal, creación de slug y
    reactivación deben converger sin 500 ni duplicados.

### PER-018 - Admin y comandos no comparten las garantías de la API ni el contexto tenant

- Severidad: media-alta.
- Tipo: caminos administrativos / multitenancy.
- Evidencia:
  - Django admin permite editar/borrar `Permiso`, cambiar `negocio`, `slug`,
    `es_sistema` y `activo` de Rol, y crear cualquier AsignacionRol
    (`apps/permisos/admin.py:6-29`).
  - La protección contra borrar roles de sistema vive solo en
    `RolViewSet.perform_destroy()` (`apps/api/views/permisos.py:87-93`), no en el
    modelo/admin.
  - `bootstrap_negocio` y `sync_permisos` heredan directamente de `BaseCommand` y
    no reciben `--tenant` (`apps/permisos/management/commands/
    bootstrap_negocio.py:21-43` y `sync_permisos.py:13-24`).
  - En tenancy activo, el router exige contexto para modelos de negocio; el
    onboarding moderno llama los helpers dentro de `tenant_context`, pero los
    comandos independientes y la documentación genérica siguen pareciendo
    seguros por sí solos.
- Impacto:
  - Un operador privilegiado puede romper invariantes o protecciones que el
    portal sí aplica.
  - Ejecutar un comando en cloud sin contexto falla o actúa sobre un destino
    distinto al supuesto, según settings y ruta operativa.
- Recomendación:
  - Llevar invariantes al modelo/servicio y restringir el admin a acciones
    seguras y scope tenant.
  - Usar el mixin/comando tenant o fallar explícitamente con instrucciones
    correctas en cloud.
- Prueba de aceptación sugerida:
  - Admin, API, seed y comando deben rechazar las mismas filas inválidas; cada
    comando cloud debe exigir y reportar tenant/alias.

## Hallazgos P3

### PER-019 - Las data migrations dependen de código vivo y no tienen reversa semántica

- Severidad: baja-media.
- Tipo: reproducibilidad de migraciones.
- Evidencia:
  - `0002_seed_rbac` importa en tiempo de ejecución
    `apps.permisos.seed` y `apps.permisos.catalogo`
    (`apps/permisos/migrations/0002_seed_rbac.py:17-56`).
  - Esos módulos ya evolucionaron con nuevos permisos/defaults, por lo que
    recrear una base hoy no ejecuta la misma migración histórica de 2026-06-08.
  - `0004_cxc_permisos_granulares` vuelve a importar el catálogo vivo y define
    una reversa vacía (`apps/permisos/migrations/
    0004_cxc_permisos_granulares.py:19-37`).
- Impacto:
  - Dos instalaciones construidas desde cero en fechas distintas pueden recibir
    seeds históricos diferentes aunque tengan la misma cadena de migraciones.
  - Revertir no revierte permisos ni asignaciones agregadas.
- Recomendación:
  - Congelar los datos/helpers necesarios dentro de cada migración o usar
    constantes históricas inmutables.
  - Marcar claramente la operación irreversible o implementar una reversa
    segura y acotada.

### PER-020 - Hay dos fallbacks que ocultan errores de configuración

- Severidad: baja-media.
- Tipo: fail-open / observabilidad.
- Evidencia:
  - `TienePermiso.has_permission()` concede acceso si no encuentra un código
    (`apps/api/permissions.py:75-94`). La factory actual siempre lo fija, pero
    usar directamente la clase o olvidar `required_permission` produce allow.
  - El filtro de template captura cualquier excepción del motor y devuelve
    `False` sin logging (`apps/permisos/templatetags/permisos.py:19-31`).
- Impacto:
  - Una vista mal configurada puede quedar abierta a todo usuario autenticado.
  - Una caída de base/cache/routing desaparece como “no tiene permiso”,
    degradando diagnóstico y ocultando incidentes.
- Recomendación:
  - Hacer default-deny cuando falte el código y fallar en checks/tests de
    configuración.
  - En UI puede conservarse el deny, pero debe emitirse logging/telemetría sin
    filtrar secretos.

### PER-021 - La suite y la documentación validan el camino feliz, no las fronteras reales

- Severidad: media como deuda preventiva.
- Tipo: cobertura / documentación operativa.
- Evidencia:
  - Las 21 pruebas propias cubren default-deny, acceso total, roles por negocio,
    scope cuando se pasa sucursal, invalidación M2M, seed básico, decoradores y
    templates.
  - No cubren dos bases con PK repetidos, más de un worker, `sucursal=None`,
    invariantes cross-negocio, usuario inactivo, cambio de rol legacy,
    duplicados NULL, carreras, tombstones ni comandos completos.
  - `test_acceso_total_no_depende_del_catalogo` consagra que un código
    inexistente autoriza a ADMIN (`apps/permisos/tests/test_engine.py:48-54`).
  - El test de scope verifica A y B cuando el parámetro está presente, pero no
    el caso omitido (`apps/permisos/tests/test_engine.py:94-107`).
  - `docs/RBAC_PERMISOS.md:187-188` afirma aislamiento cross-tenant en los
    endpoints admin, contradicho por PER-005.
  - El documento y el motor todavía describen Azure como single-worker, y el
    docstring de roles sync dice que las asignaciones no sincronizan aunque el
    endpoint existe (`apps/api/views/sync.py:268-275`).
- Impacto:
  - Las suites verdes no detectan los modos de falla que afectan revocación y
    aislamiento.
  - La documentación puede llevar a operar el caché o los endpoints con una
    garantía inexistente.
- Recomendación:
  - Convertir las reproducciones de esta auditoría en regresiones permanentes
    al corregir cada hallazgo.
  - Añadir pruebas reales multi-DB y multiproceso/Redis, no solo mocks de PK.
  - Actualizar el documento vivo con semántica exacta de `None`, workers,
    tombstones y usuario global.

## Controles que ya están bien encaminados

- `Permiso.codigo` es único y `Rol.slug` es único dentro de cada negocio
  (`apps/permisos/models.py:19-43` y `:82-87`).
- Los usuarios ordinarios sin asignación reciben un set vacío y el motor niega
  por defecto (`apps/permisos/engine.py:75-119`).
- Una asignación explícitamente inactiva y un rol inactivo no otorgan permisos.
- Cuando el consumidor sí pasa una sucursal, el resolver combina correctamente
  asignaciones globales + esa sucursal y excluye las demás.
- Las señales cubren save/delete de Rol, AsignacionRol, Permiso y cambios M2M; en
  un solo proceso y fuera de carreras transaccionales, el caché se refresca.
- Para usuarios ya vinculados a un negocio, los querysets RBAC de roles,
  asignaciones, usuarios y sucursales sí filtran por ese negocio
  (`apps/api/views/permisos.py:64-74`, `:96-109` y `:169-196`).
- La API bloquea el borrado de roles de sistema por su camino oficial.
- El soft-delete de asignaciones conserva `fecha_modificacion`, una base correcta
  para propagar bajas cuando la identidad no cambia
  (`apps/api/views/permisos.py:144-148`).
- Los endpoints de sync usan el negocio de la sucursal autenticada y separan
  asignaciones globales de las dirigidas a otra sucursal
  (`apps/api/views/sync.py:310-353`).
- `_pull_roles()` detecta códigos que la versión local no conoce y difiere el
  item en vez de guardar un rol parcial (`apps/sync/engine.py:954-973`).
- El pull ordena roles antes de asignaciones
  (`apps/sync/engine.py:435-443`) y difiere dependencias ausentes, evitando
  avanzar silenciosamente el cursor en esos casos.
- La suite existente verifica el ciclo soft-delete → reactivación secuencial y
  el scoping básico del negocio.

## Validación ejecutada

### Suite existente

Comando:

```powershell
C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py test `
  apps.permisos.tests `
  apps.api.tests.test_rbac_admin `
  apps.api.tests.test_sync_roles `
  apps.sync.tests.test_pull_roles `
  --settings=config.settings_development --keepdb -v 2
```

Resultado:

- **49 pruebas encontradas**.
- **49 aprobadas**.
- 0 fallos y 0 errores.
- `System check identified no issues`.
- Tiempo reportado por Django: 66.886 s.

Estas pruebas demuestran que el camino feliz actual funciona; no contradicen los
hallazgos, porque varios comportamientos riesgosos son semántica esperada por la
suite o no están cubiertos.

### Reproducciones adversariales temporales

Se creó un módulo de test exclusivamente durante la auditoría, se ejecutó sobre
la base de pruebas y se eliminó después. No queda código de prueba temporal en
el workspace.

Comando:

```powershell
C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py test `
  apps.permisos.tests.test_auditoria_temporal `
  --settings=config.settings_development --keepdb -v 2
```

Resultado: **7/7 casos confirmados**.

1. Una asignación de sucursal autoriza cuando se omite el scope.
2. Dos contextos con el mismo PK reutilizan una sola entrada de caché.
3. Una asignación cross-negocio pasa `full_clean()` y otorga permiso.
4. Un usuario sin negocio asignado en A administra roles de B mediante
   `?negocio=`.
5. Dos asignaciones globales idénticas coexisten; borrar una conserva el permiso.
6. Cambiar solo el M2M no actualiza `Rol.fecha_modificacion`.
7. Degradar ADMIN a CAJERA no invalida un catálogo ya cacheado.

### Checks estructurales

```powershell
C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py check `
  --settings=config.settings_development

C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py makemigrations `
  permisos --check --dry-run --settings=config.settings_development
```

Resultados:

- `System check identified no issues (0 silenced)`.
- `No changes detected in app 'permisos'`.

El primer intento con el `python` global 3.14 no inició Django porque ese
intérprete no tiene las dependencias del proyecto. Todas las validaciones
reportadas arriba se repitieron con el entorno oficial `pos_fifo`.

## Cobertura que falta antes de corregir

- Dos bases tenant reales con PK de usuario/sucursal repetidos y alternancia de
  requests en un mismo worker.
- Invalidación con tres procesos y backend compartido, incluyendo revocación.
- Carrera transacción/cache alrededor del commit.
- Semántica explícita de `sucursal=None` en motor, decorators, templates, API y
  servicios.
- `Usuario.activo=False` con sesión local ya emitida y JWT no-tenancy ya emitido.
- Cambios de `rol`, `negocio`, `is_superuser`, `Sucursal.activa` y
  `Negocio.activo`.
- Matriz cross-negocio para modelo, API, admin, seed y sync.
- Concurrencia real de alta/reactivación global y por sucursal.
- Migración de duplicados NULL existentes.
- PATCH que cambia usuario, rol o sucursal y convergencia del POS offline.
- Borrado de rol custom y reconciliación local.
- Cambio M2M directo, por admin, API y `sync_permisos` con cursor incremental.
- Catálogo completo: 31 permisos con caso positivo/negativo en el servicio final.
- Auditoría exacta de create/update/revoke/reactivate con actor global y tenant.
- Bootstrap con dos negocios, asignación inactiva, error intermedio y doble
  ejecución concurrente.
- Comandos bajo tenancy activo para uno y varios tenants.

## Orden sugerido de corrección

1. **Cerrar la mezcla cross-tenant de caché:** namespace por tenant/base y
   backend compartido coherente con los tres workers.
2. **Definir scope seguro:** `None` no debe unir sucursales implícitamente;
   propagar sucursal/contexto por todos los consumidores.
3. **Cerrar invariantes de identidad:** usuario, rol y sucursal del mismo negocio;
   usuario/negocio/sucursal activos; `?negocio=` solo para principal global real.
4. **Corregir revocaciones y sync:** identidad estable, tombstones, roles con
   soft-delete/reconciliación y timestamp/versionado de M2M.
5. **Eliminar duplicados globales y asegurar concurrencia:** limpieza de datos,
   constraints para NULL y servicios transaccionales/idempotentes.
6. **Separar administrador tenant de operador global:** retirar gradualmente el
   bypass ADMIN, denegar códigos desconocidos y proteger suscripciones/control
   plane.
7. **Cerrar ciclo de vida de usuario:** `is_active`, invalidación de campos de
   autorización, sesiones/tokens y señales post-commit.
8. **Añadir auditoría durable de RBAC** dentro del mismo flujo transaccional.
9. **Alinear catálogo y gates:** reimpresión, anulación, configuración y matriz
   automatizada de los 31 códigos.
10. **Endurecer operación:** separar seeds/política, hacer comandos tenant-aware,
    restringir admin y congelar migraciones históricas.
11. **Convertir cada reproducción en regresión** y actualizar el documento vivo
    antes de considerar cerrado el módulo.

## Trazabilidad del snapshot

- HEAD inicial: `3f22385`.
- HEAD final: `3f22385`.
- `git diff -- apps/permisos`: vacío al cierre.
- El módulo adversarial temporal fue eliminado.
- Único artefacto creado por esta auditoría:
  `docs/exploracion/AUDITORIA_CODIGO_APPS_PERMISOS.md`.
- Se preservaron todos los cambios externos existentes en inventario, sync y
  documentos de auditorías anteriores.

---

# Estado de mitigación

Fecha: 2026-08-27. Verificación previa: se releyó cada hallazgo P1 contra el
código citado. **Los diez son reales** — ninguno resultó falso positivo ni
obsoleto.

## Resumen por hallazgo

| ID | Real | Estado | Dónde quedó la corrección |
|---|---|---|---|
| PER-001 | Sí | Corregido | La clave de caché lleva `_namespace()`: el `tenant_key` activo. Bajo tenancy sin contexto **falla fuerte** (`TenantContextError`) en vez de caer al namespace implícito, igual que el router y el prefijo de media. No se deriva de `Negocio.pk`, que también es local a cada base. |
| PER-002 | Sí | Corregido | El motor detecta si el backend de caché se comparte entre procesos. Con `LocMemCache` **deja de cachear entre requests** y pasa a memoizar dentro del request (`ContextVar` + `PermisosRequestCacheMiddleware`). Con un backend compartido vuelve el caché con TTL. |
| PER-003 | Sí | Corregido | `sucursal=None` ahora significa **solo asignaciones globales**. La unión de todo el negocio existe pero hay que pedirla por su nombre: `sucursal=TODAS`. Decoradores y filtro de plantilla resuelven la sucursal real del request/instalación. |
| PER-004 | Sí | Corregido | `AsignacionRol.clean()` exige mismo negocio para usuario, rol y sucursal. El resolver además filtra `rol__negocio_id == usuario.negocio_id` y estados activos: aunque la fila exista, no se convierte en privilegio. |
| PER-005 | Sí | Corregido | `negocio_actual()` exige identidad global (`es_principal_global`: superusuario, SYSADMIN o `identity.is_global`) antes de aceptar `?negocio=`. Un usuario sin negocio ya no resuelve ninguno. |
| PER-006 | Sí | **Abierto** | Ver «Lo que no se tocó». |
| PER-007 | Sí | **Abierto** | Ver «Lo que no se tocó». |
| PER-008 | Sí | Corregido | Dos índices únicos **parciales** en lugar de `unique_together`: uno para `sucursal IS NULL` y otro para el resto. |
| PER-009 | Sí | Corregido | Dos límites por encima del acceso total: un código fuera del catálogo **siempre deniega** (con warning), y las capacidades del operador SaaS (`PERMISOS_OPERADOR_SAAS`) solo las aprueba un principal global. |
| PER-010 | Sí | Corregido | `Usuario.is_active` pasa a ser una propiedad ligada a `activo`; el motor comprueba `activo` en cada resolución; las señales observan además Usuario, Negocio y Sucursal. |
| PER-011 | Sí | Corregido | La señal limpia el memo local **de inmediato** y difiere el bump de versión global a `transaction.on_commit`. |

## El cambio que más conviene entender: qué significa llamar sin sucursal

Era el hallazgo con más superficie, porque el valor por defecto era el menos
conservador de los tres posibles:

```python
# ANTES
tiene_permiso('ventas.anular')            # unión de TODAS las sucursales
# AHORA
tiene_permiso('ventas.anular')            # solo asignaciones globales
tiene_permiso('ventas.anular', sucursal=s)  # globales + las de esa sucursal
permisos_de_usuario(u, sucursal=TODAS)      # unión, pedida por su nombre
```

Los tres consumidores que llamaban sin scope —los dos decoradores y el filtro
`|puede`— ahora resuelven la sucursal del request o de la instalación. **En una
instalación de una sola sucursal no cambia nada.** En una BD compartida por
varias, cada gate responde por la sucursal en la que se está operando.

El login del portal y el payload `/me` sí piden `TODAS` explícitamente: la
pregunta ahí es «¿puede algo en alguna parte?», que es un caso legítimo de
unión. El payload quedó documentado en el código como **pista para la UI, no
enforcement**.

## Cambios de conducta observables

1. **Un ADMIN de tenant ya no administra suscripciones.** Los endpoints de
   planes, módulos, suscripciones y overrides ahora exigen un principal global.
   El catálogo y el propio docstring de la vista ya describían esa capacidad
   como del operador del SaaS; el acceso total legacy se la concedía igual, y
   en una BD por tenant eso permitía editarse el propio plan. **Si el portal
   React muestra esa sección a un ADMIN, ahora recibirá 403.**
2. **Un permiso acotado a una sucursal deja de aplicar fuera de ella** en las
   vistas y plantillas del POS.
3. **Un código de permiso con typo deniega**, incluso para ADMIN. Antes lo
   aprobaba, así que un gate nuevo mal escrito no protegía nada y el error era
   invisible.
4. **Desactivar un usuario surte efecto en el próximo request**, no al expirar
   el caché ni al siguiente login.
5. **Un usuario sin negocio no tiene permisos de tenant** y no puede resolver
   ningún negocio con `?negocio=`.
6. **Dos asignaciones globales idénticas ya no pueden crearse**; la segunda
   levanta `IntegrityError`.
7. **Con `LocMemCache` hay una consulta más por request y usuario.** Es el
   precio de que tres workers no discrepen. Con Redis configurado, el caché
   entre requests vuelve solo.

## Despliegue: 1 migración

**`permisos.0009_asignacion_unicidad_efectiva`** — reemplaza `unique_together`
por dos índices únicos parciales, con un `RunPython` de **deduplicación previa**.

**Regla de resolución, que es una decisión de seguridad y no un detalle:**
cuando un grupo duplicado contiene alguna fila inactiva, **la fila
superviviente queda inactiva**. Una fila inactiva significa que alguien revisó
esa asignación y decidió quitarla; conservar la activa restauraría en silencio
un privilegio que un operador cree retirado — exactamente el síntoma del
hallazgo. Si el permiso hacía falta, se vuelve a otorgar desde el portal y queda
registrado: el error en esa dirección es recuperable, en la otra no se nota.

### Recomendación de infraestructura, no de código

PER-002 tiene una mitad que el código no puede resolver: **configurar un backend
de caché compartido (Redis) para el cloud.** Sin él, el motor funciona
correctamente —por eso deja de cachear entre requests— pero paga una consulta
por request. Con Redis recupera el caché y la invalidación por versión sirve
para los tres workers a la vez.

Mientras tanto, conviene corregir dos textos que se contradicen con el
`Dockerfile` (`--workers 3`): `docs/RBAC_PERMISOS.md:73-74` describe Azure como
single-worker, y el mismo supuesto estaba en el comentario del motor (ya
corregido ahí).

## Lo que no se tocó, y por qué

- **PER-006 y PER-007 (tombstones de sync).** Cambiar el usuario, el rol o la
  sucursal de una asignación crea la relación nueva en el POS local y deja la
  anterior activa para siempre; borrar un rol custom hace lo mismo. La
  corrección de fondo es una identidad cloud inmutable que viaje al local, más
  un ledger de tombstones y una reconciliación completa periódica — es un
  cambio de contrato de sincronización con su propia migración, del tamaño de
  las auditorías de `apps/sync`. **Sigue siendo un privilegio que persiste
  indefinidamente: conviene tomarlo a continuación.**
- **P2 restantes (PER-012 a PER-018)** y **P3 (PER-019 a PER-021)**: no se
  entraron en esta pasada.
- **Retirar el bypass de `ADMIN`.** La auditoría lo pide, pero exige migrar
  antes a cada admin a asignaciones explícitas con una comprobación previa de
  lockout. Lo que sí se hizo es acotarlo: ya no aprueba códigos inexistentes ni
  capacidades del operador.

## Pruebas

Suite completa, serial: **886 tests, OK.**

Módulo de regresión nuevo: `apps/permisos/tests/test_auditoria_permisos.py`
(31 tests).

Dos tests existentes **afirmaban la conducta defectuosa** y se reescribieron
sobre la correcta:

- `test_acceso_total_no_depende_del_catalogo` exigía que un ADMIN aprobara
  `codigo.inexistente` — la cita textual de PER-009. Se separó en dos: la parte
  legítima (un admin no queda bloqueado con la tabla `permisos` vacía, porque la
  fuente de verdad es el catálogo declarativo) y la corrección (un código fuera
  del catálogo deniega).
- El fixture de `test_suscripciones_admin` llamaba `op` a un usuario con
  `rol='ADMIN'` **y negocio propio** — un administrador de tenant haciendo de
  operador, apoyado justo en el bypass del hallazgo. Ahora el operador es
  SYSADMIN y hay dos tests nuevos que verifican que el dueño del negocio recibe
  403 y no puede cambiarse el plan.

**Verificación por mutación.** Revertidos los dos hallazgos centrales:

- Con `sucursal=None` volviendo a unir todas las sucursales (PER-003),
  `test_sin_scope_un_permiso_de_a_ya_no_aplica` falla con `True is not false` —
  la reproducción literal de la auditoría.
- Con `_usuario_habilitado` ignorando `activo` (PER-010),
  `test_un_usuario_desactivado_pierde_los_permisos` falla igual.

**Un test que protege a los demás.**
`test_el_catalogo_declarativo_cubre_todos_los_gates_reales` recorre el código y
las plantillas buscando gates que pidan un permiso fuera del catálogo. Hacer que
un código desconocido deniegue solo es seguro mientras eso sea cierto (PER-013),
así que el invariante quedó escrito como test: si alguien agrega un gate con un
código que no declaró, falla ahí y no en producción.

**Un error propio, corregido durante el trabajo.** La primera versión de las
señales difería *toda* la invalidación a `transaction.on_commit`, y eso dejaba
el memo local con el set viejo durante el resto del request. Tres tests
existentes lo detectaron. La separación correcta —memo local ya, versión global
al commit— es la que quedó.
