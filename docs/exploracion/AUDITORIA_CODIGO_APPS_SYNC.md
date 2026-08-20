# Auditoria de codigo - apps/sync

Fecha: 2026-08-20
Scope principal: `apps/sync`
Scope de verificacion: productor local de eventos, comandos operativos,
receptor cloud en `apps/api/views/sync.py`, serializers de maestros, modelos
replicados y pruebas de sync/API relacionadas.
Modo: lectura, ejecucion de checks/pruebas existentes y documentacion de
hallazgos; no se aplicaron cambios funcionales.

> **Estado (2026-08-20, misma fecha): MITIGADO.** Los 12 hallazgos se
> verificaron contra el codigo y los 12 resultaron reales. Todos estan
> corregidos, con pruebas de regresion. Ver
> [Estado de mitigacion](#estado-de-mitigacion) al final del documento.
> **Incluye 3 migraciones**: leer la seccion de despliegue antes de promover.

## Por que esta app sigue en la auditoria

Despues de `apps/api` y `apps/ventas`, `apps/sync` es la siguiente frontera de
mayor impacto sistemico. Es el unico puente automatico entre cada POS local y el
cloud, y transporta o actualiza:

- ventas, pagos, caja y cuentas por cobrar;
- compras, movimientos y snapshots de inventario;
- clientes, categorias y productos;
- roles, permisos y asignaciones de usuarios;
- configuracion operativa administrada desde el portal.

La app contiene aproximadamente 3,541 lineas productivas y 1,532 lineas de
pruebas propias. Tambien depende de unas 1,300 lineas del receptor cloud en
`apps/api/views/sync.py`. Por ese alcance, un error que parezca solo de
telemetria o reintento puede convertirse en perdida, duplicacion o divergencia
persistente entre sucursal y cloud.

## Resumen

La direccion arquitectonica es buena y las correcciones del roadmap reciente
son visibles en el codigo: outbox local, hash idempotente, cursor keyset
`(fecha_modificacion, id)`, marca de agua contigua, orden explicito de pulls y
handlers cloud atomicos por evento. Las 91 pruebas seleccionadas pasan.

Sin embargo, varias garantias descritas en comentarios y documentacion todavia
no se sostienen en todos los caminos. El lock del push no constituye un claim
duradero; la deduplicacion cloud no esta protegida por una restriccion unica; un
fallo del `INSERT` del outbox se ignora; siete tipos de eventos con objeto local
no pueden recuperarse de `SIN_PAYLOAD`; y algunos handlers/pulls convierten
omisiones en exito y avanzan el cursor o confirman el evento.

El resultado posible es especialmente delicado: un ciclo puede figurar
`EXITOSO` mientras maestros o permisos no se descargaron, una venta puede
confirmarse en cloud sin todas sus lineas y dos workers pueden aplicar dos veces
un pago o movimiento de caja.

## Hallazgos priorizados

### SYNC-001 - Un fallo al insertar el outbox se ignora y el hecho de negocio puede quedar sin evento

- Prioridad: P1.
- Severidad: alta.
- Tipo: bug de durabilidad / contradiccion del contrato outbox.
- Evidencia:
  - `apps/sync/events.py:21-23` documenta que es imposible conservar el hecho de
    negocio sin su evento.
  - `apps/sync/events.py:106-122` ejecuta el `INSERT` en un savepoint, captura
    cualquier excepcion y devuelve `None`, permitiendo que la transaccion
    exterior siga y haga commit.
  - `apps/sync/events.py:68` declara que `_crear_evento` nunca lanza excepcion.
  - Los helpers publicos devuelven el resultado, pero los flujos normales de
    venta, caja, inventario, cotizaciones y CxC no exigen que exista la fila.
  - `verificar_sync` solo puede reconstruir los tipos incluidos en
    `apps/sync/registry.py`; no es una garantia equivalente al outbox atomico.
- Escenario demostrable:
  - La operacion de negocio se guarda correctamente, pero el `INSERT` de
    `EventoSync` falla por un error de esquema, restriccion, tipo de dato o
    indisponibilidad de la tabla. El savepoint se revierte, el error solo queda
    en log y la operacion principal confirma sin nada que el daemon pueda
    reintentar.
- Impacto:
  - Perdida silenciosa de ventas, pagos, caja o inventario en cloud.
  - La garantia anunciada por el patron outbox no existe precisamente en el
    fallo de persistencia que deberia cubrir.
- Sugerencia de arreglo:
  - Separar fallo de serializacion de fallo de persistencia. El primero puede
    degradar a `SIN_PAYLOAD`; el segundo debe impedir el commit del hecho o usar
    un mecanismo alterno realmente durable.
  - Hacer explicito por tipo de operacion si se prefiere disponibilidad local o
    entrega garantizada, y monitorear cualquier excepcion de persistencia como
    incidente critico, no solo como log.

### SYNC-002 - La recuperacion de `SIN_PAYLOAD` no existe para siete tipos de hechos con objeto local

- Prioridad: P1.
- Severidad: alta.
- Tipo: bug de reintento / perdida permanente de eventos.
- Evidencia:
  - `apps/sync/constants.py:14-30` define 15 tipos de evento.
  - `apps/sync/registry.py:95-161` registra solo siete: venta creada, apertura,
    cierre y movimiento de caja, compra, ajuste y cotizacion creada.
  - Quedan fuera, aunque reciben `objeto_id_local`, `VENTA_ANULADA`,
    `INVENTARIO_MOVIMIENTO_REGISTRADO`, `COTIZACION_CONVERTIDA` y los cuatro
    eventos `CXC_*`.
  - `apps/sync/engine.py:139-149` marca error cuando el tipo no esta en el
    registro; tras agotar intentos termina en `DESCARTADO`.
  - `apps/sync/management/commands/verificar_sync.py:338-357` tambien itera solo
    el registro, por lo que no detecta ni hace backfill de esos hechos derivados.
- Escenario demostrable:
  - Falla temporalmente `serializar_pago_cxc`, `serializar_anulacion_venta` o
    `serializar_movimiento_inventario`. El evento se crea como `SIN_PAYLOAD`,
    pero cada push concluye que no es reserializable y finalmente lo descarta,
    aunque el pago, anulacion o movimiento siga existiendo en la BD local.
- Impacto:
  - El mecanismo presentado como recuperacion diferida solo cubre parte del
    contrato y puede perder cambios financieros posteriores a la creacion.
- Sugerencia de arreglo:
  - Registrar por tipo todos los eventos que tengan un objeto local y un
    serializador reproducible, aunque varios tipos apunten al mismo modelo.
  - Para transiciones cuyo estado actual ya no permite reconstruir el evento
    historico, persistir en la fila un envelope minimo inmutable suficiente para
    regenerarlo.
  - Agregar una asercion que compare tipos reserializables declarados contra el
    registro y pruebas parametrizadas para todos ellos.

### SYNC-003 - El claim local y la deduplicacion cloud no son seguros ante workers concurrentes

- Prioridad: P1.
- Severidad: critica para pagos y caja.
- Tipo: bug de concurrencia / doble aplicacion financiera.
- Evidencia:
  - `apps/sync/engine.py:188-218` toma `select_for_update(skip_locked=True)`,
    pero dentro de la transaccion solo actualiza `sent_at`; el estado sigue
    siendo enviable.
  - La transaccion y sus locks terminan antes del `POST` de
    `apps/sync/engine.py:237-244`. Otro worker puede reclamar las mismas filas
    mientras la primera solicitud sigue en vuelo.
  - `apps/api/views/sync.py:87-104` consulta si el hash existe antes de entrar a
    la transaccion que ejecuta el handler.
  - `apps/sync/models.py:94-100` indexa `hash_payload`, pero no lo declara unico
    ni lo combina en una restriccion unica con sucursal.
  - `_handler_cxc_pago` (`apps/api/views/sync.py:1204-1219`) y
    `_handler_movimiento_caja` (`apps/api/views/sync.py:763-790`) hacen
    `exists()` seguido de `create()` sin una restriccion de identidad estable.
  - `PagoCxC` y `MovimientoCaja` tampoco tienen una constraint que cierre esas
    ventanas de check-then-insert.
- Escenario demostrable:
  - El daemon y una ejecucion manual `sincronizar --once` se solapan. Ambos
    envian el mismo pago; dos requests cloud consultan el hash antes de que
    ninguna haya insertado el recibo y ambos handlers crean el pago.
- Impacto:
  - Pagos CxC, ingresos, gastos o retiros duplicados en cloud.
  - Una respuesta tardia de un worker tambien puede sobrescribir localmente el
    estado confirmado con `ERROR` usando una instancia obsoleta.
- Sugerencia de arreglo:
  - Persistir un claim `IN_FLIGHT` con propietario y lease antes de soltar el
    lock, y finalizarlo mediante updates condicionales por estado/claim.
  - En cloud, reservar el recibo con una restriccion unica por identidad de
    origen antes de ejecutar el handler dentro de la misma transaccion.
  - Incluir IDs de origen estables en pagos y movimientos y protegerlos tambien
    con constraints de dominio.
  - Probar con `TransactionTestCase`, dos conexiones y dos requests realmente
    concurrentes.

### SYNC-004 - Una venta puede quedar confirmada en cloud con lineas omitidas de forma irreversible

- Prioridad: P1.
- Severidad: alta.
- Tipo: bug de aplicacion parcial / integridad de reportes.
- Evidencia:
  - `apps/api/views/sync.py:649-657` busca cada producto por SKU; si no existe,
    solo registra un warning y continua con las demas lineas.
  - `apps/api/views/sync.py:103-117` confirma el evento completo cuando el
    handler retorna sin excepcion.
  - En un reenvio, `apps/api/views/sync.py:609-623` detecta que la venta ya
    existe y retorna; solo contempla corregir el cliente, no reconstruir
    detalles ni pagos faltantes.
  - El handler de cotizacion, en contraste, levanta error si falta un producto
    (`apps/api/views/sync.py:1037-1042`), por lo que los contratos no son
    consistentes.
- Escenario demostrable:
  - La sucursal vende un SKU que todavia no existe en cloud. Se crea la cabecera
    con su total y pagos, se omite esa linea y el evento se confirma. Cuando el
    producto aparece, el mismo evento ya no puede reparar la venta.
- Impacto:
  - Totales de cabecera y pagos no cuadran con detalles; top de productos,
    margenes y trazabilidad de inventario quedan incompletos.
- Sugerencia de arreglo:
  - Validar todas las dependencias antes de crear la venta y fallar el evento
    completo si falta una.
  - Hacer el reenvio correctivo idempotente para lineas y pagos usando identidad
    de origen, no solo para el cliente.

### SYNC-005 - Pulls fallidos y hasta pushes parciales pueden registrarse como `EXITOSO`

- Prioridad: P1.
- Severidad: alta.
- Tipo: bug de observabilidad / salud operativa engañosa.
- Evidencia:
  - `apps/sync/engine.py:429-439` convierte fallos de red y HTTP de cada pull en
    un `break`; retorna un conteo, no un estado de error.
  - `apps/sync/engine.py:331-364` captura las demas excepciones por entidad y
    devuelve metricas con cero, sin lista de errores.
  - `apps/sync/management/commands/sincronizar.py:131-164` imprime el pull con
    estilo de exito sin evaluar errores ni `heartbeat_ok`.
  - Ese comando crea siempre un `LogSync(resultado='EXITOSO')` en
    `apps/sync/management/commands/sincronizar.py:166-172`, incluso si el push
    reporto fallidos.
  - La ruta alterna `SyncEngine.ciclo_completo` solo considera
    `push['fallidos']` (`apps/sync/engine.py:964-974`); tambien ignora heartbeat
    y fallos de pull.
  - `sync_status` usa el ultimo `LogSync` como señal de salud y ni siquiera
    incluye `SIN_PAYLOAD` en sus conteos (`apps/sync/management/commands/sync_status.py:58-72`).
- Escenario demostrable:
  - `/health/` responde 200, pero todos los endpoints autenticados de maestros
    responden 401/500. El ciclo imprime conteos en cero, guarda `EXITOSO` y
    `sync_status` muestra una ultima corrida exitosa.
- Impacto:
  - Catalogos, reglas de credito o permisos pueden quedar obsoletos durante dias
    sin que el estado operativo lo revele.
- Sugerencia de arreglo:
  - Devolver metricas estructuradas por entidad con `ok`, error, paginas y
    cursor; clasificar el ciclo como `PARCIAL` o `FALLO` cuando corresponda.
  - Unificar el management command con `ciclo_completo` para que exista una sola
    politica de logging y hacer que `sync_status` muestre cursores bloqueados,
    antiguedad y todos los estados de cola.

### SYNC-006 - Omisiones de dependencias cuentan como aplicadas y avanzan el cursor para siempre

- Prioridad: P1.
- Severidad: alta, especialmente para RBAC.
- Tipo: bug de convergencia / autorizacion.
- Evidencia:
  - `_pull_generic` incrementa `count` y considera aplicado cualquier callback
    que retorne sin excepcion (`apps/sync/engine.py:465-483`). No existe un
    resultado `deferido` o `omitido`.
  - Si un producto existente referencia una categoria ausente,
    `apps/sync/engine.py:632-660` solo avisa y actualiza el producto sin cambiar
    la categoria; el cursor avanza.
  - Si falta el usuario o el rol, `_pull_asignaciones` retorna normalmente en
    `apps/sync/engine.py:764-793`; la asignacion se da por aplicada y no vuelve a
    descargarse cuando aparezca la dependencia.
  - `_pull_roles` resuelve permisos con un filtro
    `codigo__in` (`apps/sync/engine.py:730-741`). Los codigos desconocidos por
    desfase de version se omiten y `set()` guarda un rol parcial sin error.
- Escenario demostrable:
  - El pull de roles falla y el de asignaciones continua. Una asignacion cuyo rol
    aun no existe se omite, pero su cursor avanza. En el ciclo siguiente el rol
    llega; como la asignacion no cambio en cloud, ya no se envia otra vez.
- Impacto:
  - Usuarios sin permisos esperados o roles incompletos; productos enlazados a
    categorias viejas. El sistema converge solo si alguien modifica nuevamente
    el registro cloud o reinicia manualmente el cursor.
- Sugerencia de arreglo:
  - Hacer que cada `apply` retorne `APLICADO`, `DEFERIDO` u `OMITIDO_INTENCIONAL`.
    Solo el primero debe confirmar la marca de agua.
  - Tratar dependencias faltantes y codigos de permiso desconocidos como bloqueo
    reintentable, con diagnostico explicito.
  - Si se desea continuar con items posteriores, mantener una cola durable de
    diferidos en vez de olvidar la fila al avanzar el cursor.

### SYNC-007 - Categorias y clientes se identifican por campos mutables y pueden duplicarse al renombrarlos

- Prioridad: P1.
- Severidad: alta.
- Tipo: bug de identidad cross-DB / ruptura de relaciones historicas.
- Evidencia:
  - Los payloads de maestros incluyen el `id` cloud
    (`apps/api/serializers/maestros.py:30-42` y `:225-242`).
  - `_pull_categorias` ignora ese ID y usa `nombre` como lookup
    (`apps/sync/engine.py:613-627`).
  - `_pull_clientes` usa cedula cuando existe o `(nombre, tipo)` cuando no
    (`apps/sync/engine.py:664-698`); tampoco persiste una identidad cloud.
  - El propio docstring de `CategoriaSerializer` dice que la sucursal usa `id`
    como lookup (`apps/api/serializers/maestros.py:19-26`), pero el engine hace
    lo contrario.
  - Cambiar el nombre de una categoria no modifica automaticamente
    `Producto.fecha_modificacion`, por lo que esos productos no necesariamente
    vuelven a aparecer en el pull incremental.
- Escenario demostrable:
  - Se renombra una categoria en el portal. La sucursal crea otra categoria con
    el nombre nuevo y conserva los productos historicos enlazados a la anterior.
  - Se renombra un cliente sin cedula o se corrige su cedula; la sucursal crea
    otro cliente y las ventas/CxC previas permanecen en el registro viejo.
- Impacto:
  - Duplicados visibles, filtros y reportes fragmentados, limites de credito y
    cartera separados entre dos identidades que representan la misma entidad.
- Sugerencia de arreglo:
  - Persistir `origen_cloud_id` o una tabla de mapeo por entidad y usarla como
    identidad primaria de pull. Las claves naturales deben servir para bootstrap
    y reconciliacion, no para actualizaciones ordinarias.
  - Preparar backfill y deteccion de colisiones antes de activar el nuevo lookup.

### SYNC-008 - Un snapshot viejo puede sobrescribir inventario cloud mas reciente

- Prioridad: P2.
- Severidad: media-alta.
- Tipo: bug de orden temporal / last-write-wins incorrecto.
- Evidencia:
  - `_handler_inventario_snapshot` toma el timestamp del payload, pero hace
    `update_or_create` incondicional por sucursal/SKU
    (`apps/api/views/sync.py:908-929`).
  - No compara el timestamp entrante con el ya persistido.
  - La concurrencia descrita en SYNC-003, los reintentos y ejecuciones manuales
    permiten que eventos se apliquen fuera de orden.
- Escenario demostrable:
  - El snapshot T2 se aplica primero; despues llega un reintento de T1. El
    handler sustituye stock, bajo-stock y valor FIFO actuales por la foto vieja.
- Impacto:
  - El portal puede mostrar existencias y valuacion retrocedidas hasta que llegue
    otro snapshot.
- Sugerencia de arreglo:
  - Aplicar un update condicional solo cuando `timestamp_entrante >= timestamp`
    almacenado, preferiblemente protegido en BD.
  - Probar explicitamente entrega T2 seguida de T1.

### SYNC-009 - Un ACK 2xx incompleto no consume reintentos y puede atascar la cabeza de la cola

- Prioridad: P2.
- Severidad: media-alta.
- Tipo: bug de protocolo / reintento infinito.
- Evidencia:
  - `apps/sync/engine.py:279-290` construye el mapa desde `detalle`; si falta el
    hash de un evento solo aumenta `metricas['fallidos']`.
  - No llama `marcar_error`, por lo que `intentos` no aumenta, no queda una causa
    en `ultimo_error` y nunca alcanza `DESCARTADO`.
  - Tampoco se valida que la respuesta JSON sea un objeto con el schema esperado
    antes de usar `.get()`.
- Escenario demostrable:
  - Un proxy, version incompatible o bug cloud responde 200 con `detalle=[]`.
    Los mismos eventos mas antiguos reaparecen en cada batch indefinidamente.
- Impacto:
  - Reintentos sin limite ni diagnostico por evento; crecimiento y retraso de la
    cola, con metricas agregadas que no explican la causa.
- Sugerencia de arreglo:
  - Validar el contrato completo del ACK. Toda fila enviada debe terminar en
    confirmado o en `marcar_error('ACK ausente/invalido')`.
  - Agregar pruebas de JSON lista, `detalle` ausente, hash ausente, hash repetido
    y estado desconocido.

### SYNC-010 - El recorrido keyset puede entrar en un loop infinito si una pagina no hace avanzar la clave

- Prioridad: P2.
- Severidad: media-alta.
- Tipo: bug de disponibilidad / contrato de paginacion.
- Evidencia:
  - `_pagina_ordenada` ignora items sin clave valida y devuelve `True` incluso si
    ninguno tiene clave (`apps/sync/engine.py:497-513`).
  - `_pull_generic` solo actualiza `req_fecha/req_id` cuando `clave` no es
    `None` (`apps/sync/engine.py:465-483`).
  - Si la respuesta es paginada y `next` existe, el while continua
    (`apps/sync/engine.py:488-492`) con exactamente los mismos parametros.
  - La prueba actual de item sin ID usa una sola pagina; no cubre `next` ni falta
    de progreso (`apps/sync/tests/test_pull_keyset.py:248-263`).
- Escenario demostrable:
  - El cloud devuelve una pagina con `fecha_modificacion` o ID ausente y un
    enlace `next`. El cliente vuelve a pedir la misma frontera para siempre y el
    daemon no termina el ciclo.
- Impacto:
  - Un solo endpoint malformado detiene todos los pulls posteriores y evita el
    siguiente intervalo del daemon.
- Sugerencia de arreglo:
  - Exigir clave valida y estrictamente mayor que la frontera previa para cada
    pagina keyset; abortar con bloqueo visible si no hay progreso.
  - Añadir un limite defensivo de paginas/items por ciclo.

### SYNC-011 - La accion de Admin “Reintentar” no reactiva eventos que agotaron intentos

- Prioridad: P2.
- Severidad: media.
- Tipo: bug operativo / accion administrativa engañosa.
- Evidencia:
  - `apps/sync/admin.py:63-68` cambia `ERROR`/`DESCARTADO` a `PENDIENTE`, pero no
    reinicia `intentos`.
  - `apps/sync/engine.py:193-195` excluye del push cualquier fila cuyo contador
    sea mayor o igual a `SYNC_MAX_RETRIES`.
  - El comando `verificar_sync --reintentar-descartados`, en contraste, si pone
    `intentos=0` (`apps/sync/management/commands/verificar_sync.py:169-203`).
- Impacto:
  - El Admin informa que el evento fue puesto en cola, pero el daemon nunca lo
    selecciona; el operador puede creer que la reparacion esta en curso.
- Sugerencia de arreglo:
  - Reutilizar una unica funcion de dominio para reintentos y reiniciar contador,
    error y timestamps de manera consistente y auditada.

### SYNC-012 - Una falla de lectura en `reconciliar_cloud` se interpreta como “no existe”

- Prioridad: P2.
- Severidad: media.
- Tipo: bug de reconciliacion / creacion ambigua.
- Evidencia:
  - `_buscar` devuelve `None` tanto cuando no hay coincidencia como cuando falla
    la red o el cloud responde error (`apps/sync/management/commands/reconciliar_cloud.py:248-266`).
  - Los reconciliadores interpretan `None` como ausencia y llaman al POST de
    creacion (`:289-331`, `:338-402` y `:423-484`).
  - El caso es especialmente ambiguo para clientes sin cedula, cuya busqueda es
    por nombre/tipo y no necesariamente tiene una restriccion unica equivalente.
  - El comando termina mostrando “Reconciliacion finalizada” aun si acumulo
    errores por objeto (`:140-154`).
- Escenario demostrable:
  - El GET de busqueda de un cliente existente falla transitoriamente; el
    comando intenta crearlo como si faltara y puede duplicarlo o producir un
    error evitable.
- Impacto:
  - Bootstrap parcial, duplicados o una salida global de exito que exige revisar
    manualmente todo el scroll para descubrir fallos.
- Sugerencia de arreglo:
  - Distinguir `ENCONTRADO`, `NO_ENCONTRADO` y `ERROR`; ante error de lectura no
    escribir.
  - Acumular un resumen estructurado y terminar con codigo distinto de cero si
    hubo errores.

## Riesgos ya documentados que no se duplican como hallazgo nuevo

- `docs/BUGS.md` y `docs/ROADMAP_SYNC_CONFIABLE.md` ya describen el antiguo salto
  del high-water-mark, el orden keyset y la ventana de clientes/CxC. La auditoria
  verifico que la ruta moderna mantiene marca de agua contigua y que esas
  pruebas pasan.
- `apps/sync/engine.py:515-566` conserva deliberadamente el fallback legacy que
  avanza despues de un item fallido. Sigue siendo un riesgo mientras exista
  compatibilidad con clouds viejos, pero esta reconocido en el propio codigo y
  debe retirarse con una version minima de protocolo, no tratarse como un
  descubrimiento independiente.
- El snapshot de inventario sigue siendo O(N) y se emite post-commit por diseño.
  Es un costo conocido; SYNC-008 se refiere a la falta de proteccion temporal al
  aplicarlo, no a ese costo.

## Cobertura y pruebas ejecutadas

Comando ejecutado sin modificar codigo ni datos productivos:

```powershell
C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py test `
  apps.sync.tests `
  apps.api.tests.test_sync_extended `
  apps.api.tests.test_sync_cliente_upsert `
  apps.api.tests.test_sync_roles `
  apps.api.tests.test_sync_venta_sin_usuario `
  apps.api.tests.test_maestros_keyset `
  --keepdb --settings=config.settings_development
```

Resultado:

- 91 pruebas: OK.
- El system check ejecutado por el test runner no encontro issues.
- Se reutilizo y conservo la base de pruebas existente por `--keepdb`.
- No se corrio la suite completa del repositorio en esta pasada.
- Los logs `ERROR`/`WARNING` observados corresponden a casos negativos
  intencionales de las pruebas de serializacion y cursor.

La suite cubre bien serializadores, atomicidad normal de venta+evento, cursor
keyset, roles/asignaciones basicas, clientes cloud y handlers extendidos. No hay
pruebas actuales que ejerciten:

- dos workers de push ni dos requests cloud con el mismo hash en paralelo;
- `requests.post` y todos los formatos de ACK de `push_eventos`;
- `recibir_eventos` como endpoint batch completo;
- fallo del `INSERT` de `EventoSync` y la postcondicion esperada del negocio;
- `SIN_PAYLOAD` parametrizado para los 15 tipos;
- venta cloud con un SKU ausente y posterior reenvio correctivo;
- fallo HTTP/autenticacion de pull reflejado en `LogSync` y `sync_status`;
- dependencia de rol/usuario/categoria que aparece en un ciclo posterior;
- rename/cambio de identidad de categorias y clientes;
- snapshots fuera de orden;
- pagina keyset sin progreso con `next` presente;
- accion de reintento desde Django Admin.

## Tests recomendados antes de tocar codigo

- Crear `apps/sync/tests/test_push_protocol.py` para claims, ACKs incompletos,
  transiciones condicionales y contador de reintentos.
- Crear pruebas de concurrencia con dos conexiones para claim local y receptor
  cloud; verificar especialmente pago CxC y movimiento de caja.
- Parametrizar los 15 tipos de evento y demostrar si cada uno es
  reserializable, reemplazable o deliberadamente no recuperable.
- Probar el endpoint batch real con rollback por evento, hash duplicado
  concurrente, dependencia ausente y reenvio posterior.
- Añadir pruebas de convergencia de maestros en varios ciclos: primero falta la
  dependencia, luego aparece sin modificar la fila dependiente.
- Probar renombres y correcciones de identidad conservando PK y relaciones
  locales.
- Probar que un pull 401/500 y heartbeat fallido producen `PARCIAL`/`FALLO` y se
  ven en `sync_status`.
- Probar paginacion sin progreso y snapshot T2 seguido de T1.
- Probar las acciones de Admin contra eventos con `intentos=max_retries`.

## Orden sugerido de correccion

1. Cerrar la identidad/deduplicacion concurrente en ambos extremos y hacer
   durable el claim del push.
2. Definir de forma honesta la garantia de persistencia del outbox y completar
   la recuperacion de todos los hechos `SIN_PAYLOAD`.
3. Evitar confirmaciones parciales de ventas y proteger snapshots contra orden
   inverso.
4. Hacer que errores y diferidos de pull formen parte del resultado del ciclo;
   no avanzar cursores ante dependencias no aplicadas.
5. Introducir identidad cloud estable para categorias/clientes y migrar los
   registros existentes.
6. Endurecer ACK/keyset, corregir reintento Admin y hacer fail-closed el comando
   manual de reconciliacion.

## Conclusion

`apps/sync` tenia una base mas madura que una cola de "best effort", pero no
ofrecia entrega efectivamente unica ni convergencia observable de extremo a
extremo. Sus riesgos aparecian en las fronteras: persistir el evento,
deduplicarlo bajo concurrencia, aplicar todas las dependencias y reportar el
resultado real. Esas cuatro fronteras estan cerradas (ver abajo).

---

# Estado de mitigacion

Fecha: 2026-08-20. Verificacion previa: se releyo cada hallazgo contra el codigo
citado. **Los 12 son reales** — ninguno resulto falso positivo ni obsoleto.

## Resumen por hallazgo

| ID | Real | Estado | Donde quedo la correccion |
|---|---|---|---|
| SYNC-001 | Si | Corregido | `events._crear_evento` separa fallo de serializacion (degrada a SIN_PAYLOAD) de fallo de persistencia (reintenta sin payload y, si tampoco entra, PROPAGA). |
| SYNC-002 | Si | Corregido | Los 7 tipos faltantes entran al `registry` con `backfill=False`. `TIPOS_NO_RESERIALIZABLES` deja explicito el unico que no aplica, y un test compara catalogo vs registry. |
| SYNC-003 | Si | Corregido | `UniqueConstraint` parcial sobre `EventoSync.hash_payload` (migracion `sync.0008`). El INSERT del evento corre dentro de la transaccion del handler, asi que actua como reserva. `marcar_error` pasa a ser una transicion condicional que no degrada un CONFIRMADO. |
| SYNC-004 | Si | Corregido | `_resolver_productos_venta` valida TODAS las dependencias antes de crear la venta y falla el evento entero. El reenvio correctivo (`_reparar_lineas_venta`) reconstruye detalles y pagos, no solo el cliente. |
| SYNC-005 | Si | Corregido | `pull_maestros` devuelve metricas estructuradas por entidad; `clasificar_ciclo()` es la unica politica de veredicto, compartida por `ciclo_completo` y por el comando. `sync_status` suma SIN_PAYLOAD y muestra cursores bloqueados. |
| SYNC-006 | Si | Corregido | Sentinela `DIFERIDO`: un `apply` que no pudo aplicar no cuenta como aplicado y NO avanza la marca de agua. Cubre categoria ausente, usuario/rol ausente y codigos de permiso desconocidos. |
| SYNC-007 | Si | Corregido | `origen_cloud_id` en `Categoria` y `Cliente` (migraciones `productos.0009` y `clientes.0005`). La clave natural queda solo para adoptar la fila la primera vez. |
| SYNC-008 | Si | Corregido | `_handler_inventario_snapshot` compara el timestamp entrante contra el persistido por SKU y omite lo obsoleto. |
| SYNC-009 | Si | Corregido | El ACK se valida como contrato: formato, `detalle` como lista, hash presente. Toda fila enviada termina en confirmado o en `marcar_error`. |
| SYNC-010 | Si | Corregido | Guardarrail de progreso: si la pagina no mueve la frontera y el cloud dice que hay `next`, se aborta con bloqueo visible. Mas un tope de paginas por ciclo. |
| SYNC-011 | Si | Corregido | `reactivar_eventos()` como unica funcion de dominio, usada por el Admin y por `verificar_sync`. Reinicia `intentos`, que era lo que faltaba. |
| SYNC-012 | Si | Corregido | `_buscar` levanta ante fallo de LECTURA en vez de devolver None; las tres reconciliaciones son fail-closed y el comando sale distinto de cero si hubo errores. |

## Hallazgos adicionales encontrados al corregir

- **`_pull_generic` no aislaba el `apply` en un savepoint.** Un error de BD
  dentro de un item dejaba la transaccion abortada en Postgres y ni siquiera se
  podia guardar el cursor: el pull entero moria. Las pruebas existentes no lo
  veian porque inyectaban `ValueError`, no un error de base. Ahora cada item
  corre en su propio savepoint.
- **Colision de clave natural en el pull de maestros.** Al introducir
  `origen_cloud_id` quedo visible un caso que antes se resolvia pisando datos:
  dos registros cloud distintos que reclaman la misma clave natural local. Se
  difiere con bloqueo visible en vez de intentar un INSERT condenado por la
  unicidad de `nombre`.

## Despliegue: 3 migraciones, leer antes de promover

1. **`sync.0008_eventosync_hash_unico`** — la unica que toca datos. Antes de
   crear la constraint COLAPSA los eventos con hash repetido, conservando el
   mas antiguo. Dos filas con el mismo hash representan el mismo hecho aplicado
   dos veces (todos los payloads llevan una PK local o un timestamp propio).
   **La migracion emite un WARNING por cada hash colapsado**: revisar ese log al
   promover a produccion, porque indica donde el bug ya duplico un efecto de
   negocio. Colapsar el evento NO deshace el pago o movimiento duplicado que
   haya quedado en cloud; eso se corrige a mano.
2. **`productos.0009_categoria_origen_cloud_id`** y
   **`clientes.0005_cliente_origen_cloud_id`** — agregan la columna en NULL. No
   requieren backfill: la primera vez que baja cada registro, el pull adopta la
   fila local por clave natural y le sella la identidad.

Aplica a la BD cloud y a cada BD de sucursal (el modelo `EventoSync` es el mismo
a ambos lados).

## Cambios de conducta observables

1. **Un fallo de persistencia del outbox ahora tumba la operacion.** Es
   deliberado: es la garantia que justifica el patron. En la practica solo pasa
   con la cola rota (tabla ausente, esquema desfasado). Si el POS empieza a
   rechazar ventas con un error de `EventoSync`, correr `manage.py migrate` y
   `manage.py verificar_sync`.
2. **Una venta con un SKU que el cloud no tiene ya no se aplica parcial**: el
   evento queda en ERROR y se reintenta. Aparece como evento fallido hasta que
   el producto se replique. Es visible, y antes era silencioso y permanente.
3. **`LogSync` puede decir PARCIAL o FALLO** donde antes decia siempre EXITOSO.
   No es una regresion: es el estado real que ya existia.
4. **`reconciliar_cloud` termina con codigo distinto de cero** si hubo errores.
   Un script que lo invoque y no chequee el exit code cambiara de conducta.
5. **`_pull_*` devuelve un dict**, no un int. Contrato interno; los llamadores
   del repo estan actualizados.

## Pendiente (no bloqueante)

- **Claim durable del push (`IN_FLIGHT` + lease).** El claim local sigue sin ser
  un reclamo duradero: dos workers pueden enviar el mismo evento. Ya NO es
  peligroso — la constraint de hash garantiza que el cloud lo aplique una sola
  vez y ambos envios se resuelven como entregados — pero sigue habiendo trabajo
  duplicado. Un protocolo de lease trae sus propios modos de fallo (leases
  colgados); vale la pena solo si el desperdicio se vuelve medible.
- **Envelope inmutable para hechos derivados.** Un evento SIN_PAYLOAD de tipo
  derivado se re-serializa contra el estado ACTUAL del objeto, no contra el que
  tenia al ocurrir el hecho. Converge, pero la solucion completa es persistir en
  la fila un envelope minimo al encolar.
- **Cola durable de diferidos.** Hoy un item diferido congela la marca de agua y
  se reintenta en el ciclo siguiente. Funciona y es visible en el cursor
  bloqueado, pero un unico item problematico frena la marca de agua de esa
  entidad. Una cola de diferidos permitiria avanzar el cursor y reintentar solo
  lo pendiente.
- **`_pull_legacy` sigue existiendo.** El fallback para clouds pre-Fase 2
  conserva su punto debil (avanza tras un item fallido). Se retira con una
  version minima de protocolo, como ya decia la auditoria.

## Pruebas

Suite completa, serial: **494 tests, OK.**

```
manage.py test --settings=config.settings_development --noinput
```

Modulos de regresion nuevos:

| Archivo | Cubre |
|---|---|
| `apps/sync/tests/test_auditoria_sync.py` (31 tests) | SYNC-001, 002, 003, 005, 006, 007, 009, 010, 011 + la parte de datos de la migracion `sync.0008` |
| `apps/api/tests/test_sync_auditoria.py` (7 tests) | SYNC-003 concurrente, SYNC-004, SYNC-008 |

Verificaciones por mutacion (que la prueba realmente detecte el defecto):

- **SYNC-003.** `DeduplicacionConcurrenteTests` lanza dos requests simultaneas
  con el mismo hash. Se corrio ANULANDO el chequeo previo `exists()` del
  receptor: el test sigue pasando, lo que demuestra que quien impide la doble
  aplicacion es la constraint de BD y no el chequeo de aplicacion.
- **SYNC-006.** El test `test_pull_omite_usuario_inexistente` documentaba el
  bug (afirmaba `count == 1` para una asignacion NO aplicada). Se reescribio
  como `test_pull_difiere_usuario_inexistente_sin_darlo_por_aplicado`, mas un
  test de convergencia que baja el mismo payload en un segundo ciclo.
- **`sync.0008`.** `MigracionDedupTests` baja el indice, fabrica el estado que
  dejaba el bug (tres filas con el mismo hash, dos SIN_PAYLOAD legitimas), corre
  la funcion de colapso y verifica la invariante que la constraint exige. Es la
  unica parte del trabajo que corre contra datos productivos.

Nota de entorno: la BD de desarrollo local esta varias migraciones atras
(`sync` en 0004, `productos` en 0006, `clientes` en 0002), asi que los comandos
operativos fallan ahi hasta correr `migrate`. Es previo a este trabajo; la
suite crea su propia BD y aplica todas las migraciones.
