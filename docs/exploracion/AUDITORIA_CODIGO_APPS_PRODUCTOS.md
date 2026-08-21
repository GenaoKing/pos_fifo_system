# Auditoría profunda de código - `apps/productos`

Fecha: 2026-08-20
Revisión de cierre: `3f22385`
Modo: lectura, pruebas y documentación; no se aplicaron correcciones funcionales.

## Resumen ejecutivo

`apps/productos` es el catálogo maestro que conecta ventas, inventario FIFO,
cotizaciones, reportes, impresión de etiquetas, API y sincronización cloud. La
aplicación parece un CRUD sencillo, pero en la arquitectura distribuida el SKU,
el código de barras, el precio, el estado y la categoría son datos de identidad
y autorización operacional. Un cambio local puede alterar qué se vende, a qué
precio y cómo se reconcilia posteriormente con cloud.

La base tiene decisiones acertadas: las relaciones relevantes usan `PROTECT`,
SKU y código de barras tienen restricciones únicas, la API aplica permisos
granulares y validaciones explícitas, las categorías sincronizadas conservan un
identificador cloud estable y ventas vuelve a validar el precio recibido. No
obstante, esas garantías no son uniformes entre HTML, API, Admin y sync.

Los riesgos más urgentes son:

- Todas las mutaciones HTML de categorías y productos se protegen únicamente
  con `login_required`. Una cajera autenticada sin permisos RBAC pudo crear y
  desactivar maestros, cambiar un precio a `1.00`, administrar imágenes e
  imprimir etiquetas.
- En sucursal, las escrituras HTML se hacen directamente en la base local. El
  decorador de conectividad no las envía a cloud ni crea outbox; por diseño,
  una descarga futura puede revertirlas o dividir la identidad del producto.
- El SKU, que sync usa como clave estable de producto, se puede editar
  localmente. Se reprodujo que cambiarlo y luego descargar el SKU anterior crea
  dos productos.
- La API permite `DELETE` físico. No existe tombstone de maestro; una sucursal
  puede conservar y vender una copia que cloud ya eliminó, y el evento de venta
  posterior no podrá resolver el producto.
- Ambas páginas de catálogo insertan JSON con `json.dumps|safe` dentro de un
  `<script>`. Un nombre o descripción almacenados con `</script>` rompe el
  contexto y permite XSS persistente.
- La carga de imagen acepta contenido arbitrario y lo publica desde media sin
  validar que sea una imagen, su extensión, MIME o tamaño.
- Ni los endpoints POS ni el servicio que materializa una venta imponen de
  forma consistente que producto y categoría estén activos.
- El pull convierte `codigo_barras=NULL` en cadena vacía. Dos productos cloud
  sin código colisionan contra la unicidad local y bloquean el cursor del
  maestro.

Se documentan **22 hallazgos**:

| Prioridad | Cantidad | Criterio |
| --- | ---: | --- |
| P1 | 8 | Permite mutación no autorizada, divergencia de maestros, corrupción de identidad, XSS persistente, carga pública arbitraria o venta de datos retirados. |
| P2 | 9 | Debilita validación, trazabilidad, atomicidad, concurrencia, manejo de fallos o control operacional. |
| P3 | 5 | Deuda de pruebas, rendimiento y mantenibilidad que aumenta el costo de operar o corregir el catálogo. |

La suite seleccionada terminó con **75/75 pruebas existentes aprobadas**.
`apps/productos` no aportó casos propios. Una batería adversarial temporal
terminó con **14/14 reproducciones confirmadas** y fue retirada del workspace.
También pasaron `manage.py check` y
`makemigrations productos --check --dry-run` usando una base de prueba aislada.

## Alcance

Se inspeccionaron completamente:

- `apps/productos/models.py`
- `apps/productos/views.py`
- `apps/productos/admin.py`
- `apps/productos/urls.py`
- `apps/productos/utils.py`
- `apps/productos/apps.py`
- `apps/productos/migrations/`
- `apps/productos/tests/`
- `templates/productos/lista_productos.html`
- `templates/productos/lista_categorias.html`

También se trazaron las fronteras relevantes en:

- `apps/api/views/maestros.py`
- `apps/api/serializers/maestros.py`
- `apps/api/permissions.py`
- `apps/sync/engine.py` y `apps/sync/decorators.py`
- `apps/ventas/views.py` y `apps/ventas/services/ventas_service.py`
- `apps/inventario/models.py`
- `apps/cotizaciones/models.py`
- `apps/permisos/catalogo.py`
- `apps/auditoria/models.py` y `apps/auditoria/middleware.py`
- `apps/tenancy/media.py`
- `config/settings.py`, `config/settings_cloud.py` y `config/urls.py`
- documentación de diseño cloud y roadmaps vigente en `docs/`

El núcleo de `apps/productos` suma **1,117 líneas Python**, sin contar
migraciones. Sus dos plantillas suman **1,495 líneas**. Hay nueve migraciones,
pero el directorio de pruebas solo contiene un `__init__.py`: **0 casos
propios**.

La auditoría comenzó y cerró en `3f22385`; `apps/productos` permaneció sin
cambios. Durante la revisión había correcciones externas sin commit en API,
caja, clientes, cuentas por cobrar, inventario, permisos, sync, tenancy,
ventas y settings. No se revirtieron ni alteraron. Las referencias cross-app
corresponden al estado visible al cierre.

## Hallazgos P1

### PRO-001 - El CRUD HTML ignora el catálogo RBAC de productos y categorías

- Severidad: crítica.
- Tipo: autorización / escalada funcional / integridad comercial.
- Evidencia:
  - El catálogo declara permisos separados para ver, crear, editar y eliminar
    productos y categorías (`apps/permisos/catalogo.py:25-34`).
  - Las vistas de `apps/productos` usan `login_required`, pero ninguna usa
    `requiere_permiso` ni `requiere_permiso_api`
    (`apps/productos/views.py:20-455`).
  - Crear/editar producto, cambiar estado, administrar categorías y gestionar
    imágenes son operaciones directas sobre modelos
    (`apps/productos/views.py:77-298` y `:361-455`).
- Reproducción validada:
  - Una usuaria con rol cajera y sin permisos del módulo obtuvo respuestas
    exitosas al crear una categoría y un producto, editar su precio a `1.00` y
    desactivar ambos.
- Impacto:
  - Cualquier cuenta autenticada puede alterar el catálogo y los precios que
    alimentan ventas e inventario.
  - La interfaz de administración RBAC comunica una separación de funciones
    que el backend de productos no hace cumplir.
- Recomendación:
  - Aplicar permisos de vista/creación/edición/desactivación en cada endpoint,
    incluida la gestión de imágenes e impresión.
  - Autorizar en servidor antes de cualquier lectura o mutación; no depender de
    botones ocultos en la plantilla.
- Prueba de aceptación sugerida:
  - Una cajera sin permisos debe recibir 403 en todos los endpoints mutables y
    no debe observar ningún cambio en precio, estado, categoría o archivos.

### PRO-002 - Las escrituras locales de maestros no tienen propagación autoritativa

- Severidad: crítica en instalaciones sucursal/cloud.
- Tipo: arquitectura distribuida / consistencia / pérdida de cambios.
- Evidencia:
  - `requiere_conexion_cloud` comprueba conectividad, pero documenta que la
    escritura sigue siendo local y no se propaga
    (`apps/sync/decorators.py:27-29`).
  - Solo crear y editar productos usan ese gate; categorías, estados e imágenes
    ni siquiera lo usan (`apps/productos/views.py:77-298` y `:361-455`).
  - Las vistas escriben con `objects.create()`/`save()` y no generan outbox ni
    llaman a una API cloud (`apps/productos/views.py:98-106` y `:173-184`).
  - Los roadmaps todavía registran como pendiente eliminar o redirigir los CRUD
    locales de maestros (`docs/ROADMAP_PORTAL.md:195-200` y `:558`;
    `docs/ROADMAP_CLOUD.md:553`).
- Impacto:
  - Una corrección hecha en sucursal puede desaparecer en una descarga
    posterior o quedar distinta en cada local.
  - El operador recibe confirmación de éxito sin que cloud conozca el cambio.
  - Precio, código, estado e imagen pueden divergir sin conflicto visible.
- Recomendación:
  - Definir un único escritor autoritativo. En modo sucursal, enviar la mutación
    a cloud con idempotencia y aplicar su respuesta, o prohibir explícitamente
    la edición local.
  - Mientras exista transición, mostrar el origen y estado de sincronización de
    cada cambio y registrar conflictos.
- Prueba de aceptación sugerida:
  - Toda mutación aceptada en sucursal debe aparecer exactamente una vez en
    cloud y sobrevivir al siguiente pull; si cloud no está disponible debe
    fallar cerrado o quedar en una outbox visible y reintentable.

### PRO-003 - El SKU editable localmente rompe la identidad usada por sync

- Severidad: crítica.
- Tipo: identidad distribuida / duplicación / integridad referencial.
- Evidencia:
  - `Producto` no tiene `origen_cloud_id`; el pull usa
    `update_or_create(sku=item['sku'])` (`apps/sync/engine.py:832-865`).
  - La edición HTML acepta y persiste un SKU diferente
    (`apps/productos/views.py:142-184`).
  - La API sí reconoce la invariante y protege el SKU durante actualización en
    su serializer (`apps/api/serializers/maestros.py`).
- Reproducción validada:
  - Se creó localmente un producto descargado, se cambió su SKU mediante la
    vista y luego se ejecutó un pull con el SKU cloud anterior. El resultado
    fueron dos filas de producto.
- Impacto:
  - Lotes, ventas, cotizaciones e imágenes pueden quedar asociados a una mitad
    de una identidad dividida.
  - La duplicación no se resuelve por la restricción única porque los SKU son
    distintos.
- Recomendación:
  - Agregar una identidad cloud inmutable y única para productos, equivalente a
    la que ya poseen las categorías.
  - Tratar el SKU como dato comercial mutable solo si la sincronización se basa
    en esa identidad estable; hasta entonces, impedir su edición fuera del
    escritor autoritativo.
- Prueba de aceptación sugerida:
  - Renombrar un SKU en cloud y descargarlo debe actualizar una sola fila y
    preservar sus relaciones, nunca crear otra.

### PRO-004 - El borrado físico de maestros en API no tiene tombstones

- Severidad: crítica en sincronización incremental.
- Tipo: borrado distribuido / datos obsoletos / eventos bloqueados.
- Evidencia:
  - `ProductoViewSet` y `CategoriaViewSet` heredan `ModelViewSet`, por lo que
    exponen `destroy` sin override (`apps/api/views/maestros.py:167-248`).
  - El pull incremental solo recibe filas existentes y no modela tombstones
    (`apps/sync/engine.py:741-875`).
  - Las categorías sí tienen `origen_cloud_id`, pero no existe un registro de
    eliminación; productos dependen además del SKU.
  - Las ventas sincronizadas resuelven el producto contra el catálogo cloud;
    una referencia ausente hace fallar el evento.
- Impacto:
  - Una sucursal offline conserva como activo un maestro eliminado en cloud.
  - Puede venderlo y generar un evento que cloud no podrá materializar, dejando
    la cola en reintento permanente.
  - La baja deja de ser convergente: cada base puede terminar con un conjunto
    diferente.
- Recomendación:
  - Sustituir el borrado por baja lógica versionada o publicar tombstones en el
    feed incremental.
  - Definir política explícita para eventos históricos que referencien maestros
    retirados.
- Prueba de aceptación sugerida:
  - Tras eliminar o desactivar un producto en cloud, todas las sucursales deben
    converger al mismo estado y una venta pendiente debe reconciliarse con una
    regla de dominio determinista.

### PRO-005 - XSS persistente por insertar JSON con `safe` dentro de `<script>`

- Severidad: crítica.
- Tipo: seguridad web / ejecución persistente / secuestro de sesión.
- Evidencia:
  - La lista de productos incluye
    `products: {{ productos_json|safe }}` dentro de JavaScript
    (`templates/productos/lista_productos.html:682`).
  - La lista de categorías aplica el mismo patrón
    (`templates/productos/lista_categorias.html:369`).
  - `json.dumps()` escapa comillas, pero no neutraliza la secuencia de cierre
    HTML `</script>`.
  - En la misma plantilla, marcas usa correctamente `json_script`, lo que
    demuestra que ya existe un patrón seguro disponible
    (`templates/productos/lista_productos.html:13`).
- Reproducción validada:
  - Un nombre de producto y una descripción de categoría con
    `</script><script>...` aparecieron en la respuesta cerrando el bloque
    original y conservando el payload JavaScript.
- Impacto:
  - Un usuario que pueda crear o editar catálogo —actualmente cualquier usuario
    autenticado— puede ejecutar JavaScript en el navegador de otros operadores.
  - El payload puede actuar con la sesión de un administrador, leer datos de la
    página y realizar mutaciones protegidas por sus credenciales.
- Recomendación:
  - Usar `json_script` para ambos conjuntos y leerlos con `JSON.parse` desde un
    nodo no ejecutable.
  - Aplicar CSP como defensa adicional, sin considerarla sustituto del escape
    correcto.
- Prueba de aceptación sugerida:
  - Valores con `</script>`, `<`, `>`, `&` y separadores Unicode deben conservarse
    como datos y nunca crear un segundo elemento `<script>` ejecutable.

### PRO-006 - La carga de imagen acepta y publica contenido arbitrario

- Severidad: alta.
- Tipo: archivos / contenido activo / agotamiento de almacenamiento.
- Evidencia:
  - El endpoint asigna directamente `request.FILES['imagen']` al `ImageField` y
    llama `save()` (`apps/productos/views.py:361-407`).
  - No valida decodificación de imagen, MIME, extensión, dimensiones ni tamaño.
  - La aplicación sirve media mediante una ruta pública de Django
    (`config/urls.py`) y el almacenamiento cloud puede usar dominio público.
  - El prefijo tenant reduce colisiones, pero no valida el contenido
    (`apps/tenancy/media.py`).
- Reproducción validada:
  - Se cargaron bytes HTML con tipo `text/plain` como imagen de producto. La
    vista respondió éxito y el archivo quedó guardado.
- Impacto:
  - Una cuenta autenticada puede convertir el servidor o blob storage en
    alojamiento de archivos arbitrarios y consumir espacio sin límite.
  - Dependiendo de dominio, cabeceras y navegador, contenido activo puede crear
    una superficie adicional de phishing o XSS.
- Recomendación:
  - Validar tamaño antes de leer, decodificar con una biblioteca de imágenes,
    volver a codificar a formatos permitidos y generar un nombre/extensión del
    lado servidor.
  - Servir con `Content-Type` y `Content-Disposition` seguros desde un dominio
    sin cookies; aplicar cuotas por tenant.
- Prueba de aceptación sugerida:
  - HTML, SVG activo, archivo renombrado, imagen corrupta y payload sobre cuota
    deben ser rechazados sin crear objetos en storage.

### PRO-007 - Producto o categoría inactivos todavía pueden llegar a una venta

- Severidad: alta.
- Tipo: regla de negocio / baja lógica / consistencia POS.
- Evidencia:
  - La búsqueda general empieza con productos activos, pero solo exige
    `categoria__activa=True` cuando el cliente envía un filtro de categoría
    (`apps/ventas/views.py:202-205`).
  - La búsqueda por código y por ID comprueba el estado del producto, no el de
    la categoría (`apps/ventas/views.py:248-278`).
  - El servicio que bloquea y carga productos para materializar la venta filtra
    únicamente por ID; no exige producto ni categoría activos
    (`apps/ventas/services/ventas_service.py:554-565`).
- Reproducción validada:
  - Un producto activo dentro de una categoría inactiva apareció en búsqueda
    general y por escáner.
  - El cargador transaccional de ventas recuperó tanto un producto inactivo
    como uno cuya categoría estaba inactiva.
- Impacto:
  - La baja administrativa no es una garantía del backend. Un cliente manual,
    una pestaña antigua o una carrera puede vender un artículo retirado.
  - Reportes y reglas operativas pueden interpretar “inactivo” de forma distinta.
- Recomendación:
  - Definir una sola condición de vendibilidad y hacerla cumplir dentro de la
    transacción de venta, además de reutilizarla en búsquedas.
  - Devolver un error de dominio claro si el estado cambia entre selección y
    confirmación.
- Prueba de aceptación sugerida:
  - Ninguna ruta de búsqueda ni creación de venta debe aceptar un producto
    inactivo o perteneciente a una categoría inactiva.

### PRO-008 - Dos códigos de barras nulos pueden bloquear el cursor de productos

- Severidad: alta.
- Tipo: sincronización / normalización / disponibilidad.
- Evidencia:
  - El modelo permite `codigo_barras=NULL`, pero también lo declara único
    (`apps/productos/models.py`).
  - El pull normaliza `item.get('codigo_barras') or ''`
    (`apps/sync/engine.py:850-865`).
  - SQL solo permite una cadena vacía bajo esa restricción, aunque pueda admitir
    varios `NULL` según backend y condición.
- Reproducción validada:
  - Al descargar dos productos cloud con código nulo, el primero se guardó como
    `''`; el segundo produjo una violación de
    `productos_codigo_barras_key`. El cursor `VersionMaestro` no avanzó.
- Impacto:
  - Un dato válido según el contrato cloud puede impedir que la sucursal reciba
    todo el catálogo posterior.
  - Reintentar no cura el estado y prolonga el bloqueo.
- Recomendación:
  - Conservar `None` como `NULL` de extremo a extremo y definir una restricción
    condicional solo para valores presentes.
  - Validar el lote antes de aplicarlo y reportar el elemento conflictivo sin
    perder observabilidad del cursor.
- Prueba de aceptación sugerida:
  - Descargar múltiples productos sin código debe crear/actualizar todos y
    avanzar exactamente al cursor entregado por cloud.

## Hallazgos P2

### PRO-009 - HTML y modelo omiten validaciones que la API sí aplica

- Severidad: alta.
- Tipo: validación / invariantes / canales inconsistentes.
- Evidencia:
  - El serializer API exige precio positivo, stock mínimo no negativo y
    atributos tipo objeto (`apps/api/serializers/maestros.py`).
  - Las vistas HTML convierten valores y guardan directamente, sin formulario,
    serializer ni `full_clean()` (`apps/productos/views.py:77-184`).
  - El modelo no tiene `MinValueValidator` ni `CheckConstraint` para precio y no
    valida el esquema JSON (`apps/productos/models.py`).
- Reproducción validada:
  - Se persistieron un precio negativo, un estado fuera de `choices` y una lista
    como `atributos` usando el camino local/modelo.
- Impacto:
  - La integridad depende del canal. Datos aceptados localmente pueden romper
    ventas, UI, reportes o ser rechazados al intentar sincronizarlos.
- Recomendación:
  - Llevar invariantes esenciales al modelo/base y reutilizar un servicio de
    dominio común desde HTML y API.
- Prueba de aceptación sugerida:
  - Los mismos payloads válidos e inválidos deben producir el mismo resultado
    por Admin, HTML, API, sync y acceso de servicio.

### PRO-010 - Cambios de precio y catálogo no generan auditoría de dominio

- Severidad: alta.
- Tipo: trazabilidad / fraude interno / soporte.
- Evidencia:
  - `apps/auditoria` define acciones para crear, editar y eliminar productos,
    incluida `PRECIO_MODIFICADO`, y ofrece `registrar_cambio_precio`
    (`apps/auditoria/models.py:387-395`).
  - Las vistas de productos y los ViewSets de maestros no llaman esos
    registradores.
  - Los patrones críticos del middleware incluyen rutas antiguas como
    `/productos/editar/`, que no coinciden con la URL real parametrizada.
  - El API tenant queda fuera del middleware de auditoría por diseño.
- Reproducción validada:
  - Editar el precio mediante la vista HTML no creó un evento de auditoría de
    precio.
- Impacto:
  - No se puede reconstruir de forma confiable quién cambió precio, SKU, código,
    estado o categoría, ni distinguir operador, API y sync.
- Recomendación:
  - Emitir eventos de dominio dentro de la misma transacción de cada mutación,
    con antes/después, actor, tenant, canal y correlación.
- Prueba de aceptación sugerida:
  - Cada cambio aceptado debe producir exactamente un evento durable; un rollback
    no debe dejar auditoría huérfana.

### PRO-011 - Las acciones masivas del Admin ocultan la fecha real del cambio

- Severidad: media-alta.
- Tipo: trazabilidad / actualización masiva.
- Evidencia:
  - Activar y desactivar productos usa `queryset.update(activo=...)`
    (`apps/productos/admin.py:239-251`).
  - `update()` no ejecuta `save()` ni actualiza automáticamente
    `fecha_modificacion`.
- Reproducción validada:
  - Una desactivación masiva conservó exactamente la fecha de modificación
    anterior.
- Impacto:
  - La baja queda invisible para procesos que dependan de marcas temporales,
    investigaciones y futuras estrategias incrementales.
- Recomendación:
  - Actualizar estado y timestamp explícitamente en una operación de dominio y
    emitir auditoría por cada entidad o por lote con detalle completo.
- Prueba de aceptación sugerida:
  - Una acción masiva debe actualizar fecha, versión y auditoría de todas las
    filas afectadas de forma atómica.

### PRO-012 - El ciclo de vida de imágenes no es atómico ni converge con cloud

- Severidad: media-alta.
- Tipo: storage / consistencia / residuos.
- Evidencia:
  - Reemplazar la imagen guarda la nueva referencia sin borrar la anterior
    (`apps/productos/views.py:361-407`).
  - Eliminar borra primero el objeto de storage y después guarda el modelo, sin
    una transacción o compensación (`apps/productos/views.py:410-455`).
  - El API expone `imagen_url`, pero `_pull_productos` no descarga ni actualiza
    la imagen (`apps/sync/engine.py:832-875`).
  - Una prueba existente del motor acepta explícitamente que el producto
    descargado no tenga imagen (`apps/sync/tests/test_engine.py`).
- Reproducción validada:
  - Tras dos cargas, el archivo anterior siguió existiendo aunque ya no estaba
    referenciado por el producto.
- Impacto:
  - Se acumulan archivos huérfanos y costos de almacenamiento.
  - Un fallo entre borrado y `save()` deja una referencia rota.
  - La misma ficha muestra imágenes distintas según el nodo.
- Recomendación:
  - Modelar la imagen como recurso versionado, validar primero, confirmar la
    referencia y borrar la versión anterior mediante `on_commit` o una tarea
    idempotente.
  - Definir si se sincroniza binario, URL firmada o asset cloud estable.
- Prueba de aceptación sugerida:
  - Reemplazo, rollback, reintento y pull deben dejar una sola versión
    referenciada, sin archivo perdido ni huérfano.

### PRO-013 - La configuración de atributos no constituye un esquema aplicado

- Severidad: media-alta.
- Tipo: datos semiestructurados / validación / reportabilidad.
- Evidencia:
  - Categoría guarda `atributos_configurados` y producto guarda `atributos` como
    JSON libre (`apps/productos/models.py`).
  - No se valida obligatoriedad, tipo, rango, opciones ni claves desconocidas.
  - Las vistas de categorías ni siquiera persisten la configuración recibida;
    las de producto aceptan el JSON sin contrastarlo con su categoría.
- Reproducción validada:
  - Un producto de una categoría con atributo configurado se guardó sin ese
    atributo y con forma incompatible.
- Impacto:
  - La interfaz sugiere datos estructurados que luego no son confiables para
    filtros, reportes, integraciones o cambios de categoría.
- Recomendación:
  - Versionar un esquema explícito por categoría y validarlo en todos los
    escritores; definir migración cuando el esquema cambie.
- Prueba de aceptación sugerida:
  - Claves requeridas, tipos, opciones y transición entre versiones deben tener
    casos positivos y negativos comunes a HTML/API/sync.

### PRO-014 - Los generadores de SKU y código de barras tienen carreras

- Severidad: media-alta bajo concurrencia.
- Tipo: concurrencia / asignación de identificadores.
- Evidencia:
  - El SKU se deriva del último ID más uno sin bloqueo
    (`apps/productos/models.py`).
  - El código toma el último producto, incrementa un número y luego consulta
    existencia en un bucle (`apps/productos/utils.py`).
  - La restricción única detecta la colisión al final, pero la vista no reintenta
    con un identificador nuevo.
- Impacto:
  - Dos altas simultáneas pueden calcular el mismo valor; una falla después de
    que el usuario completó el formulario.
  - Los huecos, cambios manuales y formatos irregulares agravan la búsqueda del
    “último”.
- Recomendación:
  - Generar identificadores con una secuencia transaccional, UUID/ULID interno o
    un servicio de asignación con reintento acotado ante conflicto.
- Prueba de aceptación sugerida:
  - Varias altas concurrentes deben terminar con identificadores únicos y sin
    errores visibles ni duplicación.

### PRO-015 - Los errores y borrados protegidos no tienen contrato de dominio

- Severidad: media-alta.
- Tipo: manejo de errores / disponibilidad / filtración de detalles.
- Evidencia:
  - Las vistas capturan `Exception` de forma amplia y devuelven `str(e)` como
    respuesta 400 (`apps/productos/views.py`).
  - Eso aplana `Http404`, errores de validación, storage y fallos de base en un
    mismo estado, y puede exponer nombres de constraints o rutas.
  - Los ViewSets heredan el `destroy` estándar. Las relaciones desde lotes,
    ventas, cotizaciones y otras entidades usan `PROTECT`; `ProtectedError` no
    se traduce a un conflicto de dominio (`apps/api/views/maestros.py:167-248`).
- Impacto:
  - Clientes reciben 400 o 500 inconsistentes y no saben si reintentar.
  - Detalles internos llegan al navegador; fallos operativos pueden quedar
    clasificados como error del usuario.
- Recomendación:
  - Capturar excepciones esperadas de forma específica y mapearlas a 400, 404,
    409 o 503 con códigos estables; registrar el detalle solo del lado servidor.
  - Desactivar en vez de destruir cuando existan relaciones históricas.
- Prueba de aceptación sugerida:
  - Recurso inexistente, duplicado, protegido, storage caído y error inesperado
    deben producir contratos distintos, seguros y documentados.

### PRO-016 - El chequeo cloud ocurre antes de autenticar y cubre rutas de forma parcial

- Severidad: media.
- Tipo: orden de decoradores / dependencia externa / disponibilidad.
- Evidencia:
  - En crear y editar producto, `requiere_conexion_cloud` es el decorador externo
    y `login_required` queda dentro (`apps/productos/views.py`).
  - El gate hace una comprobación remota antes de saber si el solicitante puede
    acceder.
  - Crear/editar categorías, estados e imágenes no usan el mismo gate.
- Reproducción validada:
  - Con sync habilitado y cloud no disponible, una petición no autenticada de
    creación recibió 503 del chequeo cloud en vez de la redirección de login.
- Impacto:
  - Tráfico anónimo puede provocar llamadas externas y observar el estado de
    conectividad.
  - La política aparente de “solo editar conectado” es incompleta y, aun cuando
    pasa, no soluciona la propagación descrita en PRO-002.
- Recomendación:
  - Autenticar y autorizar primero. Sustituir el gate parcial por una política
    de escritura autoritativa común a todas las mutaciones.
- Prueba de aceptación sugerida:
  - Una petición anónima debe terminar antes de cualquier I/O cloud; todas las
    mutaciones deben compartir la misma política de consistencia.

### PRO-017 - La impresión física carece de permiso, cuota y trazabilidad propios

- Severidad: media.
- Tipo: recurso físico / abuso operacional / autorización.
- Evidencia:
  - El endpoint exige sesión y que el módulo esté habilitado, pero no permiso
    de usuario (`apps/productos/views.py:301-358`).
  - Una solicitud puede generar hasta 100 etiquetas; no hay throttling,
    confirmación privilegiada ni evento de auditoría.
- Impacto:
  - Cualquier usuario autenticado puede consumir papel/etiquetas, interrumpir el
    flujo de impresión o producir identificadores físicos no autorizados.
- Recomendación:
  - Crear permiso específico, registrar producto/cantidad/impresora/actor y
    aplicar límites por solicitud y ventana temporal.
- Prueba de aceptación sugerida:
  - Sin permiso se obtiene 403; con permiso, toda impresión queda auditada y los
    excesos son rechazados antes de hablar con la impresora.

## Hallazgos P3

### PRO-018 - La aplicación no tiene pruebas propias

- Severidad: media.
- Tipo: cobertura / regresión.
- Evidencia:
  - `apps/productos/tests/` solo contiene `__init__.py`.
  - La cobertura existente llega indirectamente desde API, sync, tenancy y
    ventas, pero no prueba el CRUD HTML, permisos, plantillas, imágenes,
    generadores ni Admin.
- Impacto:
  - Las fronteras más vulnerables pueden cambiar sin señal de CI.
- Recomendación:
  - Convertir las reproducciones de esta auditoría en pruebas permanentes, una
    vez acordado el comportamiento correcto.
- Prueba de aceptación sugerida:
  - Añadir matrices por rol/canal y casos de concurrencia, XSS, archivos, bajas,
    sync e imágenes.

### PRO-019 - La lista carga todo el catálogo y ejecuta consultas N+1

- Severidad: media.
- Tipo: rendimiento / escalabilidad / memoria.
- Evidencia:
  - La vista serializa el catálogo completo para insertarlo en la plantilla
    (`apps/productos/views.py`).
  - Cada acceso a `producto.stock_actual` agrega lotes activos mediante una
    consulta (`apps/productos/models.py`).
  - Filtros aplicados después de un prefetch pueden producir consultas
    adicionales.
- Reproducción validada:
  - La instrumentación de consultas confirmó una consulta adicional de lotes
    por cada producto al calcular `stock_actual`.
- Impacto:
  - Tiempo, memoria y tamaño HTML crecen linealmente, mientras consultas crecen
    como N+1. El navegador recibe datos que quizá no mostrará.
- Recomendación:
  - Paginar/buscar en servidor, anotar el stock agregado en una sola consulta y
    entregar solo los campos de la página actual.
- Prueba de aceptación sugerida:
  - El número de consultas debe mantenerse acotado al crecer de 10 a 1,000
    productos y el HTML inicial no debe contener el catálogo completo.

### PRO-020 - El Admin presenta indicadores inconsistentes o vacíos

- Severidad: baja-media.
- Tipo: Admin / observabilidad / código muerto.
- Evidencia:
  - `valor_inventario_display` calcula el valor, pero el `return` quedó dentro
    de una cadena triple, por lo que devuelve `None`
    (`apps/productos/admin.py:173-181`).
  - El modelo considera reposición cuando stock es `<= stock_minimo`; el Admin
    usa `<`, produciendo otra respuesta exactamente en el umbral.
  - `lotes_disponibles` construye HTML con interpolación y lo marca seguro; hoy
    el número de lote se genera internamente, pero el patrón queda frágil si su
    origen cambia (`apps/productos/admin.py:190-223`).
- Impacto:
  - El operador recibe una valoración vacía y señales de reposición distintas
    según la pantalla.
- Recomendación:
  - Reutilizar propiedades/servicios comunes, devolver explícitamente el valor
    y construir HTML con placeholders escapados.
- Prueba de aceptación sugerida:
  - Stock igual al mínimo y lotes con caracteres HTML deben renderizarse de
    forma idéntica y segura en modelo, Admin y reportes.

### PRO-021 - La configuración de formato de código de barras no se respeta

- Severidad: baja-media.
- Tipo: configuración / expectativa operacional.
- Evidencia:
  - Configuración admite `formato_codigo_barras` con valor como `RP-XXXXXX`.
  - El generador toma solo el texto antes de `-` y siempre produce seis dígitos
    (`apps/productos/utils.py`).
  - No valida cantidad o posición de `X`, longitud ni colisión con formatos
    manuales.
- Impacto:
  - Cambiar la configuración puede no producir el formato mostrado al usuario;
    etiquetas e integraciones reciben códigos inesperados.
- Recomendación:
  - Definir una gramática pequeña y validada, o reemplazar el campo libre por
    prefijo y longitud explícitos.
- Prueba de aceptación sugerida:
  - Prefijo, ancho y límites configurados deben reflejarse exactamente en los
    códigos generados o ser rechazados al guardar configuración.

### PRO-022 - Hay deuda de mantenimiento en Admin, índices y portabilidad

- Severidad: baja.
- Tipo: mantenibilidad / esquema / claridad.
- Evidencia:
  - `apps/productos/admin.py` repite imports y conserva bloques muertos.
  - SKU y código de barras combinan unicidad con índices explícitos
    redundantes en el modelo.
  - Se declara `GinIndex` para JSON, una optimización específica de PostgreSQL,
    aunque el proyecto también mantiene caminos de despliegue SQL Server.
- Impacto:
  - Aumenta ruido en migraciones, costo de escritura y riesgo de diferencias
    entre motores; dificulta distinguir reglas requeridas de optimizaciones.
- Recomendación:
  - Limpiar imports/código muerto y revisar índices contra planes reales por
    backend. Separar migraciones específicas por motor si siguen siendo
    necesarias.
- Prueba de aceptación sugerida:
  - `makemigrations --check` debe permanecer limpio y la matriz de backends
    soportados debe poder crear el esquema sin índices duplicados o incompatibles.

## Validación ejecutada

### Suite existente seleccionada

Se creó únicamente para la corrida un settings aislado con base
`test_pos_fifo_auditoria_productos_20260820`. La base fue creada y destruida por
Django; no se usó la base compartida del desarrollador.

```text
manage.py test \
  apps.productos \
  apps.api.tests.test_producto_viewset \
  apps.api.tests.test_categoria_viewset \
  apps.api.tests.test_maestros_keyset \
  apps.sync.tests.test_engine \
  apps.sync.tests.test_pull_keyset \
  apps.tenancy.tests.test_media \
  apps.ventas.tests.test_producto_precio_cache \
  --settings=config.settings_auditoria_productos_temp --noinput -v 1
```

Resultado:

- **75 pruebas ejecutadas**.
- **75 aprobadas**.
- Duración: **71.115 s**.
- `System check identified no issues`.
- Base temporal destruida al terminar.

La corrida demuestra que los contratos actualmente cubiertos permanecen
verdes; no invalida los hallazgos porque el CRUD HTML y varias invariantes no
tienen pruebas propias.

### Batería adversarial temporal

Se añadieron de manera transitoria catorce casos para observar el
comportamiento vigente. Resultado limpio final:

- **14 pruebas ejecutadas**.
- **14 reproducciones confirmadas**.
- Duración: **3.125 s**.
- `System check identified no issues`.

Los casos confirmaron:

1. Mutaciones de catálogo y precio por una cajera sin RBAC.
2. Ausencia de auditoría de cambio de precio.
3. Ruptura del contexto `<script>` desde nombre de producto.
4. Ruptura del contexto `<script>` desde descripción de categoría.
5. Persistencia local de precio negativo, choice inválido y JSON con forma
   incorrecta.
6. Duplicación después de editar localmente el SKU y descargar el SKU cloud.
7. Colisión de dos códigos nulos normalizados a cadena vacía y cursor bloqueado.
8. Desactivación masiva sin actualizar `fecha_modificacion`.
9. Producto de categoría inactiva visible por búsqueda y escáner.
10. Cargador de ventas aceptando estados inactivos.
11. Carga de HTML como imagen y archivo anterior huérfano tras reemplazo.
12. Chequeo cloud ejecutado antes de autenticación.
13. Una consulta de stock por producto.
14. Ausencia de validación entre esquema de categoría y atributos del producto.

El archivo de pruebas y el settings temporal fueron eliminados después de la
validación. No se conservaron cambios funcionales.

### Chequeos estáticos de Django

```text
manage.py check --settings=config.settings_auditoria_productos_temp
System check identified no issues (0 silenced).

manage.py makemigrations productos --check --dry-run \
  --settings=config.settings_auditoria_productos_temp
No changes detected in app 'productos'
```

## Aspectos positivos observados

- SKU y código de barras tienen unicidad en base, una última barrera útil ante
  duplicados accidentales.
- Las relaciones históricas importantes usan `PROTECT`, evitando que una baja
  silenciosa borre ventas, lotes o cotizaciones relacionadas.
- La API de maestros sí integra permisos granulares y validaciones de precio,
  stock y forma de atributos.
- Las categorías sincronizadas tienen `origen_cloud_id`, por lo que un cambio
  de nombre puede conservar identidad.
- El pull usa cursor keyset y savepoints por elemento, una base razonable para
  reintentos observables.
- Ventas vuelve a validar en servidor que el precio enviado sea positivo y
  coincida con el precio vigente; la interfaz no es la única defensa de precio.
- Las rutas mutables usan POST y conservan protección CSRF en el flujo normal.
- El almacenamiento tenant agrega prefijos por negocio, reduciendo colisiones
  y exposición accidental entre namespaces.

## Orden recomendado de remediación

1. **Cerrar autorización y XSS:** PRO-001 y PRO-005 antes de ampliar acceso al
   portal o delegar mantenimiento de catálogo.
2. **Definir autoridad del maestro:** resolver PRO-002, PRO-003, PRO-004 y
   PRO-008 como un solo diseño de identidad, mutación, baja y cursor.
3. **Blindar venta y archivos:** PRO-006, PRO-007 y PRO-012.
4. **Unificar invariantes y trazabilidad:** PRO-009, PRO-010, PRO-011,
   PRO-013, PRO-014 y PRO-015.
5. **Normalizar gates y recursos operativos:** PRO-016 y PRO-017.
6. **Crear la red de regresión y optimizar:** PRO-018 a PRO-022.

No conviene corregir únicamente el decorador cloud o volver inmutable el SKU de
la pantalla: la solución queda incompleta mientras API pueda borrar sin
tombstone y productos no tengan identidad estable.

## Criterios de cierre de la auditoría

La aplicación puede considerarse cerrada cuando, como mínimo:

- cada endpoint aplica permisos RBAC del servidor y existen pruebas negativas
  por rol;
- ninguna plantilla puede salir del contexto JSON con datos almacenados;
- HTML, API, Admin y sync comparten las mismas invariantes de producto;
- el maestro tiene identidad estable, escritor autoritativo, baja convergente y
  cursores que toleran valores nulos válidos;
- el servicio de venta rechaza dentro de la transacción cualquier producto o
  categoría inactivos;
- las imágenes se validan, versionan, limpian y sincronizan sin residuos;
- cada mutación sensible deja auditoría durable con actor, tenant, canal y
  antes/después;
- las pruebas permanentes cubren los catorce escenarios adversariales y
  concurrencia de identificadores;
- la lista de productos mantiene consultas y tamaño de respuesta acotados al
  crecer el catálogo.

## Conclusión

`apps/productos` no necesita solo endurecer formularios: necesita consolidar el
contrato de maestro entre local y cloud. Hoy el canal HTML tiene más capacidad
y menos validación que la API, mientras sync depende de campos que ese canal
puede cambiar. La combinación permite mutaciones no autorizadas, divergencia,
duplicación, baja no convergente y venta de artículos retirados.

El orden de mayor retorno es cerrar permisos/XSS y, de inmediato, diseñar la
identidad y autoridad del catálogo como una sola corrección transversal. Las
mejoras de rendimiento y Admin son importantes, pero no deben preceder a esas
garantías de seguridad e integridad.
