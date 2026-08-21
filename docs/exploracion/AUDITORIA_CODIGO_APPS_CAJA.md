# Auditoria de codigo - apps/caja

Fecha: 2026-08-20
Scope principal: `apps/caja`
Scope de verificacion: permisos locales, sucursales, flujo de ventas y pagos,
cuentas por cobrar, Django Admin, plantillas de caja, productor de eventos en
`apps/sync`, receptor cloud en `apps/api/views/sync.py` y pruebas relacionadas.
Exclusiones por prioridad de negocio: `apps/facturacion_electronica` y
`apps/suscripciones` no fueron auditadas en esta pasada.
Modo: lectura, ejecucion de checks/pruebas sobre base de test y documentacion de
hallazgos; no se aplicaron cambios funcionales.

> **Estado (2026-08-21): MITIGADO.** Los 13 hallazgos se verificaron contra el
> codigo y los 13 resultaron reales. Todos estan corregidos, con pruebas de
> regresion. Ver [Estado de mitigacion](#estado-de-mitigacion) al final.
> **Incluye 3 migraciones** y un cambio de contrato del modal de caja
> (`admin_id` -> `override_token`). Queda **una decision de negocio abierta**:
> si cobrar en efectivo sin turno abierto debe rechazarse.

## Por que esta app sigue en la auditoria

Despues de `apps/api`, `apps/ventas`, `apps/sync` e `apps/inventario`, caja es la
siguiente frontera de mayor impacto para la operacion actual. La app decide o
presenta:

- quien puede abrir y cerrar una caja;
- cuanto efectivo debe existir al final del turno;
- que retiros, gastos e ingresos modifican ese esperado;
- que administrador autorizo un movimiento sensible;
- como se conserva el cierre historico;
- como apertura, movimientos y cierre llegan al cloud.

El scope Python principal contiene 967 lineas entre modelos, vistas, Admin y
URLs, mas 999 lineas en sus dos plantillas. `apps/caja/tests` contiene solamente
un `__init__.py` vacio: no hay pruebas propias de apertura, autorizacion,
movimientos, cierre, concurrencia, sucursal, Admin ni sync.

Durante la auditoria habia correcciones activas del usuario en ventas, CxC,
sync, permisos y otros dominios. Se respetaron como trabajo en curso, se leyo el
estado actual para seguir el flujo transversal y no se modifico ninguno de esos
archivos.

## Resumen

La app ya tiene varias bases sanas: apertura, movimientos y cierre HTTP usan
`transaction.atomic()`; el outbox se crea dentro de esas transacciones; existen
indices unicos filtrados que impiden mas de un turno abierto por usuario o por
caja; los movimientos HTTP exigen monto positivo y descripcion; y el esperado
incluye ventas en efectivo, abonos CxC aplicados, retiros, gastos e ingresos.

El problema central es que el turno no es la autoridad real sobre el efectivo.
Los pagos de ventas y CxC no pertenecen a un turno ni exigen que exista uno; el
cierre los infiere despues por usuario y rango de fechas. Al mismo tiempo, el
cierre no bloquea el turno ni los hechos que intenta congelar, la autorizacion
de administrador se reduce a un `admin_id` reenviable y Django Admin permite
reescribir el historial fuera de los servicios y del outbox.

Se confirmaron 13 hallazgos: ocho P1, tres P2 y dos P3. Los riesgos mas urgentes
son concretos y reproducibles:

- una cajera puede forjar la autorizacion de un retiro enviando el ID de un
  administrador existente, sin conocer su clave ni llamar al soft-login;
- una venta en efectivo puede completarse sin ningun turno abierto;
- cualquier usuario autenticado puede abrir caja y registrar gastos aunque no
  tenga permisos de caja;
- cajas, turnos e historial no se acotan por sucursal o negocio;
- cierre y movimientos pueden competir y congelar un esperado incompleto;
- apertura y conteo final aceptan importes negativos;
- Django Admin puede reabrir, alterar o borrar hechos de caja sin auditoria ni
  eventos de sync;
- renombrar una caja fragmenta su apertura, movimientos y cierre en el cloud.

Las 71 pruebas transversales seleccionadas pasaron, pero no ejercitan las URLs
ni invariantes propias de caja. Diez reproducciones adicionales sobre la base
de test confirmaron los caminos principales descritos abajo.

## Hallazgos priorizados

### CAJA-001 - La autorizacion soft-login se puede forjar con un `admin_id`

- Prioridad: P1.
- Severidad: critica.
- Tipo: autorizacion / fraude de movimientos / trazabilidad falsa.
- Evidencia:
  - `apps/caja/views.py:36-72` autentica usuario y clave, comprueba
    `caja.administrar` y devuelve el `admin_id` en texto plano.
  - El resultado de esa autenticacion no genera un grant firmado, nonce,
    sesion corta ni registro consumible vinculado al movimiento.
  - `apps/caja/views.py:320-326` acepta `admin_id` directamente en otro request.
  - `apps/caja/views.py:356-377` no verifica que ese ID provenga de
    `api_validar_admin`; solo comprueba que exista un `Usuario` activo cuyo rol
    legacy sea `ADMIN` o `SYSADMIN`.
  - `apps/caja/views.py:379-387` persiste ese usuario como `autorizado_por`, por
    lo que el historial afirma una autorizacion que nunca ocurrio.
  - El endpoint de soft-login tampoco tiene throttling visible; cualquier
    usuario autenticado puede intentar credenciales repetidamente.
- Escenario demostrado:
  - Se creo un turno de cajera y se envio directamente un `RETIRO` con el ID de
    un administrador existente, sin llamar a `caja:api_validar_admin` y sin
    conocer su clave. La respuesta fue HTTP 200 y el movimiento quedo
    `autorizado_por` ese administrador.
- Impacto:
  - Una cajera puede retirar o ingresar efectivo atribuyendo la aprobacion a
    cualquier administrador activo cuyo ID conozca o adivine.
  - El campo de autorizacion deja de ser evidencia util ante un descuadre.
  - La enumeracion de IDs y respuestas distintas facilita descubrir cuentas
    validas.
- Sugerencia de arreglo:
  - Hacer que el soft-login emita un grant aleatorio, de un solo uso, con
    expiracion corta y vinculado a cajera, turno, tipo, monto y sucursal.
  - Consumir y marcar usado el grant dentro de la misma transaccion que crea el
    movimiento; nunca aceptar un `admin_id` como prueba de conocimiento.
  - Registrar intento, autorizador, fecha e IP y aplicar throttling por sesion,
    usuario y origen.
  - Mantener la comprobacion efectiva de `caja.administrar` al consumir el
    grant, no solo al emitirlo.

### CAJA-002 - Las ventas y cobros en efectivo no pertenecen a un turno

- Prioridad: P1.
- Severidad: critica.
- Tipo: integridad contable / perdida de efectivo esperado.
- Evidencia:
  - `Pago` solo referencia la venta en `apps/ventas/models.py:348-389`; no tiene
    FK a `TurnoCaja` ni otra identidad de sesion de efectivo.
  - `PagoCxC` referencia cuenta y usuario en
    `apps/cuentas_por_cobrar/models.py:267-305`, pero tampoco referencia turno.
  - `procesar_venta_service()` abre su transaccion en
    `apps/ventas/services/ventas_service.py:263` y registra pagos en
    `apps/ventas/services/ventas_service.py:305`, sin resolver ni exigir turno.
  - Los pagos de venta se crean en
    `apps/ventas/services/ventas_service.py:849-968` sin una comprobacion de
    caja abierta.
  - `registrar_pago_cxc_service()` bloquea cuenta y cuotas, pero crea el abono
    sin turno en `apps/cuentas_por_cobrar/services.py:427-509`.
  - `TurnoCaja.calcular_esperado()` intenta reconstruir la pertenencia despues:
    filtra pagos por usuario y fechas en `apps/caja/models.py:215-244`.
  - La propia cabecera de `apps/caja/models.py` dice que las ventas quedan
    "vinculadas al turno activo", pero ese vinculo no existe en el modelo.
- Escenario demostrado:
  - Se proceso una venta completa en efectivo, con stock y pago persistidos,
    usando un usuario que no tenia ningun turno abierto. La operacion termino
    correctamente y no se creo ni asocio un turno.
- Impacto:
  - Efectivo cobrado fuera de un turno no aparecera en ningun cierre.
  - Un pago solo se atribuye por coincidencia temporal y de usuario; si el
    usuario opera contra otra sucursal durante ese rango, el cierre puede sumar
    efectivo de la sucursal equivocada.
  - No existe una consulta exacta que responda que turno recibio un pago.
  - Una venta o abono que compita con el cierre puede quedar antes o despues del
    corte segun orden de commit, no segun el hecho operativo.
- Sugerencia de arreglo:
  - Introducir una identidad estable de sesion de caja en cada hecho que mueve
    efectivo: pago de venta, pago CxC y movimiento manual.
  - Resolver y bloquear el turno abierto dentro de la misma transaccion que
    registra el cobro; rechazar efectivo cuando no exista un turno operable.
  - Para canales que legitimamente cobran sin caja fisica, modelar un origen de
    cobro distinto y excluirlo explicitamente del arqueo, en vez de dejarlo
    implicito.
  - Migrar datos historicos con una politica visible: vinculados con alta
    confianza, ambiguos y sin turno; no inventar asociaciones silenciosas.

### CAJA-003 - Cualquier usuario autenticado puede operar una caja

- Prioridad: P1.
- Severidad: alta.
- Tipo: autorizacion / integridad de efectivo.
- Evidencia:
  - El catalogo solo declara `caja.administrar` en
    `apps/permisos/catalogo.py:41-42`; no existe un permiso separado y aplicado
    para operar caja.
  - `caja_index`, `api_abrir_turno`, `api_cerrar_turno`,
    `api_registrar_movimiento` y `api_estado_turno` usan solamente
    `login_required` en `apps/caja/views.py:82`, `167`, `234`, `306` y `423`.
  - La apertura crea el turno en `apps/caja/views.py:176-214` sin comprobar
    permiso de operacion.
  - Los gastos tampoco requieren autorizacion administrativa en
    `apps/caja/views.py:328-387`; basta que el usuario tenga un turno propio.
  - El enlace Caja se muestra sin gate en `templates/base.html:83-90`.
- Escenario demostrado:
  - Un usuario `CAJERA` sin `caja.administrar` ni asignaciones RBAC abrio una
    caja y registro un `GASTO`. Ambos endpoints respondieron HTTP 200 y el gasto
    quedo persistido.
- Impacto:
  - Un usuario creado para inventario, reportes u otra tarea puede bloquear una
    caja fisica al abrirla, registrar gastos y cerrar su propio turno.
  - La trazabilidad depende de que toda cuenta autenticada sea implicitamente
    cajera, lo que contradice el RBAC data-driven ya usado por el sistema.
- Sugerencia de arreglo:
  - Definir `caja.operar` para apertura, estado, movimientos permitidos y cierre
    propio; conservar `caja.administrar` para historial, autorizaciones y
    actuar sobre turnos ajenos.
  - Aplicar gates server-side y usar el mismo permiso para visibilidad en
    plantillas.
  - Agregar pruebas negativas por cada URL y por tipo de movimiento.

### CAJA-004 - Las operaciones y consultas no se acotan por sucursal o negocio

- Prioridad: P1.
- Severidad: alta.
- Tipo: aislamiento multi-sucursal / autorizacion.
- Evidencia:
  - `es_admin()` llama `tiene_permiso('caja.administrar')` sin sucursal en
    `apps/caja/views.py:25-29`.
  - El motor RBAC solo filtra asignaciones por sucursal cuando recibe una en
    `apps/permisos/engine.py:96-109`; con `None`, una asignacion acotada a otra
    sucursal puede habilitar el gate.
  - La pagina lista todas las cajas activas en `apps/caja/views.py:93-98`, no las
    de `request.sucursal` o del negocio actual.
  - La apertura recupera cualquier caja activa por PK en
    `apps/caja/views.py:195-210`.
  - Los administradores ven todos los turnos abiertos en
    `apps/caja/views.py:106-113`, todo el historial cerrado en
    `apps/caja/views.py:476-487` y cualquier detalle por PK en
    `apps/caja/views.py:500-513`.
  - `Caja.sucursal` es nullable en `apps/caja/models.py:42-50`, pero las vistas
    no definen como tratar filas legacy frente a sucursales reales.
- Impacto:
  - En una BD compartida, un operador puede abrir una caja de otra sucursal y
    un administrador de un negocio puede ver importes y movimientos de otro.
  - Un permiso concedido solo para la sucursal A puede habilitar acciones sobre
    la B.
  - El calculo por usuario/fecha de CAJA-002 puede contaminar el esperado entre
    sucursales.
- Sugerencia de arreglo:
  - Centralizar un queryset de caja con scope: usuario operativo -> sucursal
    actual; administrador de negocio -> sucursales de su negocio; SYSADMIN ->
    acceso global solo de forma explicita.
  - Pasar siempre la sucursal efectiva a `tiene_permiso`.
  - Resolver una politica de adopcion para cajas legacy con sucursal nula; no
    mezclarlas silenciosamente con todas las sucursales.
  - Probar listado, apertura, cierre, historial y detalle cruzando dos negocios
    y dos sucursales.

### CAJA-005 - El cierre no serializa el turno ni los hechos que congela

- Prioridad: P1.
- Severidad: alta.
- Tipo: concurrencia / doble cierre / arqueo incompleto.
- Evidencia:
  - `api_cerrar_turno()` abre una transaccion, pero busca el turno con
    `get_object_or_404()` o `.first()` sin `select_for_update()` en
    `apps/caja/views.py:246-260`.
  - `TurnoCaja.cerrar()` calcula antes de establecer la fecha de cierre y no
    comprueba el estado previo en `apps/caja/models.py:284-299`.
  - El calculo ejecuta consultas independientes de ventas, CxC y movimientos en
    `apps/caja/models.py:215-263`.
  - El endpoint de movimientos tambien lee el turno abierto sin lock en
    `apps/caja/views.py:320-348`.
  - Ventas y abonos CxC no comparten un lock con el turno, segun CAJA-002.
  - Los constraints de `apps/caja/models.py:182-195` protegen que no existan dos
    turnos abiertos por usuario/caja, pero no protegen la transicion a cerrado.
- Escenarios:
  - Dos requests de cierre pueden leer `ABIERTO`, calcular resultados distintos
    y ambos guardar/empujar un evento; el ultimo commit local gana.
  - Un movimiento puede obtener el turno abierto, esperar mientras el cierre
    congela `monto_esperado` y luego persistirse contra el turno ya cerrado.
  - Una venta en efectivo puede confirmar despues del calculo sin haber sido
    parte del cierre.
  - Se llamo `TurnoCaja.cerrar()` dos veces secuencialmente: la segunda llamada
    reescribio monto contado, fecha, cerrador y notas del cierre original.
- Impacto:
  - El cierre historico puede no coincidir con los movimientos que finalmente
    quedan asociados al turno.
  - Se pueden emitir dos hechos de cierre para una sola sesion y cloud puede
    conservar uno distinto del ultimo estado local.
  - Un faltante o sobrante puede ser artefacto de la carrera, no de la caja
    fisica.
- Sugerencia de arreglo:
  - Bloquear el turno con `select_for_update()` en cerrar y en toda operacion
    que pretenda adjuntarle efectivo.
  - Hacer la transicion `ABIERTO -> CERRADO` explicita e idempotente; cualquier
    otro estado debe ser error de negocio.
  - Con el vinculo de CAJA-002, adjuntar pagos solo despues de bloquear el turno
    y verificar que siga abierto.
  - Emitir exactamente un evento de cierre despues de una transicion exitosa y
    agregar pruebas concurrentes con conexiones independientes.

### CAJA-006 - Apertura y conteo final aceptan importes negativos

- Prioridad: P1.
- Severidad: alta.
- Tipo: validacion / valores contables imposibles.
- Evidencia:
  - La apertura convierte `fondo_apertura` a `Decimal`, pero no valida rango en
    `apps/caja/views.py:176-210`.
  - `TurnoCaja.fondo_apertura` declara `MinValueValidator(0)` en
    `apps/caja/models.py:101-109`, pero el ORM no llama `full_clean()` al usar
    `objects.create()`.
  - El cierre tampoco exige que `monto_contado` sea no negativo en
    `apps/caja/views.py:246-273`; el campo no declara un validador equivalente.
  - No hay `CheckConstraint` de BD para fondo, contado, esperado o movimientos
    en `apps/caja/models.py:176-195` y `366-373`.
  - Los `min="0"` de la plantilla, por ejemplo
    `templates/caja/index.html:503`, solo protegen el navegador normal.
- Escenario demostrado:
  - Se abrio una caja con `fondo_apertura=-100.00` y luego se cerro con
    `monto_contado=-5.00`. Ambos endpoints respondieron HTTP 200 y conservaron
    los valores negativos.
- Impacto:
  - Esperado, diferencia e historial pueden representar efectivo fisicamente
    imposible.
  - Reportes y sync reciben esos valores como hechos validos.
- Sugerencia de arreglo:
  - Validar Decimals finitos y no negativos en un servicio comun de apertura y
    cierre.
  - Agregar constraints de BD para las invariantes que siempre deben cumplirse.
  - Mapear errores de validacion a HTTP 400 y probar cero, negativos, exceso de
    digitos, strings invalidos y valores ausentes.

### CAJA-007 - Django Admin permite reescribir la historia fuera del flujo auditado

- Prioridad: P1.
- Severidad: alta.
- Tipo: bypass de invariantes / auditoria / sync.
- Evidencia:
  - `TurnoCajaAdmin` solo marca `monto_esperado` y `diferencia` como readonly en
    `apps/caja/admin.py:11-16`.
  - Por defecto siguen editables caja, usuario, fechas, fondo, monto contado,
    estado, notas y `cerrado_por`; tambien permanecen disponibles alta y baja.
  - `MovimientoCajaAdmin` no define ningun readonly ni deshabilita add/change/
    delete en `apps/caja/admin.py:19-23`.
  - `CajaAdmin` permite renombrar y reasignar libremente la identidad usada por
    sync en `apps/caja/admin.py:5-8`.
  - Esas mutaciones no pasan por `TurnoCaja.cerrar()`, no crean un registro de
    auditoria y no emiten eventos de caja.
- Impacto:
  - Un operador de Admin puede reabrir un turno, cambiar su corte, borrar un
    gasto o crear un retiro retroactivo sin huella equivalente.
  - Local y cloud pueden divergir permanentemente.
  - Renombrar la caja puede activar CAJA-008 incluso sin tocar el turno.
- Sugerencia de arreglo:
  - Hacer turnos cerrados y movimientos inmutables en Admin.
  - Reemplazar cambios contables por acciones explicitas de correccion/reversa,
    con motivo obligatorio, usuario, fecha, auditoria y outbox atomico.
  - Limitar el cambio de identidad de Caja a un servicio que preserve una clave
    estable de sync.

### CAJA-008 - El sync identifica la caja por un nombre mutable y no unico

- Prioridad: P1.
- Severidad: alta.
- Tipo: identidad distribuida / fragmentacion del turno.
- Evidencia:
  - `Caja.nombre` es un `CharField` normal y no existe constraint unico por
    sucursal en `apps/caja/models.py:20-50`.
  - Apertura y cierre serializan `turno_id_local`, pero tambien el nombre actual
    en `apps/sync/serializers.py:255-273` y `306-330`.
  - Movimiento envia `movimiento_id_local`, pero identifica el turno mediante
    `(sucursal, caja_nombre, fecha_apertura)` en
    `apps/sync/serializers.py:276-303`.
  - El receptor no usa `turno_id_local` para resolver la sesion. Crea o busca
    caja por `(nombre, sucursal)` en `apps/api/views/sync.py:514-522`.
  - Apertura, movimiento y cierre vuelven a resolver esa caja por nombre en
    `apps/api/views/sync.py:799-833`, `836-884` y `887-950`.
  - Si ya hay dos cajas con el mismo nombre/sucursal, `get_or_create()` puede
    fallar con `MultipleObjectsReturned`.
- Escenario demostrado:
  - Se recibio la apertura con `Caja nombre viejo`; antes del movimiento/cierre
    el payload uso `Caja nombre nuevo`. El movimiento no encontro el turno y el
    cierre creo por fallback otro turno cerrado bajo la caja nueva. El turno
    viejo quedo abierto en cloud.
- Impacto:
  - Un solo turno local puede convertirse en dos cajas y dos turnos cloud.
  - Movimientos quedan en reintento o se pierden operacionalmente mientras el
    cierre aparenta existir.
  - El portal puede mostrar una caja eternamente abierta y otra cerrada con el
    mismo efectivo.
- Sugerencia de arreglo:
  - Asignar a Caja una identidad estable, por ejemplo UUID/origen de sucursal,
    y transportar esa identidad en los tres eventos.
  - Usar `(sucursal, caja_origen_id)` como clave cloud y mantener nombre como
    atributo mutable.
  - Agregar unicidad defensiva apropiada y un proceso previo para reconciliar
    duplicados antes de activar la constraint.
  - Adoptar `turno_id_local` junto con sucursal como identidad del turno o una
    UUID propia; no dejar el campo emitido sin consumo.

### CAJA-009 - Un movimiento sync atrasado puede adjuntarse a un turno cerrado

- Prioridad: P2.
- Severidad: media-alta.
- Tipo: orden de eventos / inmutabilidad del cierre.
- Evidencia:
  - `_buscar_turno_abierto()` promete buscar un turno abierto, pero su primera
    consulta por caja+fecha no filtra estado en
    `apps/api/views/sync.py:483-508`.
  - Si encuentra un turno cerrado lo devuelve antes del fallback que si filtra
    `estado='ABIERTO'` en `apps/api/views/sync.py:510-511`.
  - `_handler_movimiento_caja()` crea el movimiento sobre el turno retornado en
    `apps/api/views/sync.py:846-884` sin revalidar estado ni recalcular el cierre.
- Escenario demostrado:
  - Se creo un turno cerrado con `monto_esperado=100.00` y se proceso despues un
    evento `GASTO=5.00` con la fecha exacta de apertura. El movimiento quedo
    asociado al turno cerrado y `monto_esperado` permanecio en 100.00.
- Impacto:
  - El detalle cloud contiene movimientos incompatibles con su propio cierre.
  - Reportes pueden mostrar un gasto que nunca afecto diferencia ni esperado.
- Sugerencia de arreglo:
  - Filtrar `estado='ABIERTO'` en todas las ramas y rechazar movimientos contra
    sesiones cerradas.
  - Tratar el cierre como barrera de orden por identidad estable de turno; un
    movimiento tardio debe quedar en error operativo/reconciliacion, no
    mutar silenciosamente el historial.

### CAJA-010 - `turno_id` se ignora cuando el administrador tiene turno propio

- Prioridad: P2.
- Severidad: media.
- Tipo: seleccion de destino / contrato ambiguo.
- Evidencia:
  - `api_registrar_movimiento()` busca primero el turno propio en
    `apps/caja/views.py:338-342`.
  - Solo lee `turno_id` si no encontro turno propio y el usuario es admin en
    `apps/caja/views.py:344-348`.
  - La respuesta no indica que el destino solicitado fue ignorado; devuelve el
    movimiento como exitoso.
- Escenario demostrado:
  - Un SYSADMIN con turno propio envio un gasto con `turno_id` de otra cajera.
    La respuesta fue HTTP 200, pero el gasto quedo en el turno del SYSADMIN y el
    turno solicitado no recibio nada.
- Impacto:
  - Un administrador cree corregir o registrar un movimiento en otra caja, pero
    altera su propio arqueo.
  - La equivocacion puede descubrirse solo al cerrar ambas cajas.
- Sugerencia de arreglo:
  - Si llega `turno_id`, validarlo y usarlo de forma explicita solo con
    `caja.administrar`; si no llega, usar el turno propio.
  - Incluir ID/caja/cajero de destino en confirmacion y pruebas.

### CAJA-011 - UI, soft-login y consumidor aplican tres modelos de admin distintos

- Prioridad: P2.
- Severidad: media.
- Tipo: RBAC / inconsistencia de contrato.
- Evidencia:
  - Los gates server-side generales usan el permiso data-driven
    `caja.administrar` en `apps/caja/views.py:25-29`.
  - El soft-login tambien acepta cualquier rol efectivo con ese permiso en
    `apps/caja/views.py:65-72`.
  - El consumidor de `admin_id` vuelve al rol legacy exacto
    `ADMIN|SYSADMIN` en `apps/caja/views.py:369-377`.
  - La plantilla decide autoautorizacion solo con
    `request.user.rol === 'ADMIN'` en `templates/caja/index.html:662-677`.
  - Por eso un `SYSADMIN` recibe una experiencia distinta y un rol custom con
    `caja.administrar` puede validar su clave pero ser rechazado al usar el ID.
- Impacto:
  - La configuracion RBAC promete capacidades que el flujo de caja no puede
    completar.
  - El comportamiento cambia entre UI y POST directo, y entre ADMIN, SYSADMIN y
    roles custom.
- Sugerencia de arreglo:
  - Usar una sola decision de permiso efectivo, acotada por sucursal, tanto en
    servidor como en datos hidratados a la UI.
  - Eliminar checks de rol legacy del JavaScript y del consumidor.
  - La solucion de CAJA-001 debe transportar el autorizador validado dentro del
    grant, no hacer una segunda interpretacion del rol.

### CAJA-012 - Errores de cliente se convierten en 500 y filtran excepciones internas

- Prioridad: P3.
- Severidad: baja-media.
- Tipo: contrato HTTP / observabilidad.
- Evidencia:
  - Apertura, cierre y movimiento envuelven toda la funcion en `except Exception`
    y devuelven `str(e)` como HTTP 500 en `apps/caja/views.py:176-227`,
    `246-299` y `320-416`.
  - El `get_object_or_404()` de apertura esta dentro de ese bloque en
    `apps/caja/views.py:195-196`; por eso un recurso inexistente deja de ser 404.
  - JSON invalido, decimal invalido, constraint concurrente y fallo interno
    quedan mezclados bajo la misma respuesta.
  - El soft-login captura cualquier excepcion y responde HTTP 200 con el texto
    en `apps/caja/views.py:74-75`.
- Escenario demostrado:
  - Abrir una caja con un `caja_id` inexistente devolvio HTTP 500, no 404/400.
- Impacto:
  - El frontend no puede decidir si corregir el payload, recargar estado o
    alertar una incidencia.
  - Mensajes del driver o detalles internos pueden exponerse al usuario.
  - Colisiones normales de apertura por concurrencia parecen caidas del sistema.
- Sugerencia de arreglo:
  - Parsear y validar antes del bloque transaccional con excepciones de negocio
    tipadas.
  - Conservar 400/403/404/409 segun el caso y reservar 500 para fallos
    inesperados con logging server-side y mensaje generico.
  - Definir un contrato JSON uniforme para todos los endpoints de caja.

### CAJA-013 - Los importes cero desaparecen del detalle e historial

- Prioridad: P3.
- Severidad: baja.
- Tipo: serializacion / presentacion de evidencia.
- Evidencia:
  - El detalle usa truthiness para `monto_esperado` y `monto_contado` en
    `apps/caja/views.py:523-524`; `Decimal('0.00')` se serializa como `null`.
  - `diferencia` si usa correctamente `is not None` en
    `apps/caja/views.py:525`.
  - El historial oculta el esperado cero por la misma razon en
    `templates/caja/historial.html:66-71`.
- Escenario demostrado:
  - Un turno cerrado con esperado, contado y diferencia en cero devolvio
    `esperado=null`, `contado=null` y `diferencia="0.00"`.
- Impacto:
  - Cero y dato desconocido se vuelven indistinguibles en UI/API.
  - Una caja validamente vacia aparenta no haber sido calculada o contada.
- Sugerencia de arreglo:
  - Comparar siempre con `is not None` y renderizar `0.00` explicitamente.
  - Agregar un test de contrato para cero, null legacy y valores positivos.

## Controles existentes que conviene preservar

- `TurnoCaja` declara constraints condicionales para un solo turno abierto por
  usuario y por caja en `apps/caja/models.py:182-195`. `sqlmigrate caja 0001`
  confirma que se materializan como indices unicos filtrados en SQL Server.
- Apertura, movimiento y cierre escriben su evento de outbox dentro de la misma
  transaccion HTTP en `apps/caja/views.py:176-214`, `320-392` y `246-276`.
- El movimiento HTTP rechaza tipo desconocido, monto no positivo y descripcion
  vacia en `apps/caja/views.py:328-336`.
- El cierre incluye abonos CxC solo cuando siguen `APLICADO`, lo que permite que
  una anulacion anterior al cierre salga del esperado en
  `apps/caja/models.py:233-244`.
- Detalle protege el turno de otro usuario salvo que el solicitante tenga el
  gate administrativo en `apps/caja/views.py:500-509`.

Estos controles reducen algunos errores simples, pero no sustituyen identidad de
turno, scope por sucursal, autorizacion consumible ni serializacion del cierre.

## Pruebas y verificaciones ejecutadas

### Suite transversal existente

Se ejecutaron 71 pruebas con base de test reutilizable:

```text
manage.py test \
  apps.ventas.tests.test_ventas_service \
  apps.cuentas_por_cobrar.tests.test_credito_services \
  apps.cuentas_por_cobrar.tests.test_anulacion_pago \
  apps.sync.tests.test_extended_serializers \
  apps.sync.tests.test_outbox_transaccional \
  apps.api.tests.test_sync_extended \
  apps.permisos.tests.test_cutover_local \
  --keepdb --settings=config.settings_development
```

Resultado: 71/71 OK y `System check identified no issues`.

La seleccion confirma que ventas, CxC, serializers, outbox, receptor sync y el
gate de historial no rompen sus contratos ya cubiertos. No constituye cobertura
de caja: `apps/caja/tests` no tiene casos ejecutables.

### Reproducciones dirigidas de auditoria

Se ejecutaron diez escenarios dinamicos con `django.test.TestCase`, rollback por
caso y la base `test_pos_fifo_dev`:

1. usuario sin permiso abre turno y registra gasto;
2. retiro con `admin_id` forjado sin soft-login;
3. apertura y cierre con montos negativos;
4. `turno_id` ajeno ignorado si el admin tiene turno propio;
5. segunda llamada directa a `cerrar()` reescribe el cierre;
6. venta en efectivo completa sin turno;
7. rename de caja fragmenta apertura/movimiento/cierre cloud;
8. movimiento sync tardio se adjunta a turno cerrado;
9. caja inexistente produce HTTP 500;
10. ceros se serializan como `null` en detalle.

Los diez comportamientos quedaron confirmados. En la primera corrida, ocho
escenarios pasaron y dos aserciones del arnes resultaron incorrectas: una
desconocia la compatibilidad transitoria que da acceso total al rol `ADMIN` y la
otra uso nombres de campos distintos al JSON real. Se corrigieron solo esas
aserciones y ambos escenarios restantes pasaron; no se altero codigo productivo.

### Verificaciones adicionales

- `sqlmigrate caja 0001` se uso para comprobar la existencia real de los dos
  indices unicos filtrados de turnos abiertos.
- Se inspeccionaron de extremo a extremo los payloads de apertura, movimiento y
  cierre y sus tres handlers cloud.
- No se hizo una prueba de carrera con dos conexiones reales ni se escribio en
  una base productiva. Los escenarios concurrentes se derivan de fronteras de
  lock/transaccion visibles y deben convertirse en pruebas de integracion al
  corregirlos.
- No se auditaron los detalles internos de facturacion electronica ni de
  suscripciones, segun la prioridad indicada para el negocio.

## Orden sugerido de correccion

1. Cerrar CAJA-001: reemplazar `admin_id` por autorizacion de un solo uso y
   bloquear mutaciones contables directas en Admin.
2. Definir `caja.operar`, aplicar scope por sucursal/negocio y validar importes
   antes de permitir nuevas aperturas o movimientos.
3. Hacer que todo efectivo pertenezca explicitamente a un turno y que cobrar sin
   turno falle o use un canal no-caja claramente modelado.
4. Serializar cierre, movimientos y cobros con locks y transiciones de estado
   idempotentes.
5. Introducir identidad estable de Caja/Turno para sync y reconciliar nombres
   duplicados o renombrados.
6. Endurecer contratos HTTP/UI y construir la suite propia de `apps/caja` antes
   de refactors menores.

## Criterio de cierre recomendado

La app no deberia considerarse cerrada solo porque la formula produzca el total
correcto en un caso secuencial. El criterio minimo deberia demostrar que:

- ningun hecho en efectivo se crea sin turno/origen explicito;
- cada hecho pertenece a exactamente una sucursal y un turno;
- un cierre exitoso es unico e inmutable;
- ningun movimiento puede entrar despues del corte;
- una autorizacion administrativa prueba una accion real y no un ID enviado por
  el cliente;
- Admin, HTTP y sync respetan las mismas invariantes;
- dos sucursales/negocios no pueden ver ni operar cajas entre si;
- rename, reintentos y eventos tardios no fragmentan la identidad distribuida.

---

# Estado de mitigacion

Fecha: 2026-08-21. Verificacion previa: se releyo cada hallazgo contra el codigo
citado. **Los 13 son reales** - ninguno resulto falso positivo ni obsoleto.

## Resumen por hallazgo

| ID | Real | Estado | Donde quedo la correccion |
|---|---|---|---|
| CAJA-001 | Si | Corregido | `api_registrar_movimiento` consume un `AutorizacionOverride` de un solo uso (operacion `caja.retiro`), ligado a operador, sucursal y monto. `api_validar_admin` exige motivo y emite el token. El permiso del autorizador se revalida AL CONSUMIR, no solo al emitir. |
| CAJA-002 | Si | Corregido (parcial por politica) | `Pago` y `PagoCxC` llevan FK `turno_caja`; `procesar_venta_service` y el abono de CxC lo resuelven con `turno_abierto_de()`. `calcular_esperado()` usa el vinculo exacto y solo cae a la heuristica usuario+fecha para filas historicas. **La politica de rechazo sigue abierta** - ver abajo. |
| CAJA-003 | Si | Corregido | `caja.operar` server-side en las 6 vistas (`requiere_permiso_json` / `requiere_permiso_local`), y `caja.operar` incorporado a `PERMISOS_CAJERO_DEFAULT`. |
| CAJA-004 | Si | Corregido | `cajas_en_alcance(request)` / `turnos_en_alcance(request)` acotan por sucursal. Los resuelven index, apertura, movimiento, historial y detalle. Un recurso ajeno da 404. `es_admin()` recibe la sucursal: sin ella, un rol de A habilitaba el gate en B. |
| CAJA-005 | Si | Corregido | `TurnoCaja.cerrar()` recarga bajo `select_for_update()` y verifica el estado dentro del lock; un segundo cierre concurrente pierde y no reescribe el corte. |
| CAJA-006 | Si | Corregido | Fondo de apertura y monto contado rechazan negativos con 400. |
| CAJA-007 | Si | Corregido | `TurnoCaja` y `MovimientoCaja` son de solo lectura en Admin. `Caja` sigue editable: dar de alta o desactivar una caja es configuracion, no un hecho de efectivo. |
| CAJA-008 | Si | Corregido | `Caja.origen_id` (UUID, unico, no editable). El productor lo emite y el receptor cloud resuelve por el; el nombre pasa a ser un atributo que se actualiza. |
| CAJA-009 | Si | Corregido | `_buscar_turno_abierto` filtra `estado='ABIERTO'`: un movimiento atrasado ya no se cuelga de un turno cerrado ni corrompe su corte. |
| CAJA-010 | Si | Corregido | Un `turno_id` explicito MANDA (antes solo se miraba si el usuario no tenia turno propio) y solo lo usa quien administra caja en esa sucursal. Fuera de alcance: 404. |
| CAJA-011 | Si | Corregido | La plantilla decidia con `request.user.rol === 'ADMIN'`; ahora recibe `puede_administrar_caja`, que es la MISMA llamada RBAC que hace el servidor. |
| CAJA-012 | Si | Corregido | JSON invalido y montos mal formados dan 400 con mensaje estable; los 500 usan `logger.exception` y ya no exponen `str(exc)`. `Http404` se re-lanza antes del `except Exception` para que un recurso ajeno salga como 404 y no como 500. |
| CAJA-013 | Si | Corregido | `if turno.monto_esperado` trataba `Decimal('0.00')` como ausencia. Ahora se compara con `is not None`: cero es un valor, NULL es la ausencia. |

## CAJA-002: lo que se hizo y lo que falta decidir

El hallazgo tenia dos mitades y solo una es tecnica.

**La mitad tecnica esta cerrada.** Antes ningun `Pago` sabia a que turno
pertenecia: `calcular_esperado()` sumaba el efectivo por coincidencia de usuario
y fecha. Dos turnos del mismo cajero en un dia se pisaban - el segundo arqueo
reclamaba el efectivo del primero. Ahora cada pago en efectivo nace con su
`turno_caja`, y el desglose usa ese vinculo.

**La mitad de politica sigue abierta.** La auditoria pide ademas *"rechazar
efectivo cuando no exista un turno operable"*. **No se implemento**, porque
frenaria las ventas de una tienda que no abrio caja - y eso es una decision del
negocio, no del codigo. Hoy `turno_abierto_de()` devuelve `None` sin fallar: la
venta procede y el pago queda sin turno, distinguible de los que si lo tienen.

Si el negocio quiere el rechazo, es un `if` en `_resolver_turno_caja`
(`apps/ventas/services/ventas_service.py`). La pregunta a responder antes es:
*una tienda sin caja abierta, ¿debe poder cobrar en efectivo?*

## Cambios de conducta observables

1. **El modal de caja pide motivo.** `/caja/api/validar-admin/` ya no devuelve
   `admin_id` sino un `token` de un solo uso; el retiro lo manda como
   `override_token`. Un `admin_id` crudo ahora se rechaza con 403.
2. **Un cajero sin `caja.operar` no entra al modulo.** El permiso ya viene en
   `PERMISOS_CAJERO_DEFAULT`, asi que una instalacion existente no se bloquea.
3. **Abrir, ver o mover la caja de otra sucursal devuelve 404.**
4. **Un admin que indica `turno_id` registra ahi**, no en su propio turno.
5. **El arqueo de dos turnos del mismo cajero ya no se solapa.**
6. **Un turno cerrado en cero se muestra como `0.00`**, no como "sin dato".
7. **El admin de turnos y movimientos es de solo lectura.**

## Despliegue: 3 migraciones

1. **`caja.0003_caja_origen_id`** - escrita a mano en TRES pasos (columna
   nullable -> `RunPython` que asigna un UUID por fila -> `AlterField` con
   `unique`). Un campo unico con `default=uuid.uuid4` en un solo paso evaluaria
   el default UNA vez y dejaria todas las cajas con el mismo valor.
2. **`ventas.0007_pago_turno_caja`** - FK nullable.
3. **`cuentas_por_cobrar.0007_pagocxc_turno_caja`** - FK nullable.

Las FK son nullable a proposito: los pagos historicos no tienen turno conocido y
`calcular_esperado()` los sigue atribuyendo con la heuristica vieja.

## Pendiente (no bloqueante)

- **La politica de CAJA-002** (arriba). Es lo unico que requiere una respuesta
  del negocio.
- **Backfill de `turno_caja` en pagos historicos.** Se puede inferir por
  usuario+ventana del turno, pero seria reconstruir una atribucion que nunca
  existio; conviene decidirlo con datos reales delante.
- **Paginacion del historial de turnos.** Sigue cortando a 50 sin informarlo.

## Pruebas

Suite completa, serial: **624 tests, OK.**

Modulo de regresion nuevo: `apps/caja/tests/test_auditoria_caja.py` (32 tests).
La app no tenia ninguna prueba propia - era el hallazgo transversal de la
seccion "Pruebas y verificaciones ejecutadas".

**Verificacion por mutacion.** Revirtiendo CAJA-010 (que el turno propio vuelva
a ganar sobre `turno_id`), `test_el_admin_con_turno_propio_registra_donde_pidio`
falla con `2 != 1`: el gasto aterriza en el turno del admin en vez del turno de
la cajera que se pidio - exactamente el desvio silencioso que describe la
auditoria.

**Hallazgo del propio trabajo**: `get_object_or_404` contra el alcance quedaba
atrapado por el `except Exception` de las vistas y salia como 500. Un recurso de
otra sucursal se veia como una falla del servidor en vez de un 404. El test de
alcance lo detecto antes de que llegara a ninguna parte.

**Nota sobre CAJA-011.** El primer test asumia que `rol='ADMIN'` ya no concedia
nada; es falso. `apps/permisos/engine.py:es_acceso_total` mantiene ADMIN con
acceso total por una decision transitoria explicita y documentada. La
divergencia real de este hallazgo va en el otro sentido: el supervisor con
`caja.administrar` por rol custom y sin el rol legacy, a quien la UI trataba
como cajero y le pedia credenciales de otro cuando el servidor ya lo
auto-autorizaba. El test quedo escrito sobre ese caso.
