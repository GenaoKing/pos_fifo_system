# Auditoría profunda de código - `apps/sucursales`

Fecha: 2026-08-26  
Revisión base: `bcb8621`  
Modo: lectura, pruebas y documentación; no se aplicaron correcciones funcionales.

Nota de concurrencia: `apps/sucursales` estaba limpio al comenzar la revisión.
El workspace sí contenía correcciones del usuario en `permisos`, `negocios`,
`usuarios`, `reportes`, `api`, `config/settings.py` y documentación. Esas
ediciones se preservaron. En particular, el nuevo significado fail-closed de
los permisos por sucursal cambió durante esta etapa la reproducción del endpoint
de estado: un rol local ya no ve otras sucursales, pero tampoco puede ver la
propia. Esa conducta se documenta como SUC-012 y queda pendiente de revalidación
cuando las correcciones concurrentes se estabilicen.

## Resumen ejecutivo

`apps/sucursales` es pequeña, pero su modelo es una raíz de identidad del sistema:
determina la numeración de ventas, el alcance de cajas, inventario, cotizaciones,
CxC, permisos, configuración, sincronización y auditoría. El principal problema
no es el volumen de código sino que esa identidad se trata a la vez como dato
mutable, configuración de instalación, frontera de autorización y señal de
salud. No existe un único contrato que garantice que la sucursal resuelta está
activa, pertenece al tenant actual y conserva una identidad estable.

Los riesgos más urgentes son:

- `get_sucursal_actual()` conserva una instancia completa para siempre bajo una
  clave que solo incluye `codigo`; no incluye tenant ni alias de base. El patrón
  ya produjo objetos borrados que sobreviven al rollback en la propia suite, y
  puede reutilizar la sucursal homónima de otro tenant.
- Marcar una sucursal inactiva no impide operar. El resolver, el middleware y el
  servicio de ventas aceptan el objeto sin comprobar `activa`; además, una copia
  activa puede permanecer indefinidamente en caché.
- El modelo y la autenticación aceptan que el usuario de servicio pertenezca a
  otro negocio. Un token válido del negocio B puede quedar autenticado como una
  sucursal activa del negocio A en una base compartida o legacy.
- `codigo` funciona como identidad externa en números de venta, tokens de sync,
  payloads y cachés, pero sigue siendo editable, sensible a mayúsculas y sin
  formato canónico. Un renombre deja dos identidades simultáneas en memoria.
- El ciclo de vida administrativo no está gobernado por el RBAC de negocio ni
  deja auditoría. Borrar una sucursal todavía vacía elimina asignaciones locales
  por cascada y desancla registros de auditoría mediante `SET_NULL`.

Se documentan **20 hallazgos**:

| Prioridad | Cantidad | Criterio |
| --- | ---: | --- |
| P1 | 5 | Puede mezclar tenants, permitir operación de una sucursal desactivada, cruzar una identidad de servicio o romper identidad y evidencia. |
| P2 | 11 | Debilita aprovisionamiento, mínimo privilegio, observabilidad, disponibilidad o manejo de secretos. |
| P3 | 4 | Aumenta deuda, dispersión de responsabilidades y probabilidad de regresión. |

La suite seleccionada terminó con **112/112 pruebas existentes aprobadas**.
`apps/sucursales` aporta **0 pruebas propias** en el repositorio. Una batería
adversarial temporal terminó con **22/22 reproducciones confirmadas** y se retiró
del workspace. También pasaron `manage.py check` y
`makemigrations sucursales --check --dry-run` sobre una base de prueba aislada.

## Alcance

Se inspeccionaron completamente:

- `apps/sucursales/models.py`
- `apps/sucursales/middleware.py`
- `apps/sucursales/context_processors.py`
- `apps/sucursales/admin.py`
- `apps/sucursales/views.py`
- `apps/sucursales/apps.py`
- `apps/sucursales/management/commands/crear_sucursal.py`
- las migraciones `0001` a `0003`
- `apps/sucursales/tests/`

El núcleo contiene aproximadamente **312 líneas Python**, excluyendo migraciones
y pruebas temporales. Tiene un modelo, un middleware, un context processor, un
comando legacy, tres migraciones, vistas vacías y ningún caso de prueba propio.

También se trazaron las fronteras relevantes en:

- `config/settings.py`, `config/test_runner.py` y el router de tenancy
- `apps/tenancy/`, especialmente `SyncToken` y `bootstrap_tenant`
- `apps/api/authentication.py`, endpoints de sync, estado de sucursales y comandos
  de vinculación de tokens
- `apps/permisos/`, `apps/negocios/utils.py` y sus cambios concurrentes
- `apps/configuracion/` y su caché por sucursal
- consumidores en ventas, caja, inventario, cotizaciones y CxC
- relaciones entrantes desde auditoría, sync, reportes, productos, clientes y
  suscripciones
- configuración de logs y evidencia histórica en `cloud_debug.log.1`

## Hallazgos P1

### SUC-001 - La caché de la sucursal actual no está aislada por tenant o base

- Tipo: aislamiento multi-tenant / caché / confidencialidad e integridad.
- Evidencia:
  - La clave es únicamente `sucursal_actual_<codigo>`
    (`apps/sucursales/models.py:124-135`).
  - No incorpora `tenant_key`, alias de base ni negocio.
  - Los códigos se repiten legítimamente entre bases tenant; `SyncToken` los hace
    únicos solo dentro de cada tenant (`apps/tenancy/models.py:252-262`).
  - El router dirige `Sucursal` al alias activo (`apps/tenancy/router.py:74-96`),
    pero una lectura acertada de caché evita por completo esa consulta.
  - `configuracion._sucursal_actual()` llama al resolver sin excluir tenancy
    (`apps/configuracion/utils.py:69-82`).
- Reproducción validada:
  - La primera resolución de `CACHE-ALIAS` devolvió la sucursal A y la cacheó.
  - Una segunda resolución que debía consultar la sucursal B devolvió A sin
    tocar el manager; la consulta se ejecutó una sola vez.
- Impacto:
  - Feature flags, negocio, permisos o datos operativos pueden resolverse usando
    una instancia de otro tenant dentro del mismo worker.
  - Una instancia cacheada también conserva el alias de base del objeto original,
    aumentando el riesgo de lecturas o escrituras en el contexto incorrecto.
- Recomendación:
  - No cachear instancias ORM globalmente. Cachear como máximo un PK por namespace
    y recargar en el alias activo.
  - La clave debe incluir explícitamente tenant/alias y código canónico.
- Prueba de aceptación sugerida:
  - Dos tenants con `SD-001` se resuelven alternadamente en un mismo proceso sin
    compartir objeto, PK, negocio ni alias.

### SUC-002 - Desactivar una sucursal no impide que continúe operando

- Tipo: control operativo / revocación / caché obsoleta.
- Evidencia:
  - El help text promete que una sucursal inactiva no puede operar
    (`apps/sucursales/models.py:56-60`).
  - El resolver consulta solo `codigo`, sin `activa=True` (`:130-137`).
  - El middleware inyecta el resultado sin validarlo
    (`apps/sucursales/middleware.py:25-32`).
  - Ventas devuelve cualquier sucursal no nula antes de evaluar la configuración
    de sync (`apps/ventas/services/ventas_service.py:618-658`).
  - La caché usa `timeout=None` y `Sucursal.save()` no la invalida
    (`apps/sucursales/models.py:105-109`, `:130-135`).
- Reproducción validada:
  - Una sucursal creada directamente con `activa=False` fue devuelta por el
    resolver, inyectada por middleware y aceptada por `_resolver_sucursal()` con
    `SYNC_ENABLED=True`.
  - Tras cachear una sucursal activa y desactivarla por base, se siguió recibiendo
    indefinidamente la copia con `activa=True` y el nombre anterior.
- Impacto:
  - La revocación administrativa no detiene ventas ni garantiza que nuevos hechos
    dejen de atribuirse a la tienda cerrada.
  - Un operador puede creer que desactivó una instalación mientras los workers
    continúan autorizándola.
- Recomendación:
  - Resolver exclusivamente sucursales operables y fallar explícitamente cuando
    el código apunta a una inactiva.
  - Invalidar en commit ante create/update/delete, o eliminar la caché permanente.
  - La autorización de servicio debe volver a comprobar negocio, tenant y estado.
- Prueba de aceptación sugerida:
  - Desactivar la sucursal invalida todos los workers y bloquea el siguiente
    request de venta, caja, compra, cotización y sync.

### SUC-003 - El usuario de servicio puede pertenecer a otro negocio

- Tipo: autenticación / tenant binding / escalada horizontal.
- Evidencia:
  - `Sucursal.usuario_servicio` es un `OneToOneField` independiente de `negocio`;
    no hay `clean()`, constraint ni servicio de enlace que compare negocios
    (`apps/sucursales/models.py:22-30`, `:86-94`).
  - `_attach_sucursal()` filtra usuario, estado y opcionalmente código, pero no
    `sucursal.negocio == user.negocio` (`apps/api/authentication.py:104-125`).
  - El comando `vincular_sucursal_token` crea el usuario sin `negocio` y luego lo
    vincula (`apps/api/management/commands/vincular_sucursal_token.py:45-99`).
  - El bootstrap moderno sí fija el mismo negocio en ambos lados, mostrando el
    contrato pretendido (`apps/tenancy/management/commands/bootstrap_tenant.py:385-404`).
- Reproducción validada:
  - `full_clean()` y `save()` aceptaron una sucursal de A vinculada a un usuario
    de B.
  - `SucursalTokenAuthentication.authenticate_credentials()` autenticó el token
    de B y adjuntó la sucursal activa de A.
- Impacto:
  - En modo compartido/legacy, el token puede operar endpoints de sync con la
    identidad física y relaciones de otra empresa.
  - Una inconsistencia de aprovisionamiento se convierte en una identidad válida,
    no en un error detectable.
- Recomendación:
  - Hacer el vínculo mediante un único servicio transaccional que exija tenant y
    negocio idénticos.
  - Validar nuevamente el binding durante autenticación; no confiar solo en la FK.
  - Corregir o retirar el comando legacy de vinculación.
- Prueba de aceptación sugerida:
  - Modelo, comandos y autenticador rechazan cualquier usuario global, nulo o de
    otro negocio salvo un caso explícito y auditado de plataforma.

### SUC-004 - `codigo` es una identidad externa mutable y no canónica

- Tipo: identidad / idempotencia / sincronización.
- Evidencia:
  - El campo solo impone `max_length=20` y unicidad sensible al collation
    (`apps/sucursales/models.py:32-37`).
  - No existe normalización en `save()` o `clean()`; únicamente el comando legacy
    aplica `upper().strip()` (`apps/sucursales/management/commands/crear_sucursal.py:49-68`).
  - El código forma números de venta (`apps/ventas/models.py:205`), payloads de
    sync, snapshots, claves de caché y el binding de `SyncToken`.
  - El Admin permite editarlo y no existe workflow de renombre
    (`apps/sucursales/admin.py:9-29`).
- Reproducción validada:
  - `CASE-01` y `case-01` coexistieron, y un código con espacios y `/` pasó
    `full_clean()` y persistió.
  - Tras renombrar `REN-ORIGEN` a `REN-NUEVA`, la clave vieja devolvió una copia
    cacheada y la nueva resolvió otra instancia del mismo PK con distinto código.
- Impacto:
  - El mismo punto físico puede tener dos identidades simultáneas frente a ventas,
    deduplicación, monitoreo y tokens.
  - Un cambio aparentemente cosmético puede romper sincronización o crear números
    incompatibles con consumidores externos.
- Recomendación:
  - Definir formato canónico y unicidad case-insensitive en base.
  - Tratar el código como inmutable después del aprovisionamiento; si debe cambiar,
    usar un workflow que rote tokens, invalide cachés y mantenga alias histórico.
- Prueba de aceptación sugerida:
  - Variantes de case/espacios se rechazan y un cambio directo del código falla;
    el workflow autorizado conserva resolución de eventos históricos.

### SUC-005 - El ciclo de vida administrativo puede borrar alcance y evidencia

- Tipo: integridad / RBAC / auditabilidad / operación destructiva.
- Evidencia:
  - `SucursalAdmin` no limita `delete`, `change` o queryset por negocio
    (`apps/sucursales/admin.py:8-29`).
  - `AsignacionRol.sucursal` usa `CASCADE`
    (`apps/permisos/models.py:114-121`).
  - `Auditoria.sucursal` usa `SET_NULL`
    (`apps/auditoria/models.py:190-197`).
  - Las relaciones con hechos operativos suelen usar `PROTECT`, pero una sucursal
    todavía vacía sí puede borrarse.
- Reproducción validada:
  - Al borrar una sucursal vacía se eliminó silenciosamente la asignación de rol
    local y el evento de auditoría sobrevivió sin sucursal.
- Impacto:
  - Se pierde evidencia de qué tienda cubría un permiso o un hecho histórico.
  - El borrado puede cambiar privilegios y trazabilidad como efecto secundario no
    visible para el operador.
- Recomendación:
  - Prohibir borrado ordinario; usar desactivación irreversible o archivado con
    motivo, actor y timestamp.
  - Conservar snapshot de código/nombre/negocio en auditoría y evitar `CASCADE`
    sobre grants históricos.
- Prueba de aceptación sugerida:
  - Ningún canal elimina una sucursal; desactivarla conserva asignaciones e
    historial y produce un evento inmutable.

## Hallazgos P2

### SUC-006 - El modelo acepta sucursales activas incompletas

- `negocio` y `usuario_servicio` son opcionales y no hay `clean()` ni constraints
  de estado (`apps/sucursales/models.py:22-30`, `:56-94`). Configuración también
  vive en otro modelo opcional.
- Se reprodujo una sucursal activa, válida para `full_clean()`, sin negocio ni
  identidad de servicio.
- Esto obliga a cada consumidor a interpretar `NULL` y activa fallbacks legacy
  distintos.
- Recomendación: definir estados de aprovisionamiento explícitos y permitir
  `activa=True` solo cuando negocio, configuración e identidad requeridos estén
  completos.

### SUC-007 - Una instalación ausente o mal configurada falla de forma ambigua

- `SUCURSAL_CODIGO` toma `SD-001` por defecto aunque el operador no lo declare
  (`config/settings.py:459-460`).
- El resolver devuelve `None` si no encuentra fila, y el middleware continúa por
  compatibilidad (`apps/sucursales/models.py:126-137`,
  `apps/sucursales/middleware.py:17-32`).
- Consumidores no son uniformes: ventas solo falla si sync está activo; caja con
  sucursal nula devuelve todas las cajas activas (`apps/caja/views.py:55-59`),
  mientras compras persiste `request.sucursal` directamente
  (`apps/inventario/views.py:159-166`).
- Recomendación: distinguir explícitamente `standalone`, `provisionando` y
  `operativa`; en una instalación declarada por sucursal, la ausencia debe impedir
  arrancar o pasar el health check.

### SUC-008 - `crear_sucursal` es un aprovisionador legacy parcial y no tenant-aware

- Hereda `BaseCommand`, no el mixin tenant; bajo tenancy sin contexto el router
  lanza `TenantContextError` (`apps/sucursales/management/commands/crear_sucursal.py:9-14`,
  `apps/tenancy/router.py:88-96`).
- No recibe negocio, no llama `full_clean()` y crea `negocio=NULL` (`:49-68`).
- La sucursal se guarda antes de configurar; una excepción se captura, se imprime
  como warning y el comando termina exitosamente (`:75-97`).
- Se reprodujeron los tres casos: falla sin contexto tenant, creación huérfana y
  éxito parcial tras fallar el preset.
- Recomendación: declarar el comando legacy/standalone o retirarlo a favor de
  `bootstrap_tenant`; cualquier flujo vigente debe ser atómico, idempotente y
  validar postcondiciones completas.

### SUC-009 - `api_key` es un secreto muerto almacenado y expuesto en claro

- Se genera con entropía adecuada, pero se guarda reversible y único
  (`apps/sucursales/models.py:62-68`, `:105-109`).
- El comando lo imprime completo (`apps/sucursales/management/commands/crear_sucursal.py:70-73`)
  y Admin lo muestra como readonly (`apps/sucursales/admin.py:13-27`).
- No se encontró ningún autenticador que lo consuma; la API real usa DRF Token y
  `SyncToken` hash.
- Una sentencia INSERT versionada en `cloud_debug.log.1:2250` contiene un valor
  histórico completo de 64 caracteres; no se reproduce aquí.
- Recomendación: si no tiene consumidor, migrarlo fuera del modelo. Si se adopta,
  guardar solo hash, diseñar rotación/revocación y excluir SQL sensible de logs.

### SUC-010 - `ultima_sync` puede indicar salud después de un batch fallido

- `recibir_eventos` actualiza `ultima_sync` al terminar aunque todos los handlers
  fallen, y silencia cualquier error al guardar (`apps/api/views/sync.py:142-159`).
- El dashboard deriva el semáforo de ese timestamp
  (`apps/api/views/sucursales.py:129-158`).
- Se envió un batch válido de transporte pero inválido de negocio: respondió 200,
  `recibidos=0`, `errores=1` y aun así dejó `ultima_sync` no nulo.
- Recomendación: separar `ultimo_contacto`, `ultimo_batch_exitoso` y
  `ultimo_evento_confirmado`; nunca silenciar la escritura de la señal de salud.

### SUC-011 - Heartbeat y recepción de eventos desactivan todo throttling

- Ambos endpoints declaran `@throttle_classes([])`
  (`apps/api/views/sync.py:58-63`, `:208-220`). Asignar `throttle_scope` dentro de
  la función no reinstala una clase de throttle.
- Heartbeat ejecuta un `UPDATE` por request; un token filtrado o una instalación
  defectuosa puede generar carga sostenida sin límite aplicativo.
- Recomendación: usar scopes con tasas operativas explícitas, métricas y límites
  por token/tenant, conservando margen para reconexiones legítimas.

### SUC-012 - Un rol local con `sucursales.ver` no puede ver su propia sucursal

- El endpoint valida `requiere_permiso('sucursales.ver')` sin una sucursal
  concreta (`apps/api/views/sucursales.py:53-68`).
- Con el cambio concurrente, `sucursal=None` acepta solo asignaciones globales;
  el helper correcto para respuestas agregadas es `sucursales_con_permiso()`
  (`apps/permisos/engine.py:356-394`).
- Se asignó el permiso únicamente en A y el endpoint respondió 403, sin permitir
  siquiera el status de A.
- Recomendación: resolver el conjunto autorizado antes del gate y filtrar la
  respuesta; mantener 403 únicamente para conjunto vacío.
- Estado: conducta dependiente de correcciones concurrentes en `permisos`; debe
  revalidarse antes de remediar.

### SUC-013 - El control administrativo no está integrado con el RBAC de negocio

- El catálogo solo define `sucursales.ver`
  (`apps/permisos/catalogo.py:93`); no hay capacidades para crear, editar,
  desactivar, vincular servicio o rotar credenciales.
- Django Admin usa sus permisos paralelos y un queryset sin scope de negocio
  (`apps/sucursales/admin.py:8-29`).
- Un staff con permisos Django puede saltarse el modelo de autorización que usa
  el portal; un admin de negocio no dispone de un contrato granular equivalente.
- Recomendación: definir capacidades de ciclo de vida, separar plataforma de
  negocio y aplicar el mismo servicio/autorización desde portal, comandos y Admin.

### SUC-014 - Crear, editar, desactivar y vincular una sucursal no deja auditoría

- `Sucursal.save()` solo genera `api_key`; no emite evento
  (`apps/sucursales/models.py:105-109`).
- Admin y los comandos escriben directamente sin un servicio de dominio auditado.
- No hay snapshot de antes/después, motivo de desactivación, actor ni correlación
  con token o configuración.
- Recomendación: centralizar mutaciones en un servicio transaccional que escriba
  auditoría inmutable con negocio, código, estado, actor, canal y motivo.

### SUC-015 - La ausencia no se cachea y puede consultar la base en cada request

- Cuando el código no existe se devuelve `None` sin entrada negativa
  (`apps/sucursales/models.py:130-137`).
- Se confirmó que dos llamadas consecutivas ejecutan dos búsquedas.
- En una instalación mal aprovisionada cada request web y cada helper adicional
  puede repetir la misma consulta, mientras el sistema aparenta seguir vivo.
- Recomendación: fallar el readiness de una instalación operativa; si el modo
  legacy debe continuar, usar una negativa breve, observable e invalidable.

### SUC-016 - La autenticación oculta fallos de esquema o base como token sin sucursal

- `_attach_sucursal()` captura `Exception` sin logging y convierte cualquier fallo
  en `token.sucursal=None` (`apps/api/authentication.py:104-125`).
- Una migración faltante, error de router, timeout o corrupción se presenta igual
  que un usuario humano legítimo, desplazando el diagnóstico al permiso posterior.
- Recomendación: capturar solo la excepción de compatibilidad deliberada; registrar
  y propagar fallos operativos, con métricas por tenant y código.

## Hallazgos P3

### SUC-017 - La app no tiene pruebas propias

- `apps/sucursales/tests/` contenía únicamente `__init__.py`.
- La conducta se cubre indirectamente desde ventas, API, sync, tenancy y
  configuración, pero no existen contratos de modelo, caché, lifecycle o Admin.
- Recomendación: convertir las 22 reproducciones temporales relevantes en una
  suite permanente junto con los fixes, empezando por P1.

### SUC-018 - Las responsabilidades de la app están fragmentadas

- `apps/sucursales/views.py` está vacío; el único endpoint funcional vive en
  `apps/api/views/sucursales.py` y los comandos de identidad viven repartidos
  entre `sucursales`, `api` y `tenancy`.
- No existe una capa de servicios propietaria del alta, desactivación, enlace de
  usuario, rotación o renombre.
- Recomendación: mantener los adaptadores donde corresponda, pero concentrar las
  invariantes y mutaciones en `apps/sucursales/services.py`.

### SUC-019 - Contacto y nombre no tienen normalización de dominio

- `nombre`, `direccion` y `telefono` solo aplican límites de tipo/longitud
  (`apps/sucursales/models.py:39-54`).
- El comando recorta el nombre, pero Admin y ORM no; no hay consistencia sobre
  whitespace, teléfono o nombre vacío compuesto por espacios.
- Recomendación: definir normalización mínima centralizada y validarla igual en
  modelo, servicios, importaciones y comandos.

### SUC-020 - Cachear la instancia ORM completa acopla datos y relaciones obsoletas

- Además del riesgo de seguridad de SUC-001/002, el valor serializado conserva
  todos los campos y el estado ORM indefinidamente.
- El propio test runner documenta que instancias cacheadas sobreviven al rollback
  y apuntan a FKs eliminadas (`config/test_runner.py:8-18`).
- Recomendación: resolver por request o cachear solo identidad primitiva con TTL
  corto y namespace; las relaciones deben cargarse desde el contexto actual.

## Aspectos positivos confirmados

- `api_key` usa `secrets.token_hex(32)`, no un generador predecible.
- La autenticación de servicio exige sucursal activa y, en tenancy, compara el
  código esperado del `SyncToken` (`apps/api/authentication.py:104-117`).
- La mayoría de hechos operativos usa `PROTECT`, evitando borrar por accidente una
  sucursal con ventas, cajas, inventario, CxC, sync o reportes asociados.
- El endpoint de status filtra por negocio del usuario y solo lista sucursales
  activas (`apps/api/views/sucursales.py:60-68`).
- `bootstrap_tenant` aprovisiona negocio, configuración, sucursal, usuario de
  servicio y token en un flujo coherente; es una base mejor que los comandos
  legacy dispersos.
- `verificar_instalacion` y `verificar_sync` ya diagnostican código ausente, lo
  que facilita convertir la inconsistencia en un gate de readiness.
- No se detectó drift de migraciones ni errores en `manage.py check`.

## Validación ejecutada

Base aislada: `test_pos_fifo_auditoria_sucursales_20260826`.

### Suite existente seleccionada

Se ejecutaron:

- `apps.sucursales`
- `apps.api.tests.test_reportes_permisos`
- `apps.api.tests.test_reportes_scope_negocio`
- `apps.api.tests.test_sync_extended`
- `apps.api.tests.test_sync_roles`
- `apps.tenancy.tests.test_auth`
- `apps.tenancy.tests.test_models_and_commands`
- `apps.sync.tests.test_pull_roles`
- `apps.sync.tests.test_verificar_sync`
- `apps.configuracion.tests.test_verificar_instalacion`
- `apps.ventas.tests.test_ventas_service`

Resultado: **112/112 OK**.

### Batería adversarial temporal

Resultado final: **22/22 OK**, confirmando:

- resolución, middleware y ventas con sucursal inactiva;
- estado y nombre obsoletos en caché;
- clave sin namespace tenant y renombre con identidad dividida;
- consultas repetidas cuando la sucursal no existe;
- sucursal activa huérfana;
- duplicados case-insensitive y códigos sin formato;
- usuario/token de servicio cruzado entre negocios;
- rol local bloqueado en el endpoint de status;
- batch fallido que actualiza `ultima_sync`;
- ausencia de throttling en heartbeat;
- comando sin negocio, parcial, duplicado, con secreto impreso e incompatible con
  tenancy sin contexto;
- campos críticos omitidos en Admin;
- borrado que elimina asignación y desancla auditoría.

El archivo temporal de pruebas y los settings temporales se retiraron tras cerrar
la evidencia. No se modificó código funcional.

## Orden sugerido de remediación

1. Definir un resolver único, tenant-aware y fail-closed; eliminar la caché de
   instancias y hacer efectiva la desactivación.
2. Hacer inmutables/canónicos los códigos y diseñar explícitamente cualquier
   migración de identidad.
3. Blindar el vínculo usuario de servicio ↔ negocio ↔ tenant tanto al escribir
   como al autenticar.
4. Sustituir borrado por archivado/desactivación auditada y centralizar el ciclo
   de vida en un servicio.
5. Consolidar aprovisionamiento en `bootstrap_tenant` o un equivalente atómico;
   retirar comandos legacy ambiguos.
6. Separar señales de contacto/éxito de sync, reactivar throttling y corregir el
   alcance local del endpoint de status.
7. Retirar o rediseñar `api_key`, sanear logs y convertir las reproducciones en
   pruebas permanentes.

## Criterio de cierre

La app no debería considerarse cerrada hasta que:

- una sucursal inactiva quede bloqueada en todos los canales en el siguiente
  request;
- resolver A/B entre tenants homónimos nunca mezcle caché, PK, negocio o alias;
- ninguna identidad de servicio pueda cruzar tenant o negocio;
- código y ciclo de vida tengan reglas inmutables/auditadas;
- faltar la sucursal operativa falle readiness en vez de activar fallbacks
  heterogéneos;
- status de sync distinga contacto de éxito y respete alcance local;
- la suite permanente cubra estas invariantes y pase junto con tenancy, sync,
  permisos, ventas, caja, inventario y configuración.
