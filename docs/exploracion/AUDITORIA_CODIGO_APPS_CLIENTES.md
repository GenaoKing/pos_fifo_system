# Auditoría profunda de código - `apps/clientes`

Fecha: 2026-08-26  
Revisión inicial: `bcb8621`  
Revisión al cierre: `629e76e`  
Modo: lectura, pruebas y documentación; no se aplicaron correcciones funcionales.

Nota de concurrencia: `apps/clientes` estaba limpio al comenzar la revisión. El
workspace contenía correcciones del usuario en otras aplicaciones y, durante la
auditoría, `HEAD` avanzó de `bcb8621` a `629e76e`. Se compararon ambas revisiones:
el código funcional trazado de clientes, API, sync, cotizaciones y CxC no cambió.
Sí cambió `apps/permisos/catalogo.py`, incorporando el permiso financiero
`clientes.editar_limite_credito`; el informe usa ese contrato final y la suite se
repitió contra la revisión de cierre. Todas las ediciones concurrentes se
preservaron.

## Resumen ejecutivo

`Cliente` no es solo una libreta de contactos. La misma fila decide identidad en
ventas y cotizaciones, disponibilidad de crédito, vencimientos, cartera,
sincronización y datos personales. Actualmente esos contratos no se gobiernan en
un único límite transaccional ni tienen una autoridad uniforme.

Los riesgos más urgentes son:

- Las vistas locales de listado, búsqueda y detalle exigen login, pero no
  `clientes.ver`; entregan identificación, teléfono, dirección, notas internas,
  límite, saldo y vencidos a cualquier usuario autenticado.
- Cualquier usuario autenticado puede activar o desactivar clientes porque el
  endpoint local de estado no exige permiso. Tampoco deja auditoría.
- La API protege `PATCH` solo con `clientes.editar`, por lo que permite ampliar el
  límite de crédito sin el permiso separado `clientes.editar_limite_credito`.
- El POS local todavía escribe directamente el maestro aunque la arquitectura
  declara al cloud como fuente de verdad. El siguiente pull sobrescribe esos
  cambios, incluyendo límite y plazo, y puede volver a reprogramar cartera.
- Editar, auditar el límite y reprogramar las CxC son operaciones separadas. Si la
  auditoría o la reprogramación falla, la edición ya quedó confirmada aunque el
  cliente reciba un error.
- En el modo cloud compartido/legacy, `Cliente` no pertenece a `Negocio` y el
  queryset API no aplica scope. Dos empresas autenticadas ven y modifican el
  mismo catálogo. La arquitectura DB-por-tenant contiene este riesgo solo cuando
  cada request está efectivamente en su base aislada.
- El supuesto cliente único `CONTADO` no está protegido por base de datos. Las
  vistas locales permiten crear otro o convertir uno existente; la API puede
  modificar o borrar el genérico mediante un `PATCH` parcial/`DELETE`.

Se documentan **21 hallazgos**:

| Prioridad | Cantidad | Criterio |
| --- | ---: | --- |
| P1 | 7 | Permite acceso o mutación sin autoridad, cruza tenants, altera decisiones financieras o rompe la identidad operativa. |
| P2 | 10 | Debilita integridad, auditabilidad, disponibilidad, sincronización o rendimiento en producción. |
| P3 | 4 | Aumenta deuda, ambigüedad contractual y probabilidad de regresión. |

> **Estado (2026-08-30): P1 MITIGADO (7/7, uno parcial por alcance).** Los
> siete hallazgos P1 se verificaron contra el código y los siete resultaron
> reales. Seis están corregidos por completo; CLI-004 tiene una contención, no
> la solución de fondo. Se cerró además CLI-014 y CLI-020. Ver
> [Estado de mitigación](#estado-de-mitigación) al final.
> **Incluye 1 migración con preflight que ABORTA** ante un caso ambiguo.

La suite seleccionada terminó con **110/110 pruebas existentes aprobadas**.
`apps/clientes` aporta **0 pruebas propias**. Una batería adversarial temporal
terminó con **24/24 reproducciones confirmadas** y se retiró del workspace.
También pasaron `manage.py check` y
`makemigrations clientes --check --dry-run` sobre una base de prueba aislada.

## Alcance

Se inspeccionaron completamente:

- `apps/clientes/models.py`, `views.py`, `urls.py`, `admin.py` y `apps.py`
- las migraciones `0001` a `0005`
- `apps/clientes/tests/`, actualmente sin casos de prueba
- `templates/clientes/lista_clientes.html`

También se trazaron las fronteras relevantes en:

- serializers, permisos y `ClienteViewSet` en `apps/api/`
- promoción de clientes desde eventos y pull de maestros
- permisos de negocio y sucursal
- ventas, cotizaciones y cuentas por cobrar
- auditoría y reprogramación de vencimientos
- documentación de autoridad cloud y aislamiento multi-tenant

El núcleo de la app tiene aproximadamente **483 líneas Python**, excluyendo
migraciones y pruebas temporales. Expone seis rutas Django y un modelo. La API
REST que administra el mismo modelo vive fuera de la app, en `apps/api`.

## Hallazgos P1

### CLI-001 - Las lecturas locales omiten `clientes.ver` y exponen PII y crédito

- Tipo: autorización / confidencialidad / mínimo privilegio.
- Evidencia:
  - listado, búsqueda y detalle usan solo `@login_required`
    (`apps/clientes/views.py:39-40`, `:285-287`, `:326-330`).
  - El listado serializa cédula/RNC, teléfono, dirección, límite, notas internas,
    saldo, crédito disponible y vencido (`:49-72`).
  - La búsqueda devuelve identificación, contacto, dirección, límite, saldo y
    crédito disponible (`:297-320`).
  - El catálogo define expresamente `clientes.ver` para listar y consultar
    (`apps/permisos/catalogo.py:17`).
- Reproducción validada:
  - Un usuario autenticado sin ningún permiso recibió `200` en `/clientes/` y
    `/clientes/api/buscar/`; las respuestas contenían PII, notas y cifras de
    crédito del cliente de prueba.
- Impacto:
  - Roles cuyo trabajo no requiere cartera o datos personales pueden extraer el
    catálogo completo y las condiciones financieras.
- Recomendación:
  - Aplicar `clientes.ver` en las tres rutas y definir explícitamente qué campos
    necesita el POS frente a cartera/administración.
  - Evitar insertar el dataset completo y las notas internas en el HTML.
- Prueba de aceptación sugerida:
  - Un autenticado sin `clientes.ver` recibe 403 y ningún dato; cada rol autorizado
    obtiene solo los campos requeridos por su caso de uso.

### CLI-002 - Cualquier usuario autenticado puede activar o desactivar clientes

- Tipo: autorización / integridad / continuidad operativa.
- Evidencia:
  - `toggle_estado_cliente` solo exige login y POST
    (`apps/clientes/views.py:254-256`).
  - Cambia `activo` y guarda directamente (`:260-269`).
  - No registra autor, sucursal, motivo ni valores anterior/nuevo.
- Reproducción validada:
  - Un usuario sin roles ni permisos desactivó un cliente y recibió `200`; no se
    creó ningún registro de auditoría.
- Impacto:
  - Puede bloquearse un cliente válido para crédito/cotizaciones o reactivarse uno
    dado de baja por riesgo, sin autoridad ni trazabilidad.
- Recomendación:
  - Exigir `clientes.eliminar` para la baja o definir un permiso de estado
    específico; registrar la transición en una transacción.
- Prueba de aceptación sugerida:
  - Sin permiso se responde 403; una transición autorizada produce exactamente un
    registro de auditoría con actor, sucursal, estado previo, nuevo y motivo.

### CLI-003 - La API permite ampliar crédito sin el permiso financiero separado

- Tipo: segregación de funciones / autorización financiera.
- Evidencia:
  - El catálogo separa `clientes.editar_limite_credito` porque corregir contacto no
    debe autorizar ampliar crédito (`apps/permisos/catalogo.py:19-21`).
  - El mixin mapea todo `update`/`partial_update` a `clientes.editar`
    (`apps/api/permissions.py:165-187`).
  - `ClienteWriteSerializer` incluye `limite_credito` y lo persiste
    (`apps/api/serializers/maestros.py:300-323`, `:341-346`).
  - El control financiero sí existe en la vista Django local
    (`apps/clientes/views.py:78-101`), demostrando el contrato pretendido.
- Reproducción validada:
  - Un usuario con `clientes.editar`, pero sin
    `clientes.editar_limite_credito`, elevó el límite mediante `PATCH` y recibió
    `200`; tampoco quedó auditoría financiera.
- Impacto:
  - Un operador puede eludir el override de crédito: primero eleva el límite y
    luego realiza la venta sin dejar una excepción crediticia.
- Recomendación:
  - Autorizar por campo antes de validar/persistir; cambios de límite deben exigir
    el permiso financiero tanto en API como en Django Admin.
- Prueba de aceptación sugerida:
  - Con solo `clientes.editar`, un PATCH de contacto pasa y uno que altere límite
    devuelve 403, incluso en payload parcial o si mezcla ambos tipos de campo.

### CLI-004 - La escritura local contradice la autoridad cloud y es sobrescrita

- Tipo: consistencia distribuida / fuente de verdad / integridad financiera.
- Evidencia:
  - Crear, editar y cambiar estado escriben el ORM local directamente
    (`apps/clientes/views.py:121-132`, `:192-207`, `:268-269`).
  - La arquitectura declara al cloud fuente de verdad y no define eventos
    `CLIENTE_*` desde sucursal (`docs/ROADMAP_CLOUD.md:378-380`).
  - El flujo pendiente es hacer proxy de la escritura local a la API cloud
    (`docs/ROADMAP_PORTAL.md:577`).
  - El pull reemplaza nombre, tipo, identificación, contacto, límite, plazo,
    condiciones, notas y estado (`apps/sync/engine.py:1011-1033`).
- Reproducción validada:
  - Se editó localmente un cliente ya adoptado; `_pull_clientes` restauró los
    valores del cloud, incluido el límite de crédito.
- Impacto:
  - La interfaz confirma una decisión que desaparece en el próximo pull.
  - Límites y plazos pueden divergir entre venta, cartera local y portal; el pull
    puede disparar otra reprogramación de CxC (`apps/sync/engine.py:1035-1044`).
- Recomendación:
  - Cumplir la decisión ya tomada: toda mutación local de maestros debe pasar por
    la API cloud y refrescar la réplica. Si está offline, presentar estado
    pendiente/no editable, no confirmar una escritura sin ruta de convergencia.
- Prueba de aceptación sugerida:
  - Una edición local confirmada se observa en cloud y sobrevive al siguiente
    pull; un fallo de red no deja una copia local que aparente ser definitiva.

### CLI-005 - Editar, auditar y reprogramar cartera no es una operación atómica

- Tipo: transacciones / auditabilidad / integridad de cartera.
- Evidencia:
  - La fila se guarda antes de registrar auditoría y antes de reprogramar CxC
    (`apps/clientes/views.py:207-234`).
  - No existe `transaction.atomic()` que englobe esas tres acciones.
  - El `except Exception` devuelve 400, pero no revierte el `save()` ya confirmado
    (`:247-251`).
  - La API también guarda en `super().update()` antes de reprogramar
    (`apps/api/serializers/maestros.py:355-375`).
- Reproducción validada:
  - Forzando un fallo de `Auditoria.registrar`, la respuesta fue 400 pero el nuevo
    límite quedó en base sin evidencia.
  - Forzando un fallo de reprogramación, la respuesta fue 400 pero el nuevo plazo
    quedó confirmado sin actualizar las cuotas.
- Impacto:
  - El sistema puede mostrar error al operador y aun así mutar una decisión
    financiera, o dejar plazo del cliente y vencimientos en desacuerdo.
- Recomendación:
  - Encapsular lock, validación, actualización, auditoría y reprogramación en un
    servicio transaccional único, con idempotencia donde intervenga el cloud.
- Prueba de aceptación sugerida:
  - Un fallo inyectado en auditoría o reprogramación revierte todos los cambios;
    el éxito confirma exactamente una edición y su evidencia.

### CLI-006 - El catálogo de clientes no está aislado por negocio en base compartida

- Tipo: aislamiento multi-tenant / confidencialidad / integridad.
- Evidencia:
  - `Cliente` no tiene FK a `Negocio` ni tenant key
    (`apps/clientes/models.py:8-169`).
  - `ClienteViewSet.get_base_queryset()` parte de `Cliente.objects.all()` sin
    scope por usuario/negocio (`apps/api/views/maestros.py:406-425`).
  - `cedula_rnc` y `origen_cloud_id` son únicos globalmente dentro de la base
    (`apps/clientes/models.py:32-38`, `:138-145`).
  - La limitación está reconocida: los maestros no tienen scope y el cloud
    compartido es de facto single-tenant (`docs/RBAC_PERMISOS.md:228`, `:301`).
- Reproducción validada:
  - Usuarios de dos `Negocio` distintos, cada uno con `clientes.ver`, recibieron
    por API los mismos dos clientes de ambas empresas.
- Impacto:
  - En despliegue compartido/legacy hay exposición cruzada de PII y riesgo de que
    una empresa edite o desactive el cliente de otra.
  - La unicidad global también puede impedir registrar la misma identificación en
    dos empresas independientes.
- Condición:
  - DB-por-tenant contiene el hallazgo cuando el router y el contexto de cada
    request son correctos; no corrige rutas ejecutadas fuera de ese contexto.
- Recomendación:
  - Mantener DB-por-tenant como frontera obligatoria o modelar ownership explícito;
    hacer que todo queryset falle cerrado si no hay tenant resuelto.
- Prueba de aceptación sugerida:
  - Dos tenants con datos homónimos no pueden listar, recuperar, mutar ni inferir
    existencia cruzada desde API, Admin, tareas o sync.

### CLI-007 - La identidad singleton de `CLIENTE CONTADO` no está protegida

- Tipo: invariant de dominio / disponibilidad / integridad referencial.
- Evidencia:
  - El modelo permite cualquier número de filas `tipo='CONTADO'`; no hay constraint
    condicional (`apps/clientes/models.py:13-24`, `:147-169`).
  - El helper usa `get_or_create(tipo, nombre)` y falla si hay duplicados exactos
    (`:192-203`).
  - La creación local acepta `tipo` del payload y la edición puede convertir una
    fila a `CONTADO` (`apps/clientes/views.py:121-132`, `:192-207`).
  - La API rechaza `tipo='CONTADO'` solo cuando el campo viene en el payload; un
    PATCH parcial de una fila ya CONTADO omite esa validación
    (`apps/api/serializers/maestros.py:332-339`).
  - El `ModelViewSet` conserva el `destroy` físico estándar
    (`apps/api/views/maestros.py:373-425`).
- Reproducción validada:
  - La vista local creó un segundo CONTADO y convirtió un cliente real a CONTADO.
  - Dos duplicados exactos hicieron que `get_cliente_contado()` levantara
    `MultipleObjectsReturned`.
  - La API renombró/desactivó el genérico con PATCH parcial y lo borró con DELETE
    cuando no tenía referencias.
- Impacto:
  - Cotizaciones y otros flujos que llaman el helper pueden devolver 500; ventas
    históricas pueden quedar repartidas entre varios genéricos o perder el punto
    de referencia esperado.
- Recomendación:
  - Representar contado sin cliente cuando sea posible o imponer un singleton
    inmutable mediante constraint y servicio. Bloquear create/update/delete fuera
    de ese servicio.
- Prueba de aceptación sugerida:
  - Ninguna superficie puede crear un segundo genérico, convertir una fila real,
    modificar el genérico o eliminarlo; accesos concurrentes devuelven el mismo PK.

## Hallazgos P2

### CLI-008 - Las escrituras locales omiten la validación del modelo

- Tipo: validación / integridad de datos.
- Evidencia:
  - El modelo declara choices y validadores de límite/plazo
    (`apps/clientes/models.py:19-24`, `:56-70`).
  - `objects.create()` y `save()` no ejecutan `full_clean()` automáticamente, y
    las vistas no lo llaman (`apps/clientes/views.py:121-132`, `:207`).
  - No hay constraints SQL para tipo, límite no negativo, plazo ni nombre no vacío.
- Reproducción validada:
  - La creación local persistió un tipo fuera del catálogo, nombre vacío y límite
    negativo aun teniendo validadores declarativos.
- Impacto:
  - Datos imposibles llegan a ventas, CxC, reportes y sync; otras superficies sí
    validan y producen comportamiento inconsistente.
- Recomendación:
  - Centralizar validación en servicio/modelo y añadir constraints SQL para los
    invariants que deban sobrevivir cualquier writer.
- Prueba de aceptación sugerida:
  - Django, API, Admin, sync y escrituras directas autorizadas aplican el mismo
    contrato; la base rechaza estados financieros imposibles.

### CLI-009 - Cédula/RNC no tiene formato canónico ni validación real

- Tipo: identidad / deduplicación / calidad de datos.
- Evidencia:
  - El campo solo limita longitud y unicidad literal
    (`apps/clientes/models.py:32-39`).
  - La API afirma “se valida formato básico”, pero no implementa
    `validate_cedula_rnc` (`apps/api/serializers/maestros.py:283-298`, `:326-353`).
  - El push resuelve por igualdad literal (`apps/api/views/sync.py:657-664`) y el
    pull adopta usando el mismo valor (`apps/sync/engine.py:990-999`).
- Reproducción validada:
  - La API aceptó una identificación alfabética sin formato.
  - El mismo número con y sin guiones coexistió como dos clientes.
- Impacto:
  - Una persona puede fragmentarse en varias carteras/ventas; o una corrección de
    formato puede adoptar/crear una fila distinta.
- Recomendación:
  - Separar valor normalizado de presentación y validar cédula/RNC dominicana según
    tipo, permitiendo una excepción explícita cuando la identificación no exista.
- Prueba de aceptación sugerida:
  - Variantes de espacios/guiones/case resuelven la misma identidad y valores
    inválidos se rechazan igual en todas las superficies.

### CLI-010 - La identidad de origen puede quedar a medias y degradarse al borrar sucursal

- Tipo: identidad de sync / integridad referencial.
- Evidencia:
  - `origen_sucursal` y `origen_id_local` son nulos de forma independiente
    (`apps/clientes/models.py:112-126`).
  - El constraint solo exige unicidad cuando ambos están presentes; no exige
    ambos-o-ninguno (`:160-168`).
  - La FK usa `SET_NULL`, dejando `origen_id_local` sin namespace al borrar la
    sucursal (`:112-120`).
- Reproducción validada:
  - `full_clean()` aceptó un cliente con solo `origen_id_local`.
  - Al borrar la sucursal, la fila conservó el ID local pero perdió la mitad de
    su identidad estable.
- Impacto:
  - Reintentos o migraciones no pueden demostrar de qué origen vino el ID; la
    deduplicación puede crear otra fila o enlazar un hecho al cliente incorrecto.
- Recomendación:
  - Añadir constraint ambos-o-ninguno y conservar una identidad histórica
    inmutable aunque la sucursal deje de operar; evitar borrar el namespace.
- Prueba de aceptación sugerida:
  - Estados parciales fallan en base y retirar una sucursal no altera la clave de
    correlación de clientes históricos.

### CLI-011 - La API y la creación local no dejan auditoría de mutaciones

- Tipo: auditabilidad / no repudio.
- Evidencia:
  - `ClienteViewSet` no sobreescribe create/update/destroy para auditar
    (`apps/api/views/maestros.py:373-425`).
  - Crear y cambiar estado local tampoco llaman `Auditoria.registrar`
    (`apps/clientes/views.py:104-157`, `:254-282`).
  - Editar local solo audita cambios del límite, no identidad, contacto, notas,
    estado, tipo o plazo (`:192-234`).
- Reproducción validada:
  - Crear local, desactivar y ampliar límite por API dejaron cero eventos de
    auditoría del cliente.
- Impacto:
  - No puede reconstruirse quién modificó datos personales, riesgo de crédito o
    disponibilidad, ni desde qué sucursal.
- Recomendación:
  - Unificar mutaciones en un servicio que emita evidencia append-only con diffs
    mínimos, actor, tenant, sucursal, origen y correlación.
- Prueba de aceptación sugerida:
  - Cada create/update/baja autorizado produce un único evento tras commit; un
    rollback no produce ninguno.

### CLI-012 - La auditoría de límite local no atribuye la sucursal

- Tipo: trazabilidad / atribución.
- Evidencia:
  - `Auditoria.registrar` recibe usuario, IP y objeto, pero no `sucursal`
    (`apps/clientes/views.py:209-224`).
- Reproducción validada:
  - Con una sucursal resuelta y asignación local válida, el evento de límite quedó
    con `sucursal_id=NULL`.
- Impacto:
  - En una empresa con varias cajas no puede atribuirse la decisión financiera al
    punto operativo que la ejecutó.
- Recomendación:
  - Pasar la sucursal autorizada/resuelta dentro del servicio transaccional y
    validar que pertenezca al mismo tenant/negocio.
- Prueba de aceptación sugerida:
  - El evento de cambio de límite registra siempre tenant, sucursal, actor y diff
    correctos, también desde API y tareas.

### CLI-013 - `DELETE` físico contradice “dar de baja” y produce 500 con referencias

- Tipo: ciclo de vida / disponibilidad / semántica API.
- Evidencia:
  - El catálogo describe `clientes.eliminar` como “Dar de baja clientes”
    (`apps/permisos/catalogo.py:22`).
  - `ClienteViewSet` usa el destroy físico de DRF sin traducción de dominio
    (`apps/api/views/maestros.py:373-425`).
  - Ventas, cotizaciones y CxC protegen la FK, por lo que Django levanta
    `ProtectedError` si existe historia.
- Reproducción validada:
  - DELETE eliminó una fila sin referencias.
  - La misma operación contra un cliente con venta propagó `ProtectedError` como
    error interno no controlado.
- Impacto:
  - El mismo permiso a veces borra irreversiblemente y a veces devuelve 500; el
    consumidor no recibe un conflicto de dominio accionable.
- Recomendación:
  - Implementar baja lógica idempotente; si se conserva una eliminación física
    excepcional, separarla por permiso y responder 409 ante referencias.
- Prueba de aceptación sugerida:
  - `clientes.eliminar` siempre desactiva y audita; repetir es idempotente y la
    historia permanece enlazada.

### CLI-014 - Los errores internos se devuelven literalmente al navegador

- Tipo: manejo de errores / divulgación de implementación.
- Evidencia:
  - Crear, editar y toggle capturan `Exception` y retornan `str(e)` al cliente
    (`apps/clientes/views.py:153-157`, `:247-251`, `:278-282`).
- Reproducción validada:
  - Una excepción inyectada con texto sensible fue devuelta completa en el JSON
    con estado 400.
- Impacto:
  - Puede exponer nombres de campos, restricciones, valores o detalles de base;
    además etiqueta fallos del servidor como errores del usuario.
- Recomendación:
  - Mapear excepciones de dominio conocidas; registrar el error inesperado con
    correlación y responder un mensaje genérico 500.
- Prueba de aceptación sugerida:
  - Una excepción inesperada genera 500 opaco con correlation ID y detalle solo
    en logs protegidos.

### CLI-015 - La ruta de detalle siempre falla por plantilla inexistente

- Tipo: disponibilidad / ruta incompleta.
- Evidencia:
  - La ruta `<int:cliente_id>/` está publicada
    (`apps/clientes/urls.py:11`).
  - La vista renderiza `clientes/detalle_cliente.html`
    (`apps/clientes/views.py:326-341`).
  - En `templates/clientes/` solo existe `lista_clientes.html`.
- Reproducción validada:
  - Un GET autenticado a un cliente válido levantó
    `TemplateDoesNotExist: clientes/detalle_cliente.html`.
- Impacto:
  - Cualquier enlace o consumidor de la ruta recibe 500 después de consultar
    cliente, ventas y cotizaciones.
- Recomendación:
  - Implementar la plantilla con autorización/campos mínimos o retirar la ruta y
    sus contratos hasta que exista.
- Prueba de aceptación sugerida:
  - La ruta autorizada responde 200 con historial limitado; sin permiso responde
    403 y un ID ajeno al tenant no se distingue de inexistente.

### CLI-016 - El listado y la búsqueda tienen N+1 financieros severos

- Tipo: rendimiento / disponibilidad.
- Evidencia:
  - El listado no pagina y recorre todo el catálogo
    (`apps/clientes/views.py:43-50`).
  - Por cliente calcula resumen CxC, conteo de ventas y suma de ventas
    (`:51-68`, `apps/clientes/models.py:180-190`).
  - La búsqueda llama `_resumen_credito(c)` dos veces por cliente
    (`apps/clientes/views.py:305-320`).
- Reproducción validada:
  - Dos clientes requirieron al menos 11 queries en el listado.
  - Dos resultados de búsqueda requirieron al menos 13 queries; el crecimiento es
    lineal y vuelve a calcular el mismo resumen.
- Impacto:
  - Un catálogo mediano multiplica agregaciones sobre ventas, cuentas y pagos,
    aumenta latencia y puede saturar SQL Server.
- Recomendación:
  - Paginar, proyectar campos mínimos y calcular agregados en consultas agrupadas
    o un servicio batch. En búsqueda, no entregar cartera salvo necesidad explícita.
- Prueba de aceptación sugerida:
  - El número de queries queda acotado para 1, 10 y 100 clientes, con límites de
    página y tiempos medidos sobre SQL Server.

### CLI-017 - El Admin permite mutar campos internos sin el contrato de dominio

- Tipo: superficie administrativa / integridad / segregación.
- Evidencia:
  - `ClienteAdmin` solo configura lista, filtros y búsqueda; todos los campos del
    modelo permanecen editables por defecto (`apps/clientes/admin.py:5-10`).
  - Eso incluye límite, plazo, tipo, estado y las identidades internas
    `origen_sucursal`, `origen_id_local` y `origen_cloud_id`.
  - No usa el permiso financiero separado, el servicio de reprogramación ni la
    auditoría de clientes.
- Impacto:
  - Una corrección administrativa puede romper correlación cloud, saltarse la
    reprogramación de cartera o modificar crédito sin evidencia de dominio.
- Recomendación:
  - Hacer read-only los campos de identidad sync y canalizar cambios financieros
    por el mismo servicio autorizado/transaccional; revisar permisos del Admin.
- Prueba de aceptación sugerida:
  - Admin no puede alterar identidades de sync; toda edición de límite/plazo aplica
    permiso, auditoría y efectos de cartera exactamente igual que la API.

## Hallazgos P3

### CLI-018 - La interfaz muestra acciones aunque el usuario no pueda ejecutarlas

- Tipo: UX de autorización / exposición de superficie.
- Evidencia:
  - La plantilla siempre muestra crear, editar y activar/desactivar
    (`templates/clientes/lista_clientes.html:23`, `:158-163`).
  - No usa el tag de permisos para esas acciones.
- Impacto:
  - Usuarios legítimos reciben controles que terminan en 403; en toggle, el botón
    además expone una operación que hoy sí está desprotegida.
- Recomendación:
  - Ocultar/deshabilitar por permiso efectivo sin tratar la UI como control de
    seguridad; el backend debe seguir fallando cerrado.
- Prueba de aceptación sugerida:
  - La matriz de roles controla presencia de botones y los endpoints rechazan
    llamadas directas no autorizadas.

### CLI-019 - Dos superficies CRUD mantienen contratos distintos

- Tipo: arquitectura / consistencia de validación.
- Evidencia:
  - Django expone `/clientes/...` (`apps/clientes/urls.py:6-14`) y DRF expone
    `/api/v1/maestros/clientes/...` (`apps/api/views/maestros.py:383-389`).
  - La primera acepta CONTADO/valores inválidos pero controla el permiso de límite;
    la segunda valida tipo/rangos pero omite ese permiso financiero.
  - Solo una intenta auditar límite; ambas reprograman plazo en lugares distintos.
- Impacto:
  - Corregir una superficie no corrige la otra y el comportamiento depende del
    cliente usado, no del dominio.
- Recomendación:
  - Un servicio de aplicación único debe poseer invariants, autorización por campo,
    auditoría y efectos; vistas/serializers solo adaptan transporte.
- Prueba de aceptación sugerida:
  - Una tabla contractual ejecutada contra Django, API y Admin produce los mismos
    estados, permisos y efectos.

### CLI-020 - La app no tiene pruebas propias

- Tipo: cobertura / regresión.
- Evidencia:
  - `apps/clientes/tests/` solo contenía `__init__.py` al iniciar.
  - La lógica depende de pruebas indirectas en API, ventas, CxC, sync y
    cotizaciones; no había casos para sus seis rutas ni sus invariants.
- Impacto:
  - Los defectos de permisos, template, atomicidad, N+1 y CONTADO coexistían con
    una suite global seleccionada en verde.
- Recomendación:
  - Convertir las reproducciones de este informe en pruebas permanentes, separadas
    por modelo, vistas, API/sync y queries.
- Prueba de aceptación sugerida:
  - Cobertura explícita de cada permiso, rollback, singleton, identidad, pull y
    presupuesto de queries, usando el motor soportado en CI.

### CLI-021 - Hay índices y código que sugieren responsabilidades dispersas

- Tipo: mantenibilidad / esquema.
- Evidencia:
  - `cedula_rnc` tiene `unique=True`, que ya crea índice, y además un índice
    explícito (`apps/clientes/models.py:32-38`, `:152-156`).
  - Agregados de compras viven como propiedades del modelo y ejecutan consultas
    implícitas (`:180-190`), mientras el resumen financiero vive en CxC y la vista
    ensambla ambos por fila.
- Impacto:
  - El índice redundante aumenta costo de escritura/almacenamiento según backend;
    las propiedades ocultan I/O y facilitan nuevos N+1.
- Recomendación:
  - Confirmar el índice efectivo en SQL Server antes de migrar y retirar solo si
    es redundante. Mover agregados a queries/servicios batch explícitos.
- Prueba de aceptación sugerida:
  - El esquema tiene una sola estrategia de índice por identidad y el código deja
    visible cuándo una operación hará I/O agregado.

## Observaciones transversales

### Clientes inactivos no se tratan igual en todos los consumidores

CxC exige un cliente real activo para crédito
(`apps/cuentas_por_cobrar/services.py:86-88`), y el GET de cotizaciones ofrece
solo activos (`apps/cotizaciones/views.py:61`). Sin embargo, el POST de cotización
resuelve cualquier ID sin comprobar `activo` (`apps/cotizaciones/views.py:102-107`).
Se registra como evidencia transversal para la auditoría de cotizaciones, no como
hallazgo adicional de clientes, porque la decisión ocurre en aquella app.

### La promoción desde sucursal revisó parcialmente la autoridad cloud

`_resolver_o_crear_cliente()` permite que un cliente nazca local y sea promovido
al cloud para no perder ventas/CxC, pero declara que el cloud sigue siendo autor
de las ediciones (`apps/api/views/sync.py:626-650`). Esa excepción es razonable
para continuidad operativa, pero vuelve más importante distinguir “alta local
pendiente de adopción” de “edición definitiva”. Hoy la UI no muestra ese estado.

## Validación ejecutada

Entorno:

- Python: `C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe`
- settings temporal con base aislada:
  `test_pos_fifo_auditoria_clientes_20260826`
- Django system check: sin incidencias
- deriva de migraciones de `clientes`: ninguna

Suite existente seleccionada (**110/110 OK**, 40.768 s):

- `apps.clientes`
- permisos API de clientes y negocio
- serializer extendido y pull keyset
- upsert/push/pull de clientes y engine de sync
- ventas y crédito/CxC
- exports de estado de cuenta
- PDF de cotizaciones

Suite adversarial temporal (**24/24 OK**, 4.194 s):

- acceso a listado/búsqueda sin `clientes.ver`
- toggle sin permiso ni auditoría
- template de detalle inexistente
- creación/conversión/duplicación de CONTADO
- validación omitida y mensajes internos
- fallos inyectados en auditoría y reprogramación
- atribución de sucursal en auditoría
- bypass API del permiso de límite
- mutación y borrado API del genérico
- `ProtectedError` en destroy
- cruce de catálogos entre negocios
- identificación inválida/no normalizada
- estados parciales de identidad de origen y borrado de sucursal
- sobrescritura de edición local por pull
- presupuesto de queries de listado y búsqueda

Las pruebas temporales y los settings temporales se retiraron después de obtener
la evidencia. No se cambió código de producción, migraciones, templates ni tests
permanentes.

## Orden de remediación sugerido

1. Cerrar CLI-001, CLI-002 y CLI-003: autorización uniforme y permiso financiero
   por campo en toda superficie.
2. Definir un servicio transaccional único para CLI-005, CLI-011 y CLI-012.
3. Hacer cumplir el contrato cloud de CLI-004 antes de seguir ampliando el CRUD
   local; mantener explícita la excepción de alta/promoción offline.
4. Blindar el singleton CONTADO y la baja lógica (CLI-007 y CLI-013).
5. Confirmar la frontera DB-por-tenant y añadir pruebas fail-closed para CLI-006.
6. Endurecer identidad/constraints (CLI-008 a CLI-010) con migración de datos
   previamente auditada.
7. Resolver disponibilidad/rendimiento (CLI-014 a CLI-017) y convertir las 24
   reproducciones en regresiones permanentes.

Este orden no implica corregir dentro de esta auditoría. Cada bloque requiere un
plan separado, revisión de datos reales y reejecución contra los cambios
concurrentes del usuario.

---

# Estado de mitigación

Fecha: 2026-08-30. Verificación previa: se releyó cada hallazgo P1 contra el
código citado. **Los siete son reales** — ninguno resultó falso positivo.

## Resumen por hallazgo

| ID | Real | Estado | Dónde quedó la corrección |
|---|---|---|---|
| CLI-001 | Sí | Corregido | Listado, búsqueda y detalle exigen `clientes.ver`. Sin él: redirect en las vistas HTML, 403 en la búsqueda JSON, y ningún dato en el cuerpo. |
| CLI-002 | Sí | Corregido | `toggle_estado_cliente` exige `clientes.eliminar` —desactivar **es** la baja: el modelo no borra, inactiva— y la transición se audita dentro de la misma transacción, con actor, sucursal y valores anterior/nuevo. |
| CLI-003 | Sí | Corregido | Autorización **por campo** en `ClienteWriteSerializer.validate()`: cambiar `limite_credito` exige `clientes.editar_limite_credito` además de `clientes.editar`. Cubre el payload mixto, y reenviar el mismo valor no cuenta como decisión financiera. |
| CLI-004 | Sí | **Contenido, no resuelto** | Ver abajo. |
| CLI-005 | Sí | Corregido | `transaction.atomic()` + `select_for_update()` envuelven lectura, validación, escritura, auditoría y reprogramación de cartera. |
| CLI-006 | Sí | **Sin cambios** | Ver abajo. |
| CLI-007 | Sí | Corregido | Índice único parcial `cliente_contado_singleton`, `get_cliente_contado()` sin carrera, y el genérico es inmutable e imborrable desde la API (`_proteger_generico` + `perform_destroy`). |
| CLI-014 | Sí | Corregido | Los errores internos van al log; el navegador recibe un mensaje estable. |
| CLI-020 | Sí | Corregido | La app no tenía pruebas propias. Ahora tiene 24. |

## CLI-004: qué se hizo y qué falta

El hallazgo es real y su solución de fondo es una **feature, no un arreglo**:
que toda mutación local de maestros pase por la API cloud y refresque la
réplica. Eso es lo que el roadmap ya decidió y no está construido.

Lo que sí se puede hacer sin construirla —y es la mitad que importa— es **dejar
de confirmarle al operador una decisión que va a desaparecer**. Editar un
cliente adoptado por el cloud devolvía 200, y el siguiente `_pull_clientes`
restauraba nombre, límite, plazo y condiciones, pudiendo además disparar otra
reprogramación de cartera.

Ahora, **con `SYNC_ENABLED` y sobre un cliente que tiene `origen_cloud_id`**, la
edición local devuelve **409** y le dice al operador dónde editarlo. Los
clientes nacidos en la sucursal siguen siendo editables ahí hasta que el cloud
los adopte, y una instalación standalone no cambia en nada.

**Lo que falta es el proxy de escritura**, y queda anotado en el TODO.

## CLI-006: por qué no se tocó

El aislamiento de `Cliente` por negocio en base compartida requiere modelar
ownership explícito: FK a `Negocio`, migración con backfill, y revisar la
unicidad global de `cedula_rnc` —que hoy impediría registrar la misma
identificación en dos empresas independientes—. Es un cambio de modelo de
datos, no un gate.

La contención real es DB-por-tenant, que ya es la arquitectura vigente, y esa
frontera se endureció en la mitigación de `apps/negocios` (NEG-001): un request
sin tenant resuelto ahora falla cerrado en vez de ampliar el queryset. El
despliegue compartido/legacy sigue sin aislamiento y está anotado.

## Cambios de conducta observables

1. **Un usuario sin `clientes.ver` pierde el listado, la búsqueda y el
   detalle.** Si algún rol operativo los usaba sin tener el permiso, hay que
   agregárselo — el catálogo ya lo definía para esto.
2. **Desactivar un cliente exige `clientes.eliminar`** y deja auditoría.
3. **Subir el límite de crédito por el portal exige el permiso financiero.**
   Un PATCH que mezcle teléfono y límite se rechaza entero.
4. **El cliente CONTADO no se edita ni se borra desde el portal**, ni siquiera
   por un ADMIN.
5. **Con sync activo, editar un cliente del cloud devuelve 409** en el POS
   local.
6. **Los errores de edición ya no muestran el texto de la excepción.**

## Despliegue: 1 migración, con preflight que puede abortar

**`clientes.0006_cliente_contado_singleton`** — impone el singleton, precedido
de una consolidación.

La consolidación distingue dos casos que la constraint no distingue, porque
tienen consecuencias opuestas:

- **Duplicados del genérico** (nombre `CLIENTE CONTADO`, sin cédula/RNC): son
  intercambiables por definición. Se consolidan sobre el más antiguo,
  repuntando ventas, cuentas por cobrar y cotizaciones, y las sobrantes se
  eliminan.
- **Un cliente REAL convertido a CONTADO** (con nombre propio o identificación):
  la migración **ABORTA** con el detalle de cada fila. Reasignar sus ventas al
  genérico falsificaría la historia comercial, y esa no es una decisión que un
  script deba tomar. Hay que corregir su `tipo` a PERSONAL/CORPORATIVO antes.

> **Antes de desplegar conviene comprobarlo:**
> `Cliente.objects.filter(tipo='CONTADO').values('id', 'nombre', 'cedula_rnc')`.
> Con más de una fila que no sea el genérico limpio, la migración se detiene.

## Lo que no se tocó

P2 restantes: CLI-008 (las escrituras locales omiten `full_clean`), CLI-009
(cédula/RNC sin formato canónico ni validación), CLI-010 (la identidad de origen
puede quedar a medias), CLI-011 (la API y la creación local no auditan
mutaciones), CLI-012 (la auditoría de límite local no atribuía sucursal — **ya
cubierto**: ahora se pasa `sucursal` en el cambio de estado, falta en la
edición), CLI-013 (`DELETE` físico produce 500 con referencias), CLI-015 (la
ruta de detalle falla por plantilla inexistente), CLI-016 (N+1 financieros en
listado y búsqueda), CLI-017 (el Admin muta campos internos sin contrato).

P3: CLI-018 (la UI muestra acciones no ejecutables), CLI-019 (dos superficies
CRUD con contratos distintos), CLI-021 (responsabilidades dispersas).

**CLI-015 merece atención pronto:** la ruta de detalle apunta a una plantilla
que no existe, así que hoy es un 500 garantizado. El gate de `clientes.ver` que
se agregó no lo arregla, solo lo hace inalcanzable para quien no tenga el
permiso.

## Pruebas

Suite completa, serial: **990 tests, OK.**

Módulo de regresión nuevo: `apps/clientes/tests/test_auditoria_clientes.py`
(24 pruebas).

**Verificación por mutación.** Revertidos tres hallazgos, cinco pruebas fallan:

- Sin `transaction.atomic` (CLI-005), el límite queda en `99999.00` tras un
  fallo de auditoría y el plazo en `90` tras un fallo de reprogramación — las
  dos reproducciones textuales.
- Sin el gate del toggle (CLI-002), `200 != 403`.
- Sin la autorización por campo (CLI-003), `200 != 403`, incluido el payload
  mixto.
