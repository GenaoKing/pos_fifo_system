# Auditoría profunda de código - `apps/cotizaciones`

Fecha: 2026-08-20
Revisión de cierre: `3f22385`
Modo: lectura, pruebas y documentación; no se aplicaron correcciones funcionales.

## Resumen ejecutivo

`apps/cotizaciones` no es únicamente un generador de documentos comerciales.
Sus precios se convierten en una fuente autorizada para `apps/ventas`, sus
estados participan en la prevención de ventas duplicadas y sus eventos se
replican a cloud. Por eso una cotización debe tratarse como una autorización
comercial versionada, no como un JSON confiable enviado por el navegador.

La implementación contiene bases valiosas: la creación local y su outbox se
ejecutan en una misma transacción, la conversión normal del POS bloquea la
cotización con `select_for_update`, las relaciones históricas usan `PROTECT`,
el PDF escapa los textos recibidos y el número incluye la sucursal cuando esta
puede resolverse. Sin embargo, esas garantías quedan anuladas por la frontera de
entrada y por contratos incompletos entre cotización, venta y sync.

Los riesgos más urgentes son:

- El módulo no tiene permisos RBAC declarados ni aplicados. Cualquier usuario
  autenticado con el módulo habilitado puede ver, crear, descargar y marcar
  cotizaciones como convertidas.
- El endpoint confía en el precio enviado por el navegador. Ese precio pasa a
  ser una fuente autorizada por ventas. Se reprodujo una cotización a RD$0.01
  para una unidad y una venta posterior de cinco unidades a RD$0.05 total, sin
  el permiso `ventas.aplicar_descuento`.
- La conversión no verifica que sucursal, cliente ni cantidades de la venta
  coincidan con la cotización. Una cotización de un cliente pudo convertirse en
  una venta de otro cliente.
- Un evento `COTIZACION_CREADA` atrasado sobrescribe el estado `CONVERTIDA` con
  `PENDIENTE`. Se reprodujo la reapertura y una segunda venta contra la misma
  cotización.
- Las consultas por ID y los listados no se acotan a la sucursal actual. Un
  operador de una sucursal pudo listar, abrir, descargar y cargar en el POS la
  cotización de otra.
- El endpoint legacy de conversión sigue publicado, no bloquea la fila y puede
  marcarla convertida sin venta o vincularle una venta ajena.
- El PDF afirma una validez de 15 días, pero el modelo solo revisa
  `estado == PENDIENTE`: un precio histórico permanece convertible
  indefinidamente.

Se documentan **18 hallazgos**:

| Prioridad | Cantidad | Criterio |
| --- | ---: | --- |
| P1 | 7 | Puede autorizar precios manipulados, eludir permisos comerciales, duplicar ventas, mezclar clientes/sucursales o exponer información de otra sucursal. |
| P2 | 8 | Debilita integridad matemática, estados, numeración, trazabilidad, bajas distribuidas y manejo de errores. |
| P3 | 3 | Deuda de cobertura, rendimiento y contratos HTTP que eleva el riesgo de regresión. |

La suite seleccionada terminó con **41/41 pruebas existentes aprobadas**. La
aplicación aportó solo una prueba propia, dedicada al PDF. Una batería
adversarial temporal terminó con **14/14 reproducciones confirmadas** y fue
retirada del workspace. También pasaron `manage.py check` y
`makemigrations cotizaciones --check --dry-run` sobre una base de prueba
aislada.

## Alcance

Se inspeccionaron completamente:

- `apps/cotizaciones/models.py`
- `apps/cotizaciones/views.py`
- `apps/cotizaciones/admin.py`
- `apps/cotizaciones/urls.py`
- `apps/cotizaciones/pdf_generator.py`
- `apps/cotizaciones/apps.py`
- `apps/cotizaciones/migrations/`
- `apps/cotizaciones/tests/`
- `templates/cotizaciones/lista_cotizaciones.html`
- `templates/cotizaciones/detalle_cotizacion.html`
- `templates/cotizaciones/crear_cotizacion.html`

También se trazaron las fronteras relevantes en:

- `apps/ventas/services/ventas_service.py`
- `apps/ventas/views.py`
- `apps/sync/events.py`, `serializers.py`, `registry.py` y `engine.py`
- `apps/api/views/sync.py`
- `apps/productos/models.py`
- `apps/clientes/models.py` y `views.py`
- `apps/sucursales/models.py`
- `apps/configuracion/decorators.py`, `models.py` y `utils.py`
- `apps/permisos/catalogo.py` y `engine.py`
- `apps/auditoria/`
- `apps/common/pdf/standard.py`
- `config/settings.py` y `config/urls.py`

El núcleo de `apps/cotizaciones` suma **702 líneas Python**, sin contar
migraciones ni pruebas. Sus tres plantillas suman **648 líneas**. La aplicación
tiene dos migraciones y **una sola prueba propia**, que comprueba la descarga
del PDF.

La auditoría comenzó y cerró en `3f22385`; `apps/cotizaciones` permaneció sin
cambios. Durante la revisión había correcciones externas sin commit en API,
caja, clientes, cuentas por cobrar, inventario, permisos, sync, tenancy,
ventas y settings. No se revirtieron ni alteraron. Las referencias cross-app
corresponden al estado visible al cierre.

## Hallazgos P1

### COT-001 - El módulo carece de autorización RBAC

- Severidad: crítica.
- Tipo: autorización / información comercial / separación de funciones.
- Evidencia:
  - Todas las vistas usan `login_required` y `requiere_modulo`, pero ninguna
    aplica un permiso de usuario (`apps/cotizaciones/views.py:32-305`).
  - `requiere_modulo` solo comprueba que la funcionalidad esté habilitada para
    el negocio; no evalúa al actor (`apps/configuracion/decorators.py:20-32`).
  - El catálogo RBAC no declara ninguna acción de cotizaciones
    (`apps/permisos/catalogo.py`).
  - Quedan expuestas bajo esa regla lista, creación, detalle, datos POS,
    conversión legacy y PDF (`apps/cotizaciones/urls.py:6-17`).
- Reproducción validada:
  - Una cajera sin `cotizaciones.crear` pudo listar el módulo y guardar una
    cotización. La consulta explícita al motor RBAC devolvió `False`.
- Impacto:
  - Cualquier cuenta autenticada puede emitir ofertas comerciales, aplicar
    descuentos en el documento, leer datos de clientes y alterar estados.
  - No existe forma de delegar “ver” sin entregar también “crear/convertir”.
- Recomendación:
  - Declarar al menos `cotizaciones.ver`, `cotizaciones.crear`,
    `cotizaciones.aplicar_descuento`, `cotizaciones.convertir` y
    `cotizaciones.descargar`.
  - Aplicar la autorización en servicios del servidor, no solo en vistas o
    botones.
- Prueba de aceptación sugerida:
  - Una matriz por rol debe devolver 403 para cada acción no concedida y no
    realizar consultas sensibles ni mutaciones antes del rechazo.

### COT-002 - Un precio inventado en la cotización se vuelve precio autorizado de venta

- Severidad: crítica.
- Tipo: integridad financiera / manipulación de precio / bypass de descuento.
- Evidencia:
  - `guardar_cotizacion` acepta `precio_unitario` del JSON y lo persiste sin
    compararlo con `Producto.precio_venta`
    (`apps/cotizaciones/views.py:121-135`).
  - Ventas construye por producto un conjunto de precios autorizados con el
    precio vigente y todos los precios de la cotización
    (`apps/ventas/services/ventas_service.py:568-602`).
  - El permiso `ventas.aplicar_descuento` solo se exige si el campo explícito
    `descuento` es mayor que cero (`apps/ventas/services/ventas_service.py:475-492`).
- Reproducción validada:
  - Una cajera con `ventas.crear`, pero sin `ventas.aplicar_descuento`, guardó
    una cotización de una unidad a RD$0.01 y descuento cero.
  - Después vendió cinco unidades a RD$0.01 cada una usando esa cotización. La
    venta se confirmó por RD$0.05.
- Impacto:
  - La cotización actúa como un mecanismo de autorización de precio creado por
    el mismo cliente no confiable que propone el valor.
  - El descuento real queda oculto como “precio cotizado” y evita el permiso
    específico de descuentos.
- Recomendación:
  - Resolver el precio base en servidor. Toda desviación debe modelarse como
    descuento o precio negociado, con permiso, límite, motivo, actor y auditoría.
  - La venta debe consumir una autorización inmutable emitida por el servidor,
    no volver a confiar en un número del carrito.
- Prueba de aceptación sugerida:
  - Modificar el precio en DevTools debe producir 403/409; una excepción de
    precio autorizada debe estar firmemente ligada a cotización, línea, cantidad
    máxima, cliente, sucursal y vigencia.

### COT-003 - La conversión no está ligada al cliente, sucursal ni cantidades cotizadas

- Severidad: crítica.
- Tipo: autorización contextual / transferencia de oferta / contrato comercial.
- Evidencia:
  - `_resolver_cotizacion` busca solo por ID y estado pendiente
    (`apps/ventas/services/ventas_service.py:647-679`).
  - `_validar_precios` reduce los detalles a un conjunto de precios por producto;
    no conserva cantidades ni cliente (`apps/ventas/services/ventas_service.py:585-595`).
  - El cliente de la venta se resuelve independientemente desde `cliente_id`
    (`apps/ventas/services/ventas_service.py:277-280` y `:694-710`).
  - Tampoco se contrasta `cotizacion.sucursal` con la sucursal resuelta para la
    venta.
- Reproducción validada:
  - Una cotización para una unidad autorizó cinco al mismo precio negociado.
  - Una cotización de Cliente A se convirtió correctamente en una venta de
    Cliente B.
- Impacto:
  - Una oferta especial personal o de una sucursal puede transferirse a otro
    comprador, caja o volumen.
  - La venta queda vinculada a una cotización cuyos datos no explican lo que se
    vendió.
- Recomendación:
  - Al convertir, exigir coincidencia de tenant/sucursal, cliente y líneas; cada
    línea debe tener identidad, cantidad autorizada, precio y saldo convertible.
  - Definir explícitamente si se permiten parciales, sustituciones o líneas
    adicionales y registrar cada excepción.
- Prueba de aceptación sugerida:
  - Cambiar cliente, sucursal, producto, cantidad o precio respecto a la oferta
    debe fallar, salvo una política de conversión parcial explícita y auditada.

### COT-004 - Un evento de creación atrasado reabre una cotización convertida

- Severidad: crítica.
- Tipo: orden de eventos / idempotencia semántica / venta duplicada.
- Evidencia:
  - El handler cloud de `COTIZACION_CREADA` hace `update_or_create` y copia
    directamente `estado` del payload (`apps/api/views/sync.py:1137-1165`).
  - No impide una transición regresiva de `CONVERTIDA` a `PENDIENTE`.
  - El push ordena por fecha, pero excluye eventos que alcanzaron el máximo de
    reintentos; un evento posterior puede adelantarse y el anterior enviarse
    después de una intervención (`apps/sync/engine.py:253-283`).
  - El handler de creación no limpia `venta`, por lo que puede quedar
    `PENDIENTE` con una venta ya asociada.
- Reproducción validada:
  - Se capturó el payload inicial, se convirtió normalmente la cotización y se
    aplicó después ese `COTIZACION_CREADA` antiguo.
  - Cloud la dejó `PENDIENTE` conservando la primera venta. Una segunda llamada
    al servicio creó otra venta y volvió a vincular la cotización.
- Impacto:
  - Duplica venta, cobro potencial y consumo FIFO a partir de la misma oferta.
  - El vínculo final oculta la primera conversión en la cotización, aunque la
    primera venta continúa existiendo.
- Recomendación:
  - Hacer las transiciones monotónicas y versionadas. `CREADA` debe ser
    create-only o ignorar campos de lifecycle sobre una entidad existente.
  - Comparar versión/secuencia por agregado y rechazar eventos atrasados con un
    resultado observable.
- Prueba de aceptación sugerida:
  - Todas las permutaciones y reintentos de `CREADA`/`CONVERTIDA` deben converger
    a una sola conversión y una sola venta.

### COT-005 - Las vistas no aíslan cotizaciones por sucursal

- Severidad: alta.
- Tipo: aislamiento horizontal / privacidad / operación multi-sucursal.
- Evidencia:
  - El listado parte de `.all()` sin filtro de sucursal
    (`apps/cotizaciones/views.py:37-43`).
  - Detalle, datos POS, conversión y PDF buscan únicamente por ID
    (`apps/cotizaciones/views.py:174-178`, `:196`, `:254` y `:289-292`).
  - La creación sí guarda la sucursal actual (`apps/cotizaciones/views.py:109-115`),
    por lo que el modelo ya contiene el ámbito que las lecturas ignoran.
- Reproducción validada:
  - Con sucursal A como actual, una cajera listó, abrió y cargó mediante el API
    POS una cotización creada para sucursal B.
- Impacto:
  - Expone nombre, identificación, teléfono, dirección, notas y condiciones
    comerciales de clientes de otra sucursal.
  - Permite convertir o alterar una oferta fuera del ámbito operativo del actor.
- Recomendación:
  - Centralizar un queryset `visibles_para(usuario, sucursal)` y usarlo en todas
    las rutas, incluida la conversión dentro de ventas.
  - Reservar acceso consolidado para un permiso explícito y auditable.
- Prueba de aceptación sugerida:
  - Un ID válido de otra sucursal debe devolver 404/403 de manera uniforme en
    HTML, JSON, PDF y conversión.

### COT-006 - El endpoint legacy permite estados y vínculos de venta arbitrarios

- Severidad: alta.
- Tipo: integridad de estado / carrera / endpoint obsoleto.
- Evidencia:
  - `/api/<id>/convertida/` continúa publicado aunque el POS ya no lo usa
    (`apps/cotizaciones/urls.py:14` y `apps/cotizaciones/views.py:240-248`).
  - Marca `CONVERTIDA` aunque no se envíe `venta_id`
    (`apps/cotizaciones/views.py:250-268`).
  - Si se envía, acepta cualquier `Venta` existente sin comparar cliente,
    sucursal, productos o procedencia.
  - No usa `transaction.atomic()` ni `select_for_update`; el cambio y el evento
    de sync no forman una unidad atómica.
- Reproducción validada:
  - Una llamada con `{}` dejó `estado=CONVERTIDA` y `venta=NULL`.
  - Otra vinculó una venta de Cliente B a una cotización de Cliente A.
- Impacto:
  - Corrompe reportes y sincronización, bloquea una conversión legítima o atribuye
    una venta a la oferta equivocada.
  - Dos llamadas concurrentes pueden pasar `puede_convertirse` y competir por
    el vínculo final; un fallo de outbox puede dejar el cambio sin evento.
- Recomendación:
  - Retirar la ruta si no tiene consumidor vigente. Si se conserva para una
    integración, hacer que invoque el mismo servicio transaccional de venta y
    exigir autenticación/autorización específica e idempotencia.
- Prueba de aceptación sugerida:
  - No debe existir una transición a `CONVERTIDA` sin una venta creada y validada
    en la misma transacción.

### COT-007 - La validez de 15 días existe solo como texto del PDF

- Severidad: alta.
- Tipo: vigencia comercial / precio histórico / expectativa contractual.
- Evidencia:
  - El PDF declara “validez de 15 días”
    (`apps/cotizaciones/pdf_generator.py:102-110`).
  - `puede_convertirse` solo comprueba `estado == 'PENDIENTE'`
    (`apps/cotizaciones/models.py:145-148`).
  - Datos POS y servicio de ventas dependen de esa propiedad, sin comparar
    fechas (`apps/cotizaciones/views.py:196-203` y
    `apps/ventas/services/ventas_service.py:673-679`).
- Reproducción validada:
  - Una cotización de 60 días continuó devolviendo `puede_convertirse=True` y el
    endpoint POS entregó sus precios como convertibles.
- Impacto:
  - Un precio antiguo conserva valor de autorización indefinidamente, aunque
    costos, impuestos o políticas hayan cambiado.
  - El documento comunica una regla que el sistema no cumple.
- Recomendación:
  - Persistir `valida_hasta` al emitir, incorporar `EXPIRADA` o una evaluación
    equivalente y comprobarla dentro del bloqueo transaccional de conversión.
- Prueba de aceptación sugerida:
  - Al vencer, detalle/PDF pueden seguir consultándose, pero datos POS y venta
    deben rechazar la conversión con un error de dominio estable.

## Hallazgos P2

### COT-008 - Se persisten cantidades e importes comercialmente imposibles

- Severidad: alta.
- Tipo: validación / aritmética / invariantes.
- Evidencia:
  - La vista convierte tipos y llama `objects.create()` sin formulario,
    serializer ni `full_clean()` (`apps/cotizaciones/views.py:91-145`).
  - Los `MinValueValidator` del modelo no se ejecutan automáticamente en
    `save()` (`apps/cotizaciones/models.py:170-220`).
  - No existen `CheckConstraint` para cantidad, precio, descuento, total o
    porcentaje.
  - `DetalleCotizacion.save()` permite descuento superior al subtotal y produce
    total negativo.
- Reproducción validada:
  - El endpoint guardó una línea con cantidad/precio cero y otra con RD$150 de
    descuento sobre RD$100. La cabecera terminó con total `-50.00`.
- Impacto:
  - PDF, reportes y payload de sync pueden contener ofertas negativas o de cero
    unidades.
  - Cada canal puede interpretar de forma distinta un registro inválido.
- Recomendación:
  - Validar forma, límites, redondeo y máximos en un servicio común; respaldar
    invariantes esenciales con constraints de base.
- Prueba de aceptación sugerida:
  - Cero, negativos, NaN, exceso de dígitos, descuento mayor al subtotal y
    payload no-lista deben rechazarse antes de crear cabecera o evento.

### COT-009 - La creación acepta clientes y productos inactivos

- Severidad: media-alta.
- Tipo: lifecycle / catálogo / consistencia.
- Evidencia:
  - La pantalla inicial lista activos, pero el POST resuelve cliente y producto
    solo por ID (`apps/cotizaciones/views.py:60-65` y `:101-123`).
  - No comprueba que la categoría del producto esté activa.
- Reproducción validada:
  - Un POST directo creó una cotización con cliente inactivo y producto inactivo.
- Impacto:
  - Un estado retirado en UI no es una regla del backend.
  - Se generan documentos y eventos con entidades que la operación considera
    fuera de uso.
- Recomendación:
  - Reutilizar una regla de “cotizable” para cliente, producto y categoría y
    evaluarla nuevamente al convertir.
- Prueba de aceptación sugerida:
  - La desactivación ocurrida antes del POST debe producir 409/400 y rollback
    completo.

### COT-010 - La numeración por `count()+1` no es estable ni segura

- Severidad: media-alta.
- Tipo: identidad / concurrencia / unicidad.
- Evidencia:
  - `save()` cuenta filas con el prefijo y usa `count + 1`
    (`apps/cotizaciones/models.py:117-131`).
  - No bloquea una secuencia ni reintenta al colisionar.
  - La restricción única combina `sucursal` y número; cuando `sucursal=NULL`,
    PostgreSQL permite repetir el mismo par con nulo
    (`apps/cotizaciones/models.py:107-112`).
- Reproducción validada:
  - Tras crear `00001` y `00002` y borrar la primera, la siguiente intentó
    reutilizar `00002` y produjo `IntegrityError`.
  - Dos cotizaciones legacy con `sucursal=NULL` y el mismo número se guardaron
    simultáneamente.
- Impacto:
  - Altas concurrentes o bajas intermedias pueden fallar; en legacy pueden
    existir documentos indistinguibles.
  - Sync identifica cotizaciones por sucursal+número, por lo que duplicados
    pueden colapsar en una sola fila cloud.
- Recomendación:
  - Usar contador transaccional por sucursal/fecha o identidad estable separada
    del número visible. Hacer explícita la política para filas legacy nulas.
- Prueba de aceptación sugerida:
  - Concurrencia, borrado y sucursal nula deben conservar números únicos sin
    depender de contar filas vivas.

### COT-011 - Cabecera y detalles pueden quedar con totales diferentes

- Severidad: media-alta.
- Tipo: datos derivados / Admin / consistencia.
- Evidencia:
  - `DetalleCotizacion.save()` recalcula solo la línea
    (`apps/cotizaciones/models.py:216-221`).
  - `Cotizacion.calcular_totales()` no se invoca automáticamente ni persiste por
    sí sola (`apps/cotizaciones/models.py:133-143`).
  - Admin permite editar detalles inline y los campos de cabecera sin un servicio
    que los concilie (`apps/cotizaciones/admin.py:5-16`).
  - El sync cloud recibe totales de cabecera y detalles como campos separados.
- Reproducción validada:
  - Cambiar una línea de RD$100 a RD$25 dejó la cabecera en RD$100.
- Impacto:
  - PDF, listado, detalle y conversión pueden mostrar o usar totales que no
    corresponden a las líneas.
- Recomendación:
  - Elegir una única fuente: recalcular cabecera dentro de todo cambio de líneas
    o hacer los totales derivados/no editables.
- Prueba de aceptación sugerida:
  - Crear, editar, borrar y reemplazar líneas por cualquier canal debe mantener
    `subtotal`, descuento y total exactamente reconciliados.

### COT-012 - No existe auditoría de negocio para el ciclo de la cotización

- Severidad: media-alta.
- Tipo: trazabilidad / responsabilidad / soporte.
- Evidencia:
  - No hay permisos ni acciones de cotización en `apps/auditoria`.
  - Crear y convertir generan eventos de sync, pero estos representan
    replicación técnica, no una bitácora humana inmutable con antes/después.
  - Lectura de datos, descarga de PDF, descuentos y uso del endpoint legacy no
    generan auditoría de dominio.
- Impacto:
  - No puede demostrarse quién autorizó un precio o descuento, quién consultó
    información comercial ni por qué una cotización cambió de estado.
- Recomendación:
  - Registrar emisión, consulta sensible, descarga, expiración, cancelación y
    conversión con actor, sucursal, cliente, líneas, versión y correlación con
    venta/evento sync.
- Prueba de aceptación sugerida:
  - Cada transición aceptada debe producir exactamente un evento de auditoría
    dentro de la misma transacción y un rollback no debe dejarlo huérfano.

### COT-013 - Borrar una cotización no converge entre local y cloud

- Severidad: media-alta.
- Tipo: lifecycle distribuido / tombstone / historial.
- Evidencia:
  - El modelo solo conoce `PENDIENTE` y `CONVERTIDA`; no tiene `ANULADA` ni
    `EXPIRADA` (`apps/cotizaciones/models.py:15-18`).
  - Django Admin conserva el borrado estándar de `Cotizacion`; los detalles
    caen por `CASCADE` (`apps/cotizaciones/admin.py` y
    `apps/cotizaciones/models.py:156-160`).
  - El registro sync solo define eventos de creada y convertida, no tombstone o
    anulación (`apps/sync/registry.py` y `apps/sync/events.py:328-347`).
- Impacto:
  - Una baja local deja la copia cloud vigente; una baja cloud no informa a la
    sucursal.
  - Se pierde evidencia comercial y una oferta puede seguir visible en otro nodo.
- Recomendación:
  - Reemplazar borrado por transición versionada `ANULADA`, con motivo y evento
    convergente; reservar purga física para retención controlada.
- Prueba de aceptación sugerida:
  - Anular en cualquier nodo debe converger y bloquear conversión en todos,
    preservando documento y auditoría.

### COT-014 - Los errores exponen detalles internos y usan estados incorrectos

- Severidad: media.
- Tipo: contrato HTTP / información interna / observabilidad.
- Evidencia:
  - `guardar_cotizacion`, `marcar_convertida` y PDF capturan `Exception` y
    muestran `str(e)` (`apps/cotizaciones/views.py:157-166`, `:275-279` y
    `:304-306`).
  - JSON inválido se clasifica como 500; cliente inexistente, constraint y
    errores de programación comparten caminos ambiguos.
  - `get_object_or_404` dentro del `try` de conversión se transforma en 400 en
    vez de conservar 404.
- Reproducción validada:
  - Un cuerpo JSON truncado devolvió 500 e incluyó el texto interno de la
    excepción en `error`.
- Impacto:
  - Filtra detalles de parsers, constraints o infraestructura y dificulta a los
    clientes decidir si corregir o reintentar.
- Recomendación:
  - Mapear parseo/validación a 400, ausencia a 404, conflicto a 409 y fallo
    temporal a 503; devolver códigos estables y registrar el detalle solo en
    servidor.
- Prueba de aceptación sugerida:
  - La matriz de JSON inválido, IDs ausentes, conflicto, storage/PDF y error
    inesperado debe producir respuestas diferenciadas sin trazas internas.

### COT-015 - Estado y vínculo a venta no están protegidos como una invariante

- Severidad: media.
- Tipo: modelo de estados / constraint / edición administrativa.
- Evidencia:
  - No existe constraint que exija `venta IS NOT NULL` cuando
    `estado='CONVERTIDA'`, ni la inversa para `PENDIENTE`.
  - `venta` usa `SET_NULL`; borrar la venta convierte una cotización histórica
    en `CONVERTIDA` sin venta (`apps/cotizaciones/models.py:81-89`).
  - Admin permite editar estado y venta independientemente.
  - `puede_convertirse` solo mira el estado, no el vínculo
    (`apps/cotizaciones/models.py:145-148`).
- Impacto:
  - Estados imposibles pueden bloquear conversiones legítimas o reabrirse por
    edición/sync; reportes no pueden confiar en el vínculo.
- Recomendación:
  - Encapsular transiciones en métodos/servicio, hacer campos lifecycle de solo
    lectura en Admin y respaldar combinaciones esenciales con constraints.
- Prueba de aceptación sugerida:
  - Ningún canal debe persistir `CONVERTIDA+NULL` ni `PENDIENTE+venta`; borrar o
    anular una venta debe seguir una política explícita.

## Hallazgos P3

### COT-016 - La cobertura propia se limita al PDF

- Severidad: media.
- Tipo: pruebas / regresión.
- Evidencia:
  - `apps/cotizaciones/tests/test_pdf.py` contiene un solo caso.
  - No hay pruebas propias para permisos, creación, cálculos, sucursal,
    conversión, vigencia, numeración, Admin ni errores.
  - Parte del contrato está cubierto indirectamente desde ventas y sync, pero
    esas suites no ejercían la frontera HTTP vulnerable.
- Impacto:
  - Los riesgos críticos permanecen verdes en CI porque el comportamiento no
    está expresado como expectativa.
- Recomendación:
  - Convertir las reproducciones adversariales en una suite permanente después
    de definir el comportamiento correcto.
- Prueba de aceptación sugerida:
  - Incluir matrices RBAC/sucursal, precios negociados, expiración, orden de
    eventos, numeración concurrente y coherencia cabecera-detalles.

### COT-017 - Listados y datos POS no tienen límites y agregan consultas de stock

- Severidad: media-baja.
- Tipo: rendimiento / escalabilidad / consumo de recursos.
- Evidencia:
  - El listado entrega todas las cotizaciones del queryset, sin paginación
    (`apps/cotizaciones/views.py:37-49`).
  - `obtener_datos_cotizacion` recorre cada detalle y accede a
    `p.stock_actual`, propiedad que consulta lotes por producto
    (`apps/cotizaciones/views.py:204-217` y
    `apps/productos/models.py:205-213`).
  - Guardar no limita cantidad de líneas ni longitud operacional de notas más
    allá de límites generales de request/base.
- Impacto:
  - Historial grande degrada HTML, y una cotización extensa produce patrón N+1
    al cargarse al POS.
- Recomendación:
  - Paginar listado, anotar stock en una consulta agregada y establecer máximos
    de líneas/tamaño coherentes con el negocio.
- Prueba de aceptación sugerida:
  - Consultas y tamaño de respuesta deben permanecer acotados al crecer de 10 a
    miles de cotizaciones o líneas.

### COT-018 - Los contratos de vista conservan caminos ambiguos y valores flotantes

- Severidad: baja.
- Tipo: mantenibilidad / precisión de interfaz / HTTP.
- Evidencia:
  - `crear_cotizacion` solo retorna respuesta dentro de `if request.method ==
    'GET'`, pero no declara `require_GET` (`apps/cotizaciones/views.py:52-67`).
  - La respuesta de creación y los datos POS convierten `Decimal` a `float`
    (`apps/cotizaciones/views.py:149-155` y `:206-229`).
  - La plantilla usa rutas hardcodeadas (`/cotizaciones/`, `/clientes/api/` y
    `/pos/api/`) en vez de URLs resueltas
    (`templates/cotizaciones/crear_cotizacion.html:238-339`).
- Impacto:
  - Un método inesperado puede terminar sin `HttpResponse`; floats introducen
    representaciones binarias innecesarias y prefijos de despliegue rompen rutas.
- Recomendación:
  - Declarar métodos, serializar dinero como strings decimales y generar URLs
    mediante `{% url %}` o configuración inyectada.
- Prueba de aceptación sugerida:
  - Métodos no permitidos deben devolver 405, importes conservar dos decimales
    exactos y las rutas funcionar bajo un prefijo de aplicación.

## Validación ejecutada

### Suite existente seleccionada

Se creó únicamente para la corrida un settings aislado con base
`test_pos_fifo_auditoria_cotizaciones_20260820`. Django creó y destruyó esa base;
no se usó la base compartida del desarrollador.

```text
manage.py test \
  apps.cotizaciones \
  apps.ventas.tests.test_ventas_service \
  apps.sync.tests.test_extended_serializers \
  apps.api.tests.test_sync_extended \
  --settings=config.settings_auditoria_cotizaciones_temp --noinput -v 1
```

Resultado:

- **41 pruebas ejecutadas**.
- **41 aprobadas**.
- Duración: **15.380 s**.
- `System check identified no issues`.
- Base temporal destruida al terminar.

### Batería adversarial temporal

Se añadieron transitoriamente catorce casos para observar el comportamiento
actual. El gate de módulo se aisló en el fixture para no confundir entitlement
de suscripción con autorización del usuario. Resultado definitivo:

- **14 pruebas ejecutadas**.
- **14 reproducciones confirmadas**.
- Duración: **6.044 s**.
- `System check identified no issues`.

Los casos confirmaron:

1. Listado y creación por usuario sin permiso de cotizaciones.
2. Precio RD$0.01 convertido en fuente autorizada de venta sin permiso de
   descuento y para una cantidad mayor.
3. Conversión de una cotización de Cliente A en venta de Cliente B.
4. Persistencia de cantidad/precio cero y total negativo.
5. Aceptación de cliente y producto inactivos.
6. Lectura y carga POS de una cotización de otra sucursal.
7. Estado `CONVERTIDA` sin venta mediante el endpoint legacy.
8. Vínculo de una venta ajena mediante ese endpoint.
9. Cotización de 60 días todavía convertible.
10. Colisión de numeración después de borrar una fila intermedia.
11. Duplicación del número cuando `sucursal=NULL`.
12. Totales de cabecera obsoletos tras cambiar un detalle.
13. JSON malformado devuelto como 500 con detalle interno.
14. Evento de creación atrasado que reabre la cotización y permite una segunda
    venta.

El archivo de pruebas y el settings temporal fueron eliminados después de la
validación. No se conservaron cambios funcionales.

### Chequeos estáticos de Django

```text
manage.py check --settings=config.settings_auditoria_cotizaciones_temp
System check identified no issues (0 silenced).

manage.py makemigrations cotizaciones --check --dry-run \
  --settings=config.settings_auditoria_cotizaciones_temp
No changes detected in app 'cotizaciones'
```

## Aspectos positivos observados

- La creación de cabecera, detalles y evento `COTIZACION_CREADA` ocurre dentro
  de un único `transaction.atomic()`.
- La conversión principal desde ventas bloquea la cotización antes de tocar
  inventario y evita la doble conversión concurrente en el camino normal.
- Las referencias a cliente, usuario, sucursal y producto usan `PROTECT`,
  preservando relaciones históricas ante borrados directos de maestros.
- El detalle recalcula subtotal, porcentaje y total de línea con `Decimal`.
- El número incorpora sucursal y fecha cuando la instalación está correctamente
  configurada.
- El handler cloud acota el upsert por sucursal+número y el receptor de eventos
  aplica handler y ledger idempotente dentro de la misma transacción.
- El generador PDF reutiliza `apps/common/pdf/standard.py`, cuyo helper `clean()`
  escapa contenido antes de construir `Paragraph`; nombres y notas no se
  interpretan como markup activo.
- Las plantillas usan `x-text` para resultados dinámicos, evitando inserción
  HTML directa en esas superficies.

## Orden recomendado de remediación

1. **Cerrar la autorización de precio:** COT-001 y COT-002 antes de seguir
   utilizando cotizaciones como fuente de precios para ventas.
2. **Definir el contrato de conversión:** COT-003, COT-006 y COT-007 con una
   autorización versionada por cliente, sucursal, línea, cantidad y vigencia.
3. **Hacer sync monotónico:** COT-004, y luego incorporar cancelación convergente
   de COT-013.
4. **Aislar sucursales y estados:** COT-005 y COT-015.
5. **Blindar persistencia y trazabilidad:** COT-008 a COT-012 y COT-014.
6. **Crear la red de regresión y optimizar:** COT-016 a COT-018.

No conviene resolver solo la validación del precio en el endpoint. Mientras un
evento atrasado pueda reabrir la cotización y la venta no esté ligada a
cantidad/cliente/sucursal/vigencia, el precio negociado seguirá siendo una
capacidad transferible y reutilizable.

## Criterios de cierre de la auditoría

La aplicación puede considerarse cerrada cuando, como mínimo:

- cada lectura y mutación exige permiso RBAC y ámbito de sucursal;
- los precios se originan o autorizan en servidor con actor, motivo y límites;
- una conversión verifica cliente, sucursal, productos, cantidades, vigencia y
  versión dentro del mismo bloqueo transaccional;
- eventos atrasados o duplicados nunca regresan el estado ni permiten otra
  venta;
- el endpoint legacy se elimina o comparte exactamente el servicio principal;
- cliente/producto/categoría activos y toda la aritmética se validan en todos
  los canales;
- número, estado, venta y totales están respaldados por invariantes de servicio
  y base de datos;
- expiración y anulación convergen entre sucursal y cloud sin borrado físico;
- toda emisión, descuento, descarga y transición sensible deja auditoría de
  dominio;
- las catorce reproducciones adversariales se convierten en regresiones
  permanentes con expectativas de rechazo/convergencia;
- listados y carga POS mantienen consultas y respuestas acotadas.

## Conclusión

El riesgo central de `apps/cotizaciones` es una inversión de confianza: ventas
trata el precio cotizado como legítimo, pero cotizaciones lo obtiene del
navegador sin autorización ni validación. A eso se suman un contrato de
conversión que no vincula cliente/cantidad/sucursal y un handler de sync capaz de
regresar el estado. La combinación permite crear una autorización de precio,
transferirla y reutilizarla después de una conversión.

La corrección debe diseñarse como un único flujo comercial: emisión autorizada,
snapshot inmutable/versionado, vigencia, conversión transaccional exacta y
eventos monotónicos. Corregir esos contratos produce mucho más valor que
endurecer aisladamente la pantalla o el PDF.
