# Auditoría profunda de código - `apps/auditoria`

Fecha: 2026-08-21
Revisión inicial: `3f22385`
Revisión de cierre: `65ce805`
Modo: lectura, pruebas y documentación; no se aplicaron correcciones funcionales.

Nota de concurrencia: la evidencia y las pruebas corresponden al snapshot de
`apps/auditoria` inspeccionado antes del cierre. Mientras se redactaba este
documento apareció trabajo del usuario sin commit que añadió
`TipoAccion.CIERRE_DIARIO` y migró el comando de cierre al contrato actual. Esa
corrección atiende AUD-017, pero no forma parte de esta auditoría ni fue
revalidada por ella; se conserva el hallazgo con su estado explícito.

## Resumen ejecutivo

`apps/auditoria` debería ser la fuente de evidencia transversal del POS: actor,
acción, momento, sucursal, objeto afectado, resultado y contexto técnico. Hoy sí
recibe eventos útiles de ventas, anulaciones, inventario, CxC, autenticación e
impresión, y varios de esos eventos se escriben dentro de la misma transacción
que el hecho de negocio. Sin embargo, el historial todavía no ofrece las
propiedades mínimas de un registro de auditoría confiable: aislamiento horizontal,
identidad histórica estable, inmutabilidad, cobertura verificable y atribución de
sucursal consistente.

Los riesgos más urgentes son:

- Un usuario con `auditoria.ver` asignado únicamente a la sucursal A puede abrir
  el dashboard y obtener por API registros, estadísticas y usuarios de B. Se
  reprodujo con dos sucursales del mismo negocio.
- Las filas son editables y eliminables por ORM; además, el Admin permite
  borrarlas a superusuarios. No existe append-only, hash encadenado, firma,
  versión ni almacenamiento externo que permita detectar la alteración.
- El actor se conserva solo como FK mutable. Renombrarlo cambia cómo se presenta
  un hecho pasado; al quedar la FK nula, la UI lo clasifica como `Sistema`, sin
  snapshot del username, identificador o negocio que permita reconstruirlo.
- La mayoría de productores no informa `sucursal`, aun cuando el objeto de
  dominio sí la conoce. Los helpers de venta, anulación y ajuste tampoco la
  derivan. El campo nullable termina siendo insuficiente para investigar por
  tienda.
- En modo tenancy todo `/api/` se excluye deliberadamente antes de crear contexto
  de auditoría. Precisamente las mutaciones cloud/sync quedan fuera del mecanismo
  automático y solo se ven si cada servicio recuerda emitir un evento propio.
- La lista de rutas críticas está desfasada. No reconoce las rutas reales
  `/productos/<id>/editar/`, `/pos/api/anular-venta/` ni
  `/inventario/api/ajustar/`; la edición de producto queda sin productor de
  dominio alternativo.

Se documentan **22 hallazgos**; AUD-017 ya tenía una corrección concurrente en
curso al entregar el informe:

| Prioridad | Cantidad | Criterio |
| --- | ---: | --- |
| P1 | 6 | Puede exponer evidencia de otra sucursal, borrar o reescribir historial, perder atribución o dejar operaciones críticas fuera del registro. |
| P2 | 12 | Produce hechos falsos o incompletos, debilita disponibilidad, privacidad, semántica y capacidad de investigación. |
| P3 | 4 | Limita consultas, escalabilidad, portabilidad y protección contra regresiones. |

La suite seleccionada terminó con **150/150 pruebas existentes aprobadas**.
`apps/auditoria` aporta **0 pruebas propias**. Una batería adversarial temporal
terminó con **22/22 reproducciones confirmadas** y fue retirada del workspace.
También pasaron `manage.py check` y
`makemigrations auditoria --check --dry-run` sobre una base de prueba aislada.

## Alcance

Se inspeccionaron completamente:

- `apps/auditoria/models.py`
- `apps/auditoria/middleware.py`
- `apps/auditoria/views.py`
- `apps/auditoria/admin.py`
- `apps/auditoria/urls.py`
- `apps/auditoria/apps.py`
- `apps/auditoria/migrations/0001_initial.py`
- `apps/auditoria/migrations/0002_auditoria_sucursal_and_more.py`
- `apps/auditoria/migrations/0003_alter_auditoria_accion.py`
- `apps/auditoria/tests/`
- `templates/auditoria/dashboard.html`

También se trazaron productores y fronteras relevantes en:

- `config/settings.py` y `config/urls.py`
- `apps/tenancy/context.py`, `router.py` y sus pruebas
- `apps/sucursales/models.py` y `middleware.py`
- `apps/permisos/catalogo.py`, `engine.py`, `models.py` y pruebas
- `apps/usuarios/views.py` y `models.py`
- `apps/ventas/services/ventas_service.py` y `anulaciones_service.py`
- `apps/inventario/views.py` y `services/ajustes_service.py`
- `apps/clientes/views.py`
- `apps/cuentas_por_cobrar/services.py`
- `apps/reportes/management/commands/generar_cierre_diario.py`
- `apps/api/`, `apps/sync/` y sus suites de auditoría
- `utils/impresoras/manager.py` y `views.py`
- rutas actuales de productos, ventas, inventario, caja, clientes, CxC y reportes

El núcleo de la app suma **1,224 líneas Python**, sin contar migraciones ni
pruebas. Tiene tres migraciones, dos middlewares globales, dos vistas, un modelo
y **ningún caso de prueba propio**.

La auditoría comenzó en `3f22385` y cerró con HEAD `65ce805`. Durante la revisión
el usuario avanzó correcciones y commits en paralelo. El agente no modificó
`apps/auditoria`; al cierre apareció una edición del usuario en `models.py` para
añadir `CIERRE_DIARIO`, junto con trabajo externo en permisos, reportes y
`config/urls.py`. No se revirtió ni alteró. Salvo la nota expresa de AUD-017, las
referencias y reproducciones corresponden al snapshot auditado previo a esas
ediciones concurrentes de cierre.

## Hallazgos P1

### AUD-001 - Un permiso acotado a una sucursal abre el historial completo

- Severidad: crítica en instalaciones multi-sucursal.
- Tipo: autorización horizontal / confidencialidad / RBAC.
- Evidencia:
  - Ambas vistas validan `request.user.tiene_permiso('auditoria.ver')` sin pasar
    sucursal (`apps/auditoria/views.py:23-31` y `:87-104`).
  - El motor documenta que una consulta sin sucursal significa “sí, en alguna”;
    para limitar datos debe usarse `sucursales_con_permiso()`
    (`apps/permisos/engine.py:136-168`).
  - La query del API parte de `Auditoria.objects...` sin filtro de sucursal
    (`apps/auditoria/views.py:117-134`).
  - Las cuatro estadísticas y la lista de usuarios tampoco se acotan
    (`:44-73`).
- Reproducción validada:
  - Se asignó `auditoria.ver` a un rol únicamente en sucursal A.
  - Se crearon eventos para A y B.
  - El usuario obtuvo HTTP 200, `total_registros=2`, la descripción de B y
    `total_24h=2`.
- Impacto:
  - Motivos de anulaciones, montos, nombres de clientes, usernames, IPs y errores
    de otra tienda quedan visibles a un supervisor local.
  - El conteo agregado permite inferir actividad incluso si luego se ocultaran
    filas.
- Recomendación:
  - Resolver una vez el conjunto de sucursales autorizado y aplicarlo a lista,
    estadísticas, usuarios y cualquier exportación.
  - `None` debe significar alcance global explícito; `set()` debe negar y un set
    de IDs debe filtrar obligatoriamente.
- Prueba de aceptación sugerida:
  - Con permiso solo en A, ninguna respuesta, conteo, filtro de usuario ni página
    debe revelar que existen eventos de B.

### AUD-002 - El historial no es inmutable ni permite detectar alteraciones

- Severidad: crítica.
- Tipo: integridad de evidencia / no repudio / borrado.
- Evidencia:
  - `Auditoria` no sobrescribe `save()` o `delete()`, ni usa un manager append-only
    (`apps/auditoria/models.py:11-226`).
  - `QuerySet.update()` y `QuerySet.delete()` quedan disponibles sin barrera.
  - Admin bloquea agregar y cambiar desde formularios, pero autoriza borrar a
    todo superusuario (`apps/auditoria/admin.py:108-117`).
  - No hay hash del evento anterior, firma, versión, trigger de base, outbox
    inmutable ni copia externa.
- Reproducción validada:
  - Una fila `CREATE / Original` fue cambiada por ORM a `DELETE / Alterado` y
    luego eliminada sin impedimento.
  - `AuditoriaAdmin.has_delete_permission()` devolvió `True` para un superusuario.
- Impacto:
  - La misma cuenta de alto privilegio que ejecuta una acción puede borrar su
    rastro o reescribirlo.
  - Una investigación no puede distinguir historia genuina de una tabla
    modificada después del hecho.
- Recomendación:
  - Tratar la tabla como append-only en aplicación y base; negar `UPDATE/DELETE`
    a la identidad runtime.
  - Añadir secuencia/hash encadenado o firma por lote y exportación periódica a
    almacenamiento WORM o cuenta separada.
  - La retención debe purgar mediante un proceso controlado que deje manifiesto
    firmado, nunca desde Admin.
- Prueba de aceptación sugerida:
  - Aplicación y Admin no pueden editar/borrar; una alteración directa de una fila
    debe romper una verificación automática de integridad.

### AUD-003 - La identidad histórica del actor se reescribe o desaparece

- Severidad: alta-crítica.
- Tipo: atribución / identidad mutable / evidencia.
- Evidencia:
  - Solo se guarda FK a `Usuario`, con `on_delete=SET_NULL`
    (`apps/auditoria/models.py:92-100`).
  - No existen campos snapshot para username, ID externo, negocio, rol o tipo de
    actor.
  - `__str__`, API y Admin consultan el usuario actual
    (`models.py:228-230`, `views.py:141-153`, `admin.py:127-138`).
  - Cuando la FK es nula se muestra literalmente `Sistema`, igual que un evento
    realmente automático.
- Reproducción validada:
  - Tras renombrar `audit_admin` a `actor_renombrado`, el evento antiguo pasó a
    presentarse con el nombre nuevo.
  - Al quedar `usuario_id=NULL`, una acción humana se presentó como `Sistema`.
- Impacto:
  - No se puede probar qué credencial existía al momento del hecho ni distinguir
    un actor eliminado de un job del sistema.
  - Renombres, consolidaciones y bajas de usuarios cambian retrospectivamente el
    relato de incidentes.
- Recomendación:
  - Guardar snapshot inmutable de actor: ID estable, username visible, negocio,
    canal (`web`, API, job, sistema) y, cuando aplique, identidad global/impersonada.
  - Conservar la FK solo como navegación auxiliar.
- Prueba de aceptación sugerida:
  - Renombrar o desactivar un usuario no cambia el actor snapshot; una baja se
    presenta como “usuario eliminado” y nunca como “Sistema”.

### AUD-004 - La atribución de sucursal es opcional y se omite en hechos críticos

- Severidad: alta-crítica.
- Tipo: trazabilidad física / aislamiento horizontal / completitud.
- Evidencia:
  - `sucursal` acepta `NULL` (`apps/auditoria/models.py:188-196`).
  - `registrar_venta`, `registrar_anulacion_venta` y
    `registrar_ajuste_inventario` no reciben ni derivan sucursal
    (`:303-367`).
  - Ventas y ajustes llaman esos helpers aunque `Venta`, `Lote` y `Ajuste` ya
    conocen su sucursal (`apps/ventas/services/ventas_service.py:341-346`,
    `anulaciones_service.py:150-156`,
    `apps/inventario/services/ajustes_service.py:165-169`).
  - Varios productores directos en CxC, clientes y autenticación tampoco pasan
    el campo. Impresión sí lo hace en algunos caminos, demostrando que el contrato
    existe pero no es uniforme.
  - El middleware automático tampoco copia `request.sucursal`
    (`apps/auditoria/middleware.py:216-224`).
- Reproducción validada:
  - Se llamó `registrar_venta()` con un objeto que contenía `sucursal=A`; el
    registro resultante quedó con `sucursal_id=NULL`.
- Impacto:
  - Ni siquiera después de corregir AUD-001 sería posible filtrar con seguridad
    buena parte del historial.
  - `NULL` mezcla modo legacy, jobs, errores de programación y hechos sin ámbito.
- Recomendación:
  - Exigir sucursal en eventos operativos y derivarla del objeto bajo una regla
    única; no confiar en que cada caller la recuerde.
  - Separar explícitamente eventos globales/control-plane de eventos tenant y de
    sucursal.
- Prueba de aceptación sugerida:
  - Venta, anulación, ajuste, compra, caja, CxC, cliente e impresión deben dejar
    exactamente la sucursal del objeto; `NULL` solo se acepta para tipos globales
    enumerados.

### AUD-005 - El modo tenancy excluye toda la API del middleware de auditoría

- Severidad: crítica para el portal cloud.
- Tipo: cobertura / canal API / tenancy.
- Evidencia:
  - `_skip_api_tenancy()` devuelve verdadero para cualquier path que empiece por
    `/api/` cuando tenancy está habilitado
    (`apps/auditoria/middleware.py:48-49`).
  - La exclusión se aplica en request, view, response y exception
    (`:51-58`, `:69-76`, `:89-95`, `:131-137`).
  - `SesionAuditoriaMiddleware` repite la misma exclusión (`:233-238`).
  - La API contiene sync, CRUD cloud y operaciones administrativas; la ausencia
    automática solo se compensa en los pocos servicios que emiten manualmente.
- Reproducción validada:
  - Bajo `force_tenancy()`, un POST a `/api/v1/sync/push/` terminó sin siquiera
    `request.audit_info`.
  - La suite existente también codifica la omisión en
    `apps/tenancy/tests/test_router.py:106-109`.
- Impacto:
  - Acciones realizadas con credenciales globales, servicio de sucursal o
    impersonación pueden no dejar actor ni canal en `Auditoria`.
  - La evidencia web local y cloud tiene coberturas incompatibles.
- Recomendación:
  - Sustituir el skip por un adaptador tenant-aware que escriba en el destino
    deliberado y capture identidad global, identidad operativa, tenant y sucursal.
  - Mantener una matriz de endpoints mutantes y exigir productor exactamente una
    vez por operación.
- Prueba de aceptación sugerida:
  - Cada POST/PUT/PATCH/DELETE cloud relevante deja un evento durable en el
    tenant correcto, incluso en error, sin consultar modelos tenant antes de
    enlazar el contexto.

### AUD-006 - La allowlist de URLs críticas no coincide con las rutas actuales

- Severidad: alta-crítica.
- Tipo: cobertura silenciosa / rutas legacy / falsa sensación de seguridad.
- Evidencia:
  - La lista contiene `/productos/editar/`, `/ventas/anular/`, `/ventas/pos/`,
    `/inventario/ajustar/` y `/inventario/compras/crear/`
    (`apps/auditoria/middleware.py:22-34`).
  - Las rutas reales son `/productos/<id>/editar/`,
    `/pos/api/anular-venta/`, `/pos/api/procesar-venta/`,
    `/inventario/api/ajustar/` y `/inventario/compras/nueva/`.
  - `_debe_auditar_url()` solo busca el fragmento literal con `in`
    (`middleware.py:157-177`).
  - Productos no emite auditoría de dominio para creación, edición, precio,
    toggle o imagen, aunque el catálogo declara esos tipos.
- Reproducción validada:
  - El matcher devolvió `False` para edición real de producto, anulación real de
    venta y ajuste real de inventario.
- Impacto:
  - Cambios sensibles pueden ejecutarse durante meses sin fila y sin error: la
    aplicación no sabe que la cobertura desapareció al cambiar una URL.
- Recomendación:
  - Auditar por nombre de vista/servicio o evento de dominio, no por substrings de
    URL.
  - Generar una prueba que recorra rutas mutantes y compare contra un registry de
    cobertura.
- Prueba de aceptación sugerida:
  - Cambiar el path de una vista no altera la auditoría; toda ruta mutante nueva
    falla CI hasta declarar su productor.

## Hallazgos P2

### AUD-007 - Método y status HTTP producen eventos semánticamente falsos

- Severidad: alta.
- Tipo: calidad de evidencia / falsos positivos / clasificación.
- Evidencia:
  - Todo POST no-login se clasifica `CREAR`, aunque anule, edite, cierre o sea
    rechazado (`apps/auditoria/middleware.py:187-206`).
  - Cualquier 2xx o 3xx se considera éxito (`:115-120`); un redirect por permiso
    denegado entra en la misma categoría que una confirmación.
  - La rama `VER` es inalcanzable en el flujo normal porque GET no pertenece a
    `METODOS_AUDITABLES` (`:45-46`, `:111-113`, `:205-206`).
  - El evento no incluye status, resultado de negocio, objeto ni diff
    (`:216-224`).
- Reproducción validada:
  - Un POST a un path bajo `/reportes/` cuya respuesta fue redirect a
    `/sin-permiso/` se guardó como `CREATE`, `exito=True`.
- Impacto:
  - El historial puede afirmar que se creó algo cuando la operación fue negada o
    solo mostró un formulario.
  - Métricas y alertas basadas en acción/éxito heredan datos incorrectos.
- Recomendación:
  - Emitir el evento desde el servicio después de conocer el resultado de negocio
    y usar el middleware solo para contexto/correlación.
  - Si se conserva el fallback HTTP, registrar status y resultado “desconocido”,
    nunca inferir creación.
- Prueba de aceptación sugerida:
  - Validación fallida, permiso denegado y redirect no crean un evento exitoso;
    cada operación usa un tipo de dominio explícito.

### AUD-008 - La política de fallo es contradictoria y puede impedir operaciones de sesión

- Severidad: alta.
- Tipo: disponibilidad / fail-open versus fail-closed / consistencia.
- Evidencia:
  - El middleware automático captura cualquier error de escritura, lo deja solo
    en logging y permite continuar (`apps/auditoria/middleware.py:97-129`).
  - El middleware de sesión silencia toda excepción sin logging (`:243-267`).
  - En contraste, los helpers de dominio propagan el error; ventas, anulaciones
    y ajustes los invocan dentro de `transaction.atomic()`, por lo que una caída
    de auditoría revierte el hecho de negocio.
  - Login registra después de `login()` y logout registra antes de `logout()`
    (`apps/usuarios/views.py:32-39` y `:69-81`).
- Reproducción validada:
  - Al forzar `Auditoria.registrar()` a fallar, `/logout/` respondió 500 y la
    sesión continuó autenticada.
- Impacto:
  - El mismo subsistema puede perder eventos silenciosamente o tumbar caja según
    el caller.
  - Una persona que cree haber cerrado sesión puede dejar la terminal abierta.
- Recomendación:
  - Definir por categoría una política explícita. Eventos críticos deben usar un
    outbox durable/transaccional; telemetría secundaria puede degradar con alerta
    visible.
  - La seguridad de login/logout no debe depender de la disponibilidad de la
    tabla de auditoría.
- Prueba de aceptación sugerida:
  - Con el sink caído, logout siempre invalida la sesión; los hechos críticos
    quedan pendientes en outbox o abortan bajo una política documentada y
    observable.

### AUD-009 - La anulación registra el estado nuevo como si fuera el anterior

- Severidad: alta.
- Tipo: diff falso / orden de captura / anulaciones.
- Evidencia:
  - El servicio cambia `venta.estado='ANULADA'` y guarda antes de llamar al helper
    (`apps/ventas/services/anulaciones_service.py:133-156`).
  - El helper construye `datos_anteriores={'estado': venta.estado, ...}` con el
    objeto ya mutado (`apps/auditoria/models.py:323-336`).
- Reproducción validada:
  - El evento de una venta ya anulada guardó
    `datos_anteriores.estado='ANULADA'`.
- Impacto:
  - El diff no demuestra que la transición fue `COMPLETADA -> ANULADA`.
  - Investigaciones y reconstrucción de estado reciben una preimagen falsa.
- Recomendación:
  - Capturar snapshot antes de mutar o pasar explícitamente estado anterior y
    nuevo al emisor.
- Prueba de aceptación sugerida:
  - El evento conserva preimagen `COMPLETADA`, postimagen `ANULADA`, motivo, actor
    y versión/fecha dentro de la misma transacción.

### AUD-010 - El modelo acepta acciones, niveles y resultados incoherentes

- Severidad: alta.
- Tipo: validación / taxonomía / integridad semántica.
- Evidencia:
  - `choices` no crea constraints de base y `registrar()` llama directamente
    `.objects.create()` sin `full_clean()` (`apps/auditoria/models.py:103-109`,
    `:198-210`, `:234-274`).
  - No hay regla entre `exito` y `mensaje_error` (`:175-186`).
- Reproducción validada:
  - Se persistió `accion='FORGED_EVENT'`,
    `nivel_importancia='INVENTADO'`, `exito=True` y un mensaje de error no vacío.
- Impacto:
  - Filtros, dashboards y alertas omiten o interpretan mal filas válidas para la
    base pero inválidas para el contrato.
- Recomendación:
  - Validar DTO/evento antes de persistir y respaldar taxonomía e invariantes con
    `CheckConstraint` cuando sea viable.
  - Versionar el esquema de payload por tipo.
- Prueba de aceptación sugerida:
  - Tipo/nivel desconocido y combinaciones de resultado imposibles se rechazan
    atómicamente por todos los caminos, incluido ORM bulk.

### AUD-011 - `X-Forwarded-For` no confiable controla la atribución de IP

- Severidad: alta.
- Tipo: spoofing / procedencia / alertas.
- Evidencia:
  - `get_client_ip()` toma siempre el primer valor de `HTTP_X_FORWARDED_FOR`, sin
    validar que `REMOTE_ADDR` sea un proxy confiable
    (`apps/auditoria/models.py:478-493`).
  - Ese valor alimenta eventos de autenticación, operaciones y detección de
    cambio de IP.
- Reproducción validada:
  - Con `REMOTE_ADDR=10.0.0.10` y header controlado por cliente, el helper atribuyó
    `203.0.113.77`.
- Impacto:
  - Un atacante puede plantar una IP ajena, ocultar correlación de intentos o
    provocar alertas contra terceros.
- Recomendación:
  - Configurar una cadena de proxies confiables y resolver desde el salto de
    confianza; sin proxy reconocido, usar `REMOTE_ADDR`.
- Prueba de aceptación sugerida:
  - XFF directo de un cliente se ignora; uno agregado por el proxy autorizado se
    resuelve de forma determinista y validada.

### AUD-012 - El detector de cambio de IP escribe sesión en cada request y genera falsos incidentes

- Severidad: media-alta.
- Tipo: sesiones / rendimiento / señal de seguridad débil.
- Evidencia:
  - Cada request autenticado asigna de nuevo `request.session['audit_ip']`, aunque
    el valor sea igual (`apps/auditoria/middleware.py:233-265`).
  - Una asignación marca la sesión modificada y fuerza persistencia bajo backends
    normales.
  - Cualquier cambio se registra como `ERROR_SISTEMA` nivel alto; no distingue
    roaming móvil, VPN, proxy, IPv6 privacy o spoofing.
  - Cualquier fallo se silencia con `pass` (`:266-267`).
- Reproducción validada:
  - Una sesión inicialmente no modificada quedó `modified=True` al repetir la
    misma IP.
  - Un XFF falsificado generó un evento alto de cambio de IP con metadata
    controlada por el cliente.
- Impacto:
  - Aumenta escrituras y contención de sesión en cada navegación y llena el log
    con una señal poco accionable.
  - Si el sink falla, tampoco queda diagnóstico de que el detector está ciego.
- Recomendación:
  - Escribir solo cuando cambie, normalizar red/proxy y tratarlo como señal de
    riesgo correlacionada, no `ERROR_SISTEMA` aislado.
  - Instrumentar fallos del detector con métrica/alerta sin recursión.
- Prueba de aceptación sugerida:
  - IP estable no modifica sesión; cambios legítimos no disparan incidente y
    headers no confiables no controlan la señal.

### AUD-013 - Las excepciones se guardan completas y duplicadas sin redacción

- Severidad: alta.
- Tipo: privacidad / secretos / manejo de errores.
- Evidencia:
  - `process_exception()` se ejecuta para cualquier vista autenticada, no solo
    rutas críticas (`apps/auditoria/middleware.py:131-153`).
  - Inserta `str(exception)` tanto en descripción como en `mensaje_error`
    (`:143-147`).
  - Ambos campos son `TextField` sin límite operativo (`models.py:111-114`,
    `:182-186`).
- Reproducción validada:
  - `RuntimeError('password=secreto-prueba')` quedó en claro en los dos campos.
- Impacto:
  - Errores de SDK, base de datos, URLs firmadas o payloads pueden copiar tokens,
    PII y credenciales a una tabla ampliamente consultable.
  - La duplicación aumenta almacenamiento y superficie de exposición.
- Recomendación:
  - Registrar código/clase/correlation ID y mensaje sanitizado; detalles técnicos
    deben ir a un sink restringido con redacción central.
  - Aplicar límites de tamaño y política por clase de error.
- Prueba de aceptación sugerida:
  - Una matriz de password, token, Authorization, DSN, RNC y datos personales no
    aparece en ninguna representación del evento.

### AUD-014 - Dashboard, API y Admin muestran UTC como si fuera hora local

- Severidad: media-alta para investigaciones.
- Tipo: tiempo / presentación / correlación.
- Evidencia:
  - El proyecto usa `TIME_ZONE='America/Santo_Domingo'` y `USE_TZ=True`
    (`config/settings.py:226-230`).
  - Modelo, API y Admin llaman `strftime()` directamente, sin
    `timezone.localtime()` (`apps/auditoria/models.py:228-230`,
    `views.py:141-145`, `admin.py:121-125`).
- Reproducción validada:
  - Un evento a `2026-08-21 02:00 UTC`, equivalente a `20/08 22:00` local, fue
    devuelto como `21/08/2026 02:00:00`.
- Impacto:
  - Eventos cercanos a medianoche aparecen en otro día y se correlacionan cuatro
    horas tarde con caja, cámaras o soporte.
- Recomendación:
  - Serializar ISO 8601 con offset y convertir explícitamente a zona del negocio
    o del usuario; indicar la zona en UI/exportaciones.
- Prueba de aceptación sugerida:
  - Un instante conocido se presenta con offset `-04:00` y coincide en dashboard,
    API, Admin y exportaciones.

### AUD-015 - Parámetros de búsqueda malformados causan 500

- Severidad: media-alta.
- Tipo: validación de entrada / disponibilidad / API interna.
- Evidencia:
  - `pagina`, `por_pagina` y `usuario_id` se convierten con `int()` sin captura
    (`apps/auditoria/views.py:106-126`).
  - `por_pagina` solo tiene máximo, no mínimo (`:108`).
  - Las fechas se pasan crudas al ORM (`:127-130`).
- Reproducción validada:
  - `pagina=abc` y `usuario_id=abc` levantaron `ValueError`.
  - `por_pagina=0` levantó `ZeroDivisionError` en `Paginator`.
  - `fecha_desde=no-es-fecha` levantó `ValidationError`.
- Impacto:
  - Un usuario autorizado o un frontend defectuoso genera 500 y, por el propio
    middleware, más filas de error con el texto de excepción.
- Recomendación:
  - Validar mediante formulario/serializer con rangos, choices y fechas; devolver
    400 estructurado.
- Prueba de aceptación sugerida:
  - Toda combinación inválida devuelve 400 estable sin query costosa ni evento de
    error de sistema.

### AUD-016 - `registrar_compra()` no puede serializar su payload documentado

- Severidad: alta si se activa el helper.
- Tipo: helper dormido / JSON / disponibilidad.
- Evidencia:
  - El helper coloca `compra.proveedor` —instancia de modelo— dentro de
    `datos_nuevos` (`apps/auditoria/models.py:369-383`).
  - `JSONField` requiere valores serializables y el helper no transforma ID o
    nombre.
  - No existen consumidores externos actuales de `registrar_compra()`; por eso el
    defecto permanece oculto.
- Reproducción validada:
  - Una compra mínima con proveedor modelo levantó `TypeError` al persistir el
    JSON.
- Impacto:
  - Adoptar el helper recomendado puede hacer rollback de una compra si se llama
    dentro de `atomic()`.
- Recomendación:
  - Definir payload primitivo y versionado (`proveedor_id`, nombre snapshot) y
    validarlo antes de abrir la transacción de negocio.
- Prueba de aceptación sugerida:
  - El helper persiste con proveedor real y su JSON puede serializarse/deserializarse
    sin adaptadores implícitos.

### AUD-017 - El cierre diario usaba un esquema de auditoría inexistente

- Severidad: alta.
- Tipo: consumidor legacy / operación parcial / observabilidad.
- Estado al entregar: corrección concurrente en curso; pendiente de revalidación
  fuera de esta auditoría.
- Evidencia:
  - El comando pasa `tabla`, `registro_id` e `importancia`, campos ausentes del
    modelo, y acciones fuera del catálogo
    (`apps/reportes/management/commands/generar_cierre_diario.py:56-77`).
  - Lo hace después de crear cierre, PDF y guardar referencia (`:31-64`).
  - El bloque `except` repite el mismo `.objects.create()` inválido (`:66-78`).
- Reproducción validada:
  - Crear una fila con el kwargs exacto del comando levantó `TypeError` por campos
    desconocidos.
- Cambio concurrente observado después de la reproducción:
  - El usuario reemplazó esos kwargs por `Auditoria.registrar()`, añadió
    `CIERRE_DIARIO` al catálogo y protegió el error original si la auditoría de
    fallo tampoco puede escribirse.
  - El cambio todavía estaba sin commit y no se incluyó en la batería definitiva;
    requiere su propia migración/check y pruebas del comando completo.
- Impacto:
  - El cierre puede quedar creado y el PDF escrito, pero el job termina fallando;
    el intento de registrar el error también falla y oculta la causa original.
- Recomendación:
  - Migrar al contrato actual mediante un servicio transaccional/idempotente y no
    usar auditoría como paso posterior que redefine el éxito del job.
- Prueba de aceptación sugerida:
  - Éxito y fallo del cierre dejan eventos válidos una sola vez; un fallo del sink
    no sustituye la excepción original ni duplica cierres.

### AUD-018 - La taxonomía promete una cobertura que los productores no implementan

- Severidad: alta.
- Tipo: cobertura de dominio / contrato declarativo / acciones huérfanas.
- Evidencia:
  - `TipoAccion` declara 32 valores para productos, inventario, ventas, usuarios,
    permisos, backup, configuración y sistema
    (`apps/auditoria/models.py:47-90`).
  - No hay productores externos para `PRODUCTO_CREADO/EDITADO/ELIMINADO`,
    `PRECIO_MODIFICADO`, `COMPRA_REGISTRADA`, `LOTE_CREADO`, las cinco acciones
    de usuario, las dos de permisos ni las dos de backup.
  - Configuración solo se usa para un override de crédito; los cambios de
    `ConfiguracionNegocio` no lo emiten.
  - Los helpers existentes se mezclan con `CREAR/EDITAR` genéricos, dificultando
    métricas consistentes.
- Impacto:
  - El catálogo y el dashboard sugieren cobertura completa donde solo existen
    etiquetas disponibles.
  - Altas/bajas de usuario, permisos, precio, producto, backup y configuración
    pueden no ser reconstruibles.
- Recomendación:
  - Mantener un registry de evento -> productor -> esquema -> criticidad y hacer
    fallar CI si un tipo obligatorio carece de emisor probado.
  - Retirar tipos puramente aspiracionales o marcarlos explícitamente no
    soportados.
- Prueba de aceptación sugerida:
  - Una matriz de operaciones críticas produce exactamente un evento del tipo
    esperado con actor, ámbito, pre/postimagen y correlación.

## Hallazgos P3

### AUD-019 - El visor se anuncia completo, pero oculta datos necesarios para investigar

- Severidad: media.
- Tipo: observabilidad / UI / contrato incompleto.
- Evidencia:
  - El dashboard dice “Registro completo de acciones del sistema”
    (`templates/auditoria/dashboard.html:19-23`).
  - El API solo entrega fecha, usuario, acción, descripción, nivel, éxito e IP
    (`apps/auditoria/views.py:140-153`).
  - Omite sucursal, content type/object ID, datos anteriores/nuevos, metadata,
    mensaje de error y user agent.
  - Tampoco existe filtro por sucursal, objeto o correlation ID
    (`views.py:92-115`, template `:45-130`).
- Impacto:
  - El investigador debe saltar a Django Admin o base de datos y no puede validar
    un diff desde la interfaz RBAC.
- Recomendación:
  - Añadir detalle seguro por evento, filtro de ámbito y campos redactados según
    permiso; no exponer payload completo en la lista.
- Prueba de aceptación sugerida:
  - Desde el visor autorizado se reconstruye quién, qué, antes/después, dónde,
    resultado y correlación sin acceder a Admin.

### AUD-020 - No existe lifecycle de retención y las consultas degradarán con el crecimiento

- Severidad: media.
- Tipo: almacenamiento / privacidad / rendimiento.
- Evidencia:
  - No hay comando, setting ni política de archivo/purga en `apps/auditoria`.
  - Descripción, user agent, error y tres JSON no tienen cuota de tamaño
    (`apps/auditoria/models.py:111-186`).
  - El middleware puede crear eventos por excepción y cambio de IP en tráfico
    normal.
  - Los filtros de fecha usan `fecha_hora__date`, que normalmente aplica una
    transformación a la columna (`apps/auditoria/views.py:127-130`), y la
    paginación es por offset/count total (`:136-164`).
- Impacto:
  - La tabla crece indefinidamente con PII y texto controlable, mientras búsquedas
    y conteos se vuelven más caros.
  - No hay equilibrio documentado entre investigación, obligación legal y
    minimización de datos.
- Recomendación:
  - Definir retención por tipo/nivel, particionado/archivo verificable y límites
    de payload; usar rangos datetime indexables y keyset para navegación profunda.
- Prueba de aceptación sugerida:
  - Un volumen objetivo conserva SLA medido, la purga deja manifiesto verificable
    y ninguna categoría supera su retención aprobada.

### AUD-021 - La relación genérica no garantiza la identidad histórica del objeto

- Severidad: media-baja.
- Tipo: GenericForeignKey / portabilidad / objeto mutable.
- Evidencia:
  - `object_id` es `PositiveIntegerField` y `GenericForeignKey` no crea FK real
    hacia el objeto (`apps/auditoria/models.py:116-131`).
  - Limita productores futuros con PK UUID/string.
  - Admin resuelve y muestra el objeto en su estado actual; si falta, solo indica
    “Objeto eliminado” (`apps/auditoria/admin.py:173-190`).
  - Muchos eventos no guardan snapshot suficiente para identificar el objeto si
    se renombra o elimina.
- Impacto:
  - El enlace puede quedar huérfano o apuntar a una identidad cuyo nombre actual
    no coincide con el ocurrido; nuevos modelos con PK no entero no caben.
- Recomendación:
  - Guardar `object_type`, `object_id` textual e identificador/nombre snapshot
    inmutables; usar el enlace genérico solo como conveniencia.
- Prueba de aceptación sugerida:
  - Renombrar/eliminar el objeto no cambia su identidad histórica y un modelo UUID
    puede emitir sin pérdida.

### AUD-022 - La app crítica no tiene pruebas propias

- Severidad: media.
- Tipo: cobertura / regresión.
- Evidencia:
  - `apps/auditoria/tests/` contiene únicamente `__init__.py`.
  - Ninguna prueba propia cubre vistas, filtros, Admin, helpers, middleware,
    timestamps, redacción, inmutabilidad, sucursal o taxonomía.
  - Parte de la conducta aparece indirectamente en suites de tenancy y módulos de
    negocio, pero no existe un contrato central.
- Impacto:
  - Rutas desfasadas, helper JSON inválido y fuga entre sucursales permanecieron
    sin alerta aunque otras 150 pruebas seleccionadas pasaban.
- Recomendación:
  - Convertir las reproducciones de esta auditoría en una suite permanente por
    capas: modelo/integridad, emisor, middleware, RBAC/scope, privacidad y UI.
- Prueba de aceptación sugerida:
  - CI falla ante pérdida de ámbito, modificación/borrado, evento faltante,
    redacción incompleta, timestamp incorrecto o productor sin esquema.

## Validación ejecutada

### Suite existente seleccionada

Se creó únicamente para la corrida un settings aislado con base
`test_pos_fifo_auditoria_auditoria_20260821`. Django creó y destruyó esa base; no
se usó la base compartida del desarrollador. Los resultados siguientes se
obtuvieron antes de la edición concurrente que añadió `CIERRE_DIARIO`.

```text
manage.py test \
  apps.auditoria \
  apps.permisos.tests.test_engine \
  apps.permisos.tests.test_cutover_local \
  apps.tenancy.tests.test_router \
  apps.ventas.tests.test_ventas_service \
  apps.ventas.tests.test_anulaciones \
  apps.inventario.tests.test_auditoria_inventario \
  apps.caja.tests.test_auditoria_caja \
  apps.cuentas_por_cobrar.tests.test_auditoria_cxc \
  --settings=config.settings_auditoria_auditoria_temp --noinput -v 1
```

Resultado:

- **150 pruebas ejecutadas**.
- **150 aprobadas**.
- Duración: **45.130 s**.
- `System check identified no issues`.
- Base temporal destruida al terminar.

### Batería adversarial temporal

Se añadieron transitoriamente veintidós casos para observar el comportamiento
actual. Resultado definitivo:

- **22 pruebas ejecutadas**.
- **22 reproducciones confirmadas**.
- Duración: **3.310 s**.
- `System check identified no issues`.

Los casos confirmaron:

1. Lectura de B con permiso acotado a A.
2. Estadísticas de B incluidas para el mismo visor.
3. Renombre del actor reescribiendo su presentación histórica.
4. FK nula clasificando una acción humana como `Sistema`.
5. Edición y borrado por ORM.
6. Permiso de borrado de evidencia para superusuario en Admin.
7. Acción, nivel y resultado incoherentes persistidos.
8. Tres variantes numéricas malformadas levantando excepción.
9. Fecha inválida levantando excepción.
10. UTC mostrado sin conversión a Santo Domingo.
11. XFF no confiable controlando la IP atribuida.
12. Sesión marcada modificada aun con IP idéntica.
13. XFF falsificado generando alerta de cambio de IP.
14. Tres rutas críticas reales fuera de la allowlist.
15. Redirect guardado como creación exitosa.
16. Secreto de una excepción conservado dos veces en claro.
17. Helper de venta omitiendo sucursal disponible.
18. Anulación guardando `ANULADA` como estado anterior.
19. Helper de compra fallando al serializar proveedor modelo.
20. Esquema legacy del comando de cierre rechazado por el modelo.
21. Fallo de auditoría impidiendo logout.
22. POST API omitido antes de crear contexto bajo tenancy.

El archivo de pruebas y el settings temporal fueron eliminados después de la
validación. No se conservaron cambios funcionales.

### Chequeos estáticos de Django

```text
manage.py check --settings=config.settings_auditoria_auditoria_temp
System check identified no issues (0 silenced).

manage.py makemigrations auditoria --check --dry-run \
  --settings=config.settings_auditoria_auditoria_temp
No changes detected in app 'auditoria'
```

## Aspectos positivos observados

- El acceso al dashboard y API tiene gate RBAC explícito y la navegación también
  consulta `auditoria.ver`.
- En modo DB-per-tenant, el router aloja `Auditoria` en la base tenant activa, una
  base útil para aislamiento entre negocios cuando el contexto ya está enlazado.
- Ventas, anulaciones y ajustes escriben su evento dentro de la transacción del
  hecho; no dejan auditoría de una operación que luego hace rollback por otra
  causa.
- Los eventos admiten preimagen, postimagen, metadata, objeto, IP, user agent,
  nivel y resultado.
- El modelo preserva filas cuando el objeto genérico desaparece y usa `SET_NULL`
  para no bloquear lifecycle de usuario/sucursal.
- Hay índices para usuario/fecha, acción/fecha, objeto, nivel, éxito y
  sucursal/fecha.
- Admin bloquea agregar y modificar mediante su UI ordinaria y escapa los JSON al
  renderizarlos con `format_html`.
- El dashboard usa `json_script` y Alpine `x-text`, evitando insertar directamente
  las descripciones como HTML activo.
- La API limita `por_pagina` a 100 por arriba y usa paginación.
- Las tres migraciones estaban alineadas con el snapshot auditado; la adición
  concurrente posterior de `CIERRE_DIARIO` debe volver a pasar el check.
- Algunos productores recientes —impresión y edición de compra— ya pasan sucursal
  explícita y datos antes/después, un patrón aprovechable para normalizar el resto.

## Orden recomendado de remediación

1. **Cerrar acceso e identidad:** AUD-001, AUD-003 y AUD-004 como un único
   contrato actor/tenant/sucursal.
2. **Hacer la evidencia resistente:** AUD-002 con append-only, privilegios
   separados, verificación y archivo externo.
3. **Garantizar cobertura real:** AUD-005, AUD-006 y AUD-018 mediante registry de
   eventos de dominio y matriz de endpoints.
4. **Definir semántica y fallo:** AUD-007, AUD-008, AUD-009 y AUD-010 antes de
   confiar en métricas o bloquear operaciones por el sink.
5. **Proteger procedencia y privacidad:** AUD-011 a AUD-013.
6. **Corregir consumidores rotos:** AUD-016 y revalidar la corrección concurrente
   de AUD-017; después migrar todos los productores al mismo contrato versionado.
7. **Completar operación del visor:** AUD-014, AUD-015 y AUD-019 a AUD-022.

No conviene empezar agregando más llamadas sueltas a `Auditoria.registrar()`.
Sin ámbito obligatorio, identidad snapshot, esquema validado y política de fallo,
cada llamada nueva puede aumentar volumen sin aumentar evidencia confiable.

## Criterios de cierre de la auditoría

La aplicación puede considerarse cerrada cuando, como mínimo:

- todo lector aplica negocio y conjunto exacto de sucursales a filas, conteos,
  usuarios, filtros y exportaciones;
- cada evento conserva actor snapshot, canal, tenant, sucursal y objeto snapshot;
- el runtime no puede actualizar ni borrar y existe verificación independiente de
  integridad;
- cada operación crítica web/API/job tiene exactamente un productor de dominio
  probado;
- ninguna cobertura depende de fragments de URL;
- los eventos usan esquema y taxonomía versionados con constraints de resultado;
- la anulación conserva preimagen y postimagen correctas;
- la caída del sink sigue una política uniforme, observable y segura para sesión
  y transacciones;
- IP/proxy se resuelve desde una cadena de confianza y las excepciones se redactan;
- la UI usa hora local explícita y rechaza filtros inválidos con 400;
- helpers y comandos actuales serializan únicamente payloads primitivos válidos;
- existe retención verificable, límites de tamaño y SLA de consulta a volumen
  objetivo;
- las veintidós reproducciones quedan convertidas en pruebas de rechazo,
  aislamiento, integridad o degradación controlada.

## Conclusión

El problema principal de `apps/auditoria` no es que falten columnas: es que la
tabla todavía funciona como un log de aplicación mutable y de cobertura optativa,
no como evidencia. Una fila puede cruzar sucursales al consultarse, perder actor y
sucursal, guardar una transición falsa, aceptar una acción inventada y luego ser
editada o borrada sin dejar señal.

La mejora de mayor retorno es definir un sobre de evento inmutable y versionado
—actor snapshot, tenant, sucursal, objeto, pre/postimagen, resultado y
correlación— y obligar a que cada servicio crítico lo emita mediante outbox hacia
un sink append-only verificable. Sobre esa base sí tiene sentido mejorar el
dashboard, la retención y las alertas; antes de ella, “más logs” no equivale a
“más trazabilidad”.
