# Auditoria de codigo - apps/ventas

Fecha: 2026-08-20
Scope principal: `apps/ventas`
Scope de verificacion: consumidores directos en `apps/inventario`, `apps/sync`,
`apps/api`, `apps/cuentas_por_cobrar`, `apps/facturacion_electronica`,
`apps/cotizaciones`, `apps/permisos`, templates y JavaScript del POS.
Modo: lectura, ejecucion de checks/pruebas existentes y documentacion de
hallazgos; no se aplicaron cambios funcionales.

> **Estado (2026-08-20, misma fecha): MITIGADO.** Los 14 hallazgos se
> verificaron contra el codigo y los 14 resultaron reales. Todos estan
> corregidos, con pruebas de regresion. Ver
> [Estado de mitigacion](#estado-de-mitigacion) al final del documento, que
> tambien lista los cambios de conducta observables y lo que queda pendiente.

## Por que esta app sigue en la auditoria

Despues de `apps/api`, `apps/ventas` es la siguiente app de mayor criticidad
operativa porque origina la transaccion que conecta:

- cobro y cierre de caja;
- consumo FIFO y valuacion de inventario;
- cuentas por cobrar;
- e-CF y notas de credito;
- sincronizacion sucursal-cloud;
- reportes y trazabilidad de auditoria.

Ademas, contiene aproximadamente 2,278 lineas productivas y solo 247 lineas de
pruebas propias. Las pruebas de venta completas que existen hoy viven
principalmente bajo CxC y cubren el camino de credito; la venta de contado y
varios bordes de integridad no tienen cobertura directa.

## Resumen

La separacion reciente entre views y services es una buena base: la creacion y
anulacion normales se ejecutan dentro de `transaction.atomic()`, los eventos
principales usan outbox y los efectos secundarios se difieren con
`transaction.on_commit()`. Sin embargo, la transaccion no esta cerrada en todos
sus bordes.

Los riesgos mas importantes son: ventas locales sin sucursal, consumo FIFO no
serializado frente a concurrencia, mutaciones desde Django Admin que evitan el
service, autorizacion RBAC no aplicada en el endpoint, valores financieros
confiados al cliente y anulaciones no idempotentes. Estos defectos pueden
producir perdida silenciosa en cloud, inventario/caja inconsistentes o ventas
fiscalmente no emitibles.

## Hallazgos priorizados

### VENTAS-001 - La venta local se crea sin sucursal y pierde su identidad multi-sucursal

- Prioridad: P1.
- Severidad: alta.
- Tipo: bug / integridad multi-sucursal / perdida de datos en sync.
- Evidencia:
  - `apps/ventas/services/ventas_service.py:357-365` crea `Venta` sin asignar
    `sucursal`.
  - `apps/ventas/models.py:149-158` solo usa el prefijo de sucursal cuando
    `self.sucursal` ya esta asignada; en caso contrario genera el numero legacy
    `V-<fecha>-<secuencia>`.
  - `apps/sync/serializers.py:64-84` serializa
    `sucursal_codigo=None` para esa venta.
  - `apps/cuentas_por_cobrar/services.py:263-279` copia
    `sucursal=venta.sucursal`, por lo que una CxC originada por el POS tambien
    queda sin sucursal local.
  - `apps/api/views/sync.py:605-623` deduplica globalmente por
    `numero_venta`; no incluye la sucursal en la identidad.
  - `docs/ROADMAP_CLOUD.md:358-360` da por implementados tanto
    `Venta.sucursal` como el prefijo de sucursal.
- Escenario demostrable:
  - Dos instalaciones locales pueden generar `V-20260820-0001`. Cuando ambas
    sincronizan, el cloud acepta la primera y trata la segunda como reenvio de
    la misma venta, omitiendo sus detalles y pagos.
- Impacto:
  - Una venta valida puede no llegar nunca al cloud y los reportes consolidados
    pueden quedar incompletos sin un error visible en la sucursal.
- Sugerencia de arreglo:
  - Resolver `get_sucursal_actual()` antes de crear la cabecera, asignarla a la
    venta y fallar de forma explicita si una instalacion configurada no puede
    resolverla.
  - Agregar una garantia de identidad cloud que incluya sucursal, aunque se
    conserve `numero_venta` como identificador visible.

### VENTAS-002 - La numeracion basada en `count()` colisiona bajo concurrencia

- Prioridad: P2.
- Severidad: media-alta.
- Tipo: bug de concurrencia / disponibilidad.
- Evidencia:
  - `apps/ventas/models.py:155-158` cuenta ventas con el prefijo y construye el
    proximo numero antes del `INSERT`.
  - `apps/ventas/models.py:23-26` declara `numero_venta` como unico, pero no hay
    lock, secuencia de BD ni reintento ante `IntegrityError`.
- Escenario demostrable:
  - Dos cajeros/procesos cierran ventas del mismo prefijo simultaneamente; ambos
    leen el mismo conteo y calculan el mismo numero. Una de las transacciones
    falla al insertar.
- Impacto:
  - El POS puede responder 500 despues de que el cajero ya confirmo el pago.
  - Un borrado o correccion excepcional que deje huecos tambien puede hacer que
    `count() + 1` reutilice un numero existente.
- Sugerencia de arreglo:
  - Usar un contador transaccional por sucursal/fecha bloqueado con
    `select_for_update()`, una secuencia apropiada o un mecanismo de reintento
    acotado que no dependa de contar filas.

### VENTAS-003 - La validacion y el consumo FIFO no forman una operacion serializable

- Prioridad: P1.
- Severidad: alta.
- Tipo: bug de concurrencia / integridad de inventario.
- Evidencia:
  - `apps/ventas/services/ventas_service.py:288-305` valida stock por cada linea
    con un agregado sin lock y sin consolidar IDs repetidos del carrito.
  - `apps/inventario/fifo_logic.py:43-48` selecciona lotes sin
    `select_for_update()`.
  - `apps/inventario/fifo_logic.py:99-103` aplica un read-modify-write ordinario
    sobre `cantidad_actual`.
  - `apps/ventas/services/ventas_service.py:399-416` solo registra warnings si
    FIFO entrega `cantidad_faltante`; la venta continua y hace commit.
- Escenarios demostrables:
  - Dos ventas concurrentes leen las mismas existencias y consumen el mismo
    lote. Segun el orden de los `UPDATE`, puede ocurrir sobreventa, lost update o
    movimientos cuya suma no coincide con `Lote.cantidad_actual`.
  - Un payload con el mismo producto en dos lineas puede hacer que ambas pasen
    la validacion individual; la segunda queda parcialmente sin FIFO y aun asi
    la venta se completa cuando el inventario negativo esta deshabilitado.
- Impacto:
  - Inventario fisico, movimientos, costo y unidades vendidas dejan de cuadrar.
- Sugerencia de arreglo:
  - Normalizar/agrupar el carrito por producto, bloquear los lotes FIFO dentro
    de la misma transaccion y exigir consumo completo cuando no se permite
    inventario negativo.
  - Tratar `cantidad_faltante` como error de negocio, no solo como warning.

### VENTAS-004 - Django Admin permite mutar ventas saltandose toda la logica transaccional

- Prioridad: P1.
- Severidad: alta.
- Tipo: bug / integridad financiera / bypass de auditoria.
- Evidencia:
  - `apps/ventas/admin.py:62-77` deja `estado`, `usuario`, `notas` y
    `motivo_anulacion` editables en la cabecera.
  - `apps/ventas/admin.py:6-19` deja editables producto, cantidad, precio y
    descuento en el inline de detalles; tampoco deshabilita borrar lineas.
  - `apps/ventas/admin.py:29-34` deja editables y eliminables los pagos en el
    inline, aunque el admin individual de `Pago` los marca read-only en
    `apps/ventas/admin.py:196-215`.
  - Esas escrituras no pasan por `procesar_venta_service()` ni
    `anular_venta_service()`.
- Escenarios demostrables:
  - Cambiar `estado` a `ANULADA` desde admin no devuelve FIFO, no revierte CxC,
    no audita, no sincroniza y no encola la nota de credito.
  - Editar una cantidad/precio/pago cambia parte de la venta, pero no recompone
    cabecera, movimientos FIFO, e-CF ya emitido ni eventos cloud.
- Impacto:
  - Se pueden crear inconsistencias financieras y fiscales desde una interfaz
    oficial del sistema, incluso por accidente.
- Sugerencia de arreglo:
  - Hacer inmutables cabecera, detalles y pagos despues de creados.
  - Exponer anulacion/reversa como acciones explicitas que invoquen los services
    y mantengan las mismas garantias que el POS.

### VENTAS-005 - El endpoint de venta no aplica RBAC y confia precio/descuento al navegador

- Prioridad: P1.
- Severidad: alta.
- Tipo: seguridad / autorizacion / integridad financiera.
- Evidencia:
  - `apps/ventas/views.py:383-385` protege `procesar_venta` solo con
    `@login_required`.
  - `apps/ventas/services/ventas_service.py:19-24` declara expresamente que el
    service no valida rol.
  - `apps/permisos/catalogo.py:51-55` define permisos separados para
    `ventas.crear` y `ventas.aplicar_descuento`.
  - `apps/usuarios/models.py:74-77` documenta que el enforcement real debe vivir
    en el motor RBAC, no en el campo legacy `rol`.
  - `apps/ventas/services/ventas_service.py:328-333` calcula la cabecera desde
    `precio_venta` y `descuento` recibidos del cliente, y
    `apps/ventas/services/ventas_service.py:388-394` persiste esos mismos valores
    sin contrastarlos con el producto ni con un permiso de descuento.
- Escenario demostrable:
  - Cualquier usuario autenticado, incluso sin rol asignado que incluya
    `ventas.crear`, puede llamar el endpoint directamente. Tambien puede alterar
    el JSON para vender a un precio arbitrario o aplicar descuento sin
    `ventas.aplicar_descuento`.
- Impacto:
  - El catalogo RBAC comunica controles que el servidor no cumple y permite
    manipular importes de venta fuera de la UI.
- Sugerencia de arreglo:
  - Exigir `ventas.crear` server-side y `ventas.aplicar_descuento` cuando exista
    descuento.
  - Resolver el precio desde BD o exigir un origen autorizado y auditable para
    precios historicos de cotizacion/overrides.

### VENTAS-006 - El payload acepta cantidades, precios, descuentos y totales no positivos

- Prioridad: P1.
- Severidad: alta.
- Tipo: bug / validacion de entrada / integridad financiera.
- Evidencia:
  - `apps/ventas/services/ventas_service.py:124-135` convierte importes, pero no
    valida la forma completa ni rangos de cada item.
  - `apps/ventas/services/ventas_service.py:288-305` permite que una cantidad
    cero o negativa pase la comparacion de stock.
  - `apps/ventas/services/ventas_service.py:328-339` acepta precio no positivo o
    descuento mayor al subtotal siempre que el cliente envie el mismo total
    calculado.
  - `apps/ventas/models.py:75-79`, `205-213` y `310-314` usan validators Django,
    pero `objects.create()`/`save()` no ejecutan `full_clean()` y las migraciones
    no agregan `CheckConstraint` equivalentes.
- Escenario demostrable:
  - Una linea con cantidad negativa y total negativo puede superar la validacion
    de stock, crear cabecera/detalle/pago negativos y no generar movimiento FIFO.
- Impacto:
  - Ventas, caja, impuestos y reportes pueden incorporar montos imposibles.
- Sugerencia de arreglo:
  - Validar el payload con un schema/serializer: IDs enteros, cantidad positiva,
    precio positivo, descuento entre cero y subtotal, total positivo y precision
    monetaria acotada.
  - Respaldar invariantes criticas con constraints de BD donde sea posible.

### VENTAS-007 - Un metodo de pago desconocido o deshabilitado crea una venta sin pagos

- Prioridad: P1.
- Severidad: alta.
- Tipo: bug / integridad de caja.
- Evidencia:
  - `apps/ventas/services/ventas_service.py:125-127` acepta cualquier string en
    `metodo_pago`.
  - `apps/ventas/views.py:72-91` limita metodos solo en la interfaz segun
    configuracion; el service no repite ese control.
  - `apps/ventas/services/ventas_service.py:437-539` tiene ramas para los cinco
    metodos conocidos, pero no contiene un `else` que rechace otros valores.
- Escenario demostrable:
  - Enviar `metodo_pago="otro"` crea una venta `CONTADO`, sus detalles, FIFO,
    auditoria y evento de sync, pero no crea ningun `Pago`; el endpoint devuelve
    exito.
- Impacto:
  - La venta aumenta ingresos mientras el cierre de caja no registra forma de
    cobro ni monto recibido.
- Sugerencia de arreglo:
  - Validar contra un allowlist y contra los metodos habilitados en
    `ConfiguracionNegocio` antes de tocar inventario.
  - Como postcondicion, comprobar que pagos de contado sumen exactamente el
    total y que el desglose de credito cuadre con inicial/capital.

### VENTAS-008 - La anulacion concurrente no es idempotente

- Prioridad: P1.
- Severidad: alta.
- Tipo: bug de concurrencia / doble reversa.
- Evidencia:
  - `apps/ventas/services/anulaciones_service.py:109-127` lee la venta dentro de
    un atomic, pero no usa `select_for_update()` ni una transicion condicional de
    estado.
  - `apps/inventario/fifo_logic.py:146-190` vuelve a procesar todos los
    movimientos `VENTA` cada vez; no bloquea venta/lotes ni verifica una anulacion
    previa.
- Escenario demostrable:
  - Dos requests de anulacion pueden leer `COMPLETADA` antes de que alguno haga
    commit. Ambos generan movimientos de anulacion, eventos, auditoria y hooks;
    dependiendo del interleaving tambien pueden devolver stock dos veces.
- Impacto:
  - Stock inflado, CxC/NC duplicadas o trazabilidad contradictoria.
- Sugerencia de arreglo:
  - Bloquear la venta al iniciar, verificar el estado bajo lock y hacer la
    reversa idempotente con una marca/constraint que impida una segunda
    aplicacion.

### VENTAS-009 - Una venta permitida sin stock puede quedar imposible de anular

- Prioridad: P2.
- Severidad: media-alta.
- Tipo: bug de flujo / inventario negativo / anulacion.
- Evidencia:
  - `apps/ventas/services/ventas_service.py:285-286` omite toda validacion cuando
    se permite inventario negativo.
  - `apps/inventario/fifo_logic.py:86-130` retorna `success=True` aunque no haya
    lotes y toda la cantidad quede faltante.
  - `apps/ventas/services/ventas_service.py:410-416` solo deja warning y completa
    la venta.
  - `apps/inventario/fifo_logic.py:146-157` considera error que no existan
    movimientos de venta.
  - `apps/ventas/services/anulaciones_service.py:193-201` convierte ese caso en
    `FIFORollbackError`, provocando rollback de toda la anulacion.
- Escenario demostrable:
  - Con inventario negativo habilitado, vender un producto sin ningun lote crea
    la venta sin movimientos FIFO. Luego no se puede cambiar a `ANULADA`, revertir
    CxC ni encaminar la correccion fiscal mediante el service.
- Sugerencia de arreglo:
  - Modelar explicitamente la salida negativa o permitir una reversa de cero
    movimientos cuando la venta nunca consumio lotes, sin omitir las demas
    consecuencias de la anulacion.

### VENTAS-010 - La conversion de cotizacion ocurre despues de la venta y fuera de su transaccion

- Prioridad: P2.
- Severidad: media-alta.
- Tipo: bug transaccional / doble venta.
- Evidencia:
  - `static/js/pos/punto_venta.js:918-937` envia `cotizacion_id` junto con la
    venta, pero `procesar_venta_service()` no lo consume.
  - `static/js/pos/punto_venta.js:977-986` hace un segundo request para marcar la
    cotizacion; si falla, solo escribe un warning en consola.
  - `apps/cotizaciones/views.py:233-259` valida y vincula la cotizacion en otra
    operacion independiente, sin lock compartido con la venta.
- Escenario demostrable:
  - La venta hace commit y se cae la red antes del segundo request: la cotizacion
    sigue `PENDIENTE` y puede convertirse de nuevo.
  - Dos cajeros cargan la misma cotizacion pendiente y ambos venden; una queda
    vinculada y la otra se vuelve una venta sin origen, aunque ambas descontaron
    inventario.
- Impacto:
  - Ventas duplicadas e inventario consumido dos veces por una sola cotizacion.
- Sugerencia de arreglo:
  - Resolver, bloquear, validar y marcar la cotizacion dentro del atomic de
    `procesar_venta_service()`, vinculandola a la venta creada en la misma
    transaccion.

### VENTAS-011 - El e-CF tipo 31 se valida solo en JavaScript y puede fallar despues del commit

- Prioridad: P1.
- Severidad: alta si el modulo e-CF esta activo.
- Tipo: bug fiscal / validacion en capa incorrecta.
- Evidencia:
  - `static/js/pos/punto_venta.js:908-915` exige cliente con RNC para tipo 31 en
    la UI.
  - `apps/ventas/services/ventas_service.py:141-145` solo verifica que el tipo
    sea `31` o `32`; no valida comprador/RNC.
  - `apps/ventas/services/ventas_service.py:260-263` encola el e-CF despues del
    commit.
  - `apps/facturacion_electronica/services/cola_emision.py:110-135` crea el ECF
    pendiente sin construir aun el documento fiscal.
  - `apps/facturacion_electronica/services/venta_to_ecf.py:344-355` rechaza mas
    tarde el tipo 31 sin cliente/RNC valido.
- Escenario demostrable:
  - Un cliente HTTP directo o una UI desactualizada envia tipo 31 sin comprador
    fiscal. La venta, FIFO, pagos y respuesta de exito quedan confirmados; el
    documento fiscal falla de manera asincrona.
- Impacto:
  - Una venta que el cajero considera completada queda sin e-CF emitible y
    requiere intervencion posterior.
- Sugerencia de arreglo:
  - Ejecutar antes de la transaccion todas las precondiciones deterministas del
    tipo fiscal solicitado, reutilizando una validacion de dominio compartida
    con el mapper.

### VENTAS-012 - El costo FIFO calculado nunca se persiste en `DetalleVenta`

- Prioridad: P2.
- Severidad: media.
- Tipo: bug / costo y margen.
- Evidencia:
  - `apps/ventas/services/ventas_service.py:388-394` crea el detalle con el
    default `costo_fifo=0`.
  - `apps/inventario/fifo_logic.py:91-130` calcula y retorna
    `costo_total_fifo`.
  - `apps/ventas/services/ventas_service.py:399-416` no conserva el detalle ni
    copia `resultado['costo_fifo']` al registro.
  - `apps/ventas/models.py:273-283` calcula margen desde ese campo.
  - `apps/sync/serializers.py:88-100` propaga el mismo costo a cloud, por lo que
    el cero se replica.
- Impacto:
  - Los margenes mostrados por admin son cero/incorrectos y la venta pierde el
    snapshot de costo que deberia sobrevivir a futuras correcciones del lote.
- Sugerencia de arreglo:
  - Conservar la instancia de detalle y actualizar su `costo_fifo` con el costo
    total efectivamente consumido antes de cerrar la transaccion.
  - Cubrir consumos desde varios lotes y ventas parcialmente negativas.

### VENTAS-013 - La configuracion de dias de anulacion no controla la regla real

- Prioridad: P2.
- Severidad: media.
- Tipo: bug de configuracion / tiempo.
- Evidencia:
  - `apps/ventas/models.py:162-170` usa
    `settings.ANULACION_DIAS_PERMITIDOS` con default fijo de 15.
  - `apps/configuracion/models.py:152` define `dias_anulacion` como
    configuracion de negocio.
  - `apps/ventas/views.py:652-683` muestra `config.dias_anulacion` en la UI.
  - `apps/ventas/services/anulaciones_service.py:115-118` tambien cita ese valor
    en el error, aunque no fue el valor usado para decidir.
  - `apps/ventas/models.py:170` elimina timezone a la fecha limite y la compara
    con `datetime.now()` naive.
- Escenario demostrable:
  - Configurar 5 o 30 dias cambia el texto de la pantalla, pero la regla sigue
    siendo 15. En un host UTC, la comparacion naive tambien puede adelantar o
    atrasar varias horas el vencimiento respecto a Santo Domingo.
- Sugerencia de arreglo:
  - Usar una unica fuente de verdad (`get_config().dias_anulacion`) y comparar
    datetimes aware con `timezone.now()`.

### VENTAS-014 - La anulacion de una venta inexistente responde 500 en vez de 404

- Prioridad: P3.
- Severidad: baja.
- Tipo: bug de contrato HTTP / manejo de errores.
- Evidencia:
  - `apps/ventas/services/anulaciones_service.py:109-110` usa
    `get_object_or_404()`, que levanta `Http404`, no `ErrorVentaBase`.
  - `apps/ventas/views.py:733-751` captura `ErrorVentaBase` y despues cualquier
    otra excepcion como 500.
  - `apps/ventas/views.py:709-714` documenta 404 para venta inexistente.
- Impacto:
  - Un ID inexistente se registra y presenta como fallo interno del servidor;
    clientes/reintentos no pueden distinguirlo de una interrupcion real.
- Sugerencia de arreglo:
  - Traducir `Venta.DoesNotExist` a una excepcion tipada con status 404 o capturar
    `Http404` explicitamente en la vista.

## Cobertura y pruebas ejecutadas

Comandos ejecutados sin modificar codigo ni datos productivos:

```powershell
C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py check --settings=config.settings_development

C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py test `
  apps.ventas.tests.test_accesos_rapidos_pos `
  apps.ventas.tests.test_pdf_financiacion `
  apps.ventas.tests.test_producto_precio_cache `
  apps.cuentas_por_cobrar.tests.test_credito_services `
  apps.cuentas_por_cobrar.tests.test_anulacion_pago `
  apps.sync.tests.test_outbox_transaccional `
  --settings=config.settings_development --noinput
```

Resultado:

- `manage.py check`: sin issues.
- 34 pruebas: OK.
- La base de pruebas fue creada y destruida por Django.
- No se corrio la suite completa del repositorio en esta pasada.

Que esas pruebas pasen confirma los caminos que ejercitan, pero no cubre los
hallazgos anteriores. No hay pruebas actuales que demuestren:

- venta local con sucursal y prefijo;
- dos cierres simultaneos o dos anulaciones simultaneas;
- producto repetido en el payload;
- cantidades/precios/descuentos no validos;
- metodo de pago desconocido o deshabilitado;
- usuario autenticado sin `ventas.crear` o sin
  `ventas.aplicar_descuento`;
- costo FIFO persistido en el detalle;
- venta negativa sin lotes y su anulacion;
- conversion atomica de cotizacion;
- tipo 31 sin comprador fiscal desde el service;
- plazo de anulacion configurable.

## Tests recomendados antes de tocar codigo

- Crear `apps/ventas/tests/test_ventas_service.py` para invariantes de payload,
  pagos, precio/descuento, costo FIFO, sucursal y postcondiciones.
- Crear `apps/ventas/tests/test_permisos.py` para `ventas.crear`,
  `ventas.aplicar_descuento` y anulacion coherente con RBAC.
- Crear pruebas `TransactionTestCase` con dos conexiones para venta concurrente,
  numeracion y doble anulacion; `TestCase` normal no reproduce estos races.
- Probar producto repetido y consumo distribuido entre varios lotes.
- Probar inventario negativo sin lote y reversa posterior.
- Probar conversion de cotizacion con rollback y doble submit.
- Probar e-CF 31 sin cliente, cliente contado y cliente sin RNC desde
  `procesar_venta_service()`.
- Probar que Django Admin no puede editar estado, detalle ni pagos de una venta
  cerrada.

## Orden sugerido de correccion

1. Cerrar identidad por sucursal y numeracion segura antes de operar una segunda
   sucursal fisica contra el mismo cloud.
2. Hacer atomico/serializado el consumo FIFO y la anulacion.
3. Bloquear mutaciones inconsistentes desde Django Admin.
4. Aplicar RBAC y validacion server-side de precios, descuentos, cantidades y
   metodos de pago.
5. Validar e-CF 31 antes del commit y hacer atomica la conversion de cotizacion.
6. Persistir costo FIFO y alinear la regla configurable de anulacion.
7. Corregir contratos HTTP menores y ampliar cobertura de regresion.

## Conclusion

`apps/ventas` tenia una direccion arquitectonica razonable, pero no era una
frontera transaccional cerrada: varias entradas oficiales (HTTP, Admin y flujos
de dos requests) podian evitar las invariantes que los services intentaban
concentrar. Esa frontera ya esta cerrada (ver abajo); lo que resta es de otro
orden de magnitud.

---

# Estado de mitigacion

Fecha: 2026-08-20. Verificacion previa: se releyo cada hallazgo contra el codigo
citado. **Los 14 son reales** — ninguno resulto falso positivo ni obsoleto.

## Resumen por hallazgo

| ID | Real | Estado | Donde quedo la correccion |
|---|---|---|---|
| VENTAS-001 | Si | Corregido | `ventas_service._resolver_sucursal()` asigna la sucursal a la venta y el modelo vuelve a prefijar el numero. Falla explicito si la instalacion sincroniza y no puede resolverla. |
| VENTAS-002 | Si | Corregido | `Venta._siguiente_numero_venta()` usa el MAXIMO sufijo (no `count()`) y `save()` reintenta dentro de un savepoint ante `IntegrityError`. |
| VENTAS-003 | Si | Corregido | `obtener_lotes_fifo(..., bloquear=True)` toma `SELECT ... FOR UPDATE`; el stock se valida agregando por producto; un faltante de FIFO aborta la venta si no hay inventario negativo. |
| VENTAS-004 | Si | Corregido | `VentaAdmin`, sus inlines, `DetalleVentaAdmin` y `PagoAdmin` son solo lectura. La unica mutacion es la accion `anular_ventas`, que llama a `anular_venta_service`. |
| VENTAS-005 | Si | Corregido | `ventas.crear` y `ventas.aplicar_descuento` se exigen en el service (y el view corta antes con 403). El precio se contrasta contra el producto o la cotizacion de origen. |
| VENTAS-006 | Si | Corregido | `_normalizar_carrito()` valida id/cantidad/precio/descuento por linea; el total debe ser positivo. |
| VENTAS-007 | Si | Corregido | `_validar_metodo_pago()` (allowlist + flags de `ConfiguracionNegocio`) y `_verificar_pagos()` como postcondicion de caja. |
| VENTAS-008 | Si | Corregido | `anular_venta_service` toma `select_for_update()` sobre la venta; `anular_venta_devolver_stock` bloquea lotes y es idempotente. |
| VENTAS-009 | Si | Corregido | Una venta sin movimientos FIFO ya no es error: la reversa devuelve `sin_movimientos` y la anulacion sigue su curso. |
| VENTAS-010 | Si | Corregido | `procesar_venta_service` recibe `cotizacion_id`, la bloquea, la valida y la marca CONVERTIDA en el mismo atomic. El POS ya no hace el segundo request. |
| VENTAS-011 | Si | Corregido | `_validar_precondiciones_ecf()` corre antes del commit, reutilizando la regla de dominio del mapper (`venta_to_ecf.motivo_tipo_31_no_emitible`). |
| VENTAS-012 | Si | Corregido | El detalle conserva su instancia y persiste `costo_fifo` con el costo realmente consumido. |
| VENTAS-013 | Si | Corregido | `Venta.puede_anularse()` usa `get_config().dias_anulacion` y compara datetimes aware. |
| VENTAS-014 | Si | Corregido | `VentaNoEncontradaError` (404) reemplaza el `get_object_or_404` del service. |

## Hallazgos adicionales encontrados al corregir

No estaban en la auditoria original; aparecieron al escribir las pruebas.

- **`VentaAdmin` reventaba al listar.** `format_html('${:.2f}', obj.total)`
  levanta `ValueError` en esta version de Django (`format_html` escapa cada
  argumento a `SafeString`, y un spec numerico sobre un str falla). La
  changelist de ventas devolvia 500. Mismo defecto en
  `DetalleVentaAdmin.margen_display`. Corregido preformateando el numero.
- **`ConfiguracionNegocio.load()` tenia una carrera leer-y-crear.** En una
  instalacion recien montada, dos requests simultaneos veian `None` los dos y
  el segundo moria con `IntegrityError` sobre la pk. Corregido con
  `get_or_create(pk=1)`.
- **El POS enviaba el request de "cotizacion convertida" DOS veces.** Ambas
  llamadas quedaron eliminadas: la conversion ahora es server-side.

## Cambios de conducta observables

Intencionales, pero se ven desde fuera. Tenerlos presentes al desplegar:

1. **Numeracion de ventas.** Una instalacion con su `Sucursal` provisionada
   pasa de `V-20260820-0001` a `SD-001-V20260820-0001`. Es la conducta que el
   roadmap daba por implementada (Fase 2) y es lo que evita la colision de
   identidad en el cloud. Las ventas ya emitidas no cambian.
2. **RBAC en el POS.** Un usuario sin `ventas.crear` ya no puede vender, y sin
   `ventas.aplicar_descuento` no puede aplicar descuentos. ADMIN/SYSADMIN
   conservan acceso total (`es_acceso_total`) y el rol Cajero por defecto trae
   ambos permisos (`PERMISOS_CAJERO_DEFAULT`), asi que una instalacion con el
   bootstrap RBAC corrido no cambia. **Verificar antes de desplegar** que los
   cajeros de cada cliente tengan su `AsignacionRol`.
3. **Metodos de pago deshabilitados.** `pago_tarjeta` viene en `False` por
   defecto: un POST con `metodo_pago="tarjeta"` en un negocio que no la tiene
   habilitada ahora se rechaza (antes se aceptaba). Coincide con lo que la UI
   ya ofrecia.
4. **Precio autoritativo.** Si el catalogo de la caja quedo desactualizado, la
   venta se rechaza pidiendo recargar en vez de vender al precio viejo.
5. **Instalacion con sync y sin sucursal.** Deja de facturar con un error
   explicito. Una instalacion standalone (`SYNC_ENABLED=False`) sigue operando
   con numeracion legacy y solo deja un warning en el log.
6. **Django Admin.** Ventas, detalles y pagos son solo lectura. Para anular,
   usar la accion "Anular ventas seleccionadas (con motivo)".

## Pendiente (no bloqueante)

- **Identidad compuesta en el cloud.** `_handler_venta_creada`
  (`apps/api/views/sync.py`) sigue deduplicando solo por `numero_venta`. Con el
  prefijo de sucursal restaurado la colision ya no puede producirse, asi que la
  causa raiz esta cerrada. Cambiar la dedup a `(sucursal, numero_venta)` exige
  ademas migrar la unicidad de `Venta.numero_venta` a una constraint compuesta;
  hacerlo a medias convierte una colision heredada en `IntegrityError` (500) en
  vez del skip silencioso actual. Tratar como trabajo propio, con migracion.
- **`CheckConstraint` de respaldo.** Las invariantes de importes se validan en
  el service. Respaldarlas en BD (cantidad > 0, precio > 0, total > 0) sigue
  siendo deseable como red de seguridad para escrituras que no pasen por el
  service.
- **`_puede_anular` sigue usando el rol legacy** (`ADMIN`/`SYSADMIN`) en vez de
  `ventas.anular`. Es coherente con el estado actual del rollout RBAC y estaba
  fuera del alcance de esta auditoria.

## Pruebas

Suite completa, serial:

```
manage.py test --settings=config.settings_development --noinput
```

Resultado: **456 tests, OK.** La suite tampoco tiene ya los 2 fallos locales de
Windows que documentaba la nota historica: ambos estaban corregidos en el repo.

Modulos de regresion nuevos:

| Archivo | Cubre |
|---|---|
| `apps/ventas/tests/test_ventas_service.py` (31 tests) | VENTAS-001, 002, 003, 005, 006, 007, 010, 011, 012 |
| `apps/ventas/tests/test_anulaciones.py` (13 tests) | VENTAS-004, 008, 009, 013, 014 |
| `apps/ventas/tests/test_concurrencia.py` (5 tests) | VENTAS-002, 003, 008 con hilos reales (`TransactionTestCase`) |

Los tests de concurrencia se validaron por mutacion: al quitar el
`select_for_update()` de `procesar_venta_fifo`,
`test_consumo_fifo_simultaneo_no_pierde_actualizaciones` falla con la invariante
contable rota (`lote=2, movimientos=6` sobre un stock de 5).

Detalle util al leer ese test: la carrera de FIFO **no** se reproduce llamando a
`procesar_venta_service` desde dos hilos, porque el indice unico de
`numero_venta` los serializa en el INSERT de la cabecera. Esa serializacion es
accidental — desapareceria si la numeracion pasara a una secuencia de BD — asi
que el lock se prueba directamente sobre `procesar_venta_fifo`.

