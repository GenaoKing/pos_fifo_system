# Auditoria de codigo - apps/inventario

Fecha: 2026-08-20
Scope principal: `apps/inventario`
Scope de verificacion: permisos locales, modelos de productos/sucursales,
consumo FIFO desde ventas, auditoria, productor de eventos en `apps/sync`,
receptor cloud en `apps/api/views/sync.py`, reportes que leen lotes y pruebas
relacionadas.
Exclusiones por prioridad de negocio: `apps/facturacion_electronica` y
`apps/suscripciones` no fueron auditadas en esta pasada.
Modo: lectura, ejecucion de checks/pruebas sobre base de test y documentacion de
hallazgos; no se aplicaron cambios funcionales.

> **Estado (2026-08-20, misma fecha): MITIGADO.** Los 14 hallazgos se
> verificaron contra el codigo y los 14 resultaron reales. Todos estan
> corregidos, con pruebas de regresion. Ver
> [Estado de mitigacion](#estado-de-mitigacion) al final del documento.
> **Incluye 2 migraciones** (`inventario.0006` y `sync.0009`).

## Por que esta app sigue en la auditoria

Despues de `apps/api`, `apps/ventas` y `apps/sync`, inventario es la siguiente
frontera operativa de mayor impacto para el negocio actual. La app es la fuente
local de verdad para:

- compras y costos de adquisicion;
- creacion de lotes y existencias disponibles;
- consumo y devolucion FIFO usados por ventas;
- mermas, danos, conteos y correcciones manuales;
- valuacion del inventario;
- ledger y snapshots que se replican al cloud.

El scope principal contiene aproximadamente 1,897 lineas productivas entre
modelos, vistas, logica FIFO, Admin y URLs. En contraste, la app tiene 10 pruebas
propias y todas se concentran en `compra_editar`; no hay pruebas propias para
crear compras, ajustar inventario, RBAC, concurrencia, Admin ni sync de compras.

Durante la auditoria existia una correccion activa, no atribuible a este
documento, en `apps/inventario/fifo_logic.py`. Esa correccion agrega locks al
consumo FIFO e idempotencia a la devolucion por anulacion. Fue respetada,
inspeccionada como estado actual y no fue modificada.

## Resumen

La app tiene una base conceptual clara: cada `DetalleCompra` genera un `Lote`,
cada cambio de existencias pretende generar un `MovimientoLote`, las compras y
ajustes HTTP usan transacciones y las correcciones de compra registran una
`Auditoria`. Ademas, el WIP actual ya corrige dos riesgos importantes del lado de
ventas: consumo FIFO concurrente y devolucion repetida por anulacion.

Sin embargo, las invariantes no estan centralizadas. Crear una compra, editarla,
guardar un modelo, usar Django Admin y sincronizar al cloud siguen siendo caminos
distintos con reglas diferentes. Se confirmaron 14 hallazgos: siete P1, seis P2
y uno P3.

Los riesgos mas urgentes son concretos y reproducibles:

- una cajera sin `compras.registrar` puede crear una compra y aumentar stock;
- el endpoint acepta cantidades negativas y crea compras/lotes negativos;
- cada ajuste HTTP crea dos movimientos locales;
- editar o volver a guardar un ajuste vuelve a aplicar toda su cantidad;
- dos ajustes concurrentes pueden aprobarse sobre el mismo saldo y perder una
  actualizacion;
- una correccion de compra puede competir con una venta y restaurar stock ya
  consumido;
- cada linea de compra puede quedar duplicada y luego divergente en el ledger
  cloud.

Las 39 pruebas existentes seleccionadas pasan, pero no ejercitan esos caminos.
Doce reproducciones adicionales sobre la base de test confirmaron los defectos
principales y los errores de Admin/HTTP descritos abajo.

## Hallazgos priorizados

### INVENTARIO-001 - Un usuario sin permisos de compras puede consultar costos y crear stock

- Prioridad: P1.
- Severidad: alta.
- Tipo: seguridad / autorizacion / integridad de inventario.
- Evidencia:
  - `apps/permisos/catalogo.py:35-38` define `compras.ver`,
    `compras.registrar`, `inventario.ver` e `inventario.ajustar`.
  - `apps/inventario/views.py:53-65` protege el listado solo con
    `login_required`; el chequeo de Admin esta comentado y el queryset incluye
    todas las compras locales.
  - `apps/inventario/views.py:77-98` deja la creacion de compras disponible para
    cualquier usuario autenticado. El POST crea `Compra`, `DetalleCompra`,
    `Lote` y `MovimientoLote` en `apps/inventario/views.py:146-194`.
  - `apps/inventario/views.py:228-274` y `apps/inventario/views.py:281-309`
    tampoco exigen `inventario.ver` o `compras.ver` para busqueda, costos y
    detalle.
  - `apps/inventario/views.py:596-614` exige el modulo de etiquetas, pero no un
    permiso de compras antes de producir una impresion fisica.
  - La plantilla oculta el enlace de editar, pero muestra siempre Compras y
    Nueva Compra en `templates/base.html:144-158` y
    `templates/inventario/compras_lista.html:24-29`. Ocultar enlaces tampoco
    sustituiria el gate server-side.
  - Los dos gates que si existen llaman `tiene_permiso` sin
    `request.sucursal` en `apps/inventario/views.py:390` y
    `apps/inventario/views.py:641`; asi ignoran el scope por sucursal que soporta
    `apps/permisos/engine.py:122-133`.
- Escenario demostrado:
  - Se autentico un usuario `CAJERA` sin `compras.registrar` y se envio un POST
    valido a `inventario:compra_crear`. La respuesta fue HTTP 200 y quedaron
    creados la compra y su lote.
- Impacto:
  - Un usuario operativo puede inflar existencias y costos sin autorizacion.
  - Tambien puede ver proveedores, facturas, costos unitarios y totales de
    compra aunque no tenga `compras.ver`.
  - Puede disparar impresiones de etiquetas de compras arbitrarias.
- Sugerencia de arreglo:
  - Aplicar `requiere_permiso_local` a paginas y `requiere_permiso_json` a
    endpoints fetch: `compras.ver`, `compras.registrar`, `inventario.ver` e
    `inventario.ajustar` segun corresponda.
  - Resolver permisos contra `request.sucursal`, tanto en las vistas como en el
    filtro `puede` que muestra acciones locales.
  - Agregar pruebas negativas por cada URL; no limitar la cobertura a ocultar
    enlaces.

### INVENTARIO-002 - Crear compras acepta cantidades y costos no positivos

- Prioridad: P1.
- Severidad: alta.
- Tipo: validacion / corrupcion de stock y valuacion.
- Evidencia:
  - `apps/inventario/views.py:161-180` convierte `cantidad` y `costo_unitario`,
    pero nunca exige que sean mayores que cero ni reutiliza `_validar_linea`.
  - El producto se recupera solo por `id` en `apps/inventario/views.py:166-167`,
    por lo que tambien puede registrarse una compra de un producto inactivo.
  - Los validadores declarados en `DetalleCompra` y `Lote` en
    `apps/inventario/models.py:110-125` y `apps/inventario/models.py:219-233`
    no se ejecutan automaticamente al usar `objects.create()`/`save()`.
  - Los `Meta` de esos modelos no contienen `CheckConstraint` que funcione como
    ultima barrera en la base de datos.
- Escenario demostrado:
  - Se envio una compra con `cantidad=-3` y `costo_unitario=10.00`. El endpoint
    respondio HTTP 200 y persistio `Compra.total=-30.00`, un detalle de -3, un
    lote con `cantidad_inicial=-3`/`cantidad_actual=-3` y un movimiento de -3.
- Impacto:
  - Existencias, costo de compra y ledger pueden comenzar en valores
    imposibles.
  - Las fuentes de stock divergen: varios helpers excluyen lotes no positivos,
    mientras `Producto.stock_actual` suma cualquier lote activo.
  - Reportes, reposicion y snapshots pueden mostrar cifras distintas para el
    mismo producto.
- Sugerencia de arreglo:
  - Centralizar la validacion de linea y usarla en crear y editar.
  - Exigir producto activo, cantidad entera positiva y costo decimal positivo;
    definir tambien la politica para productos repetidos.
  - Agregar constraints de BD para cantidad inicial, cantidad de compra y costo.
  - No confiar en `MinValueValidator` como control de persistencia ORM.

### INVENTARIO-003 - Cada ajuste HTTP crea dos movimientos para un solo cambio de stock

- Prioridad: P1.
- Severidad: alta.
- Tipo: duplicacion de ledger / trazabilidad incorrecta.
- Evidencia:
  - `AjusteInventario.save()` actualiza el lote y crea un `MovimientoLote` en
    `apps/inventario/models.py:416-437`.
  - Despues de `AjusteInventario.objects.create()`, la vista crea un segundo
    movimiento en `apps/inventario/views.py:821-846`.
  - El propio comentario en `apps/inventario/views.py:831-835` reconoce que el
    ledger queda duplicado.
  - La vista vuelve a guardar el lote en `apps/inventario/views.py:848-850` con
    el valor calculado antes del `save()` del modelo. Por eso el stock suele
    cambiar una vez, aunque el historial diga que cambio dos.
- Escenario demostrado:
  - Sobre un lote de 10 se registro una merma de 2. El lote termino en 8, pero
    aparecieron dos movimientos con la misma referencia al ajuste y cantidad
    -2: uno `AJUSTE` y otro `MERMA`.
- Impacto:
  - El ledger local deja de representar la secuencia real del stock.
  - Cualquier auditoria, exportacion o calculo que sume movimientos cuenta dos
    salidas por una sola merma.
  - La clasificacion tambien diverge: el modelo registra siempre `AJUSTE`,
    mientras la vista intenta conservar `MERMA` o `DANO`.
- Sugerencia de arreglo:
  - Tener un unico servicio de ajuste que bloquee el lote, cambie el saldo y
    cree exactamente un movimiento con el tipo correcto.
  - Eliminar efectos secundarios duplicados del `save()` o del endpoint; no
    conservar dos autoridades.
  - Antes de limpiar datos historicos, identificar pares duplicados por
    `referencia_tipo`, `referencia_id`, lote y cantidad.

### INVENTARIO-004 - Editar o volver a guardar un ajuste reaplica toda su cantidad

- Prioridad: P1.
- Severidad: alta.
- Tipo: no idempotencia / corrupcion de stock.
- Evidencia:
  - `AjusteInventario.save()` no distingue insercion de actualizacion en
    `apps/inventario/models.py:416-437`; cada llamada suma nuevamente
    `self.cantidad` y crea otro movimiento.
  - `AjusteInventarioAdmin` permite editar lote, tipo, cantidad, motivo y
    usuario en `apps/inventario/admin.py:176-206`, por lo que el camino es
    alcanzable desde Admin.
  - El metodo tampoco envuelve ajuste, lote y movimiento en su propia
    transaccion. Fuera de una transaccion exterior puede persistir una parte y
    fallar despues.
- Escenario demostrado:
  - Se creo una merma de -2 sobre un lote de 10: saldo 8. Se cambio solamente el
    texto del motivo y se llamo `save()` otra vez: saldo 6 y un segundo
    movimiento de -2.
- Impacto:
  - Una correccion de texto, usuario o clasificacion altera fisicamente el
    inventario otra vez.
  - Si se modifica la cantidad, se aplica el nuevo valor completo en vez del
    delta respecto del valor anterior.
  - Un fallo al crear el movimiento puede dejar ajuste y lote sin ledger
    equivalente.
- Sugerencia de arreglo:
  - Tratar el ajuste aplicado como inmutable o implementar una correccion
    explicita que revierta/aplique deltas con auditoria.
  - Sacar la mutacion multi-modelo de `save()` y llevarla a un servicio atomico.
  - Hacer el Admin de ajustes de solo lectura despues de aplicar el hecho.

### INVENTARIO-005 - Dos ajustes concurrentes pueden aprobarse sobre el mismo saldo

- Prioridad: P1.
- Severidad: alta.
- Tipo: concurrencia / lost update / validacion fuera del lock.
- Evidencia:
  - El lote se lee sin lock en `apps/inventario/views.py:794`.
  - La suficiencia se valida antes de abrir la transaccion en
    `apps/inventario/views.py:801-806`.
  - Dentro del `atomic`, `cantidad_anterior` y `cantidad_nueva` se calculan sobre
    la misma instancia leida antes en `apps/inventario/views.py:817-820`.
  - `AjusteInventario.save()` tambien hace un read-modify-write de
    `self.lote.cantidad_actual` sin `select_for_update` en
    `apps/inventario/models.py:419-424`.
- Escenario demostrable:
  - Dos requests leen un lote con 10 y solicitan retirar 8. Ambos pasan la
    validacion. Cada uno registra -8, pero ambos pueden terminar escribiendo 2
    desde una copia vieja; el saldo final no representa los -16 del ledger.
  - Segun el orden de las escrituras, tambien puede restaurarse una actualizacion
    aplicada por otro flujo.
- Impacto:
  - El inventario y sus movimientos se desacoplan bajo operaciones simultaneas.
  - El chequeo de stock insuficiente no protege contra dos usuarios ajustando el
    mismo lote.
- Sugerencia de arreglo:
  - Abrir `transaction.atomic()` antes de leer y obtener el lote con
    `select_for_update()`.
  - Revalidar saldo despues del lock y persistir un unico ajuste/movimiento.
  - Agregar una prueba `TransactionTestCase` con dos conexiones reales.

### INVENTARIO-006 - Editar una compra puede restaurar stock consumido por una venta concurrente

- Prioridad: P1.
- Severidad: alta.
- Tipo: concurrencia / violacion de la invariante FIFO.
- Evidencia:
  - La compra se recupera antes del `atomic` y sin lock en
    `apps/inventario/views.py:394`.
  - Los detalles y lotes se cargan sin `select_for_update` en
    `apps/inventario/views.py:448-475`.
  - La decision de si el lote esta intacto se toma sobre esas instancias, pero
    el cambio estructural escribe `cantidad_actual=cantidad` en
    `apps/inventario/views.py:514-529`.
  - Incluso la correccion exclusiva de costo ejecuta `lote.save()` sin
    `update_fields` en `apps/inventario/views.py:493-500`, por lo que Django
    vuelve a escribir tambien una `cantidad_actual` que puede estar obsoleta.
  - En contraste, el consumo de venta ya bloquea lotes en
    `apps/inventario/fifo_logic.py:49-58` y
    `apps/inventario/fifo_logic.py:96-115`; el lock es unilateral porque la
    edicion de compra no participa.
- Escenario demostrable:
  - La correccion lee un lote intacto con 10. Una venta bloquea y consume 2,
    dejando 8. La correccion continua con su instancia de 10 y guarda el lote;
    puede devolverlo a 10, aunque el movimiento de venta -2 siga existiendo.
  - La misma ventana existe entre dos ediciones simultaneas de la compra.
- Impacto:
  - Se puede revender inventario ya entregado.
  - El saldo del lote deja de cuadrar con sus movimientos y con el costo FIFO de
    las ventas.
- Sugerencia de arreglo:
  - Bloquear compra, detalles y lotes antes del snapshot y de `_estado_linea_compra`.
  - Usar un orden de locks compatible con ventas/anulaciones y guardar solo
    campos concretos cuando el cambio es exclusivamente de costo.
  - Revalidar que el lote siga intacto inmediatamente antes de modificarlo o
    borrarlo.

### INVENTARIO-007 - Una compra tiene dos autoridades y se duplica en el ledger cloud

- Prioridad: P1.
- Severidad: alta.
- Tipo: contrato sync / duplicacion y divergencia persistente.
- Evidencia:
  - `_encolar_compra_y_movimientos` emite `COMPRA_REGISTRADA` y ademas un
    `INVENTARIO_MOVIMIENTO_REGISTRADO` por cada movimiento inicial en
    `apps/inventario/views.py:35-47`.
  - El receptor de `COMPRA_REGISTRADA` crea una fila de ledger por detalle con
    `movimiento_id_local=None` en `apps/api/views/sync.py:968-993`.
  - El evento de movimiento pasa por otro handler en
    `apps/api/views/sync.py:996-998` con el ID real del `MovimientoLote`.
  - `_registrar_movimiento_inventario_sync` hace `update_or_create` por ID cuando
    existe, pero usa una deduplicacion natural separada cuando el ID es nulo en
    `apps/api/views/sync.py:1095-1116`.
  - La restriccion unica de `apps/sync/models.py:393-398` tampoco une una fila
    con ID y otra con `NULL`.
- Escenario demostrado:
  - Se aplico al receptor una compra con una linea y luego su movimiento
    inicial, exactamente como los emite la vista. El cloud termino con dos
    `InventarioMovimientoSync` para la misma entrada de cuatro unidades.
  - Si luego se corrige cantidad/costo, la fila con ID puede actualizarse pero
    la fila natural existente no se actualiza. Si se elimina la linea, no hay
    evento tombstone y ambas filas permanecen.
- Impacto:
  - El ledger cloud duplica entradas de compra y puede quedar con dos versiones
    distintas del mismo hecho.
  - Una suma de movimientos infla compras y la trazabilidad cloud no converge
    despues de correcciones.
- Sugerencia de arreglo:
  - Elegir una autoridad: la compra transporta lineas o los movimientos
    transportan el ledger, pero no ambos como filas independientes.
  - Usar una identidad estable para cada linea/movimiento y definir update,
    correccion y tombstone.
  - Preparar una migracion de deduplicacion antes de agregar una restriccion que
    formalice la identidad elegida.

### INVENTARIO-008 - La numeracion por `count()` colisiona por concurrencia y por huecos

- Prioridad: P2.
- Severidad: media-alta.
- Tipo: concurrencia / disponibilidad de compras.
- Evidencia:
  - `Compra.save()` calcula `count() + 1` en
    `apps/inventario/models.py:80-87`.
  - `_crear_lote()` repite el patron en `apps/inventario/models.py:149-154`.
  - `numero_compra` y `numero_lote` son unicos en
    `apps/inventario/models.py:12-16` y `apps/inventario/models.py:208-212`.
  - La edicion soportada borra lotes en `apps/inventario/views.py:504-507`, por
    lo que puede crear huecos dentro de la secuencia diaria.
- Escenarios demostrables:
  - Dos compras o detalles concurrentes leen el mismo conteo y proponen el mismo
    numero; una transaccion falla por unicidad.
  - Se crearon lotes 00001 y 00002, se elimino 00001 y se intento crear otro. El
    conteo restante fue uno, se volvio a proponer 00002 y la insercion fallo por
    `IntegrityError`.
- Impacto:
  - Registrar o corregir una compra puede responder 500 aun con datos validos.
  - El segundo escenario no necesita concurrencia; basta una eliminacion de
    linea no final el mismo dia.
- Sugerencia de arreglo:
  - No derivar identidad desde el conteo de filas vivas. Usar una secuencia
    durable por fecha/sucursal, un correlativo bloqueado o un generador con
    retry acotado ante unicidad.
  - Incluir el codigo de sucursal si los numeros deben ser globalmente unicos al
    consolidar bases.

### INVENTARIO-009 - Volver a guardar un detalle intenta crear un segundo lote

- Prioridad: P2.
- Severidad: media-alta.
- Tipo: contrato de modelo / no idempotencia.
- Evidencia:
  - `DetalleCompra.save()` decide crear lote mediante el atributo transitorio
    `_lote_creado` en `apps/inventario/models.py:135-142`.
  - Ese atributo solo se agrega a la instancia en memoria despues de crear el
    lote en `apps/inventario/models.py:181` y se pierde al recargar desde BD.
  - `Lote.detalle_compra` es `OneToOneField` en
    `apps/inventario/models.py:200-206`, por lo que el segundo intento viola
    unicidad.
  - La vista de edicion conoce el problema y asigna manualmente el atributo
    privado en `apps/inventario/views.py:497` y
    `apps/inventario/views.py:521`.
- Escenario demostrado:
  - Se creo un detalle y su lote, se recargo el detalle desde BD y se llamo
    `save()` sin cambiarlo. El modelo intento crear otro lote y produjo
    `IntegrityError`.
- Impacto:
  - Cualquier caller nuevo, script, señal o formulario que no conozca el flag
    privado falla al actualizar el detalle.
  - Fuera de una transaccion exterior, una falla durante la creacion del lote o
    movimiento puede dejar el detalle persistido parcialmente.
- Sugerencia de arreglo:
  - No usar estado efimero como guarda de una invariante persistente.
  - Crear detalle+lote+movimiento en un servicio atomico, o como minimo consultar
    la relacion OneToOne/usar `get_or_create` con semantica explicita.
  - Mantener `save()` libre de creaciones laterales inesperadas.

### INVENTARIO-010 - Eliminar una linea borra el historial local de movimientos

- Prioridad: P2.
- Severidad: media-alta.
- Tipo: auditoria / trazabilidad destructiva.
- Evidencia:
  - `MovimientoLote` se documenta como historial completo en
    `apps/inventario/models.py:275-279`.
  - Su FK usa `on_delete=models.CASCADE` en
    `apps/inventario/models.py:289-294`.
  - La correccion de compra borra primero el lote en
    `apps/inventario/views.py:504-507`; el comentario confirma que la cascada
    elimina sus movimientos.
  - Despues se encolan solo la compra y los movimientos que aun existen en
    `apps/inventario/views.py:574-579`.
- Impacto:
  - Una entrada que existio deja de aparecer en el ledger local, aunque ya pudo
    haberse sincronizado, impreso o usado en una conciliacion.
  - `Auditoria` conserva un antes/despues de la compra, pero no reemplaza la
    secuencia inmutable de movimientos ni genera una reversa contable.
- Sugerencia de arreglo:
  - Tratar movimientos aplicados como inmutables.
  - Corregir una entrada con movimiento compensatorio/estado de anulacion y
    emitir el mismo hecho al cloud, en vez de borrar fisicamente el ledger.
  - Si se permite hard delete solo antes de publicar/aplicar, modelar y probar
    explicitamente esa frontera.

### INVENTARIO-011 - El formulario Admin de compras no puede construirse

- Prioridad: P2.
- Severidad: media.
- Tipo: bug de Django Admin.
- Evidencia:
  - `Compra.fecha_compra` usa `auto_now_add=True` y por tanto no es editable en
    `apps/inventario/models.py:23-26`.
  - `CompraAdmin.fieldsets` incluye `fecha_compra` en
    `apps/inventario/admin.py:23-26`, pero `readonly_fields` solo contiene
    `numero_compra` y `fecha_creacion` en `apps/inventario/admin.py:20`.
- Escenario demostrado:
  - `CompraAdmin.get_form(request, compra)` lanzo
    `FieldError: 'fecha_compra' cannot be specified ... as it is a non-editable
    field`.
- Impacto:
  - Abrir alta/cambio de compras desde Django Admin termina en error antes de
    mostrar el formulario.
  - El `manage.py check` general no detecta este fallo de construccion tardia.
- Sugerencia de arreglo:
  - Declarar `fecha_compra` de solo lectura o retirarla del fieldset.
  - Antes de reactivar edicion por Admin, decidir que campos pueden cambiar sin
    saltarse total, lote, auditoria y outbox.

### INVENTARIO-012 - El detalle Admin de lotes llama un metodo inexistente

- Prioridad: P2.
- Severidad: media.
- Tipo: bug de Django Admin.
- Evidencia:
  - `LoteAdmin` incluye `porcentaje_consumido` como campo de solo lectura en
    `apps/inventario/admin.py:61-68`.
  - El callback llama `obj.get_porcentaje_consumido()` en
    `apps/inventario/admin.py:108-110`.
  - `Lote` solo define `esta_agotado()` y `get_valor_actual()` en
    `apps/inventario/models.py:268-272`; no existe el metodo invocado.
- Escenario demostrado:
  - Ejecutar el callback para un lote real lanzo `AttributeError`.
- Impacto:
  - La pantalla de detalle/cambio de lote falla al evaluar el campo de solo
    lectura.
- Sugerencia de arreglo:
  - Implementar el calculo en un unico lugar o calcularlo directamente en el
    `ModelAdmin`, con manejo explicito de cantidad inicial cero.
  - Al hacer usable la pantalla, mantener cantidades, costo y estado protegidos
    contra cambios que no creen movimiento/auditoria/outbox.

### INVENTARIO-013 - El manejador de errores de ajustes tambien falla en Windows

- Prioridad: P2.
- Severidad: media.
- Tipo: manejo de errores / contrato JSON / compatibilidad operativa.
- Evidencia:
  - El `except Exception` de `api_ajustar_inventario` imprime un emoji con
    `print()` en `apps/inventario/views.py:891-894` antes de construir la
    respuesta JSON.
  - En la consola Windows usada por el proyecto, `stdout` puede operar con
    `cp1252`, que no puede codificar ese caracter.
- Escenario demostrado:
  - Se solicito ajustar un lote inexistente. El `Http404` entro al handler y el
    `print("ERROR ...")` lanzo `UnicodeEncodeError` en `encodings/cp1252.py`.
    El JSON previsto en `apps/inventario/views.py:895-897` nunca se devolvio.
- Impacto:
  - Un error de negocio o infraestructura se sustituye por otro error del
    logger improvisado.
  - El frontend puede recibir HTML 500 en vez del contrato JSON y el traceback
    principal queda enmascarado.
- Sugerencia de arreglo:
  - Usar `logger.exception()` con texto portable y respuesta JSON estable.
  - Manejar `Http404`, validacion y errores inesperados por separado; no imprimir
    tracebacks directamente a stdout.

### INVENTARIO-014 - Recursos inexistentes se convierten en errores de servidor

- Prioridad: P3.
- Severidad: baja-media.
- Tipo: contrato HTTP / observabilidad.
- Evidencia:
  - `api_lotes_producto` llama `get_object_or_404` dentro de un `try` general en
    `apps/inventario/views.py:697-703` y captura el `Http404` como cualquier
    excepcion en `apps/inventario/views.py:728-732`, devolviendo 500.
  - `compra_imprimir_etiquetas` repite el patron en
    `apps/inventario/views.py:610-630` y devuelve 400 para una compra ausente.
  - El ajuste de lote inexistente tambien entra al `except Exception` descrito
    en INVENTARIO-013.
- Escenario demostrado:
  - Consultar lotes de un producto inexistente produjo HTTP 500, no 404.
- Impacto:
  - Errores normales de recurso ausente parecen incidentes de servidor y
    contaminan monitoreo/logs.
  - Los clientes no pueden distinguir identificador invalido de fallo interno.
- Sugerencia de arreglo:
  - Dejar `Http404` fuera del `try` amplio o capturarlo explicitamente y devolver
    404 con el contrato JSON esperado.
  - Reservar 500 para errores realmente inesperados y no exponer `str(e)` como
    detalle interno.

## Supuestos y riesgos que no se elevaron como hallazgo actual

- La arquitectura documentada asigna una BD local a cada sucursal: cada POS es
  dueno de su ledger y el cloud agrega snapshots. Bajo ese supuesto, no se marco
  como bug actual que `obtener_stock_disponible`, `obtener_lotes_fifo`,
  valuacion y reportes locales consulten lotes sin filtro de sucursal.
- Si una misma BD local llega a contener lotes de varias sucursales, esas
  consultas, `compras_lista`, los endpoints de lotes/ajustes y el serializer de
  snapshot mezclaran existencias y las etiquetaran con una sola sucursal. Antes
  de soportar una BD POS compartida, todo ese scope debe revisarse y probarse.
- El costo O(N) del snapshot completo ya esta reconocido en el diseño de sync y
  no se duplico como hallazgo de correccion.
- Los defectos de numeracion, FIFO y anulacion ya documentados en la auditoria de
  ventas no se repiten aqui cuando la correccion activa los cubre. INVENTARIO-006
  si es nuevo porque el editor de compras no participa en los locks de ventas.
- Facturacion electronica y suscripciones quedaron fuera por instruccion de
  prioridad de negocio, no porque se consideren auditadas o libres de riesgo.

## Cobertura y pruebas ejecutadas

Comando de pruebas existentes, sin modificar codigo ni datos productivos:

```powershell
C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py test `
  apps.inventario.tests `
  apps.sync.tests.test_extended_serializers `
  apps.api.tests.test_sync_extended `
  apps.permisos.tests.test_cutover_local `
  apps.ventas.tests.test_concurrencia `
  apps.ventas.tests.test_anulaciones `
  --keepdb --settings=config.settings_development
```

Resultado:

- 39 pruebas existentes: OK.
- El system check ejecutado por el runner no encontro issues.
- Se reutilizo y conservo la base de pruebas con `--keepdb`.
- No se corrio la suite completa del repositorio.

Ademas se ejecutaron dos suites efimeras desde stdin contra la base de test; no
se agregaron archivos de prueba al repositorio:

- 8 reproducciones de negocio: OK al afirmar el comportamiento defectuoso.
  Confirmaron creacion por cajera, compra negativa, doble movimiento de ajuste,
  reaplicacion al guardar, guardado no idempotente de detalle, colision por hueco
  de lote, duplicacion cloud y callback faltante del Admin.
- 4 reproducciones de frontera: OK al afirmar `FieldError` de CompraAdmin,
  `AttributeError` de LoteAdmin y los HTTP 500 para producto/lote inexistentes.
- La reproduccion del ajuste inexistente mostro ademas el
  `UnicodeEncodeError` de `cp1252` descrito en INVENTARIO-013.

Las pruebas propias de inventario cubren bien la regla funcional de editar una
linea intacta frente a una consumida, incluida la correccion exclusiva de costo.
No cubren:

- gates de listar, ver, crear, buscar e imprimir compras;
- cantidades/costos cero o negativos y productos inactivos;
- crear compras concurrentes o secuencias con huecos;
- dos ajustes simultaneos;
- actualizar un `AjusteInventario` ya aplicado;
- un ajuste y una venta concurrentes sobre el mismo lote;
- una venta concurrente con correccion de compra;
- guardar un `DetalleCompra` recargado;
- formularios reales de Django Admin;
- eliminacion/correccion end-to-end hasta el ledger cloud;
- equivalencia exacta entre saldo del lote y secuencia de movimientos.

## Tests recomendados antes de tocar codigo

- Crear `apps/inventario/tests/test_permisos.py` y parametrizar todas las URLs
  para usuario sin permiso, permiso global, permiso de la sucursal actual y
  permiso asignado solo a otra sucursal.
- Crear `test_compra_crear.py` para limites de cantidad/costo, producto inactivo,
  JSON invalido, duplicados y rollback total.
- Crear `test_ajustes.py` para exactamente un movimiento, tipo correcto,
  inmutabilidad, rollback y contrato JSON de errores.
- Crear `TransactionTestCase` con dos conexiones para ajuste-ajuste,
  ajuste-venta y compra-editar contra venta.
- Cubrir numeracion concurrente y huecos no finales tanto para compra como lote.
- Probar el contrato persistente de `DetalleCompra.save()` al crear, recargar y
  actualizar.
- Probar Admin con `get_form` y requests reales a add/change, y asegurar que no
  exista una ruta que cambie stock sin ledger, auditoria y outbox.
- Agregar una prueba end-to-end que emita los eventos reales de una compra,
  procese ambos en cloud y exija una sola fila por movimiento; repetir tras
  editar y eliminar una linea.
- Agregar una conciliacion de invariante por lote: saldo inicial mas suma de
  movimientos debe igualar `cantidad_actual`, con excepciones historicas
  explicitamente catalogadas.

## Orden sugerido de correccion

1. Unificar ajustes en un servicio atomico con lock y un solo movimiento;
   cerrar INVENTARIO-003, 004, 005 y 013 juntos, con migracion/conciliacion de
   duplicados historicos.
2. Aplicar RBAC server-side y validacion positiva en compras antes de exponer
   mas operaciones a roles configurables.
3. Hacer que la edicion de compra use los mismos locks e invariantes que ventas
   y anulaciones; probar concurrencia real.
4. Elegir una sola identidad/autoridad para el ledger cloud de compras y definir
   correcciones/tombstones antes de limpiar duplicados.
5. Reemplazar numeracion por conteo y extraer la creacion atomica
   detalle+lote+movimiento del `save()`.
6. Sustituir el borrado destructivo por una correccion auditable y sincronizable.
7. Reparar y endurecer Django Admin, y normalizar contratos 404/JSON.

## Conclusion

`apps/inventario` modelaba correctamente las entidades centrales, pero no
garantizaba que cada operacion autorizada produjera exactamente un cambio de
stock, exactamente un movimiento y exactamente una representacion cloud. El
mismo lote se mutaba desde compras, ajustes, ventas, anulaciones, modelos y
Admin con reglas de locking, auditoria y sync distintas.

Esa frontera ya esta unificada para ajustes y compras (ver abajo).

---

# Estado de mitigacion

Fecha: 2026-08-20. Verificacion previa: se releyo cada hallazgo contra el codigo
citado. **Los 14 son reales** — ninguno resulto falso positivo ni obsoleto.

## Resumen por hallazgo

| ID | Real | Estado | Donde quedo la correccion |
|---|---|---|---|
| INVENTARIO-001 | Si | Corregido | `requiere_permiso_local` / `requiere_permiso_json` en las 8 vistas: `compras.ver`, `compras.registrar`, `inventario.ver`, `inventario.ajustar`. Se eliminaron los chequeos comentados. |
| INVENTARIO-002 | Si | Corregido | `compra_crear` reusa `_validar_linea` (la misma que editar) y exige producto ACTIVO. Los errores de validacion son 400, no 500. |
| INVENTARIO-003 | Si | Corregido | `apps/inventario/services/ajustes_service.py`: UN movimiento, con el tipo real del ajuste. La creacion manual del endpoint desaparecio. |
| INVENTARIO-004 | Si | Corregido | `AjusteInventario.save()` ya no mueve inventario (es un registro, no un aplicador) y el Admin de ajustes es inmutable. |
| INVENTARIO-005 | Si | Corregido | El service abre el atomic, toma el lote con `select_for_update()` y revalida la suficiencia BAJO el lock. |
| INVENTARIO-006 | Si | Corregido | `compra_editar` bloquea compra y lotes (en orden de id, compatible con FIFO) antes del snapshot. La correccion de solo-costo usa `update_fields`. |
| INVENTARIO-007 | Si | Corregido | `_handler_compra` dejo de escribir ledger: la autoridad son los eventos de movimiento, que traen `movimiento_id_local`. Migracion `sync.0009` colapsa los duplicados historicos. |
| INVENTARIO-008 | Si | Corregido | `_siguiente_correlativo` usa el MAXIMO sufijo, no `count()`, y `_guardar_con_correlativo` reintenta en savepoint ante colision. Aplica a `Compra` y a `Lote`. |
| INVENTARIO-009 | Si | Corregido | La guarda de creacion del lote es el estado persistente (`Lote.objects.filter(detalle_compra=self).exists()`), no el atributo efimero `_lote_creado`. |
| INVENTARIO-010 | Si | Corregido | `_anular_lote_por_correccion`: movimiento compensatorio + lote en cero e inactivo, en vez de `lote.delete()` con CASCADE sobre el ledger. `Lote.detalle_compra` pasa a `SET_NULL` (migracion `inventario.0006`). |
| INVENTARIO-011 | Si | Corregido | `fecha_compra` declarada readonly (es `auto_now_add`). Ademas `CompraAdmin` queda sin add/change/delete: crear una compra desde el Admin generaba stock sin outbox. |
| INVENTARIO-012 | Si | Corregido | El porcentaje consumido se calcula en el `ModelAdmin`, con el caso `cantidad_inicial = 0` contemplado. |
| INVENTARIO-013 | Si | Corregido | `logger.exception` en vez del `print` con emoji que reventaba con `UnicodeEncodeError` en la consola cp1252. Errores tipados por el service. |
| INVENTARIO-014 | Si | Corregido | `get_object_or_404` fuera del `try` amplio; producto, lote y compra inexistentes devuelven 404 con contrato JSON. Ya no se expone `str(e)`. |

## Hallazgo adicional encontrado al corregir

- **`CompraAdmin` permitia crear compras sin outbox.** No estaba como hallazgo
  propio, pero es de la misma familia que INVENTARIO-011: alta/edicion desde el
  Admin generaba `Lote` y `MovimientoLote` (via `DetalleCompra.save()`) y
  NINGUN evento de sync. El stock existia local y el cloud no se enteraba. Se
  cerro junto con el FieldError.

## Cambios de conducta observables

1. **RBAC en inventario.** Sin `compras.ver` no se lista ni se ve una compra;
   sin `compras.registrar` no se crea ni se edita; sin `inventario.ver` no se
   consultan lotes; sin `inventario.ajustar` no se ajusta. El rol Cajero por
   defecto NO trae estos permisos: verificar que quien registra compras en cada
   cliente tenga su rol con `compras.registrar` antes de desplegar.
2. **Un ajuste deja un solo movimiento.** Los reportes que sumaban movimientos
   contaban DOS salidas por cada merma; sus cifras historicas van a diferir de
   las nuevas. Los duplicados historicos NO se limpian automaticamente (ver
   pendientes).
3. **Eliminar una linea de compra ya no borra el lote.** Queda inactivo, en
   cero, desvinculado de la compra y con su historial completo. Consultas que
   asuman `lote.detalle_compra` no nulo deben tolerar el null.
4. **Compras con cantidad o costo no positivos se rechazan** con 400. Si algun
   flujo dependia de cargar una nota de credito como cantidad negativa, ahora
   falla: eso se modela como ajuste, no como compra.
5. **Django Admin de compras y ajustes es de solo lectura.**

## Despliegue: 2 migraciones

1. **`inventario.0006_alter_lote_detalle_compra`** — hace nullable la FK. Sin
   datos que transformar.
2. **`sync.0009_dedup_ledger_compras`** — elimina del ledger cloud las filas de
   compra con `movimiento_id_local = NULL` **solo cuando existe su gemela con
   ID**. Conservadora a proposito: una linea que solo tiene la fila sin ID se
   CONSERVA y se reporta por WARNING, porque borrarla perderia el unico
   registro de esa entrada. Revisar ese log al promover.

## Pendiente (no bloqueante)

- **Conciliacion de los movimientos duplicados historicos.** El doble
  movimiento por ajuste dejo pares en el ledger LOCAL de cada sucursal. No se
  limpian en esta pasada: identificarlos exige decidir cual de los dos es el
  bueno (el modelo escribia `AJUSTE`, la vista `MERMA`/`DANO`), y el criterio
  afecta reportes historicos. Se detectan por `referencia_tipo='AjusteInventario'`
  + mismo `referencia_id`, lote y cantidad.
- **`CheckConstraint` de respaldo.** Las cantidades y costos se validan en la
  vista. Respaldarlos en BD (cantidad > 0, costo > 0, `cantidad_inicial` > 0)
  sigue siendo deseable para escrituras que no pasen por ahi.
- **Scope por sucursal en los gates.** `tiene_permiso` se llama sin
  `request.sucursal`, asi que un permiso acotado a otra sucursal igual habilita.
  Con una BD local por sucursal el riesgo es teorico; hay que cerrarlo antes de
  una BD POS compartida, junto con el resto del scope que la auditoria ya
  documenta en "Supuestos".
- **Compras no participan del snapshot por sucursal** en consultas de stock.
  Sigue valiendo el supuesto de una BD por sucursal descrito en la auditoria.

## Pruebas

Suite completa, serial: **529 tests, OK.**

Modulos de regresion nuevos:

| Archivo | Cubre |
|---|---|
| `apps/inventario/tests/test_auditoria_inventario.py` (28 tests) | INVENTARIO-001, 002, 003, 004, 008, 009, 011, 012, 013, 014 |
| `apps/inventario/tests/test_concurrencia_inventario.py` (4 tests) | INVENTARIO-005 y 006 con hilos reales (`TransactionTestCase`) |
| `apps/api/tests/test_sync_auditoria.py` (+2 tests) | INVENTARIO-007 |

Ademas se actualizo `test_compra_editar.test_eliminar_linea_intacta_borra_lote`,
que afirmaba la conducta destructiva. Ahora se llama
`test_eliminar_linea_intacta_anula_el_lote_sin_borrar_su_historial` y verifica
que la secuencia de movimientos cuadre en cero.

**Verificacion por mutacion.** Quitando el `select_for_update()` del service de
ajustes, los tres tests de `AjustesConcurrentesTests` fallan:

```
test_dos_ajustes_simultaneos_no_sobregiran_el_lote
    AssertionError: 2 != 1  -> los dos retiros de 8 pasan sobre un lote de 10
test_el_lock_es_lo_que_sostiene_la_invariante
    AssertionError: ledger=4 vs lote=7
```

Ese `ledger=4 vs lote=7` es exactamente el lost update descrito en
INVENTARIO-005: el historial dice que quedan 4 y el lote afirma 7.

La invariante `suma(movimientos) == lote.cantidad_actual` se verifica en los
tests de ajustes, de concurrencia y de eliminacion de linea. Es la conciliacion
por lote que la auditoria recomendaba.
