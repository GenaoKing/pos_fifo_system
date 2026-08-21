# Auditoría profunda de código - `apps/cuentas_por_cobrar`

Fecha: 2026-08-20  
Revisión auditada: `e1cd524`  
Modo: lectura, pruebas y documentación; no se aplicaron correcciones funcionales.

> **Estado (2026-08-21): MITIGADO.** Los 16 hallazgos se verificaron contra el
> código y los 16 resultaron reales. Todos están corregidos, con pruebas de
> regresión. Ver [Estado de mitigación](#estado-de-mitigación) al final.
> **Incluye 2 migraciones y un cambio de contrato del POS** (el override de
> crédito ya no viaja como `admin_override_id`).

## Resumen ejecutivo

`apps/cuentas_por_cobrar` tiene una base transaccional mejor que otros módulos
revisados: el registro y la anulación de abonos bloquean filas, la aplicación a
cuotas es FIFO, existen auditorías de negocio y los eventos de sincronización se
encolan dentro de las transacciones principales. La suite existente también es
útil y quedó completamente verde.

Sin embargo, la revisión profunda encontró riesgos importantes en los bordes del
módulo: la autorización para exceder límites de crédito se representa con un ID
de administrador falsificable; el límite puede superarse con dos ventas
concurrentes; el portal local no aplica alcance por sucursal; cualquier usuario
autenticado puede cambiar el límite de un cliente; y la réplica cloud de un
abono actualiza la cuenta, pero no sus cuotas. También hay inconsistencias
contables al anular ventas con abonos ya aplicados y ausencia de idempotencia en
el comando de cobro.

Se documentan **16 hallazgos**:

| Prioridad | Cantidad | Criterio |
| --- | ---: | --- |
| P1 | 9 | Puede permitir crédito o cobros no autorizados, duplicar movimientos, romper integridad financiera o producir divergencia local/cloud. |
| P2 | 5 | Puede producir condiciones comerciales incorrectas, reportes engañosos o una superficie de seguridad explotable. |
| P3 | 2 | Afecta completitud documental, diagnóstico y robustez de la interfaz. |

La suite seleccionada ejecutó **54 pruebas existentes, 54 aprobadas**. Además,
se ejecutaron **13 reproducciones adversariales temporales, 13 aprobadas**: en
este segundo grupo “aprobada” significa que la prueba logró demostrar la
condición de riesgo esperada. Ninguna de esas reproducciones quedó incorporada
al código productivo ni al repositorio.

## Alcance

Se inspeccionaron completamente:

- `apps/cuentas_por_cobrar/models.py`
- `apps/cuentas_por_cobrar/services.py`
- `apps/cuentas_por_cobrar/views.py`
- `apps/cuentas_por_cobrar/admin.py`
- `apps/cuentas_por_cobrar/excel_generator.py`
- `apps/cuentas_por_cobrar/pdf_generator.py`
- `apps/cuentas_por_cobrar/urls.py`
- `apps/cuentas_por_cobrar/tests/`
- `templates/cuentas_por_cobrar/`

También se trazaron las dependencias críticas en:

- `apps/ventas/services/ventas_service.py`
- `apps/ventas/services/anulaciones_service.py`
- `apps/clientes/views.py`
- `apps/permisos/decorators.py` y `apps/permisos/engine.py`
- `apps/sync/serializers.py`
- `apps/api/views/sync.py`
- `static/js/pos/punto_venta.js`
- `templates/pos/punto_venta.html`

El núcleo revisado suma 1,623 líneas Python en siete archivos principales, dos
plantillas con 398 líneas y cuatro módulos con 37 pruebas propias. Por decisión
de negocio no se incluyeron `apps/facturacion_electronica` ni
`apps/suscripciones`.

Durante la auditoría ocurrió una limpieza o sustitución externa del workspace.
La revisión se reinició sobre el árbol limpio de `e1cd524`; no se restauraron
documentos ni cambios que desaparecieron por esa operación. Los hashes al final
identifican exactamente el snapshot funcional auditado.

## Hallazgos P1

### CXC-001 - El override del límite se autoriza con un ID de administrador falsificable

- Severidad: crítica.
- Tipo: autorización / integridad financiera.
- Evidencia:
  - `_obtener_admin_override` solo comprueba que el ID recibido pertenezca a un
    usuario activo con rol `ADMIN` o `SYSADMIN` en
    `apps/cuentas_por_cobrar/services.py:114-123`.
  - El ID llega dentro de `credito_data` y basta para omitir el rechazo del
    límite en `apps/cuentas_por_cobrar/services.py:235-244`.
  - La cuenta registra ese usuario como autorizador aunque no existe una prueba
    criptográfica o de sesión que vincule su aprobación con la venta
    (`apps/cuentas_por_cobrar/services.py:263-305`).
  - El flujo normal del POS obtiene un `admin_id` después de validar y luego lo
    vuelve a enviar como dato manipulable en
    `static/js/pos/punto_venta.js:688-708` y `:939-955`.
  - `motivo_override` es opcional y puede quedar vacío
    (`apps/cuentas_por_cobrar/models.py:138-145` y
    `apps/cuentas_por_cobrar/services.py:276-277`).
- Reproducción validada:
  - Con un cliente de límite 100 y una venta financiada por 200 se suministró el
    ID de un administrador activo, sin ejecutar su validación de credenciales y
    sin motivo. La cuenta fue creada y quedó atribuida a ese administrador.
- Impacto:
  - Un operador capaz de emitir una venta puede atribuir una excepción a un
    administrador cuyo ID conozca o adivine.
  - La auditoría registra una identidad, pero no demuestra que esa persona
    autorizó esa operación concreta.
- Recomendación:
  - Sustituir el ID por una autorización de uso único, de vida corta y ligada a
    usuario autorizador, operador, sucursal, cliente, monto y operación.
  - Consumir el token atómicamente y guardar su identificador en la auditoría.
  - Exigir motivo no vacío y permiso explícito para exceder crédito.
- Prueba de aceptación sugerida:
  - Un ID crudo, un token reutilizado, vencido, de otra sucursal o emitido para
    otro monto debe ser rechazado sin crear venta, cuenta, cuotas ni outbox.

### CXC-002 - Dos ventas concurrentes pueden superar el límite de crédito

- Severidad: crítica.
- Tipo: condición de carrera / integridad financiera.
- Evidencia:
  - `saldo_pendiente_cliente` agrega el saldo sin bloquear al cliente ni sus
    cuentas (`apps/cuentas_por_cobrar/services.py:50-58`).
  - `crear_cuenta_para_venta` lee saldo y límite, compara y luego crea la cuenta
    en pasos separados (`apps/cuentas_por_cobrar/services.py:235-288`).
  - La transacción exterior de la venta no elimina la carrera: no existe
    `select_for_update()` sobre una fila común que serialice las decisiones de
    crédito del mismo cliente.
- Reproducción validada:
  - Dos conexiones y transacciones independientes evaluaron en paralelo dos
    créditos de 60 para un cliente con límite 100 y saldo inicial cero.
  - Ambas decisiones fueron aceptadas y el saldo final quedó en 120.
- Impacto:
  - El límite funciona como validación informativa, no como invariante bajo
    concurrencia real de cajas o reintentos paralelos.
- Recomendación:
  - Bloquear la fila del cliente —o una fila dedicada de exposición crediticia—
    antes de calcular el saldo.
  - Recalcular saldo y límite después de adquirir el bloqueo y conservarlo hasta
    confirmar la cuenta.
  - Si el despliegue exige mayor concurrencia, mantener una exposición
    materializada con actualización condicional en la base de datos.
- Prueba de aceptación sugerida:
  - Dos transacciones sincronizadas para 60 + 60 sobre límite 100 deben producir
    exactamente una cuenta y un rechazo, nunca saldo 120.

### CXC-003 - Vistas y acciones locales no aplican alcance por sucursal

- Severidad: alta.
- Tipo: autorización horizontal / aislamiento operativo.
- Evidencia:
  - La lista parte de todas las cuentas y solo excluye anuladas
    (`apps/cuentas_por_cobrar/views.py:77-104`).
  - El estado de cuenta busca un cliente global por ID y todas sus cuentas
    (`apps/cuentas_por_cobrar/views.py:114-128`).
  - Cobrar, anular e imprimir reciben IDs globales y no verifican que la cuenta
    pertenezca a la sucursal efectiva del usuario
    (`apps/cuentas_por_cobrar/views.py:225-270`, `:273-304` y `:307-321`).
  - Los decoradores consultan `user.tiene_permiso(codigo)` sin pasar una
    sucursal (`apps/permisos/decorators.py:27-36` y `:49-55`).
  - `api_resumen_cliente` y `api_metodos_credito` requieren login, pero ni
    siquiera un permiso CxC (`apps/cuentas_por_cobrar/views.py:186-222`).
- Reproducción validada:
  - Un usuario con rol asignado únicamente a la sucursal A pudo listar una
    cuenta de B y registrar un abono sobre ella. El saldo de B fue modificado.
  - Un usuario autenticado sin rol CxC pudo obtener el resumen financiero de un
    cliente.
- Impacto:
  - Una caja puede consultar cartera, cobrar o reimprimir movimientos de otra
    sucursal.
  - El permiso nominal no garantiza el alcance al que fue asignado el rol.
- Recomendación:
  - Centralizar un queryset CxC con alcance por negocio y sucursal efectiva.
  - Resolver cada ID desde ese queryset; un recurso fuera de alcance debe
    responder 404 o 403 de manera consistente.
  - Pasar la sucursal al motor RBAC y aplicar el mismo scope en listados,
    resúmenes, exportaciones, cobros, anulaciones e impresión.
- Prueba de aceptación sugerida:
  - Rol A no lista, resume, exporta, cobra, anula ni imprime cuentas de B; acceso
    global solo para un rol definido explícitamente para ello.

### CXC-004 - Cualquier usuario autenticado puede elevar el límite de un cliente

- Severidad: crítica.
- Tipo: autorización cross-app / bypass de control financiero.
- Evidencia:
  - El catálogo sí define `clientes.editar` en
    `apps/permisos/catalogo.py:19`.
  - La creación y edición de clientes usan solamente `@login_required` y método
    HTTP (`apps/clientes/views.py:72-99` y `:121-156`).
  - La edición asigna directamente `data['limite_credito']` y guarda el cliente
    en `apps/clientes/views.py:150-156`.
  - La validación de CxC confía en ese campo como límite autorizado
    (`apps/cuentas_por_cobrar/services.py:235-244`).
- Reproducción validada:
  - Un usuario autenticado sin permisos modificó por POST el límite de un
    cliente hasta 999,999 y recibió respuesta exitosa.
- Impacto:
  - El usuario puede evitar completamente el flujo de override: primero eleva
    el límite y luego vende a crédito sin dejar una excepción crediticia.
- Recomendación:
  - Aplicar `clientes.editar` en el servidor y separar, si corresponde,
    `clientes.editar_limite_credito` del permiso para datos generales.
  - Auditar valor anterior, nuevo, motivo, autorizador y sucursal.
  - No aceptar el límite desde formularios que solo deban editar identidad o
    contacto.
- Prueba de aceptación sugerida:
  - Un usuario sin el permiso financiero puede editar campos permitidos, pero
    cualquier intento de cambiar el límite es rechazado y auditado.

### CXC-005 - El abono replicado en cloud no actualiza las cuotas

- Severidad: alta.
- Tipo: sincronización / divergencia de estado financiero.
- Evidencia:
  - La creación de una CxC sí serializa el snapshot de cuotas
    (`apps/sync/serializers.py:159-194`).
  - El evento de pago envía `pago_id_local`, aplicaciones y saldo de la cuenta,
    pero no el estado posterior de las cuotas
    (`apps/sync/serializers.py:199-215`).
  - Las aplicaciones contienen IDs de cuotas locales
    (`apps/cuentas_por_cobrar/services.py:459-476`), que no son claves portables
    entre bases de datos.
  - El handler cloud crea el pago y cambia `cuenta.saldo`, pero no aplica el
    monto a `CuotaCxC` (`apps/api/views/sync.py:1190-1221`).
  - En contraste, el handler de anulación sí consume un snapshot y actualiza
    cada cuota por número (`apps/api/views/sync.py:1230-1277`).
- Reproducción validada:
  - Se replicó un pago cuyo saldo de cuenta posterior era 50. La cuenta cloud
    quedó en 50, pero la suma de sus cuotas permaneció en 90 y todas siguieron
    pendientes.
- Impacto:
  - Aging, próxima cuota, detalle del cliente y cualquier reporte basado en
    cuotas pueden contradecir el saldo total de la misma cuenta.
  - Una anulación posterior parte de datos cloud que ya estaban divergentes.
- Recomendación:
  - Enviar el snapshot posterior de cuotas, identificado por `numero`, y
    aplicarlo en la misma transacción que el pago y la cuenta.
  - Validar al final `sum(cuotas.saldo) == cuenta.saldo` salvo una excepción
    contable explícita.
- Prueba de aceptación sugerida:
  - Crear CxC, aplicar abono local, replicar y comprobar igualdad exacta de
    saldo, estados y fechas de todas las cuotas en ambos lados.

### CXC-006 - Anular una venta deja abonos aplicados sin reembolso ni reversa

- Severidad: crítica.
- Tipo: integridad contable / flujo incompleto.
- Evidencia:
  - La anulación de venta llama a `anular_cuenta_por_venta` dentro de su
    transacción (`apps/ventas/services/anulaciones_service.py:109-136`).
  - Ese servicio marca la cuenta anulada y emite auditoría/outbox, pero no
    inspecciona sus pagos (`apps/cuentas_por_cobrar/services.py:633-649`).
  - `marcar_anulada` lleva cuenta y cuotas a saldo cero
    (`apps/cuentas_por_cobrar/models.py:204-209`).
  - Los registros `PagoCxC` conservan estado `APLICADO`, monto, método y
    aplicaciones.
- Reproducción validada:
  - Una cuenta con abono aplicado de 40 fue anulada a través del flujo de venta.
    Cuenta y cuotas quedaron anuladas en cero; el pago de 40 continuó aplicado y
    no se generó reembolso ni reversa.
- Impacto:
  - El sistema deja de mostrar una obligación, pero conserva dinero aplicado a
    ella sin decidir si debe devolverse, acreditarse al cliente o permanecer en
    caja.
  - Reportes de cobro, caja, estado de cuenta y auditoría pueden interpretar de
    forma distinta el mismo evento.
- Recomendación:
  - Definir la política contable antes de corregir: bloquear la anulación si hay
    abonos, anularlos LIFO y generar egresos/reembolsos, o convertirlos en saldo
    a favor formal.
  - Ejecutar venta, inventario, caja, CxC, pagos, auditoría y outbox en una sola
    unidad atómica con precondiciones explícitas.
- Prueba de aceptación sugerida:
  - La anulación con abonos no puede terminar en `cuenta=ANULADA` y
    `pago=APLICADO` sin un asiento compensatorio trazable.

### CXC-007 - Una cantidad de cuotas sin tope puede crear montos negativos

- Severidad: alta.
- Tipo: validación / disponibilidad / integridad monetaria.
- Evidencia:
  - `normalizar_cantidad_cuotas` impone mínimo 1, pero no máximo
    (`apps/cuentas_por_cobrar/models.py:72-76`).
  - El POS también limita solo por abajo
    (`templates/pos/punto_venta.html:1120-1127`).
  - `_montos_cuotas` redondea cada cuota a centavos y absorbe toda la diferencia
    en la última (`apps/cuentas_por_cobrar/services.py:195-200`).
  - El servicio crea una fila por cuota sin validar monto mínimo o positividad
    (`apps/cuentas_por_cobrar/services.py:252-288`).
  - `CuotaCxC.monto` y `saldo` no tienen validadores mínimos ni restricciones de
    base de datos (`apps/cuentas_por_cobrar/models.py:228-240`).
- Reproducción validada:
  - Financiar 1.00 en 200 cuotas produjo 199 cuotas de 0.01 y una cuota final de
    **-0.99**.
- Impacto:
  - La deuda puede contener cuotas negativas aunque el total cuadre.
  - Un número extremo puede consumir memoria, CPU y muchas escrituras dentro de
    la transacción de venta.
- Recomendación:
  - Definir un máximo de negocio y exigir que el saldo en centavos sea al menos
    la cantidad de cuotas si ninguna puede ser cero.
  - Distribuir centavos enteros: cociente y residuo, sin generar negativos.
  - Agregar `CheckConstraint` para montos y saldos no negativos.
- Prueba de aceptación sugerida:
  - Casos 1/1, 1/2, 1/100, máximo permitido y máximo + 1; ninguna cuota puede ser
    negativa y la suma debe ser exacta.

### CXC-008 - Django Admin permite saltarse servicios, auditoría e invariantes

- Severidad: alta.
- Tipo: superficie administrativa / integridad.
- Evidencia:
  - `CuentaPorCobrarAdmin` solo marca timestamps como readonly; cliente, venta,
    sucursal, importes, estado y autorización quedan editables
    (`apps/cuentas_por_cobrar/admin.py:28-33`).
  - Los admins independientes de `CuotaCxC` y `PagoCxC` no declaran campos de
    solo lectura ni bloquean alta, edición o eliminación
    (`apps/cuentas_por_cobrar/admin.py:37-47`).
  - Las protecciones de bloqueo, FIFO, LIFO, auditoría y outbox viven en
    `services.py`, no en métodos inevitables del modelo.
  - Los modelos carecen de constraints que obliguen las igualdades entre cuenta,
    cuotas y pagos (`apps/cuentas_por_cobrar/models.py:117-164`, `:228-240` y
    `:267-318`).
- Impacto:
  - Una edición administrativa puede dejar saldo de cuenta distinto a la suma
    de cuotas, alterar pagos aplicados o crear movimientos que nunca se
    sincronicen.
  - El log genérico del admin no sustituye la auditoría financiera del dominio.
- Recomendación:
  - Hacer estos registros de solo lectura en admin y exponer acciones de negocio
    que llamen servicios transaccionales.
  - Bloquear add/change/delete directos para pagos y cuotas, salvo una herramienta
    de reparación controlada con doble aprobación.
  - Añadir constraints locales donde la base pueda expresar la invariante.
- Prueba de aceptación sugerida:
  - Ningún cambio financiero desde admin puede persistir fuera de un servicio
    que produzca auditoría y evento de sincronización.

### CXC-009 - Registrar un abono no es idempotente

- Severidad: alta.
- Tipo: reintentos / duplicación financiera.
- Evidencia:
  - El endpoint recibe cuenta, método, monto, referencia y notas, pero ningún ID
    único de comando (`apps/cuentas_por_cobrar/views.py:225-239`).
  - El servicio bloquea correctamente la cuenta, pero siempre crea un nuevo
    `PagoCxC` después de aplicar el monto
    (`apps/cuentas_por_cobrar/services.py:445-489`).
  - `PagoCxC` no posee una clave de idempotencia ni restricción equivalente
    (`apps/cuentas_por_cobrar/models.py:267-318`).
- Reproducción validada:
  - Dos POST idénticos y secuenciales recibieron 200, crearon dos pagos y
    redujeron el saldo dos veces.
- Impacto:
  - Un timeout, doble envío, proxy o reintento automático puede cobrar dos veces
    aunque el operador haya intentado una sola operación.
- Recomendación:
  - Generar un UUID de operación en el cliente y exigirlo en servidor.
  - Aplicar unicidad por origen/sucursal + UUID dentro de la misma transacción;
    al repetirlo, devolver el pago original sin mutar saldos.
  - No deduplicar únicamente por fecha y monto: dos pagos reales pueden coincidir.
- Prueba de aceptación sugerida:
  - Cien reintentos concurrentes con la misma clave deben crear un pago y un
    solo efecto contable.

## Hallazgos P2

### CXC-010 - Los métodos de crédito no se resuelven por sucursal

- Severidad: media-alta.
- Tipo: configuración / aislamiento por sucursal.
- Evidencia:
  - `MetodoPlazoCredito` permite una sucursal nullable, pero `nombre` es único
    globalmente (`apps/cuentas_por_cobrar/models.py:12-70`).
  - El fallback selecciona el primer método activo del tipo por ID, sin usar la
    sucursal de la venta (`apps/cuentas_por_cobrar/services.py:89-109`).
  - Un ID explícito también se acepta globalmente si está activo.
  - `api_metodos_credito` devuelve todos los métodos activos a cualquier usuario
    autenticado (`apps/cuentas_por_cobrar/views.py:186-205`).
- Reproducción validada:
  - Con el método de cuotas de B creado antes que el de A, una venta sin ID tomó
    el método de B. La API devolvió ambos métodos a un usuario sin rol CxC.
- Impacto:
  - Una sucursal puede aplicar interés, inicial, frecuencia o plazo definidos
    para otra.
- Recomendación:
  - Resolver primero la configuración específica de `venta.sucursal` y luego un
    default global explícito; nunca “primer ID”.
  - Aplicar el mismo scope en endpoint y servicio y rediseñar la unicidad para
    representar claramente defaults globales y overrides por sucursal.

### CXC-011 - El operador puede alterar condiciones comerciales sin aprobación específica

- Severidad: media-alta, sujeta a la política comercial deseada.
- Tipo: control de negocio / trazabilidad.
- Evidencia:
  - El payload prevalece sobre el interés configurado
    (`apps/cuentas_por_cobrar/services.py:181-192`).
  - También puede cambiar cantidad, frecuencia y primera fecha
    (`apps/cuentas_por_cobrar/services.py:246-261`).
  - El POS expone esos campos como editables
    (`templates/pos/punto_venta.html:1106-1143`).
  - La auditoría de creación conserva interés y cantidad, pero no identifica la
    desviación frente al método ni registra frecuencia/primera fecha como una
    excepción aprobada (`apps/cuentas_por_cobrar/services.py:307-326`).
- Impacto:
  - Un operador puede llevar interés a cero, mover el primer vencimiento o
    escoger otra frecuencia sin que quede claro si fue política permitida o una
    excepción.
- Recomendación:
  - Definir qué campos son configurables por venta y cuáles requieren permiso o
    aprobación.
  - Guardar defaults, valores efectivos, diferencias, motivo y autorizador.
  - Validar rangos de fecha y planes permitidos por sucursal.

### CXC-012 - El estado `VENCIDA` queda obsoleto con el paso del tiempo

- Severidad: media.
- Tipo: modelo temporal / exactitud de consulta.
- Evidencia:
  - El propio modelo documenta que `estado` solo se recalcula por eventos y que
    una cuenta puede estar vencida de hecho sin estar marcada `VENCIDA`
    (`apps/cuentas_por_cobrar/models.py:179-187`).
  - La lista filtra por el valor persistido del campo
    (`apps/cuentas_por_cobrar/views.py:80-90`).
  - Vistas y exportaciones muestran `cuenta.estado` almacenado
    (`apps/cuentas_por_cobrar/views.py:25-74`,
    `apps/cuentas_por_cobrar/excel_generator.py:58-68` y
    `apps/cuentas_por_cobrar/pdf_generator.py:49-58`).
- Reproducción validada:
  - Una cuenta abierta con fecha límite pasada devolvió `esta_vencida=True`, pero
    no apareció al filtrar `estado=VENCIDA`.
- Impacto:
  - Cobranza y exportaciones pueden omitir o rotular incorrectamente deuda ya
    vencida hasta que ocurra otro evento.
- Recomendación:
  - Preferir un estado efectivo calculado en consulta/presentación o ejecutar
    una transición programada idempotente con auditoría.
  - Alinear filtro, badges, PDF, Excel y API sobre la misma definición.

### CXC-013 - La lista predeterminada puede ocultar deuda abierta detrás del tope de 300

- Severidad: media.
- Tipo: completitud de interfaz / cobranza.
- Evidencia:
  - La consulta predeterminada excluye solo `ANULADA`, por lo que incluye cuentas
    `PAGADA` (`apps/cuentas_por_cobrar/views.py:83-90`).
  - El resumen se calcula sobre toda la consulta, pero el JSON visible se corta
    silenciosamente a 300 (`apps/cuentas_por_cobrar/views.py:98-105`).
  - El orden por defecto es fecha e ID descendentes
    (`apps/cuentas_por_cobrar/models.py:156-164`).
  - La interfaz denomina el filtro vacío “Estados abiertos” aunque el backend
    incluye pagadas (`templates/cuentas_por_cobrar/lista.html:29-34`).
- Reproducción validada:
  - Una deuda abierta antigua y 300 cuentas pagadas más recientes produjeron un
    resumen con saldo 100, pero la deuda no estuvo en `cuentas_json`.
- Impacto:
  - El total indica deuda, pero el operador no puede localizarla en la lista
    inicial; la cartera más antigua puede quedar escondida indefinidamente.
- Recomendación:
  - Hacer que el default represente realmente estados abiertos y paginar en el
    servidor con total y navegación explícitos.
  - No usar un slice silencioso para datos operativos.

### CXC-014 - El Excel permite inyección de fórmulas desde datos de usuario

- Severidad: media.
- Tipo: exportación / seguridad de escritorio.
- Evidencia:
  - Nombre y documento del cliente se escriben directamente en celdas
    (`apps/cuentas_por_cobrar/excel_generator.py:38-40`).
  - La referencia de pago también se inserta sin neutralización
    (`apps/cuentas_por_cobrar/excel_generator.py:91-108`).
  - `openpyxl` interpreta cadenas iniciadas por `=` como fórmulas.
- Reproducción validada:
  - Un cliente llamado `=1+1` y una referencia `=1+2` fueron exportados como
    celdas con `data_type='f'`, no como texto literal.
- Impacto:
  - Al abrir el archivo, Excel puede evaluar contenido no confiable. Fórmulas
    más agresivas pueden inducir enlaces externos, engaño visual o acciones
    dependientes de la configuración de Office.
- Recomendación:
  - Neutralizar valores textuales que empiecen por `=`, `+`, `-` o `@` antes de
    escribirlos y forzar tipo texto.
  - Centralizar un helper de exportación segura y probar todos los campos
    provenientes de clientes, referencias y notas.

## Hallazgos P3

### CXC-015 - El PDF omite silenciosamente abonos después del número 50

- Severidad: baja-media.
- Tipo: completitud documental.
- Evidencia:
  - El generador toma únicamente `abonos[:50]` sin agregar aviso, conteo total o
    páginas adicionales (`apps/cuentas_por_cobrar/pdf_generator.py:103-122`).
- Impacto:
  - Un estado de cuenta puede parecer completo y no serlo, lo que complica
    conciliaciones o disputas con clientes de alta actividad.
- Recomendación:
  - Incluir todos los abonos con paginación, o declarar de forma prominente el
    rango y el total omitido y ofrecer un anexo completo.

### CXC-016 - Errores de entrada terminan como 500 y exponen el texto de la excepción

- Severidad: baja-media.
- Tipo: robustez / contrato HTTP.
- Evidencia:
  - El registro de pago parsea JSON y `Decimal` dentro de un `try` genérico y
    devuelve `str(exc)` con HTTP 500 (`apps/cuentas_por_cobrar/views.py:225-245`).
  - La anulación usa el mismo patrón (`apps/cuentas_por_cobrar/views.py:273-288`).
  - Fechas inválidas llegan a `date.fromisoformat` sin convertirse a un error de
    dominio (`apps/cuentas_por_cobrar/services.py:42-47`).
- Impacto:
  - JSON mal formado o tipos inválidos parecen fallas del servidor y pueden
    filtrar detalles internos útiles para reconocimiento.
- Recomendación:
  - Validar entrada con formularios/serializers o DTOs tipados; devolver 400 con
    códigos de error estables y registrar el detalle completo solo del lado del
    servidor.

## Controles que ya están bien encaminados

- `CuentaPorCobrar.venta` es `OneToOneField`, lo que evita dos cuentas normales
  para la misma venta (`apps/cuentas_por_cobrar/models.py:107-111`).
- La numeración de cuotas es única por cuenta mediante constraint
  (`apps/cuentas_por_cobrar/models.py:234-240`).
- `registrar_pago_cxc_service` bloquea la cuenta y sus cuotas, valida que el
  monto sea positivo y no supere el saldo, y aplica FIFO
  (`apps/cuentas_por_cobrar/services.py:427-479`).
- `anular_pago_cxc_service` bloquea pago, cuenta y cuotas, exige motivo y obliga
  reversa LIFO; esto protege contra reconstrucciones ambiguas.
- La reprogramación bloquea cuenta y cuotas antes de cambiar vencimientos.
- Creación, pago, reversa y anulación emiten auditoría y eventos outbox desde los
  servicios de dominio.
- La venta envuelve la creación de su CxC en la misma transacción
  (`apps/ventas/services/ventas_service.py:188-229`).
- Las pruebas existentes cubren cálculo de interés, redondeo normal, límite sin
  concurrencia, aplicación FIFO, reversa LIFO, permisos básicos, exportaciones y
  parte de la sincronización.

Estos controles reducen el riesgo de las rutas normales, pero no cierran los
hallazgos de autorización, concurrencia, idempotencia y sincronización descritos
arriba.

## Validación ejecutada

### Suite existente

Comando:

```powershell
C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py test apps.cuentas_por_cobrar apps.sync.tests.test_outbox_transaccional apps.api.tests.test_sync_extended apps.permisos.tests.test_cutover_local --keepdb --settings=config.settings_development
```

Resultado:

- 54 pruebas ejecutadas.
- 54 aprobadas.
- `System check identified no issues`.
- Tiempo: 44.674 s.
- Incluye 37 pruebas propias de CxC y 17 pruebas vecinas de outbox, sync y
  permisos.

Los mensajes de error de serialización observados durante la corrida pertenecen
a casos que simulan deliberadamente un serializador roto; la suite terminó en
`OK`.

### Reproducciones adversariales temporales

Se ejecutaron con el runner de Django sobre la base de pruebas y sin conservar
archivos en el repositorio:

| Caso | Resultado demostrado |
| --- | --- |
| Alcance por sucursal | Rol limitado a A listó y cobró una cuenta de B. |
| Resumen sin permiso | Usuario sin rol obtuvo HTTP 200 y datos financieros. |
| Método global | El fallback de A tomó el método de B y la API expuso ambos. |
| Sync de pago | Cuenta cloud cambió a 50; cuotas permanecieron sumando 90. |
| Anulación de venta | Cuenta anulada; abono de 40 permaneció `APLICADO`. |
| Vencimiento temporal | `esta_vencida=True`; filtro `VENCIDA` no devolvió la cuenta. |
| Reintento de cobro | Dos POST idénticos generaron dos abonos. |
| Fórmula en Excel | Nombre y referencia `=...` quedaron como fórmulas. |
| Cuotas extremas | 1.00 / 200 produjo última cuota de -0.99. |
| Override falsificado | ID de admin, sin prueba ni motivo, autorizó exceso. |
| Edición del cliente | Usuario sin permiso elevó el límite a 999,999. |
| Tope de lista | 300 pagadas recientes ocultaron deuda abierta antigua. |
| Carrera de límite | Dos créditos de 60 sobre límite 100 dejaron saldo 120. |

Resultado: 13 casos ejecutados, 13 condiciones de riesgo reproducidas.

## Cobertura que falta antes de corregir

La suite actual es verde porque no cubre los límites de confianza donde se
encontraron los problemas. Antes o junto con cada arreglo conviene agregar:

- Autorización de override ligada a operación, expiración, consumo único,
  sucursal, monto y motivo.
- Dos o más transacciones reales compitiendo por el límite del mismo cliente.
- Matriz A/B para list, detalle, resumen, exportación, pago, reversa e impresión.
- Permisos separados para editar datos del cliente y su límite de crédito.
- Paridad exacta local/cloud de cuenta, cuotas y pago después de aplicar y
  anular un abono.
- Política completa de anulación de venta con uno o varios abonos y con caja
  abierta/cerrada.
- Idempotencia secuencial y concurrente del cobro.
- Propiedades monetarias: cuotas no negativas, suma exacta y cotas de cantidad.
- Métodos globales y por sucursal con orden determinista.
- Estado efectivo al cruzar medianoche/fecha límite sin otro movimiento.
- Paginación con más de 300 cuentas y más de 50 abonos.
- Neutralización de `=`, `+`, `-` y `@` en cada campo de texto exportable.
- JSON, números y fechas inválidas con respuestas 400 estables.
- Inmutabilidad o acciones controladas desde Django Admin.

## Orden sugerido de corrección

1. Cerrar CXC-001 y CXC-004: hoy el límite puede evitarse sin una autorización
   financiera demostrable.
2. Corregir CXC-002 con serialización por cliente; es una invariante que debe
   sostenerse antes de aumentar concurrencia entre cajas.
3. Aplicar alcance por sucursal de extremo a extremo (CXC-003 y CXC-010).
4. Definir la política de anulación con dinero aplicado y corregir CXC-006.
5. Alinear el contrato de pago local/cloud (CXC-005) y luego agregar
   idempotencia de comando (CXC-009).
6. Blindar cuotas y montos (CXC-007) y cerrar las rutas de edición directa del
   admin (CXC-008).
7. Resolver control de términos, vencimiento efectivo y completitud de listas
   (CXC-011 a CXC-013).
8. Endurecer Excel, PDF y errores HTTP (CXC-014 a CXC-016).

Para evitar arreglos parciales, CXC-001/CXC-004, CXC-003/CXC-010 y
CXC-005/CXC-009 deberían tratarse como grupos de diseño, aunque puedan
entregarse en cambios separados y pequeños.

## Trazabilidad del snapshot

Hashes SHA-256 de los archivos más sensibles al cierre de la auditoría:

| Archivo | SHA-256 |
| --- | --- |
| `apps/cuentas_por_cobrar/models.py` | `5EC3D86FF110F7BE04574CF932AC7CADE8E72DA9E6826F59B884B935F0B359FE` |
| `apps/cuentas_por_cobrar/services.py` | `3BFD75FF0E0D1E79B6324313BDD41AEA12459F7DB499D0EBDEDDBD707D16CA8B` |
| `apps/cuentas_por_cobrar/views.py` | `0098DD2ABE9FE256828F0801C7B73752A5E3271341243C105B4BD9FA4D85845B` |
| `apps/api/views/sync.py` | `10906E0121EC3070760324501931D39978FA617250876483C35F3FD5FE25C06C` |
| `apps/ventas/services/ventas_service.py` | `9BDAC90671783B184E9062B55573DF53390B09CC4B43A45FBA705E0BB2C51E1D` |

## Cierre

La prioridad no era reescribir el módulo: los servicios de pago y reversa ya
tenían buenos cimientos. El riesgo estaba en quién podía ordenar una operación,
bajo qué sucursal y condiciones, y cómo se conservaba la misma verdad
financiera a través de concurrencia, admin, anulaciones y sync. Esos límites de
confianza ya están cerrados (ver abajo).

---

# Estado de mitigación

Fecha: 2026-08-21. Verificación previa: se releyó cada hallazgo contra el código
citado. **Los 16 son reales** — ninguno resultó falso positivo ni obsoleto.

## Resumen por hallazgo

| ID | Real | Estado | Dónde quedó la corrección |
|---|---|---|---|
| CXC-001 | Sí | Corregido | `AutorizacionOverride` (`apps/permisos/models.py`): token de un solo uso, de vida corta (5 min) y ligado a operación, operador, sucursal, monto máximo y cliente. Se consume bajo `select_for_update`. El motivo es obligatorio al emitir. |
| CXC-002 | Sí | Corregido | `crear_cuenta_para_venta` bloquea la fila del cliente (`select_for_update`) ANTES de calcular el saldo, y sostiene el lock hasta el commit de la venta. |
| CXC-003 | Sí | Corregido | `cuentas_en_alcance(request)` centraliza el scope por sucursal; listado, estado de cuenta, cobro, anulación e impresión resuelven sus IDs contra ese queryset. Un recurso fuera de alcance da 404. |
| CXC-004 | Sí | Corregido | `clientes.crear` / `clientes.editar` server-side, y el límite de crédito exige el permiso separado `clientes.editar_limite_credito`. Todo cambio de límite queda auditado con valor anterior y nuevo. |
| CXC-005 | Sí | Corregido | El evento de pago lleva el snapshot posterior de cuotas identificado por `numero` (clave portable). El handler cloud lo aplica en la misma transacción y verifica que `sum(cuotas.saldo) == cuenta.saldo`. |
| CXC-006 | Sí | Corregido | **Decisión de política**: se BLOQUEA la anulación si hay abonos aplicados. Ver la nota de abajo. |
| CXC-007 | Sí | Corregido | `MAX_CUOTAS = 60` y reparto en centavos enteros (cociente + residuo). Ninguna cuota puede quedar negativa y la suma es exacta por construcción. |
| CXC-008 | Sí | Corregido | Cuentas, cuotas y pagos son de solo lectura en el admin. El catálogo `MetodoPlazoCredito` sigue editable: es configuración, no un hecho contable. |
| CXC-009 | Sí | Corregido | `clave_idempotencia` en `PagoCxC` con constraint única parcial; un reintento con la misma clave devuelve el pago original sin mover saldos. |
| CXC-010 | Sí | Corregido | `_metodos_credito_en_alcance()` resuelve primero los métodos de la sucursal y sólo cae a los globales si no hay propios. El endpoint exige `cuentas_por_cobrar.ver`. |
| CXC-011 | Sí | Corregido | La auditoría de creación registra `desviaciones`: qué campos se apartaron del método configurado, con default y valor efectivo. |
| CXC-012 | Sí | Corregido | El filtro usa el vencimiento EFECTIVO (`fecha_limite < hoy` sobre estados abiertos) y el payload expone `estado_efectivo`. |
| CXC-013 | Sí | Corregido | El filtro vacío ahora significa realmente "estados abiertos", y el corte a 300 se informa (`cuentas_ocultas`) en vez de ser silencioso. |
| CXC-014 | Sí | Corregido | `texto_seguro()` neutraliza `=`, `+`, `-`, `@`, tab y CR antes de escribir cualquier texto de usuario en el Excel. |
| CXC-015 | Sí | Corregido | El PDF declara el truncado en el título de la sección: "mostrando los N más recientes de M; K no listados". |
| CXC-016 | Sí | Corregido | JSON y montos inválidos devuelven 400 con mensaje estable; los 500 usan `logger.exception` y ya no exponen `str(exc)`. |

## CXC-006: la política que se eligió, y por qué

La auditoría pedía **definir la política contable antes de corregir**, con tres
opciones: bloquear, revertir automáticamente en LIFO con egreso de caja, o
convertir el saldo en crédito a favor.

Se implementó **bloquear**, porque es la única que no inventa un asiento
contable. El operador revierte los abonos con `anular_pago_cxc_service` —que ya
existía, es LIFO, exige motivo y deja auditoría— y después anula la venta. Cada
paso queda trazado y la decisión sobre el dinero la toma una persona.

**Si el negocio prefiere la reversa automática o el saldo a favor, es un cambio
de una función** (`anular_cuenta_por_venta`) más el asiento correspondiente. La
decisión es del negocio, no del código.

## Cambios de conducta observables

1. **El POS debe cambiar.** El flujo de override ya no envía
   `admin_override_id`: `/caja/api/validar-admin/` ahora recibe `operacion`,
   `motivo` (obligatorio), `monto` y `cliente_id`, y devuelve un `token` que la
   venta manda como `credito.override_token`. **El JS del POS todavía envía el
   ID viejo** — hasta actualizarlo, un intento de exceder el límite se rechaza
   con `LimiteCreditoExcedidoError`, que es el lado seguro del fallo.
2. **Anular una venta con abonos aplicados devuelve 409** con el detalle de
   cuánto hay aplicado y qué hacer.
3. **Editar el límite de crédito exige un permiso propio.** Quien sólo tiene
   `clientes.editar` puede corregir un teléfono, no ampliar crédito.
4. **Cobrar y anular abonos de otra sucursal devuelve 404.** Un rol acotado a A
   ya no ve ni toca la cartera de B.
5. **Máximo 60 cuotas.** Un plan mayor se rechaza al crear la venta.
6. **El listado por defecto ya no incluye cuentas PAGADAS**, y el filtro
   VENCIDA encuentra la deuda vencida de hecho aunque su `estado` diga otra
   cosa.
7. **El admin de CxC es de solo lectura.**

## Despliegue: 2 migraciones

1. **`permisos.0005_autorizacionoverride`** — tabla nueva.
2. **`cuentas_por_cobrar.0006_pagocxc_clave_idempotencia_and_more`** — campo
   nullable + constraint única PARCIAL (sólo sobre claves presentes), así que
   los pagos históricos y los replicados por sync conviven sin conflicto.

Ninguna transforma datos existentes.

## Pendiente (no bloqueante)

- **Actualizar el POS al flujo de token.** Es el único punto donde una
  funcionalidad queda temporalmente inaccesible (el override de límite). El
  cambio en `static/js/pos/punto_venta.js` es acotado: pedir `motivo`, mandar
  `operacion`/`monto`/`cliente_id` a `validar-admin` y guardar el `token`
  devuelto en vez del `admin_id`.
- **`caja.retiro` sigue usando `admin_id` crudo.** El modelo
  `AutorizacionOverride` ya contempla esa operación (`OP_CAJA_RETIRO`), pero
  migrar el flujo de retiros pertenece a la auditoría de `apps/caja`.
- **Idempotencia concurrente del cobro.** La constraint única garantiza un solo
  pago por clave; el chequeo previo evita el trabajo. Falta el test de N
  reintentos concurrentes con la misma clave.
- **Paginación real del listado.** Hoy el corte a 300 se informa; una
  paginación server-side con navegación sigue siendo lo correcto para carteras
  grandes.

## Pruebas

Suite completa, serial: **588 tests, OK.**

Módulo de regresión nuevo:
`apps/cuentas_por_cobrar/tests/test_auditoria_cxc.py` (22 tests), más 5 tests
nuevos en `test_credito_services.py` para el flujo de autorización.

**Verificación por mutación.** Anulando el bloqueo de CXC-006,
`test_anular_una_venta_con_abonos_se_bloquea` falla — que es la reproducción de
la auditoría: cuenta anulada en cero y el abono de 40 todavía `APLICADO`.

El test `test_el_id_crudo_de_un_admin_ya_no_autoriza_nada` es el complemento
directo de la reproducción de CXC-001: envía `admin_override_id` como antes y
verifica que ahora se rechace.

**Hallazgo del propio trabajo**: el primer intento de bloquear la anulación usó
`cuenta.pagos`, pero el `related_name` real es `pagos_cxc`. El test lo detectó
antes de que llegara a ninguna parte.
