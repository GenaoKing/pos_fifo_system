# Auditoría profunda de código - `apps/negocios`

Fecha: 2026-08-26  
Revisión base: `cc103df`  
Modo: lectura, pruebas y documentación; no se aplicaron correcciones funcionales.

> **Estado (2026-08-28): P1 MITIGADO (5/5).** Los cinco hallazgos P1 se
> verificaron contra el código y los cinco resultaron reales; los cinco están
> corregidos, con pruebas de regresión. Se cerró además NEG-010 y NEG-015. Ver
> [Estado de mitigación](#estado-de-mitigación) al final.
> **Sin migraciones.** Incluye un cambio de contrato: `negocio_actual()` ya no
> puede usarse para decidir alcance global, y los builders de reportes reciben
> el scope tipado.

Nota de concurrencia: `apps/negocios` estaba limpio al comenzar la revisión. El
usuario trabajó simultáneamente en `apps/auditoria`, `apps/permisos` y
`apps/reportes`; esas correcciones se preservaron. Las reproducciones finales se
ejecutaron contra el working tree que ya incluía la nueva migración de auditoría.
Después de esa revalidación apareció además un cambio concurrente en
`apps/tenancy/tests/test_router.py`; no formó parte de la suite seleccionada y se
considera no revalidado aquí. No modifica el código funcional trazado en este
informe. Ningún archivo funcional de `apps/negocios` fue modificado.

## Resumen ejecutivo

`Negocio` es el tenant lógico que agrupa usuarios, roles, sucursales y
entitlements. Aunque la app tiene poco código, su helper `negocio_actual()` decide
el alcance de reportes, cartera, RBAC y estado de sucursales. El riesgo principal
no está en el CRUD, sino en que el helper usa `None` para estados con autoridades
opuestas: “operador global sin filtro” y “no fue posible resolver un tenant”.
Varios consumidores interpretan ambos como acceso global.

Los riesgos más urgentes son:

- Un usuario `ADMIN` sin negocio no es considerado principal global por
  `negocio_actual()`, pero el motor legacy sí le concede acceso total. Reportes,
  CxC y estado de sucursales convierten el `None` resultante en queryset sin
  scope, exponiendo todos los negocios de una base compartida.
- Cuando un `SYSADMIN` intenta acotar por un negocio inexistente o inactivo, el
  resolver devuelve `None`; esos mismos consumidores amplían la consulta a todos
  los tenants en vez de devolver 404/403 o un conjunto vacío.
- `Negocio.activo=False` no constituye una revocación uniforme. Un ADMIN del
  negocio puede volver a iniciar sesión y consultar su sucursal, mientras un rol
  granular queda bloqueado. Un token de servicio también sigue operando si la
  sucursal permanece activa.
- El estado global se concede por el campo legacy mutable
  `Usuario.rol='SYSADMIN'`, incluso a una cuenta no staff, no superusuario y sin
  identidad global del control plane.
- En DB-per-tenant se supone exactamente una fila `Negocio` self-row, pero el
  esquema admite varias. Los comandos de bootstrap/normalización eligen la
  primera por PK y dejan las demás sin reconciliar.

Se documentan **17 hallazgos**:

| Prioridad | Cantidad | Criterio |
| --- | ---: | --- |
| P1 | 5 | Puede ampliar scope entre tenants, impedir una revocación efectiva o seleccionar una identidad de tenant incorrecta. |
| P2 | 9 | Debilita ciclo de vida, auditabilidad, consistencia de identidad, validación o disponibilidad. |
| P3 | 3 | Aumenta deuda documental, dispersión de responsabilidades y riesgo de regresión. |

La suite seleccionada terminó con **110/110 pruebas existentes aprobadas**.
`apps/negocios` aporta **0 pruebas propias**. Una batería adversarial temporal
terminó con **24/24 reproducciones confirmadas** y se retiró del workspace.
También pasaron `manage.py check` y
`makemigrations negocios --check --dry-run` sobre una base de prueba aislada.

## Alcance

Se inspeccionaron completamente:

- `apps/negocios/models.py`
- `apps/negocios/utils.py`
- `apps/negocios/admin.py`
- `apps/negocios/apps.py`
- la migración `0001_initial`
- `apps/negocios/tests/`, sin casos permanentes

El núcleo contiene aproximadamente **130 líneas Python**, excluyendo migración y
pruebas temporales. Tiene un modelo, un resolver, un Admin y ninguna URL, vista,
API o servicio de ciclo de vida propio.

También se trazaron:

- el motor y los endpoints RBAC
- reportes cloud, cartera y estado de sucursales
- login legacy, autenticación DB-per-tenant y tokens de servicio
- relaciones desde usuarios, sucursales, roles y suscripciones
- `Tenant` del control plane y los comandos de bootstrap/normalización
- `ConfiguracionNegocio`, que conserva otra copia de identidad comercial
- invalidación de permisos y auditoría concurrentemente corregida

## Hallazgos P1

### NEG-001 - `None` mezcla “sin tenant” con “scope global” y expone otros negocios

- Tipo: aislamiento multi-tenant / autorización / fail-open.
- Evidencia:
  - `negocio_actual()` devuelve `None` para usuarios inactivos, huérfanos no
    globales y resoluciones imposibles (`apps/negocios/utils.py:31-52`).
  - Un `ADMIN` legacy recibe acceso total con solo estar autenticado y activo,
    aunque `negocio_id` sea nulo (`apps/permisos/engine.py:171-193`, `:248-250`).
  - CxC interpreta `negocio=None` como queryset sin filtro
    (`apps/api/views/cuentas_por_cobrar.py:53-69`).
  - Reportes interpreta `None` como “todas las sucursales”
    (`apps/api/services/reporting.py:105-122`).
  - Estado de sucursales aplica filtro solo si el negocio no es `None`
    (`apps/api/views/sucursales.py:60-68`).
- Reproducción validada:
  - Una cuenta `ADMIN`, activa, no staff, no superusuario y con
    `negocio_id=NULL` no fue reconocida como principal global.
  - Aun así `tiene_permiso('sucursales.ver')` devolvió `True` y
    `/api/v1/sucursales/status/` devolvió las sucursales de los dos negocios de
    prueba.
- Impacto:
  - Un error de aprovisionamiento o una cuenta huérfana puede convertirse en
    acceso horizontal a reportes, cartera y estado operacional.
- Recomendación:
  - El resolver debe retornar un resultado tipado: tenant resuelto, global
    explícito o error/no resuelto. No reutilizar `None` para los tres estados.
  - Ningún consumidor debe decidir acceso global a partir de ausencia de tenant;
    debe exigir una autoridad global ya verificada.
- Prueba de aceptación sugerida:
  - Un ADMIN huérfano recibe 403 en reportes, CxC y sucursales; solo una identidad
    global explícita puede pedir scope global.

### NEG-002 - Una selección inexistente o inactiva amplía el scope del SYSADMIN

- Tipo: autorización / validación / confusión de scope.
- Evidencia:
  - Para un principal global, `?negocio=<id>` busca solo filas activas y devuelve
    `.first()` o `None` (`apps/negocios/utils.py:47-52`).
  - No distingue ausencia del parámetro, negocio no encontrado y negocio
    inactivo.
  - Los consumidores globales citados en NEG-001 convierten `None` en “sin filtro”.
- Reproducción validada:
  - Un SYSADMIN solicitó `?negocio=999999` y recibió las dos sucursales, no un
    error ni cero resultados.
  - Al seleccionar expresamente el ID de un negocio inactivo también recibió las
    sucursales de ambos negocios, incluida la empresa que no había seleccionado.
- Impacto:
  - Un typo, bookmark obsoleto o tenant desactivado ensancha una consulta que el
    operador intentó acotar. Es el comportamiento opuesto a fail-closed.
- Recomendación:
  - Si se suministra `?negocio`, exigir formato válido y resolver exactamente una
    fila activa; de lo contrario responder 400/404. El scope global debe requerir
    ausencia deliberada de selector y autoridad global.
- Prueba de aceptación sugerida:
  - Inexistente/inactivo devuelve 404 y jamás ejecuta un queryset global; omitir
    el parámetro conserva la operación global solo para el operador autorizado.

### NEG-003 - Desactivar un negocio no revoca de forma uniforme

- Tipo: revocación / autenticación / continuidad operativa.
- Evidencia:
  - Para un usuario asignado, el resolver devuelve `user.negocio` sin comprobar
    `activo` (`apps/negocios/utils.py:35-40`).
  - Los roles granulares sí filtran `rol__negocio__activo=True`
    (`apps/permisos/engine.py:268-295`, `:374-387`).
  - El atajo `ADMIN`/`SYSADMIN` no revisa el estado del negocio
    (`apps/permisos/engine.py:171-193`).
  - El login legacy valida usuario y permisos, pero no `user.negocio.activo`
    (`apps/api/auth_views.py:368-391`).
  - El autenticador de tokens de sucursal solo exige usuario-servicio y sucursal
    activa (`apps/api/authentication.py:104-125`); no revisa el negocio.
- Reproducción validada:
  - Un ADMIN asignado a un negocio desactivado inició una sesión nueva y consultó
    su sucursal con 200.
  - Un operador granular equivalente quedó correctamente rechazado con 403 y no
    pudo iniciar sesión.
  - Un token de servicio de una sucursal activa siguió recibiendo 200 de sync
    después de desactivar su negocio.
- Impacto:
  - El significado de “desactivar tenant” depende del tipo de credencial. Las
    cuentas más privilegiadas y la integración operativa son precisamente las
    que pueden seguir funcionando.
- Recomendación:
  - Definir una única cadena de revocación: `Tenant`, self-row `Negocio`, usuario,
    membership, sucursal y token. Toda autenticación y autorización debe negar si
    cualquiera de los padres está inactivo.
- Prueba de aceptación sugerida:
  - Desactivar el tenant invalida inmediatamente login nuevo, refresh, access
    existente, Admin, API humana y token de sucursal; reactivar sigue un workflow
    explícito y auditado.

### NEG-004 - El rol legacy mutable `SYSADMIN` basta para ser principal global

- Tipo: autoridad de plataforma / escalada de privilegios.
- Evidencia:
  - `es_principal_global()` acepta `user.rol == 'SYSADMIN'` además de superusuario
    e identidad global (`apps/negocios/utils.py:55-70`).
  - `Usuario.rol` es un campo tenant-local editable y se conserva como legacy
    (`apps/usuarios/models.py:81-90`).
  - El Admin de usuarios expone `rol` junto a negocio, staff y superusuario
    (`apps/usuarios/admin.py:28`).
- Reproducción validada:
  - Una cuenta ordinaria con `is_staff=False`, `is_superuser=False`, sin negocio y
    sin `Identity.is_global` pudo seleccionar el negocio B únicamente por tener
    el texto legacy `SYSADMIN`.
- Impacto:
  - Una mutación local del usuario puede fabricar autoridad de plataforma sin una
    credencial global del control plane ni una asignación RBAC auditable.
- Recomendación:
  - Bajo DB-per-tenant, reconocer autoridad global solo desde la identidad/control
    plane autenticados. Retirar progresivamente `SYSADMIN` como fuente autónoma
    de autoridad en filas tenant-locales.
- Prueba de aceptación sugerida:
  - Cambiar el campo legacy no concede selección cross-tenant; una identidad
    global revocada pierde el acceso aunque la copia local conserve el string.

### NEG-005 - La cardinalidad del self-row no está protegida en DB-per-tenant

- Tipo: identidad de tenant / aprovisionamiento / integridad.
- Evidencia:
  - El modelo no tiene constraint o validación de cardinalidad
    (`apps/negocios/models.py:19-45`).
  - El diseño DB-per-tenant requiere una sola fila `Negocio` self-row
    (`docs/TENANCY_DB_PER_TENANT.md:77-82`).
  - `bootstrap_tenant` selecciona `order_by('id').first()` y actualiza esa fila
    (`apps/tenancy/management/commands/bootstrap_tenant.py:297-305`).
  - `normalizar_import_tenant` repite la selección de la primera
    (`apps/tenancy/management/commands/normalizar_import_tenant.py:178-197`).
- Reproducción validada:
  - Dos filas `Negocio` distintas pasaron `full_clean()` y coexistieron.
- Impacto:
  - Un tenant puede quedar dividido entre dos negocios locales; el bootstrap
    retitula solo el de menor PK mientras usuarios, roles o sucursales pueden
    seguir ligados al segundo.
- Condición:
  - En el modo legacy compartido varias filas son legítimas. La regla singleton
    aplica cuando el alias activo representa una base tenant dedicada.
- Recomendación:
  - Hacer que bootstrap/normalización fallen si el conteo no es 0 o 1, salvo un
    modo de reconciliación explícito. Verificar que todas las relaciones apunten
    al self-row antes de declarar el tenant operativo.
- Prueba de aceptación sugerida:
  - Una base tenant con dos filas no puede arrancar el provisioning normal; el
    diagnóstico lista cada relación y exige una decisión de consolidación.

## Hallazgos P2

### NEG-006 - Tres fuentes de identidad comercial pueden divergir

- Tipo: consistencia de datos / autoridad / operación.
- Evidencia:
  - El control plane `Tenant` mantiene `nombre`, `slug`, `rnc` y `activo`
    (`apps/tenancy/models.py:10-27`).
  - El self-row `Negocio` repite esos cuatro atributos
    (`apps/negocios/models.py:22-39`).
  - `ConfiguracionNegocio` conserva nombre/RNC por sucursal para tickets y
    configuración (`apps/configuracion/models.py:42-59`).
  - No existe FK cross-DB, versión, outbox o reconciliador entre las copias.
- Reproducción validada:
  - Después de crear `Tenant` y `Negocio` iguales, se cambió nombre, RNC y activo
    del self-row; el control plane permaneció activo con los datos anteriores.
  - Una configuración de sucursal aceptó otro nombre y otro RNC distintos de
    `Negocio`.
- Impacto:
  - Login/routing, portal, permisos y documentos impresos pueden identificar de
    forma diferente a la misma empresa. La desactivación en una copia no implica
    revocación en las demás.
- Recomendación:
  - Declarar autoridad por campo y sincronización unidireccional. La configuración
    operativa no debe duplicar identidad legal sin una razón y workflow explícitos.
- Prueba de aceptación sugerida:
  - El verificador de instalación detecta cualquier drift y el workflow de cambio
    actualiza o deriva todas las proyecciones de una única autoridad.

### NEG-007 - El ciclo de vida del tenant no deja auditoría de dominio

- Tipo: auditabilidad / no repudio.
- Evidencia:
  - `Negocio.save()` solo autogenera slug; no registra create/update/estado
    (`apps/negocios/models.py:50-64`).
  - La app no ofrece servicio o vista que envuelva las mutaciones.
  - El Admin no sobreescribe `save_model`, `delete_model` ni permisos de ciclo de
    vida (`apps/negocios/admin.py:6-12`).
  - El middleware de auditoría declara vistas operativas concretas, no el Admin de
    Negocio.
- Reproducción validada:
  - Crear, desactivar y borrar un negocio por ORM dejó cero registros de
    `Auditoria`; no existe productor de dominio al cual acudir.
- Impacto:
  - No puede reconstruirse quién cambió identidad legal, reactivó un tenant o
    inició una eliminación.
- Recomendación:
  - Introducir un servicio de ciclo de vida transaccional y auditado; restringir
    el Admin a invocarlo o hacerlo read-only para operaciones sensibles.
- Prueba de aceptación sugerida:
  - Crear, cambiar identidad, desactivar, reactivar y retirar producen eventos con
    actor global, tenant_key, motivo, diff y correlación; rollback no deja evento.

### NEG-008 - Borrar un negocio puede eliminar en cascada seguridad y entitlements

- Tipo: operación destructiva / integridad / recuperación.
- Evidencia:
  - Usuarios y sucursales usan `PROTECT`, pero roles usan `CASCADE`
    (`apps/usuarios/models.py:72-79`, `apps/sucursales/models.py:22-30`,
    `apps/permisos/models.py:58-63`).
  - Suscripción y overrides de módulos también usan `CASCADE`
    (`apps/suscripciones/models.py:58-76`, `:82-104`).
  - El Admin conserva el borrado estándar (`apps/negocios/admin.py:6-12`).
- Reproducción validada:
  - Un negocio sin usuarios/sucursales, pero con rol, suscripción y override, se
    borró; las tres configuraciones desaparecieron en cascada.
- Impacto:
  - Un tenant todavía no operativo o temporalmente sin relaciones protegidas
    puede perder configuración comercial y de seguridad sin tombstone ni
    workflow de retención.
- Recomendación:
  - Usar desactivación como operación normal; separar una purga excepcional con
    preflight, backup, aprobación, auditoría y reporte de cascadas.
- Prueba de aceptación sugerida:
  - El Admin no ofrece delete normal; la purga enumera relaciones, exige motivo y
    deja evidencia recuperable antes de borrar.

### NEG-009 - `slug` es mutable y todavía participa en identidad legacy

- Tipo: identidad / compatibilidad / sesiones.
- Evidencia:
  - El help text lo llama identificador de routing/tenant
    (`apps/negocios/models.py:23-29`).
  - El Admin lo deja editable y lo prepopula desde nombre
    (`apps/negocios/admin.py:8-12`).
  - Los JWT legacy emiten `tenant_id=user.negocio.slug`
    (`apps/api/auth_views.py:34-46`) y el payload hace el mismo fallback
    (`apps/api/auth_views.py:421-453`).
  - La arquitectura nueva declara `tenant_key`, no slug, como identificador
    técnico estable (`docs/TENANCY_DB_PER_TENANT.md:113-120`).
- Reproducción validada:
  - Tras cambiar `token-v1` a `token-v2`, los tokens nuevos emitieron el nuevo
    `tenant_id`; el token ya emitido conservó el valor anterior.
- Impacto:
  - Dos sesiones válidas de la misma empresa pueden exponer identidades distintas
    a clientes legacy, logs o integraciones.
- Recomendación:
  - Eliminar slug como claim de identidad técnica; emitir `tenant_key` estable.
    Tratar el slug restante como alias de presentación con workflow de cambio.
- Prueba de aceptación sugerida:
  - Renombrar el negocio no cambia routing, namespace ni claim estable; clientes
    legacy migran explícitamente y sin fallback ambiguo.

### NEG-010 - Un selector no numérico provoca excepción en vez de error de entrada

- Tipo: validación / disponibilidad.
- Evidencia:
  - `_query_param()` entrega texto sin validación y el resolver lo pasa a
    `filter(pk=...)` (`apps/negocios/utils.py:47-50`, `:73-78`).
- Reproducción validada:
  - `?negocio=no-es-un-id` para un SYSADMIN levantó `ValueError` al preparar el
    lookup de PK.
- Impacto:
  - Un parámetro de usuario puede convertirse en 500 y ruido de observabilidad.
- Recomendación:
  - Parsear entero positivo o usar un serializer de query params; devolver 400
    con contrato estable.
- Prueba de aceptación sugerida:
  - Vacío, texto, negativo, decimal y overflow devuelven 400 sin consultar datos ni
    revelar detalles del backend.

### NEG-011 - El RNC no es canónico ni tiene una política de unicidad

- Tipo: identidad legal / calidad de datos.
- Evidencia:
  - `rnc` es un `CharField` libre, sin validator, normalización, índice o constraint
    (`apps/negocios/models.py:30-35`).
- Reproducción validada:
  - Dos negocios guardaron el mismo RNC literal.
  - El mismo número con y sin guiones coexistió como valores distintos.
- Impacto:
  - Puede duplicarse una entidad legal, fallar una reconciliación con control
    plane o mostrarse una identidad fiscal inconsistente.
- Recomendación:
  - Definir si la unicidad aplica globalmente, por ambiente o no aplica; almacenar
    una forma normalizada y validar la presentación dominicana.
- Prueba de aceptación sugerida:
  - Variantes de formato convergen al mismo valor; la política de duplicados queda
    impuesta en base y probada durante importaciones.

### NEG-012 - Las escrituras directas evitan validadores y permiten nombre vacío

- Tipo: validación / invariant de modelo.
- Evidencia:
  - El modelo no implementa `clean()` y `save()` no llama `full_clean()`
    (`apps/negocios/models.py:19-64`).
  - Las restricciones de `CharField`/`SlugField` son principalmente de formularios,
    no constraints SQL.
- Reproducción validada:
  - `Negocio.objects.create(nombre='')` persistió una empresa sin nombre y generó
    el slug genérico `negocio`.
- Impacto:
  - Comandos, scripts o imports pueden crear tenants que el Admin normal no
    permitiría y cuya identidad visible es vacía.
- Recomendación:
  - Centralizar altas en un servicio, validar invariants y añadir constraints SQL
    para los estados que nunca sean válidos.
- Prueba de aceptación sugerida:
  - ORM autorizado, Admin, bootstrap e imports rechazan nombre vacío/espacios y
    slugs inválidos con el mismo contrato.

### NEG-013 - La autogeneración de slug se puede omitir o quedar solo en memoria

- Tipo: persistencia / consistencia del modelo.
- Evidencia:
  - La generación ocurre únicamente en `save()` (`apps/negocios/models.py:50-53`).
  - `bulk_create()` y `QuerySet.update()` no llaman ese método.
  - Si `save(update_fields=...)` genera slug pero `slug` no está en
    `update_fields`, Django no lo persiste.
- Reproducción validada:
  - `bulk_create` dejó una fila con slug vacío.
  - Partiendo de una fila legacy con slug vacío, `save(update_fields=['nombre'])`
    mostró el slug autogenerado en memoria, pero tras `refresh_from_db()` volvió a
    estar vacío.
- Impacto:
  - Estado Python y base difieren; tokens o procesos posteriores pueden usar un
    identificador vacío o distinto.
- Recomendación:
  - No depender de `save()` para una identidad requerida. El servicio debe
    calcularla antes, y `save()` debe ampliar correctamente `update_fields` como
    defensa adicional; verificar datos existentes.
- Prueba de aceptación sugerida:
  - Ningún writer soportado deja slug vacío y la instancia coincide con la base
    después de cualquier actualización parcial.

### NEG-014 - La generación de slug tiene una carrera TOCTOU

- Tipo: concurrencia / aprovisionamiento.
- Evidencia:
  - `_slug_unico()` consulta `exists()` en un loop y guarda después, fuera de una
    reserva/lock (`apps/negocios/models.py:55-64`).
  - La constraint única evita duplicados, pero no evita que dos altas paralelas
    elijan el mismo candidato y una termine en `IntegrityError`.
- Estado de evidencia:
  - Confirmado por inspección del algoritmo; no se forzó concurrencia en la suite
    temporal para evitar introducir fragilidad dependiente del motor.
- Impacto:
  - Aprovisionamientos concurrentes con nombres iguales pueden fallar de forma no
    controlada o requerir limpieza/reintento manual.
- Recomendación:
  - Tratar el conflicto único como parte normal del algoritmo y reintentar dentro
    de un servicio transaccional con límite; aceptar slug/tenant_key explícito en
    provisioning.
- Prueba de aceptación sugerida:
  - Dos altas paralelas finalizan con slugs distintos o un conflicto de dominio
    controlado, nunca 500 ni estado parcial.

## Hallazgos P3

### NEG-015 - La app no tiene pruebas propias

- Tipo: cobertura / regresión.
- Evidencia:
  - `apps/negocios/tests/` solo contenía `__init__.py` al iniciar.
  - Las pruebas existentes cubren casos válidos desde permisos/API, pero no los
    estados ambiguos de `None`, negocio inactivo, self-row múltiple o ciclo de
    vida.
- Impacto:
  - El resolver central podía permanecer en verde mientras consumidores distintos
    convertían el mismo resultado en vacío o acceso global.
- Recomendación:
  - Convertir las reproducciones en pruebas permanentes del contrato tipado y una
    matriz común de consumidores.
- Prueba de aceptación sugerida:
  - La misma tabla de casos ejecuta negocio asignado, global, huérfano, inactivo,
    inexistente y selector inválido contra reportes, CxC, sucursales y RBAC.

### NEG-016 - La documentación del modelo describe una arquitectura retirada

- Tipo: deuda documental / decisiones de arquitectura.
- Evidencia:
  - El módulo afirma forward-compatibilidad con `django-tenants` y que slug puede
    mapear al schema (`apps/negocios/models.py:1-12`).
  - La arquitectura vigente usa DB-per-tenant y `tenant_key` estable; el slug no
    es el identificador técnico (`docs/TENANCY_DB_PER_TENANT.md:77-82`, `:113-120`).
- Impacto:
  - Un mantenedor puede volver a usar slug para routing o asumir que cambiarlo es
    seguro/inseguro por razones que ya no representan el sistema actual.
- Recomendación:
  - Reescribir docstring y help text con la relación exacta entre `Tenant`, base
    activa, self-row y slug visible.
- Prueba de aceptación sugerida:
  - La documentación de modelo y Admin nombra `tenant_key` como identidad estable
    y explica que el self-row no controla routing.

### NEG-017 - Las responsabilidades de ciclo de vida están dispersas fuera de la app

- Tipo: mantenibilidad / ownership.
- Evidencia:
  - La app solo aporta modelo, helper y Admin; no hay servicio de aplicación.
  - Bootstrap/normalización mutan identidad y estado desde `apps/tenancy`.
  - Login decide vigencia desde `apps/api`, permisos desde `apps/permisos`, tokens
    desde `apps/api/authentication` y configuración comercial desde otras apps.
- Impacto:
  - Ningún punto puede garantizar por sí solo que desactivar, renombrar o retirar
    un negocio aplique todos los efectos requeridos.
- Recomendación:
  - Definir un servicio/orquestador de ciclo de vida con operaciones explícitas y
    consumidores obligatorios; mantener el modelo como dato, no como workflow.
- Prueba de aceptación sugerida:
  - Una prueba de contrato de desactivación comprueba en una operación login,
    refresh, tokens, permisos, sucursales, jobs y auditoría.

## Observaciones transversales

### El comportamiento correcto ya existe parcialmente

El motor granular filtra negocios activos y el RBAC devuelve querysets vacíos si
`negocio_actual()` no resuelve. Esto demuestra que el sistema ya contiene una
semántica fail-closed; el defecto aparece donde `None` se interpreta como global
por compatibilidad con SYSADMIN. La remediación debe preservar el acceso global
del operador, pero expresarlo como una autoridad distinta y no como ausencia de
tenant.

### DB-per-tenant reduce exposición, pero no elimina los invariants

En una base realmente dedicada, “todos los negocios” normalmente significa la
misma base física y reduce el radio de NEG-001/NEG-002. Aun así, self-rows
múltiples, una importación restaurada o un request ejecutado en `default` pueden
reabrir el cruce. Además, la revocación inconsistente y el drift control-plane /
self-row permanecen aunque solo exista una empresa por base.

### Facturación electrónica y suscripciones no se auditaron como apps

Se inspeccionaron únicamente sus FKs y efectos necesarios para entender el ciclo
de vida de `Negocio`. Sus defectos internos quedan fuera de alcance y continúan
sin prioridad, conforme a la indicación del negocio.

## Validación ejecutada

Entorno:

- Python: `C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe`
- settings temporal con base aislada:
  `test_pos_fifo_auditoria_negocios_20260826`
- Django system check: sin incidencias
- deriva de migraciones de `negocios`: ninguna

Suite existente seleccionada (**110/110 OK**, 38.846 s):

- `apps.negocios`
- motor y auditoría de permisos
- scope de negocio en CxC, reportes, sucursales y clientes
- login del portal y refresh con tenant context
- autenticación y comandos de tenancy
- verificación de instalación

Suite adversarial temporal (**24/24 OK**, 2.430 s):

- ADMIN huérfano con permiso total y scope global accidental
- negocio inexistente/inactivo que amplía selección SYSADMIN
- selector no numérico
- revocación distinta para ADMIN y rol granular
- login nuevo de negocio inactivo
- token de sucursal bajo negocio inactivo
- autoridad global derivada solo del string SYSADMIN
- múltiples self-rows
- nombre vacío y RNC duplicado/no normalizado
- bypass de slug con bulk/update parcial
- mutabilidad del claim legacy
- drift `Tenant` / `Negocio` / `ConfiguracionNegocio`
- cascadas de roles/suscripción/overrides
- ausencia de auditoría y campos sensibles editables en Admin

Una revalidación combinada contra el working tree concurrente terminó con
**134/134 OK** (110 existentes + 24 adversariales, 39.474 s).

Las pruebas y settings temporales se retiraron después de capturar la evidencia.
No se modificó código de producción, migraciones, templates ni tests permanentes.

## Orden de remediación sugerido

1. Reemplazar la semántica ambigua de `None` y cerrar NEG-001/NEG-002 en todos los
   consumidores con un resultado de scope explícito.
2. Definir y aplicar la cadena de revocación de NEG-003; retirar la autoridad
   global tenant-local de NEG-004.
3. Añadir preflight de cardinalidad/reconciliación para NEG-005 antes de nuevos
   tenants o imports.
4. Declarar autoridad de identidad y reconciliar las tres copias (NEG-006),
   manteniendo `tenant_key` estable y fuera de slug.
5. Introducir ciclo de vida auditado y no destructivo (NEG-007/NEG-008).
6. Normalizar modelo/slug/RNC y concurrencia (NEG-009 a NEG-014) después de auditar
   datos reales.
7. Convertir las 24 reproducciones en regresiones permanentes y actualizar la
   documentación (NEG-015 a NEG-017).

Este orden no implica corregir dentro de esta auditoría. Cada bloque requiere un
plan separado, revisión de datos reales y reejecución contra las correcciones
concurrentes del usuario.

---

# Estado de mitigación

Fecha: 2026-08-28. Verificación previa: se releyó cada hallazgo P1 contra el
código citado. **Los cinco son reales** — ninguno resultó falso positivo.

## Resumen por hallazgo

| ID | Real | Estado | Dónde quedó la corrección |
|---|---|---|---|
| NEG-001 | Sí | Corregido | `resolver_negocio()` devuelve un resultado **tipado** (`TENANT` / `GLOBAL` / `SIN_ACCESO`) en vez de `Negocio \| None`. Los tres consumidores citados —cartera, reportes y estado de sucursales— usan `resolucion.filtrar()`, que ante un fallo devuelve `none()`, no el queryset completo. |
| NEG-002 | Sí | Corregido | Un `?negocio=` inexistente, inactivo o no numérico da `SIN_ACCESO` y las vistas responden 403. Ya no cae a `GLOBAL`. |
| NEG-003 | Sí | Corregido | El resolver comprueba `negocio.activo`, así que desactivar el tenant revoca también a sus cuentas más privilegiadas — que eran justamente las que seguían funcionando. |
| NEG-004 | Sí | Corregido | Bajo tenancy, la autoridad global solo la concede el control plane (`Identity.is_global` o superusuario). El rol legacy `SYSADMIN` —una fila tenant-local y editable— deja de fabricar autoridad de plataforma. Sin tenancy sigue valiendo: no hay control plane con el cual contrastar. |
| NEG-005 | Sí | Corregido | `Negocio.self_row()` levanta `NegocioAmbiguo` si hay más de una fila. `bootstrap_tenant` y `normalizar_import_tenant` lo usan en vez de `order_by('id').first()`. |
| NEG-010 | Sí | Corregido | Un selector no numérico se rechaza en el resolver, no revienta aguas abajo. |
| NEG-015 | Sí | Corregido | La app no tenía pruebas propias. Ahora tiene 22. |

## El núcleo: `None` significaba tres cosas con autoridades opuestas

Es el hallazgo que explica a los otros. `negocio_actual()` devolvía `None` para:

1. «Sos operador global: consultá sin filtro.»
2. «No pude resolver un tenant.» (usuario huérfano, inactivo)
3. «Pediste un negocio que no existe o está inactivo.»

Y los tres consumidores convertían `None` en «queryset sin filtro». Es decir:
**los dos casos de fallo se leían como el permiso más amplio del sistema.** Un
SYSADMIN que pedía `?negocio=999999` —un typo, un bookmark viejo— recibía todos
los negocios en lugar de un 404.

## La decisión que hubo que tomar: hasta dónde llega el fail-closed

Al aplicar la corrección, veinte pruebas existentes fallaron. Todas usaban un
fixture con un `ADMIN` activo **sin negocio** — literalmente la reproducción de
NEG-001. Pero la lectura ingenua («huérfano ⇒ denegar») rompería algo real: una
instalación local de un solo negocio que nunca corrió el bootstrap dejaría el
POS sin reportes ni cartera por un dato de aprovisionamiento, en un escenario
donde **no hay nada que aislar**.

La regla que quedó no es «huérfano = denegar» sino **«denegar donde hay algo que
aislar»**:

- **Bajo tenancy:** siempre denegar. Cada base es un negocio; un usuario sin FK
  es un error de aprovisionamiento, no una configuración.
- **Sin tenancy, un solo negocio activo:** resolver a ese negocio. Es el único
  alcance posible.
- **Sin tenancy, varios negocios:** denegar. Es el escenario de la reproducción.

Con esa regla, dieciocho de las veinte pruebas volvieron a pasar sin tocarlas.

## Cambios de conducta observables

1. **`negocio_actual()` ya no decide alcance global.** Sigue existiendo y sigue
   devolviendo `Negocio | None`, pero `None` significa «no hay tenant», nunca
   «todos». Quien necesite distinguir debe usar `resolver_negocio()`.
2. **Los builders de reportes reciben `resolucion=` en vez de `negocio=`.** Un
   builder llamado sin scope devuelve vacío, no todo.
3. **Un `?negocio=` inválido devuelve 403** en reportes y estado de sucursales.
4. **Desactivar un negocio corta el acceso de sus usuarios**, incluido el ADMIN.
5. **Bajo tenancy, `rol='SYSADMIN'` ya no permite seleccionar otro negocio.**
6. **El provisioning se detiene si la base tenant tiene dos filas `Negocio`** en
   vez de retitular la de menor PK y dejar la otra colgando.

## Despliegue

**Sin migraciones.** Todo el cambio es de código.

> **Antes de desplegar conviene mirar dos cosas:** que ninguna base tenant tenga
> más de una fila `Negocio` (`Negocio.self_row()` ahora falla ahí), y que los
> usuarios estén enlazados a su negocio — el bootstrap ya lo hace, pero una
> instalación migrada a mano puede tener huérfanos.

## Lo que no se tocó

P2 restantes: NEG-006 (tres fuentes de identidad comercial pueden divergir),
NEG-007 (el ciclo de vida del tenant no deja auditoría de dominio), NEG-008
(borrar un negocio elimina en cascada seguridad y entitlements), NEG-009 (`slug`
mutable participando en identidad legacy), NEG-011 (el RNC no es canónico ni
tiene política de unicidad), NEG-012 (las escrituras directas evitan
validadores), NEG-013 (la autogeneración de slug se puede omitir), NEG-014 (la
generación de slug tiene una carrera TOCTOU).

P3: NEG-016 (documentación que describe una arquitectura retirada), NEG-017
(responsabilidades de ciclo de vida dispersas fuera de la app).

**Nota sobre NEG-008:** `Usuario.negocio` pasó a `PROTECT` en la mitigación de
`apps/usuarios` (USR-003), así que borrar un negocio con usuarios ya falla. El
resto de la cascada —roles, asignaciones, entitlements— sigue abierta.

## Pruebas

Suite completa, serial: **966 tests, OK.**

Módulo de regresión nuevo: `apps/negocios/tests/test_auditoria_negocios.py`
(22 pruebas).

**Verificación por mutación.** Revertidos NEG-002 y NEG-003, tres pruebas
fallan con el síntoma exacto: `'GLOBAL' != 'SIN_ACCESO'` para el selector
inexistente y el inactivo, y `'TENANT' != 'SIN_ACCESO'` para el ADMIN de un
negocio desactivado.

**Un error propio.** El test que verifica que el provisioning ya no elige por PK
buscaba la subcadena `Negocio.objects.order_by('id')`, y
`ConfiguracionNegocio.objects.order_by('id')` **la contiene**: falso positivo.
Se cambió por una expresión regular con frontera de palabra.
