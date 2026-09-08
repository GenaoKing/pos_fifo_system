# Auditoría profunda de código - `apps/suscripciones`

Fecha: 2026-08-30  
Revisión inicial y de cierre: `f807e36`  
Modo: lectura, pruebas y documentación; no se aplicaron correcciones funcionales.

Nota de concurrencia: `apps/suscripciones` y sus integraciones específicas estaban
limpias al iniciar y permanecieron sin cambios funcionales durante la auditoría.
Mientras se trabajaba aparecieron correcciones del usuario en `apps/clientes`, su
migración/pruebas y los maestros de `apps/api`; se preservaron y no forman parte de
esta revisión. Las pruebas descritas abajo se ejecutaron con ese estado concurrente,
pero cualquier edición posterior a la corrida debe considerarse no revalidada.

## Resumen ejecutivo

`apps/suscripciones` es el control comercial que decide qué capacidades tiene cada
negocio y, opcionalmente, cada sucursal. Aunque hoy no gestiona cobros, renovaciones
ni facturación de la suscripción, sus resultados ya gobiernan ventas a crédito,
e-CF, cotizaciones, financiación y etiquetas. Por eso un error en el resolutor puede
encender funciones no contratadas o apagar funciones operativas del POS.

La base conceptual es buena: catálogo declarativo, cierre transitivo de
dependencias, planes más overrides, módulos core, compatibilidad con flags legacy,
permiso separado del entitlement y hooks para impedir degradaciones con datos en
vuelo. El problema central es que esas reglas no forman todavía un único contrato
cerrado de extremo a extremo.

Los riesgos principales son:

- Una suscripción inactiva, `plan=null`, un plan borrado o la eliminación del último
  override se confunden con “negocio sin aprovisionar” y habilitan **todos** los
  módulos. La operación administrativa que parece suspender o vaciar el plan puede
  hacer exactamente lo contrario.
- El caché usa solo `Negocio.pk`: en DB-per-tenant dos bases con el mismo PK pueden
  compartir el set de módulos dentro del worker. Además, producción usa tres workers
  Gunicorn y LocMemCache, por lo que una invalidación no llega a los otros procesos.
- PATCH parcial, DELETE y cambios de plan/estado omiten `puede_desactivarse`; el único
  guard existente se puede eludir por las rutas API normales.
- `RequiereModulo` toma únicamente `user.negocio`. Ignora
  `request.auth.sucursal`, `request.sucursal` y los overrides de sucursal; un token
  de servicio con usuario sin negocio falla abierto.
- El enforcement está incompleto: las vistas HTML de CxC y reportes on-demand
  responden por URL aunque el plan no incluya esos módulos.
- Plantillas y sync siguen leyendo flags legacy, mientras servicios y decoradores
  leen el entitlement nuevo. La interfaz puede ocultar una capacidad que el backend
  ejecuta o mostrar una que el backend rechaza.
- El bootstrap hace unión de flags entre sucursales y no crea overrides locales. Si
  A tenía e-CF activo y B apagado, el resultado habilita e-CF también en B. Las
  configuraciones legacy con `sucursal=NULL` se ignoran.
- Los hooks de datos bloqueantes tragan cualquier excepción y autorizan la baja. Una
  falla de base, import o esquema se interpreta como “no hay datos pendientes”.

Se documentan **19 hallazgos**:

| Prioridad | Cantidad | Criterio |
| --- | ---: | --- |
| P1 | 10 | Puede habilitar módulos no contratados, mezclar entitlements entre tenants, ignorar una baja/override o degradar funciones con datos en vuelo. |
| P2 | 7 | Debilita consistencia transaccional, fuentes de verdad, onboarding, trazabilidad e invariantes administrativas. |
| P3 | 2 | Deja errores de catálogo y fronteras críticas sin diagnóstico ni regresión suficiente. |

> **Estado (2026-08-30): P1 MITIGADO (5/10).** Los diez hallazgos P1 se
> verificaron contra el código y los diez resultaron reales. Cinco están
> corregidos —SUS-001 a SUS-005, los que gobiernan el resolutor, el caché y el
> guard—; los otros cinco son enforcement disperso y onboarding, y quedan
> documentados. Ver [Estado de mitigación](#estado-de-mitigación) al final.
> **Sin migraciones.**

La validación seleccionada terminó con **31/31 pruebas existentes aprobadas**; la
app aporta **20 pruebas propias**. Una batería adversarial temporal terminó con
**21/21 reproducciones confirmadas** y se retiró del workspace. También pasaron
`manage.py check` y `makemigrations suscripciones --check --dry-run` contra una
base temporal independiente, destruida al finalizar.

## Alcance

Se inspeccionaron completamente:

- `apps/suscripciones/models.py`
- `apps/suscripciones/engine.py`
- `apps/suscripciones/registry.py`
- `apps/suscripciones/seed.py`
- `apps/suscripciones/signals.py`
- `apps/suscripciones/admin.py`
- `apps/suscripciones/apps.py`
- `apps/suscripciones/management/commands/`
- `apps/suscripciones/migrations/`
- `apps/suscripciones/tests/`

Se trazaron además las fronteras relevantes:

- API y autenticación: permisos, serializers, viewsets, payload `/auth/me` y tokens
  de sucursal;
- consumidores locales: ventas, anulaciones, CxC, cotizaciones, financiación,
  etiquetas y reportes;
- `ConfiguracionNegocio`, context processor, plantillas y sync de flags legacy;
- bootstrap DB-per-tenant, `Tenant.plan_slug`, router y caché;
- catálogo RBAC, Admin y auditoría de dominio;
- despliegue cloud con Gunicorn.

No se reauditaron internamente e-CF ni los cálculos de cada módulo consumidor. Se
revisó únicamente si `suscripciones` los habilita, deshabilita o degrada de manera
coherente. Cobro recurrente, precio, renovación y facturación de la suscripción no
existen en esta app y se tratan como alcance futuro, acorde con la prioridad actual
del negocio, no como defectos de esta entrega.

## Contrato efectivo observado

La resolución normal pretende ser:

```text
plan + inclusiones - exclusiones
        -> cierre de dependencias
        -> módulos core
        -> apagados de sucursal
```

Hay, sin embargo, cuatro identidades distintas del estado:

| Superficie | Fuente usada |
| --- | --- |
| Servicios/decoradores locales | `engine`, resuelto desde la sucursal global de settings |
| Permiso DRF de módulo | `user.negocio`, sin sucursal |
| Payload `/auth/me` | módulos del negocio, sin sucursal |
| Plantillas y sync de configuración | flags `ConfiguracionNegocio.modulo_*` |

Mientras esas cuatro superficies no converjan en un resolutor común, cambiar un plan
no garantiza una conducta uniforme.

## Hallazgos P1

### SUS-001 - Suspender, vaciar o borrar puede habilitar todos los módulos

- Tipo: fail-open / ciclo de vida / control comercial invertido.
- Evidencia:
  - `tiene_plan` exige suscripción activa y `plan_id`; si no hay plan ni overrides,
    el resolutor retorna todo el registro (`apps/suscripciones/engine.py:73-87`).
  - `plan` acepta null y usa `SET_NULL` al borrar (`apps/suscripciones/models.py:65-69`).
  - La API permite `plan=null` y editar `activa`
    (`apps/api/serializers/suscripciones.py:25-37`).
  - DELETE está habilitado en el viewset completo de overrides
    (`apps/api/views/suscripciones.py:56-69`).
- Reproducción validada:
  - `activa=False` sin overrides devolvió exactamente todas las keys.
  - PATCH `plan=null` devolvió todos los módulos en `modulos_activos`.
  - Borrar un plan limitado dejó `plan_id=NULL` y una lectura fresca habilitó todo.
  - Borrar el último override de una suscripción custom habilitó todo.
  - Un objeto con la relación `plan` ya cacheada también puede lanzar `ValueError`
    durante la ventana de borrado, antes de una lectura fresca.
- Impacto:
  - Una suspensión comercial o baja de plan puede activar e-CF, financiación y demás
    capacidades premium.
  - El operador recibe una respuesta coherente en forma, pero opuesta a la intención.
- Recomendación:
  - Modelar explícitamente `SIN_APROVISIONAR`, `ACTIVA`, `SUSPENDIDA` y
    `CUSTOM_VACÍA`; solo el primer estado puede adoptar una política de contingencia.
  - Prohibir null/borrado que deje suscriptores ambiguos o migrarlos atómicamente a
    un estado/plan explícito.
- Prueba de aceptación sugerida:
  - Cada transición de plan, suspensión, borrado y último override debe producir un
    set definido; ninguna baja puede aumentar capacidades.

### SUS-002 - El caché puede mezclar entitlements entre bases tenant

- Tipo: aislamiento DB-per-tenant / identidad de caché.
- Evidencia:
  - La clave contiene versión global y `negocio_id`, pero no tenant key ni alias de
    base (`apps/suscripciones/engine.py:34-69`).
  - En DB-per-tenant los PK son locales a cada base. El motor de permisos ya documenta
    y usa `tenant_key` precisamente por esa razón
    (`apps/permisos/engine.py:127-152`).
- Reproducción validada:
  - Dos objetos de contextos distintos con `pk=1` resolvieron una sola vez; el segundo
    recibió el set cacheado del primero.
- Impacto:
  - Dentro del mismo worker, un tenant puede recibir temporalmente módulos del tenant
    atendido antes con el mismo PK.
- Recomendación:
  - Incluir un namespace técnico inmutable de tenant y fallar en voz alta si tenancy
    está activo sin contexto; no derivarlo de PK ni slug comercial.
- Prueba de aceptación sugerida:
  - Alternar dos bases con PK coincidentes nunca comparte entrada, versión ni objeto
    cacheado.

### SUS-003 - La invalidación no converge entre los tres workers cloud

- Tipo: caché distribuido / revocación tardía.
- Evidencia:
  - El TTL es 300 segundos y la versión vive en el backend de caché
    (`apps/suscripciones/engine.py:34-55`).
  - Los settings efectivos usan `LocMemCache`; la comprobación de runtime devolvió
    `django.core.cache.backends.locmem.LocMemCache`.
  - El contenedor arranca Gunicorn con tres workers (`Dockerfile:37`).
  - Las señales solo incrementan la versión visible al proceso actual
    (`apps/suscripciones/signals.py:19-31`).
- Impacto:
  - Tras suspender o quitar un módulo, dos workers pueden conservarlo hasta cinco
    minutos; habilitaciones también pueden tardar de forma no determinista.
- Recomendación:
  - Usar caché compartido para este control o no cachearlo cuando el backend sea
    local, como ya distingue el motor de permisos.
- Prueba de aceptación sugerida:
  - Tres procesos precargados convergen al mismo set dentro de un SLA medido después
    de cada mutación.

### SUS-004 - PATCH, DELETE y cambio de plan evaden el guard de degradación

- Tipo: bypass de invariante / API administrativa.
- Evidencia:
  - `_validar()` solo actúa si `validated_data` trae simultáneamente
    `incluido=False`, `modulo` y `negocio`; un PATCH de solo `incluido` retorna sin
    validar (`apps/api/views/suscripciones.py:67-82`).
  - `destroy` no está sobrescrito y no llama `puede_desactivarse`.
  - Cambiar `plan` o `activa` usa un `ModelViewSet` sin calcular módulos retirados
    (`apps/api/views/suscripciones.py:47-53`).
- Reproducción validada:
  - Con el guard simulado como bloqueante, PATCH parcial cambió la inclusión y el
    guard no fue llamado.
  - DELETE retiró una inclusión sin llamar el guard.
  - Cambiar Empresarial a Básico tampoco llamó el guard.
- Impacto:
  - Se pueden retirar CxC/e-CF aunque existan dependientes o datos en vuelo por las
    rutas oficiales del operador.
- Recomendación:
  - Centralizar toda transición en un servicio que compare set anterior/nuevo,
    valide cada módulo retirado y confirme todo en una sola transacción.
- Prueba de aceptación sugerida:
  - POST, PATCH parcial/completo, DELETE, cambio de plan, suspensión y Admin aplican
    exactamente el mismo guard.

### SUS-005 - Tokens y requests de sucursal ignoran el override local

- Tipo: scope de sucursal / fail-open de identidad.
- Evidencia:
  - `RequiereModulo` usa solo `user.negocio` y no pasa sucursal al motor
    (`apps/api/permissions.py:200-218`).
  - La autenticación sí adjunta la sucursal al token
    (`apps/api/authentication.py:104-127`).
  - `modulo_activo` retorna True cuando el negocio es None
    (`apps/suscripciones/engine.py:115-118`).
- Reproducción validada:
  - Un usuario de negocio con CxC apagado en su sucursal obtuvo permiso DRF=True,
    aunque el motor con esa sucursal devolvía False.
  - Un usuario de servicio con `negocio=NULL`, token ligado a una sucursal cuyo plan
    no incluía CxC, también obtuvo permiso=True.
- Impacto:
  - El override de sucursal no protege endpoints API y datos sync.
  - Filas legacy de usuarios de servicio sin negocio convierten cualquier gate de
    módulo en fail-open.
- Recomendación:
  - Resolver primero `request.auth.sucursal`/`request.sucursal`, comprobar su negocio
    y consistencia con el usuario, y pasar ambos al motor.
- Prueba de aceptación sugerida:
  - Matriz usuario humano/token × negocio presente/ausente × override on/off con
    resultado idéntico en DRF y motor.

### SUS-006 - CxC y reportes on-demand no tienen enforcement HTML de módulo

- Tipo: cobertura de enforcement / acceso directo por URL.
- Evidencia:
  - Las vistas locales de CxC exigen login y permiso, pero no `requiere_modulo`
    (`apps/cuentas_por_cobrar/views.py:144-208` y las demás acciones del archivo).
  - `reportes_on_demand` valida login/alcance, no el módulo
    (`apps/reportes/views.py:442-468`); sus APIs siguen el mismo patrón.
  - La navegación de CxC ni siquiera está condicionada por módulo, y reportes solo se
    oculta con el flag legacy (`templates/base.html:184-228`).
- Reproducción validada:
  - Con plan Básico, CxC fuera del set efectivo, GET `/cuentas-por-cobrar/` respondió
    200 a un admin con permiso.
  - Con `reportes_ondemand` fuera del plan y flag legacy apagado, GET
    `/reportes/on-demand/` respondió 200.
- Impacto:
  - Ocultar menús no aplica el contrato comercial; URLs guardadas o llamadas
    directas conservan funcionalidad.
- Recomendación:
  - Inventariar cada módulo vendible y aplicar el gate en todos sus puntos de entrada
    server-side, conservando permisos como capa adicional.
- Prueba de aceptación sugerida:
  - Por cada módulo apagado, todas las vistas, APIs, comandos/jobs y servicios de alta
    rechazan de forma consistente; las lecturas históricas permitidas se documentan.

### SUS-007 - Plantillas, sync y backend leen fuentes de verdad diferentes

- Tipo: split-brain de configuración / UX y ejecución divergentes.
- Evidencia:
  - `configuracion.utils.modulo_activo()` ya delega al engine cuando resuelve negocio
    (`apps/configuracion/utils.py:55-77`).
  - El context processor entrega el modelo `ConfiguracionNegocio` crudo
    (`apps/configuracion/context_processors.py:8-22`).
  - Menús de cotizaciones, financiación y reportes consultan `config.modulo_*`
    (`templates/base.html:192-228`).
  - El selector e-CF también usa el flag legacy
    (`templates/pos/punto_venta.html:755-768`).
  - El pull de sync serializa los mismos flags legacy
    (`apps/api/views/sync.py:431-438`).
  - El servicio de ventas decide CxC/e-CF mediante el resolutor nuevo
    (`apps/ventas/services/ventas_service.py:266-275` y `:397-400`).
- Impacto:
  - La UI puede ocultar la selección fiscal mientras el backend encola un e-CF.
  - Un módulo habilitado en el plan puede seguir oculto; uno retirado puede seguir
    visible y terminar en 404 o ejecutarse si su vista carece de gate.
- Recomendación:
  - Exponer un snapshot efectivo único por negocio/sucursal y hacer que plantillas,
    API, sync, servicios y diagnóstico lo consuman.
- Prueba de aceptación sugerida:
  - Para cada combinación flag legacy/plan/override, UI, payload, endpoint y servicio
    reportan exactamente el mismo estado efectivo.

### SUS-008 - La unión del bootstrap habilita módulos en sucursales que los tenían apagados

- Tipo: migración back-compat / expansión de alcance.
- Evidencia:
  - La derivación usa unión: basta una configuración del negocio con flag True
    (`apps/suscripciones/seed.py:56-71`).
  - El bootstrap crea solo `NegocioModulo`; nunca materializa
    `SucursalModuloOverride` para las sucursales que estaban en False
    (`apps/suscripciones/seed.py:75-98`).
  - El comentario afirma que así “preserva exactamente” la conducta (`:58-60`).
- Reproducción validada:
  - Sucursal A con e-CF=True y B con e-CF=False produjo e-CF activo para B y cero
    overrides locales de compensación.
- Impacto:
  - Migrar un negocio multi-sucursal puede habilitar hardware, reportes o emisión
    fiscal en locales donde estaban explícitamente apagados.
- Recomendación:
  - Derivar el set de negocio y, en la misma transacción, crear overrides negativos
    por sucursal para conservar cada estado previo.
- Prueba de aceptación sugerida:
  - Una matriz de flags divergentes A/B/C conserva bit por bit la conducta de cada
    sucursal después del bootstrap.

### SUS-009 - La configuración legacy sin sucursal se ignora durante la migración

- Tipo: compatibilidad / pérdida de estado de instalación.
- Evidencia:
  - `ConfiguracionNegocio.sucursal` admite null para instalaciones legacy
    (`apps/configuracion/models.py:28-36`).
  - La derivación filtra exclusivamente `sucursal__negocio=negocio`
    (`apps/suscripciones/seed.py:66`).
  - Aun sin flags encontrados añade CxC, creando un override explícito y evitando que
    el resolutor caiga en el fail-open de “sin aprovisionar” (`:62-64`, `:89-98`).
- Reproducción validada:
  - Una configuración legacy `sucursal=NULL, modulo_ecf=True` fue ignorada; después
    del bootstrap e-CF quedó fuera del set.
- Impacto:
  - Una instalación antigua puede perder impresión, cotizaciones o e-CF al migrar,
    pese a que el comando declara preservar la conducta.
- Recomendación:
  - Exigir asignación inequívoca antes de migrar; en una instalación de negocio único,
    adoptar explícitamente la fila legacy o abortar con diagnóstico.
- Prueba de aceptación sugerida:
  - Cero, una y varias filas legacy se resuelven sin `.first()` implícito: o hay una
    adopción demostrable o no se escribe nada.

### SUS-010 - Un error al comprobar datos se interpreta como autorización para apagar

- Tipo: fail-open de integridad / concurrencia TOCTOU.
- Evidencia:
  - `_datos_bloqueantes` captura cualquier `Exception` y retorna None
    (`apps/suscripciones/engine.py:152-168`).
  - Los hooks buscan CxC/e-CF solo mediante sucursales del negocio (`:125-149`), por
    lo que registros legacy con sucursal nula quedan fuera.
  - `puede_desactivarse` consulta y retorna antes de que la API guarde, sin lock ni
    servicio transaccional compartido (`:171-189`; `apps/api/views/suscripciones.py:63-69`).
  - El servicio de ventas también comprueba el módulo antes de entrar a su transacción
    (`apps/ventas/services/ventas_service.py:266-278`).
- Reproducción validada:
  - Un hook que lanzó `ZeroDivisionError` produjo `(True, '')` y autorizó la baja.
- Impacto:
  - Una indisponibilidad de tabla o error de código permite degradar justo cuando no
    se pudo demostrar que es seguro.
  - Una venta a crédito/e-CF puede comenzar entre la comprobación y la baja.
- Recomendación:
  - Fallar cerrado y registrar el error; serializar transición y creación de datos
    incompatibles mediante un servicio/lock o estado de drenaje.
- Prueba de aceptación sugerida:
  - Excepción, timeout, fila legacy y carrera concurrente nunca confirman la baja sin
    demostrar ausencia de trabajo en vuelo.

## Hallazgos P2

### SUS-011 - Las señales publican invalidación antes del commit

- Tipo: consistencia transaccional / caché obsoleto.
- Evidencia:
  - `post_save`, `post_delete` y `m2m_changed` llaman `invalidar_cache()` directamente
    (`apps/suscripciones/signals.py:19-31`).
  - El patrón corregido en permisos difiere la publicación con
    `transaction.on_commit` porque otro lector puede recachear el estado viejo bajo
    la versión nueva (`apps/permisos/signals.py:18-23` y `:33-55`).
- Reproducción validada:
  - Dentro de `transaction.atomic()`, crear un plan llamó la invalidación antes de
    salir del bloque; luego la transacción fue revertida.
- Impacto:
  - Otro request puede leer datos no confirmados/anteriores bajo la versión nueva y
    conservarlos por el TTL; un rollback también provoca invalidaciones ficticias.
- Recomendación:
  - Limpiar cualquier memo privado de inmediato y publicar la versión compartida
    exclusivamente `on_commit`.
- Prueba de aceptación sugerida:
  - Commit publica una vez; rollback no publica; un lector concurrente nunca cachea
    el estado pre-commit bajo la versión post-commit.

### SUS-012 - Registro en código y espejo DB pueden divergir silenciosamente

- Tipo: doble fuente de verdad / catálogo.
- Evidencia:
  - `Modulo` se describe como espejo DB, pero `key` y `core` son editables en el
    modelo/Admin (`apps/suscripciones/models.py:14-23`;
    `apps/suscripciones/admin.py:12-16`).
  - Planes y overrides referencian el espejo DB, mientras dependencias y core salen
    del registro en código (`apps/suscripciones/engine.py:76-96`).
  - `cierre_dependencias` ignora keys desconocidas (`apps/suscripciones/registry.py:88-101`).
  - `sync_modulos` hace upsert, pero no elimina filas retiradas o renombradas
    (`apps/suscripciones/seed.py:35-41`).
- Reproducción validada:
  - Se añadió un módulo solo en DB, se asignó a un plan y el API/modelo lo aceptó;
    el resolutor lo eliminó silenciosamente del set efectivo.
- Impacto:
  - El operador ve un plan que enumera un módulo que runtime no reconoce; cambiar
    `core` en Admin tampoco cambia la regla real.
- Recomendación:
  - Elegir una fuente autoritativa, hacer el espejo inmutable y validar drift antes de
    servir o desplegar.
- Prueba de aceptación sugerida:
  - Duplicado, key desconocida, dependencia ausente, rename y fila obsoleta hacen
    fallar el check de sistema con un diff accionable.

### SUS-013 - `activo` y varios estados administrativos no tienen semántica efectiva

- Tipo: invariante de modelo / UI engañosa.
- Evidencia:
  - `Plan.activo` existe (`apps/suscripciones/models.py:35-46`), pero el engine solo
    mira `suscripcion.activa` y `plan_id` (`apps/suscripciones/engine.py:76-80`).
  - El serializer permite asignar cualquier `Plan.objects.all()`, incluidos inactivos
    (`apps/api/serializers/suscripciones.py:25-28`).
  - `SucursalModuloOverride` dice que solo apaga, pero expone `activo=True`; el engine
    simplemente ignora esas filas (`apps/suscripciones/models.py:111-126`;
    `apps/suscripciones/engine.py:105-112`).
  - Exclusiones de módulos core pueden persistirse, pero core se vuelve a añadir
    siempre (`apps/suscripciones/engine.py:94-96`).
- Reproducción validada:
  - Un plan marcado inactivo continuó otorgando e-CF.
- Impacto:
  - Campos y filas que parecen cambiar disponibilidad pueden ser no-op, conservar
    contratos retirados o mostrar estado contradictorio.
- Recomendación:
  - Definir por separado “no vendible a nuevas altas” y “no efectivo”; expresar solo
    estados válidos con constraints y validación común.
- Prueba de aceptación sugerida:
  - Toda combinación admitida tiene semántica documentada; estados imposibles se
    rechazan en ORM, Admin, API y comandos.

### SUS-014 - El plan del control plane puede divergir del plan operativo

- Tipo: onboarding / dos bases / diagnóstico engañoso.
- Evidencia:
  - `Tenant` conserva `plan_slug` en el control plane (`apps/tenancy/models.py:10-25`).
  - `bootstrap_tenant --plan` acepta texto libre
    (`apps/tenancy/management/commands/bootstrap_tenant.py:18-46`).
  - El comando escribe ese valor en `Tenant.plan_slug` antes de aprovisionar
    (`:123-149`).
  - En la base tenant busca `.first()` y, si no existe, omite silenciosamente la
    asignación (`:371-385`).
- Impacto:
  - Un typo puede dejar el control plane anunciando un plan inexistente mientras la
    suscripción operativa conserva el plan anterior o queda custom.
- Recomendación:
  - Validar el slug antes de tocar ambas bases y verificar postcondición cruzada antes
    de reactivar/publicar el tenant.
- Prueba de aceptación sugerida:
  - Plan desconocido aborta sin cambios; plan válido termina idéntico en control plane,
    DB tenant y set efectivo.

### SUS-015 - Los cambios comerciales no dejan auditoría durable

- Tipo: trazabilidad / no repudio administrativo.
- Evidencia:
  - Viewsets llaman `serializer.save()` sin actor, before/after ni evento
    (`apps/api/views/suscripciones.py:47-82`).
  - Admin registra modelos sin integración con la auditoría de dominio
    (`apps/suscripciones/admin.py:12-46`).
  - Los comandos escriben directamente por seed (`bootstrap_suscripciones.py:19-30` y
    `sync_modulos.py:11-16`).
  - `apps/auditoria` ya define `CONFIGURACION` como acción disponible
    (`apps/auditoria/models.py:157-163`).
- Impacto:
  - No puede reconstruirse quién cambió un plan, suspendió un negocio, retiró e-CF o
    dejó el sistema en fail-open, ni por qué canal ocurrió.
- Recomendación:
  - El servicio único de transición debe registrar actor real/global, tenant,
    sucursal, canal, motivo y diff efectivo dentro de la misma unidad de confirmación.
- Prueba de aceptación sugerida:
  - Cada mutación confirmada genera exactamente un evento append-only; rollback no
    deja evento ni cambio.

### SUS-016 - Bootstrap y sync pueden quedar parcialmente aplicados y reportar éxito pobre

- Tipo: comando operativo / atomicidad / observabilidad.
- Evidencia:
  - Ambos comandos carecen de `transaction.atomic`, `--dry-run`, objetivo acotado y
    modo estricto (`bootstrap_suscripciones.py:16-31`; `sync_modulos.py:8-16`).
  - El seed escribe catálogo, varios planes, suscripciones y overrides en bucles
    sucesivos (`apps/suscripciones/seed.py:35-98`).
  - La salida solo cuenta filas; no informa altas, cambios, skips, divergencias ni
    postcondición efectiva.
- Impacto:
  - Un fallo intermedio deja algunos negocios aprovisionados y otros no; reintentar
    puede conservar overrides previos por `get_or_create` sin explicar el drift.
- Recomendación:
  - Añadir preflight/dry-run, transacción por ámbito definido, resumen de cambios y
    postcondiciones. Para flotas, usar ledger por tenant.
- Prueba de aceptación sugerida:
  - Inyectar fallo en cada etapa deja rollback total o checkpoint explícito reanudable;
    la salida identifica exactamente cada negocio afectado.

### SUS-017 - `sync_modulos` no sincroniza realmente los planes default

- Tipo: drift de seed / nombre de comando engañoso.
- Evidencia:
  - `crear_planes_default` solo asigna módulos si el plan está recién creado o su M2M
    está vacío (`apps/suscripciones/seed.py:44-53`).
  - El comando afirma sincronizar catálogo y planes default
    (`apps/suscripciones/management/commands/sync_modulos.py:8-16`).
- Reproducción validada:
  - Se retiró impresión térmica de Básico manteniendo otro módulo; ejecutar la función
    de sync no la restauró.
- Impacto:
  - Añadir una capacidad a `TIERS` no actualiza instalaciones existentes; dos tenants
    con el mismo slug comercial pueden tener composiciones distintas sin versión.
- Recomendación:
  - Versionar presets y distinguir plan administrado por código de plan custom. El
    sync debe mostrar diff y requerir confirmación/migración explícita.
- Prueba de aceptación sugerida:
  - La misma versión de preset produce el mismo set en todas las bases o reporta una
    excepción custom deliberada.

## Hallazgos P3

### SUS-018 - Una key desconocida puede aprobarse en el camino fail-open

- Tipo: typo / contrato default-deny del catálogo.
- Evidencia:
  - `registry.validar()` detecta desconocidas, pero los gates no lo llaman
    (`apps/suscripciones/registry.py:112-114`).
  - `modulo_activo` retorna True para cualquier string si `negocio=None`, antes de
    consultar el registro (`apps/suscripciones/engine.py:115-118`).
- Reproducción validada:
  - `modulo_con_typo` no existía en el registro y aun así devolvió True sin negocio.
- Impacto:
  - Un gate nuevo mal escrito queda invisible justo en contextos incompletos/token
    legacy; no hay log que revele el error.
- Recomendación:
  - Desconocidas siempre deben denegar y registrar alerta; validar catálogo completo
    (duplicados, dependencias y ciclos) en checks de sistema.
- Prueba de aceptación sugerida:
  - Toda key desconocida deniega en cualquier contexto y `manage.py check` localiza
    la declaración/consumidor inválido.

### SUS-019 - La suite verde no cubre las fronteras que alteran el contrato

- Tipo: cobertura / falsa confianza.
- Evidencia:
  - Las 20 pruebas propias cubren cierre, plan/override básico, fail-open intencional,
    invalidación simple, alias y derivación de un solo negocio/sucursal.
  - Las pruebas API seleccionadas cubren cambio de plan, inclusión y un POST de
    exclusión completo, pero no PATCH parcial, DELETE, null, suspensión, plan borrado,
    branch override, token sin negocio, multi-DB, multiworker, rollback ni UI legacy.
- Reproducción validada:
  - Las 31 pruebas existentes pasaron mientras las 21 reproducciones adversariales
    confirmaron los comportamientos descritos.
- Impacto:
  - Cambios futuros pueden conservar verde la suite y aun invertir una suspensión,
    saltar el guard o mezclar tenants.
- Recomendación:
  - Tras acordar los contratos, convertir cada reproducción en una regresión y añadir
    una matriz real PostgreSQL multi-DB/multiproceso.
- Prueba de aceptación sugerida:
  - CI cubre estados, canales y contextos: API/Admin/comando/ORM × tenant/sucursal ×
    commit/rollback × workers/bases.

## Validación ejecutada

### Suite existente seleccionada

Se usó un settings temporal con base
`test_pos_fifo_auditoria_suscripciones_20260830`; Django la creó y destruyó. No se
usó la base de desarrollo compartida.

```text
manage.py test \
  apps.suscripciones.tests.test_engine \
  apps.suscripciones.tests.test_enforcement \
  apps.suscripciones.tests.test_registry \
  apps.api.tests.test_suscripciones_admin \
  apps.api.tests.test_modulo_gating \
  --settings=config.settings_auditoria_suscripciones_temp --noinput -v 1
```

Resultado:

- **31 pruebas ejecutadas**.
- **31 aprobadas**.
- Duración: **6.162 s**.
- `System check identified no issues`.
- Base temporal destruida al terminar.

### Batería adversarial temporal

Se añadieron transitoriamente 21 casos para observar el comportamiento actual.
Resultado definitivo:

- **21 pruebas ejecutadas**.
- **21 reproducciones confirmadas**.
- Duración: **2.741 s**.
- `System check identified no issues`.

Los casos confirmaron:

1. Suscripción inactiva habilitando todo.
2. PATCH `plan=null` habilitando todo.
3. Borrado del plan habilitando todo en una lectura fresca.
4. Borrado del último override custom habilitando todo.
5. PATCH parcial omitiendo el bloqueo.
6. DELETE de una inclusión omitiendo el bloqueo.
7. Cambio de plan omitiendo el bloqueo.
8. Excepción del hook autorizando la baja.
9. Permiso DRF ignorando override de sucursal.
10. Token de sucursal con usuario sin negocio fallando abierto.
11. Colisión de caché con PK igual en dos contextos.
12. Key desconocida aprobada sin negocio.
13. Módulo existente solo en DB desapareciendo del set efectivo.
14. Plan inactivo continuando efectivo.
15. Sync sin restaurar un preset default no vacío.
16. Señal invalidando antes del commit/rollback.
17. Vista HTML de CxC accesible sin módulo.
18. Reportes on-demand accesible sin módulo.
19. `/auth/me` ignorando override de sucursal.
20. Unión de flags activando una sucursal previamente apagada.
21. Configuración legacy sin sucursal ignorada.

El archivo de pruebas y el settings temporal fueron eliminados después de la
validación. No se conservaron cambios funcionales.

### Chequeos estáticos de Django

```text
manage.py check --settings=config.settings_auditoria_suscripciones_temp
System check identified no issues (0 silenced).

manage.py makemigrations suscripciones --check --dry-run \
  --settings=config.settings_auditoria_suscripciones_temp
No changes detected in app 'suscripciones'
```

## Aspectos positivos observados

- `SuscripcionNegocio.negocio` es OneToOne, evitando dos suscripciones ordinarias
  para el mismo negocio.
- Los overrides de negocio y sucursal tienen unicidad por módulo.
- El registro declarativo mantiene dependencias fuera de la base y el cierre
  transitivo evita olvidar dependencias al habilitar.
- Los módulos core se agregan defensivamente y no pueden apagarse desde el resolutor
  de sucursal.
- El alias legacy de financiación está centralizado y probado.
- El servicio de ventas aplica el gate de CxC antes de tocar inventario y decide e-CF
  antes de confirmar la venta.
- Hay hooks explícitos para CxC abiertas y e-CF en proceso; constituyen una buena
  extensión para un workflow de drenaje una vez que fallen cerrado y sean atómicos.
- Las señales cubren modelos y M2M por los caminos ORM ordinarios.
- El endpoint administrativo ya quedó restringido a operador SaaS global, no al
  ADMIN del tenant, gracias a la corrección previa del motor RBAC.
- El bootstrap es reejecutable en el sentido de que usa upsert/get-or-create y no
  duplica las claves normales.
- El payload expone módulos como pista UI y los permisos siguen siendo una capa
  ortogonal; esa separación conceptual es correcta.

## Orden recomendado de remediación

1. **Cerrar la máquina de estados:** SUS-001 antes de tocar caché o UI. Suspensión,
   custom vacío y sin aprovisionar deben ser estados distintos.
2. **Construir una transición única y segura:** SUS-004, SUS-010, SUS-011 y SUS-015,
   con diff anterior/nuevo, drenaje, transacción, `on_commit` y auditoría.
3. **Aislar y hacer converger el caché:** SUS-002 y SUS-003, reutilizando el patrón
   tenant-aware/backend-aware de permisos.
4. **Resolver request y sucursal una sola vez:** SUS-005 y el payload de `/me`.
5. **Completar enforcement y fuente de verdad:** SUS-006 y SUS-007 en HTML, DRF,
   servicios, jobs, sync y templates.
6. **Rehacer bootstrap con preservación demostrable:** SUS-008, SUS-009 y SUS-014.
7. **Endurecer catálogo/modelos/comandos:** SUS-012, SUS-013, SUS-016, SUS-017 y
   SUS-018.
8. **Convertir reproducciones en regresiones:** SUS-019, incluyendo PostgreSQL
   multi-DB y tres procesos reales.

No conviene corregir solo el fail-open cambiándolo globalmente a fail-closed: el
historial documenta que eso ya dejó al POS sin impresión durante una ventana de
aprovisionamiento. La solución es distinguir estados y hacer atómico el onboarding,
no elegir una sola política para situaciones semánticamente distintas.

## Criterios de cierre de la auditoría

La app puede considerarse cerrada cuando, como mínimo:

- ninguna suspensión, null, borrado o último override aumenta capacidades;
- el estado sin aprovisionar es explícito, temporal, observable y no se confunde con
  suspensión/custom vacío;
- claves de caché incluyen tenant técnico y las invalidaciones convergen entre todos
  los workers después del commit;
- todas las mutaciones pasan por un servicio transaccional, auditado y con guard de
  datos/dependencias fail-closed;
- tokens, usuarios y payloads aplican negocio y sucursal coherentes;
- cada punto de entrada server-side de un módulo vendible aplica entitlement más
  permiso;
- templates, API, sync, jobs y servicios consumen el mismo snapshot efectivo;
- bootstrap conserva exactamente flags por sucursal y aborta configuraciones legacy
  ambiguas;
- `Tenant.plan_slug`, suscripción operativa y set efectivo coinciden;
- registro y espejo DB no pueden divergir silenciosamente;
- estados de plan/core/override tienen constraints y semántica verificable;
- comandos ofrecen preflight, dry-run, postcondiciones y resultado reanudable;
- las 21 reproducciones adversariales se convierten en pruebas de rechazo,
  aislamiento o convergencia.

---

# Estado de mitigación

Fecha: 2026-08-30. Verificación previa: se releyó cada hallazgo P1 contra el
código citado. **Los diez son reales** — ninguno resultó falso positivo.

## Resumen por hallazgo

| ID | Real | Estado | Dónde quedó la corrección |
|---|---|---|---|
| SUS-001 | Sí | Corregido | `estado_suscripcion()` distingue cuatro estados: `SIN_APROVISIONAR`, `SUSPENDIDA`, `CON_PLAN`, `CUSTOM`. Solo el primero adopta la política de contingencia. |
| SUS-002 | Sí | Corregido | La clave de caché lleva el `tenant_key` activo, y bajo tenancy sin contexto falla fuerte — igual que permisos y configuración. |
| SUS-003 | Sí | Corregido | TTL de 30 s con backend local (los tres workers convergen en medio minuto) y 300 s con uno compartido. |
| SUS-004 | Sí | Corregido | `GuardDegradacionMixin` compara el set efectivo **antes y después** dentro de una transacción, y el override valida además la **intención**. Cubre POST, PATCH parcial, DELETE, cambio de plan y suspensión. |
| SUS-005 | Sí | Corregido | `RequiereModulo` resuelve `request.sucursal` / `request.auth.sucursal` primero y usa **su** negocio cuando el usuario no lo trae; `modulo_activo()` hace lo mismo. |
| SUS-006 | Sí | **Abierto** | Enforcement HTML de módulo en CxC y reportes on-demand. |
| SUS-007 | Sí | **Abierto** | Plantillas y sync leen flags legacy; servicios leen el entitlement. |
| SUS-008 | Sí | **Abierto** | El bootstrap hace unión de flags entre sucursales. |
| SUS-009 | Sí | **Abierto** | Las configuraciones legacy con `sucursal=NULL` se ignoran al migrar. |
| SUS-010 | Sí | **Abierto** | Los hooks de datos bloqueantes tragan cualquier excepción. |

## SUS-001: el hallazgo que invierte el significado de una operación

Es el más grave y vale explicarlo entero. `not tiene_plan and not overrides`
mezclaba situaciones con consecuencias **opuestas**:

- «todavía nadie configuró este negocio» — donde el fail-open es una decisión
  deliberada, para que una instalación nueva no arranque sin funciones;
- «lo suspendí», «le quité el plan», «borré el plan», «borré su último
  override» — que son **decisiones**, y todas se leían como la primera.

Se reprodujo: `activa=False` sin overrides devolvía **todas** las keys, y un
PATCH `plan=null` también. La operación administrativa que parece suspender
hacía exactamente lo contrario: habilitaba e-CF, financiación y el resto de las
capacidades premium.

La regla que quedó: **si existe una fila de suscripción, hubo una decisión.**
Solo la ausencia total —ni suscripción ni overrides— es «sin aprovisionar». Una
suspensión deja lo mínimo con lo que el POS sigue siendo usable (`core`).

El test `test_ninguna_baja_aumenta_capacidades` deja ese criterio escrito como
invariante, que es como lo formula el propio informe.

## SUS-004: por qué comparar sets no alcanzaba solo

La corrección natural es comparar el entitlement efectivo antes y después, y
validar cada módulo que desaparece: así da igual por dónde llegue el cambio.
Pero hay un caso que esa comparación **no** ve, y es instructivo.

Excluir `ventas` mientras `cuentas_por_cobrar` sigue activo **no retira**
`ventas` del set: el cierre de dependencias vuelve a agregarlo. O sea que la
exclusión no produce ninguna baja… y tampoco tiene efecto. Rechazarla con un
motivo claro es mejor que aceptarla y que no haga nada.

Por eso se valida la **intención** además del **efecto**: la primera cubre la
exclusión explícita, la segunda cubre el cambio de plan, la suspensión y el
DELETE. Lo detectó una prueba existente que empezó a fallar.

## Cambios de conducta observables

1. **Suspender un negocio ahora suspende.** Antes le habilitaba todos los
   módulos. **Si alguna instalación tiene hoy `activa=False` y opera con
   funciones premium, esas funciones desaparecen al desplegar** — que es lo
   correcto, pero conviene saberlo antes.
2. **`plan=null` deja solo los módulos core**, no todos.
3. **Un override de sucursal apagado ahora bloquea los endpoints API** de ese
   módulo, y un token de servicio con usuario sin negocio deja de ser
   fail-open.
4. **Bajar de plan, suspender o borrar un override pueden devolver 400** si hay
   datos en vuelo o dependientes activos. Antes las tres rutas esquivaban el
   guard.
5. **Un cambio de entitlement converge entre workers en ≤30 s** en vez de hasta
   300.

## Despliegue

**Sin migraciones.**

> **Revisar antes de desplegar:**
> `SuscripcionNegocio.objects.filter(Q(activa=False) | Q(plan__isnull=True))`.
> Cada fila ahí opera **hoy** con todos los módulos y pasará a operar con los
> que le correspondan. Si alguna estaba suspendida "de mentira" —usada como
> forma de dejar el plan abierto— hay que darle un plan explícito antes.

**Recomendación de infraestructura (tercera vez):** Redis como caché compartido
en el cloud. Permisos, configuración y ahora entitlements pagan el mismo precio
—TTL corto para no discrepar entre los tres workers de Gunicorn— y los tres lo
recuperan con un backend compartido.

## Lo que no se tocó, y por qué

Los cinco P1 restantes forman un bloque distinto: no son el resolutor sino **el
enforcement disperso y el onboarding**.

- **SUS-006** — CxC y reportes on-demand responden por URL aunque el plan no
  incluya el módulo. Es agregar `requiere_modulo` a esas vistas HTML; se dejó
  fuera porque cambia el acceso a dos módulos completos y merece verificarse
  contra planes reales antes.
- **SUS-007** — plantillas y sync leen flags legacy mientras los servicios leen
  el entitlement. Unificarlo es retirar los flags, y eso toca el pull.
- **SUS-008 y SUS-009** — el bootstrap une flags entre sucursales (si A tenía
  e-CF y B no, ambas terminan con e-CF) e ignora las configuraciones legacy sin
  sucursal. Son correcciones del onboarding, con impacto en instalaciones ya
  migradas.
- **SUS-010** — los hooks de datos bloqueantes tragan cualquier excepción, así
  que un fallo de base se interpreta como «no hay datos pendientes» y autoriza
  la baja. **Es el más barato de los cinco y el más peligroso**: convierte un
  error de infraestructura en una autorización.

P2: SUS-011 a SUS-017. P3: SUS-018, SUS-019.

## Pruebas

Suite completa, serial: **1054 tests, OK.**

Módulo de regresión nuevo:
`apps/suscripciones/tests/test_auditoria_suscripciones.py` (22 pruebas), sobre
las 20 que la app ya tenía.

**Verificación por mutación.** Revertidos los cuatro hallazgos centrales, ocho
pruebas fallan:

- Con los estados confundidos (SUS-001), la suscripción suspendida vuelve a
  devolver todas las keys.
- Sin el namespace de tenant (SUS-002), `1 != 2`.
- Sin el guard por efecto y sin `perform_destroy` (SUS-004), `200 != 400` para
  el cambio de plan y la suspensión, y `204 != 400` para el DELETE.

**Dos correcciones de mis propias pruebas.** La primera: el test de DELETE
fallaba con `204 != 400` y **el código tenía razón** — borrar un override
`incluido=True` redundante con el plan no retira nada. Se cambió a un escenario
donde el override es la única fuente. La segunda: intenté construir el dato
bloqueante con una `CuentaPorCobrar` real y fui descubriendo campos requeridos
de a uno; el informe ya indicaba el método correcto —simular el guard como
bloqueante— porque lo que se prueba es el **cableado**, no la lógica de
`puede_desactivarse`, que tiene sus propias pruebas.
