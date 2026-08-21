# Auditoría profunda de código - `apps/reportes`

Fecha: 2026-08-20  
Revisión de cierre: `3f22385`  
Modo: lectura, pruebas y documentación; no se aplicaron correcciones funcionales.

## Resumen ejecutivo

`apps/reportes` concentra información de ventas, pagos, CxC, inventario y caja,
pero hoy combina conceptos distintos —dashboard operativo, consultas on-demand,
snapshots y cierre físico— sin una frontera clara. Los cálculos básicos del
dashboard usan fechas locales y filtran ventas completadas correctamente; sin
embargo, los riesgos encontrados afectan confidencialidad, aislamiento por
sucursal y exactitud financiera.

Los problemas más urgentes son:

- Los PDFs de cierre se guardan con nombre predecible bajo una ruta `/media/`
  pública, que evita el control de permiso del endpoint de descarga.
- El reporte que acepta una “fecha de corte” histórica o futura siempre calcula
  el inventario actual y lo etiqueta con la fecha solicitada.
- Un permiso asignado solo a la sucursal A habilita consultas consolidadas que
  incluyen ventas, inventario y usuarios de B.
- El primer cierre creado para una fecha queda congelado: nuevas ventas,
  anulaciones o reversas posteriores nunca lo actualizan.
- El comando de cierre automático persiste el cierre y el PDF, pero después
  falla siempre al intentar usar campos inexistentes del modelo de auditoría.
  Bajo DB-per-tenant tampoco establece un tenant activo.
- Los helpers marcados `@transaction.atomic` abren la transacción en `default`,
  no en el alias tenant al que el router envía las escrituras.
- Dos tenants que generan un cierre para la misma fecha escriben el mismo path
  de PDF, sin el prefijo de media tenant ya disponible en el proyecto.

Se documentan **16 hallazgos**:

| Prioridad | Cantidad | Criterio |
| --- | ---: | --- |
| P1 | 7 | Puede exponer datos financieros, romper aislamiento tenant/sucursal o presentar cortes materialmente incorrectos. |
| P2 | 6 | Puede producir reportes engañosos, snapshots inexistentes/inconsistentes o degradación operativa importante. |
| P3 | 3 | Afecta claridad del contrato de permisos, presentación y diagnóstico. |

La validación seleccionada terminó con **31/31 pruebas existentes aprobadas**.
Además se reprodujeron **12 condiciones adversariales** sobre la base de
pruebas. Las reproducciones fueron temporales y no se incorporaron al código.

## Alcance

Se inspeccionaron completamente:

- `apps/reportes/models.py`
- `apps/reportes/report_manager.py`
- `apps/reportes/views.py`
- `apps/reportes/pdf_generator.py`
- `apps/reportes/management/commands/generar_cierre_diario.py`
- `apps/reportes/urls.py`
- `apps/reportes/admin.py`
- `apps/reportes/tests/`
- `templates/reportes/`

También se trazaron dependencias relevantes en:

- `apps/ventas/models.py`
- `apps/inventario/models.py`
- `apps/cuentas_por_cobrar/models.py`
- `apps/caja/models.py`
- `apps/permisos/engine.py` y `apps/permisos/catalogo.py`
- `apps/tenancy/router.py`, `context.py`, `media.py` y `management/base.py`
- `apps/auditoria/models.py`
- `config/urls.py`
- `instalar_cierre.ps1`

El núcleo suma 1,527 líneas Python en los archivos principales y 1,604 líneas
de plantillas. La app tiene solo tres pruebas propias.

La auditoría comenzó en `e1cd524`. Durante la revisión, un cambio externo llevó
el workspace a `3f22385`. La comparación entre ambos commits no mostró cambios
en `apps/reportes`, `templates/reportes`, `config/urls.py`, `instalar_cierre.ps1`
ni las dependencias de caja, tenancy y permisos citadas aquí. No se revirtieron
ni alteraron las correcciones externas; el informe queda trazado al HEAD final.

## Hallazgos P1

### RPT-001 - Los PDFs financieros tienen una ruta pública y predecible

- Severidad: crítica.
- Tipo: control de acceso / exposición de información.
- Evidencia:
  - El endpoint oficial exige `reportes.consolidado.ver` antes de descargar un
    cierre (`apps/reportes/views.py:798-835`).
  - El PDF se guarda como
    `MEDIA_ROOT/reportes/cierres/cierre_YYYYMMDD.pdf`
    (`apps/reportes/pdf_generator.py:27-31`).
  - `config/urls.py:54-56` publica todo `MEDIA_ROOT` mediante
    `django.views.static.serve`, sin login ni permiso, y lo hace sin condicionar
    la ruta a `DEBUG`.
- Reproducción validada:
  - `/media/reportes/cierres/cierre_20260820.pdf` resolvió directamente a la
    vista `serve`, no a `descargar_pdf_cierre` ni a un gate de autenticación.
- Impacto:
  - Quien conozca el host puede enumerar fechas y descargar cierres con ventas,
    cobros y desglose por cajero sin autenticarse.
  - El control del endpoint `/reportes/pdf/cierre/<id>/` no protege el mismo
    archivo cuando se accede por `/media/`.
- Recomendación:
  - No publicar documentos financieros como media pública.
  - Guardarlos en almacenamiento privado y entregarlos únicamente mediante una
    vista autorizada o una URL firmada de vida corta.
  - En producción, retirar el `serve` global de Django y configurar por separado
    media pública y documentos privados.
- Prueba de aceptación sugerida:
  - Un usuario anónimo y un usuario sin permiso deben recibir 404/403 tanto por
    ID como por cualquier path de media conocido o adivinado.

### RPT-002 - La fecha de corte del inventario no representa inventario histórico

- Severidad: crítica.
- Tipo: exactitud financiera / contrato engañoso.
- Evidencia:
  - El endpoint acepta una fecha arbitraria y luego consulta todos los lotes que
    tienen stock **ahora**, sin filtrar movimientos ni reconstruir el estado a
    esa fecha (`apps/reportes/views.py:614-625`).
  - Devuelve los valores actuales rotulados con la fecha recibida
    (`apps/reportes/views.py:675-684`).
  - `ReporteManager.generar_inventario_valorizado` repite el mismo patrón: usa
    productos y cantidades actuales, aunque recibe `fecha`
    (`apps/reportes/report_manager.py:169-240`).
  - No hay rechazo de fechas futuras.
  - Si ya existe un snapshot, el manager lo conserva, pero la vista vuelve a
    calcular y devolver datos actuales; respuesta y fila persistida pueden
    representar cifras diferentes para la misma fecha
    (`apps/reportes/views.py:619-684` y
    `apps/reportes/report_manager.py:177-182`).
- Reproducciones validadas:
  - Un lote creado hoy con 10 unidades apareció en un corte etiquetado
    `2020-01-01`.
  - Después de bajar el lote de 10 a 4, una segunda petición para `2020-01-02`
    devolvió 4, mientras el snapshot guardado para esa misma fecha siguió en 10.
  - `2099-12-31` fue aceptado y persistido como fecha de snapshot con stock
    actual.
- Impacto:
  - Un reporte presentado como corte histórico puede usarse en contabilidad,
    auditoría o seguros aunque nunca haya representado el inventario de ese día.
  - Dos consumidores pueden recibir verdades distintas para la misma fecha.
- Recomendación:
  - Elegir un contrato explícito:
    - “inventario actual”: eliminar la fecha elegible y usar timestamp real; o
    - “inventario histórico”: reconstruir cantidades desde movimientos hasta el
      instante de corte, o leer un snapshot que fue capturado realmente entonces.
  - Rechazar fechas futuras y devolver el ID/fecha de generación del snapshot
    que sustenta la respuesta.
- Prueba de aceptación sugerida:
  - Compras, ventas y ajustes posteriores al corte no pueden modificar su
    resultado; dos consultas del mismo snapshot deben ser idénticas.

### RPT-003 - El permiso por sucursal habilita reportes globales

- Severidad: alta.
- Tipo: autorización horizontal / alcance de datos.
- Evidencia:
  - `es_admin` consulta `user.tiene_permiso('reportes.consolidado.ver')` sin
    pasar sucursal (`apps/reportes/views.py:325-329`).
  - El motor RBAC, cuando recibe `sucursal=None`, considera todas las
    asignaciones activas del usuario (`apps/permisos/engine.py:96-119`).
  - Ventas por período, top, inventario y ventas por cajero construyen querysets
    globales sin `sucursal` (`apps/reportes/views.py:451-484`, `:549-562`,
    `:619-625` y `:715-731`).
  - La pantalla on-demand lista todos los usuarios activos, no los cajeros de la
    sucursal autorizada (`apps/reportes/views.py:336-355`).
  - `CierreCaja`, `TopProducto` e `InventarioValorizado` no guardan sucursal
    (`apps/reportes/models.py:8-225`).
- Reproducción validada:
  - Un rol con `reportes.consolidado.ver` asignado únicamente a A consultó un
    período con una venta de 100 en A y otra de 250 en B. La API devolvió dos
    ventas y total 350.00.
- Impacto:
  - La asignación “solo A” no limita ventas, costos, inventario ni usuarios de B.
  - En una instalación con varias sucursales, el alcance indicado por RBAC no
    coincide con el alcance de los datos.
- Recomendación:
  - Definir por separado `reportes.sucursal.ver` y
    `reportes.consolidado.ver` global.
  - Resolver sucursales efectivas antes del gate y reutilizar el mismo scope en
    todos los querysets, listas de usuarios, snapshots y PDFs.
  - Una asignación acotada a A nunca debe otorgar consolidación de B.
- Prueba de aceptación sugerida:
  - Matriz A/B para cada endpoint y descarga, incluyendo un rol global explícito
    que sí pueda consolidar ambas.

### RPT-004 - El primer cierre diario queda congelado aunque cambien los datos

- Severidad: alta.
- Tipo: consistencia financiera / lifecycle.
- Evidencia:
  - `generar_cierre_diario` devuelve inmediatamente el registro existente y no
    recalcula (`apps/reportes/report_manager.py:28-31`).
  - La fecha es única y el modelo no tiene versión, estado de reemplazo ni
    timestamp de última reconstrucción (`apps/reportes/models.py:13-17` y
    `:83-114`).
  - El endpoint permite generar el cierre del día actual en cualquier momento
    (`apps/reportes/views.py:373-390`).
  - Ventas tardías, anulaciones, pagos CxC anulados o correcciones posteriores no
    invalidan el cierre.
- Reproducción validada:
  - Se generó un cierre con una venta de 100; después se añadió otra de 50 y se
    volvió a ejecutar el manager. Retornó el mismo ID y total 100, aunque había
    dos ventas completadas por 150.
- Impacto:
  - Un cierre preliminar puede convertirse silenciosamente en el registro
    definitivo del día.
  - Reintentar el comando da apariencia de idempotencia, pero conserva datos
    obsoletos.
- Recomendación:
  - Separar “borrador/recalculable” de “cierre final”.
  - Solo congelar después de verificar turnos cerrados y aplicar una política de
    ajustes posteriores; cualquier regeneración debe versionarse y auditarse.
  - Mostrar fecha de cálculo y versión en pantalla/PDF.
- Prueba de aceptación sugerida:
  - Una mutación posterior debe regenerar una nueva versión o quedar bloqueada
    por una regla contable explícita, nunca ignorarse silenciosamente.

### RPT-005 - La automatización de cierre persiste datos y luego falla siempre

- Severidad: crítica para operación automática.
- Tipo: comando operativo / auditoría / consistencia parcial.
- Evidencia:
  - El comando genera el cierre, genera el PDF y guarda el path antes de auditar
    (`apps/reportes/management/commands/generar_cierre_diario.py:31-42`).
  - Luego llama `Auditoria.objects.create` con `tabla`, `registro_id` e
    `importancia` (`:56-64`), campos que no existen en el modelo actual. El
    modelo usa `content_type/object_id` y `nivel_importancia`
    (`apps/auditoria/models.py:103-131` y `:205-209`).
  - El bloque de error repite los mismos argumentos inválidos (`:66-78`).
  - La clase hereda directamente de `BaseCommand`; no solicita tenant ni usa
    `TenantCommandMixin` (`:1-9`). Bajo DB-per-tenant una consulta de reportes
    sin contexto es rechazada por el router
    (`apps/tenancy/router.py:63-71`).
  - `instalar_cierre.ps1:2` apunta a
    `scripts/ejecutar_servicio_cierre.bat`, archivo ausente del repositorio.
    Además configura un servicio de autoarranque, no una programación diaria,
    y su descripción dice 7 PM mientras el modelo documenta 10 PM.
- Reproducciones validadas:
  - El comando creó el cierre, guardó `archivo_pdf` y luego lanzó
    `TypeError: Auditoria() got unexpected keyword arguments...`.
  - Con tenancy activado y sin contexto, el manager lanzó
    `TenantContextError`.
- Impacto:
  - El scheduler registra fallo aunque el sistema ya mutó datos y archivos.
  - Los reintentos encuentran el cierre congelado y vuelven a fallar al auditar.
  - La automatización incluida no puede instalarse de forma reproducible desde
    este checkout ni recorrer tenants de forma segura.
- Recomendación:
  - Migrar el comando a `TenantCommandMixin` o a un orquestador que itere tenants
    activos explícitamente.
  - Usar `Auditoria.registrar` con campos válidos y una acción formal del
    catálogo.
  - Versionar un launcher real y usar Task Scheduler/cron con hora y zona
    documentadas; no NSSM auto-start para una tarea one-shot.
  - Definir el comportamiento de reintento después de PDF o auditoría fallidos.
- Prueba de aceptación sugerida:
  - Ejecución local y por tenant debe terminar código 0, crear una auditoría y
    ser reintentable sin ocultar ni duplicar efectos.

### RPT-006 - `transaction.atomic` protege `default`, no la base tenant

- Severidad: alta cuando `TENANCY_DB_PER_TENANT_ENABLED=true`.
- Tipo: transacciones multi-base / atomicidad aparente.
- Evidencia:
  - Los tres generadores se decoran con `@transaction.atomic` sin argumento
    `using` (`apps/reportes/report_manager.py:18-20`, `:117-119` y `:167-169`).
  - Django usa `default` cuando no se especifica alias.
  - El router envía modelos de negocio al alias tenant activo
    (`apps/tenancy/router.py:49-71`).
- Impacto:
  - Las lecturas y escrituras tenant ocurren fuera de la transacción abierta en
    `default`.
  - El delete + recreate de top productos puede quedar parcial si falla a mitad,
    aunque el código parezca atómico.
  - Los reportes pueden mezclar datos que cambian durante la generación.
- Recomendación:
  - Resolver el alias tenant y usar `transaction.atomic(using=alias)`.
  - Ejecutar todas las operaciones del manager con `.using(alias)` o un helper
    de servicio que garantice una sola conexión de dominio.
  - Agregar una prueba multi-DB que falle dentro del bloque y compruebe rollback
    real en la base tenant.

### RPT-007 - Los PDFs de distintos tenants colisionan por fecha

- Severidad: alta cuando varios tenants comparten filesystem o container.
- Tipo: aislamiento de media / sobrescritura.
- Evidencia:
  - `_build_path` solo incorpora la fecha, no tenant, cierre ni versión
    (`apps/reportes/pdf_generator.py:27-31`).
  - El proyecto ya posee `tenant_media_name` y exige prefijo tenant cuando
    tenancy está activo (`apps/tenancy/media.py:16-61`), pero reportes no lo usa.
  - `archivo_pdf` guarda un path de hasta 500 caracteres, normalmente absoluto
    (`apps/reportes/models.py:102-108`).
- Reproducción validada:
  - Dos cierres distintos con fecha `2026-08-20` produjeron exactamente la misma
    ruta de salida.
- Impacto:
  - El PDF del tenant B puede sobrescribir el de A; ambos registros apuntan al
    mismo archivo final.
  - Combinado con RPT-001, la colisión puede convertirse en fuga cross-tenant.
- Recomendación:
  - Guardar nombres relativos bajo prefijo tenant y un identificador/versionado
    no predecible, usando el storage configurado en vez de `open`/filesystem
    local directo.
  - Hacer la escritura atómica mediante archivo temporal y rename cuando el
    backend lo permita.

## Hallazgos P2

### RPT-008 - `CierreCaja` no es una conciliación de caja física

- Severidad: alta por ambigüedad contable.
- Tipo: modelo de dominio / nombre engañoso.
- Evidencia:
  - El reporte diario agrega ventas completadas, pagos, cobros CxC y anulaciones
    por fecha (`apps/reportes/report_manager.py:33-109`).
  - No consulta `Caja`, `TurnoCaja` ni `MovimientoCaja`.
  - El cierre físico real sí maneja fondo de apertura, efectivo esperado,
    retiros, gastos, ingresos, contado y diferencia
    (`apps/caja/models.py:201-299`).
  - `CierreCaja` carece de caja, turno, monto contado, esperado y diferencia
    (`apps/reportes/models.py:8-123`).
- Impacto:
  - El documento titulado “Cierre de caja” puede confundirse con una conciliación
    física aunque sea un resumen comercial consolidado.
  - No permite explicar faltantes/sobrantes ni verificar que todos los turnos
    fueron cerrados.
- Recomendación:
  - Renombrarlo “Resumen diario de ventas y cobros”, o construir un cierre
    consolidado a partir de `TurnoCaja` cerrados y sus diferencias.
  - Mostrar explícitamente qué cifras son facturación, flujo y conciliación.

### RPT-009 - El snapshot de top productos siempre falla y oculta datos ficticios

- Severidad: media-alta.
- Tipo: bug funcional / dato financiero falso.
- Evidencia:
  - El manager agrega `Sum('total')` sobre `DetalleVenta`
    (`apps/reportes/report_manager.py:137-143`), pero el campo real es
    `total_linea` (`apps/ventas/models.py:301-305`).
  - Después pretende guardar `margen_promedio = 25.0` como placeholder
    (`apps/reportes/report_manager.py:147-160`).
  - El endpoint calcula un ranking correcto por separado, llama al manager y
    silencia cualquier excepción (`apps/reportes/views.py:549-581`).
- Reproducción validada:
  - El manager lanzó `FieldError` por el campo inexistente.
  - El endpoint respondió HTTP 200 con `success=true`, pero no creó ningún
    `TopProducto`.
- Impacto:
  - El usuario cree que se generó un reporte persistente cuando la tabla sigue
    vacía.
  - Si se corrige solo el nombre del campo, comenzará a persistirse un margen
    inventado de 25%, potencialmente más peligroso que el fallo actual.
- Recomendación:
  - Eliminar el snapshot si no tiene consumidor o corregirlo como una sola fuente
    de verdad usada por la respuesta.
  - Calcular margen desde `total_linea - costo_fifo`, con ponderación definida;
    nunca persistir placeholders como cifras reales.

### RPT-010 - La lista de cajeros permite XSS almacenado en la página on-demand

- Severidad: media-alta.
- Tipo: seguridad frontend / serialización insegura.
- Evidencia:
  - La vista entrega una lista de diccionarios de usuarios
    (`apps/reportes/views.py:346-354`).
  - La plantilla la inserta dentro de `<script>` mediante
    `cajeros: {{ cajeros|safe }}`
    (`templates/reportes/on_demand.html:9-18`).
  - La misma app usa correctamente `json_script` para las métricas de dashboard,
    por lo que existe un patrón seguro disponible
    (`templates/reportes/dashboard.html:11`).
- Reproducción validada:
  - Un nombre de usuario con `</script><script>window.__audit_xss=1</script>`
    apareció sin escapar dentro de la respuesta HTML y cerró el bloque script.
- Impacto:
  - Datos almacenados en nombre/apellido/username pueden ejecutar JavaScript en
    la sesión de un administrador que abra reportes on-demand.
- Recomendación:
  - Serializar con `json_script` y leer mediante `JSON.parse(textContent)`.
  - Aplicar una prueba con `</script>`, comillas y caracteres Unicode.

### RPT-011 - Pantalla, API y PDF muestran cierres diferentes

- Severidad: media.
- Tipo: contrato de presentación / completitud.
- Evidencia:
  - El modelo calcula efectivo, transferencia, tarjeta, CxC, descuentos y
    anulaciones (`apps/reportes/models.py:19-81`).
  - `api_cierre_manual` omite tarjeta, descuentos y anulaciones de su respuesta;
    incluye CxC (`apps/reportes/views.py:402-416`).
  - La pantalla muestra total de ventas, efectivo, transferencia y cantidad;
    tampoco presenta tarjeta ni CxC
    (`templates/reportes/on_demand.html:530-546`).
  - El PDF sí incluye tarjeta y cobros CxC en flujo y descuentos/anulaciones en
    resumen (`apps/reportes/pdf_generator.py:57-83`).
- Impacto:
  - Dos representaciones del mismo cierre no permiten reconciliarse visualmente.
  - Un día dominado por tarjeta o cobros de cartera parece incompleto en la
    pantalla aunque el PDF contenga la cifra.
- Recomendación:
  - Definir un serializer único de cierre y consumirlo en API, HTML y PDF.
  - Añadir una igualdad de control: suma de componentes mostrados y explicación
    explícita de cualquier diferencia frente a ventas facturadas.

### RPT-012 - Dashboard e inventario escalan con consultas por producto

- Severidad: media.
- Tipo: rendimiento / disponibilidad.
- Evidencia:
  - El dashboard recorre todos los productos activos con stock mínimo y ejecuta
    un `SUM` de lotes por cada uno (`apps/reportes/views.py:151-169`).
  - El generador de snapshot recorre productos y hace `lotes.exists()` antes de
    iterar cada queryset (`apps/reportes/report_manager.py:184-229`).
  - El endpoint de inventario devuelve todos los lotes activos y todos sus
    detalles en una respuesta síncrona, sin paginación
    (`apps/reportes/views.py:619-684`).
- Reproducción validada:
  - Cinco productos sin lotes produjeron al menos cinco consultas `SUM` separadas
    únicamente para stock bajo.
- Impacto:
  - El tiempo del dashboard y el número de queries crecen linealmente con el
    catálogo; el snapshot puede aproximarse a dos consultas por producto.
  - Catálogos/lotes grandes pueden agotar timeout o memoria del worker.
- Recomendación:
  - Anotar stock con una agregación filtrada en un único queryset.
  - Prefetch de lotes activos para snapshots o agregación SQL por producto.
  - Paginar/streaming para el detalle y fijar un rango máximo razonable en los
    reportes por período.

### RPT-013 - La identidad de snapshots no está protegida contra concurrencia

- Severidad: media.
- Tipo: constraints / condición de carrera.
- Evidencia:
  - `InventarioValorizado.fecha` no es única
    (`apps/reportes/models.py:179-225`).
  - El manager hace “buscar y luego crear” sin lock ni constraint
    (`apps/reportes/report_manager.py:177-182` y `:234-241`).
  - `TopProducto` no tiene unicidad por período + producto
    (`apps/reportes/models.py:126-173`).
  - Su regeneración elimina filas y luego las recrea
    (`apps/reportes/report_manager.py:123-163`); bajo tenancy, RPT-006 elimina la
    protección transaccional aparente.
- Impacto:
  - Dos solicitudes concurrentes pueden crear dos inventarios para la misma
    fecha o mezclar rankings duplicados/parciales.
  - Los consumidores no tienen una clave inequívoca de versión del reporte.
- Recomendación:
  - Agregar unicidad y versionado explícito según la política elegida.
  - Usar upsert/lock en la base correcta y conservar una cabecera de generación
    con estado `GENERANDO/COMPLETO/ERROR`.

## Hallazgos P3

### RPT-014 - `reportes.ver` existe, pero el dashboard no lo aplica

- Severidad: baja-media, depende de la política deseada para cajeros.
- Tipo: contrato RBAC ambiguo.
- Evidencia:
  - El catálogo define `reportes.ver` y `reportes.consolidado.ver`
    (`apps/permisos/catalogo.py:66-69`).
  - `dashboard` y `api_metricas_hoy` solo exigen login; el alcance se decide con
    los flags legacy `es_cajera/es_admin`
    (`apps/reportes/views.py:38-105` y `:275-320`).
  - Los reportes on-demand sí usan el permiso consolidado.
- Impacto:
  - Revocar `reportes.ver` no revoca el dashboard personal; el permiso declarado
    no describe el enforcement real.
- Recomendación:
  - Si el dashboard personal es parte obligatoria del POS, documentarlo y retirar
    el permiso muerto. Si debe ser configurable, aplicar `reportes.ver` y scope
    de sucursal de forma consistente.

### RPT-015 - El PDF imprime IDs de cajero en lugar de nombres

- Severidad: baja.
- Tipo: presentación / prueba engañosa.
- Evidencia:
  - El manager usa el ID como clave del JSON y guarda el nombre dentro de
    `data['nombre']` (`apps/reportes/report_manager.py:76-95`).
  - El PDF itera esa clave y la imprime como “Cajero”, ignorando `nombre`
    (`apps/reportes/pdf_generator.py:87-103`).
  - La prueba construye manualmente `resumen_cajeros` con username como clave,
    por lo que no reproduce la forma real del manager
    (`apps/reportes/tests/test_pdf_generator.py:29-47`).
- Impacto:
  - El cierre automático muestra números internos donde el lector espera
    nombres.
- Recomendación:
  - Consumir `data['nombre']` y generar el fixture del test mediante
    `ReporteManager`.

### RPT-016 - Errores importantes se silencian o se devuelven sin contrato estable

- Severidad: baja-media.
- Tipo: observabilidad / manejo de errores.
- Evidencia:
  - La generación opcional del PDF, top productos e inventario silencian
    cualquier excepción (`apps/reportes/views.py:392-400`, `:573-581` y
    `:669-673`).
  - Los handlers genéricos devuelven `str(e)` con HTTP 500
    (`apps/reportes/views.py:421-424`, `:516-519`, `:593-596`, `:686-689` y
    `:788-791`).
  - La plantilla on-demand carga Chart.js desde CDN sin integridad ni fallback
    local (`templates/reportes/on_demand.html:6-8`), relevante para instalaciones
    POS sin Internet estable.
- Impacto:
  - La UI puede declarar éxito aunque no haya snapshot/PDF y operaciones no
    reciben una señal diagnóstica estructurada.
  - El texto de excepción puede filtrar detalles internos; sin Internet, los
    gráficos fallan aunque los datos estén disponibles.
- Recomendación:
  - Registrar excepciones con correlación, devolver códigos estables y diferenciar
    `success`, `partial_success` y `error`.
  - Servir dependencias críticas localmente o aportar fallback; no exponer el
    texto interno al cliente.

## Controles que ya están bien encaminados

- Dashboard y API usan `timezone.localdate()` y existen pruebas explícitas para
  el borde UTC/Santo Domingo.
- Las métricas excluyen ventas anuladas mediante `estado='COMPLETADA'`.
- El dashboard de cajera limita ventas y cobros CxC al usuario que los registró.
- Las agregaciones monetarias usan `Decimal`, `Sum` y `Coalesce`; la conversión a
  `float` se reserva principalmente para hidratación visual.
- El acceso on-demand usa el permiso granular
  `reportes.consolidado.ver`, no una comparación directa de rol.
- La API de cierre manual rechaza fechas futuras.
- El PDF reutiliza componentes comunes de encabezado, tablas, moneda y footer.
- Los dashboards hidratan métricas con `json_script`, patrón correcto que puede
  reutilizarse para corregir RPT-010.
- Cuando tenancy está desactivado y toda la operación vive en `default`, los
  decoradores atómicos del manager sí protegen la base local.

Estos controles explican por qué las tres pruebas actuales pasan, pero no cubren
los límites de confianza encontrados.

## Validación ejecutada

### Suite existente ampliada

Comando:

```powershell
C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py test apps.reportes apps.caja.tests apps.inventario.tests apps.permisos.tests.test_engine apps.tenancy.tests.test_media --keepdb --settings=config.settings_development
```

Resultado:

- 31 pruebas ejecutadas.
- 31 aprobadas.
- `System check identified no issues`.
- Tiempo: 21.270 s.
- Incluye las 3 pruebas propias de reportes y 28 pruebas vecinas de caja,
  inventario, RBAC y media tenant.

La corrida aislada de `apps.reportes` también terminó 3/3 en 3.995 s.

### Reproducciones adversariales temporales

| Caso | Resultado demostrado |
| --- | --- |
| Scope A/B | Permiso asignado solo en A obtuvo 2 ventas y 350.00 incluyendo B. |
| Inventario pasado | Corte 2020 incluyó lote creado actualmente. |
| Respuesta vs snapshot | Segunda respuesta bajó a 4; snapshot de la misma fecha quedó en 10. |
| Inventario futuro | Se aceptó y persistió un corte `2099-12-31`. |
| Cierre obsoleto | Venta posterior no cambió el cierre de 100. |
| Top snapshot | Manager lanzó `FieldError`; endpoint respondió éxito sin filas. |
| Comando automático | Persistió cierre/PDF y luego falló por campos de auditoría. |
| XSS almacenado | `</script><script>...` quedó crudo en la página on-demand. |
| Colisión PDF | Dos cierres de la misma fecha resolvieron el mismo path. |
| Media pública | La URL conocida resolvió directamente a `serve`. |
| N+1 | Cinco productos provocaron al menos cinco agregaciones de stock. |
| Tenancy sin contexto | El manager fue rechazado por `TenantContextError`. |

Resultado: 12 condiciones de riesgo reproducidas.

Nota de transparencia: en la primera pasada, diez de once casos terminaron
verdes y el de scope falló porque la aserción esperaba el texto `350` mientras
la API devolvió el valor equivalente `350.00`. Se corrigió únicamente la
comparación temporal a `Decimal` y el caso se reejecutó 1/1. La condición de
fuga ya estaba demostrada en la respuesta original. El caso tenancy se ejecutó
por separado y terminó 1/1.

## Cobertura que falta antes de corregir

- Acceso anónimo y sin permiso a documentos por endpoint, media y storage.
- Scope A/B para cada métrica, usuario, cierre, snapshot y PDF.
- Paridad entre corte actual, snapshot real e inventario reconstruido histórico.
- Cierres generados antes/después de ventas tardías, anulaciones y reversas CxC.
- Comando automático local y por tenant, incluyendo auditoría y reintentos.
- Rollback real en alias tenant para cada manager.
- Dos tenants generando PDF de la misma fecha en storage compartido.
- Contrato único API/HTML/PDF con tarjeta, CxC, descuentos y anulaciones.
- Top productos con `total_linea` y margen FIFO real.
- XSS con nombres, username, SKU y nombre de producto.
- Concurrencia en snapshots y regeneración de ranking.
- Presupuesto de queries con 1, 100 y 10,000 productos/lotes.
- Política efectiva de `reportes.ver` para cajeros.
- Fixture PDF generado desde el manager, no JSON manual divergente.

## Orden sugerido de corrección

1. Privatizar los PDFs y cerrar colisiones tenant (RPT-001 y RPT-007).
2. Corregir o retirar el contrato histórico de inventario (RPT-002).
3. Aplicar scope de sucursal a todo el módulo (RPT-003).
4. Definir lifecycle/versionado del cierre y la diferencia entre resumen diario
   y conciliación física (RPT-004 y RPT-008).
5. Reparar la automatización y atomicidad multi-base (RPT-005 y RPT-006).
6. Eliminar el falso éxito/falso margen del top (RPT-009).
7. Cerrar XSS y unificar el contrato de presentación (RPT-010 y RPT-011).
8. Optimizar queries, fijar identidad de snapshots y completar observabilidad
   (RPT-012, RPT-013 y RPT-016).
9. Alinear permisos y detalles PDF (RPT-014 y RPT-015).

## Trazabilidad del snapshot

Hashes SHA-256 al cierre:

| Archivo | SHA-256 |
| --- | --- |
| `apps/reportes/models.py` | `E78384D20E64437C6105EA42804E9AD5A63BAA2E8B1AEFEF7E64385D50B8FFDE` |
| `apps/reportes/report_manager.py` | `9CEC7FC36C2D426F1976B9F63843A942EE28A846047E7615F287D33FAAE254DC` |
| `apps/reportes/views.py` | `49ED536E3CECE3258BAF4DE56F5F02DFDEE8DB949050B7C75C3475C63791E2DE` |
| `apps/reportes/pdf_generator.py` | `5E23D4780061306BDC9AE8629F5CBD862DEAB743505151A2BFDB8E4C9C0CF21E` |
| `apps/reportes/management/commands/generar_cierre_diario.py` | `D5043E2F8B040E181A6F2346C01E6CC57FD0FA0950D0F910929644ECDA2B21DD` |
| `config/urls.py` | `A9F27D8912E834E9B68F53DB4AF75C9B23D9AF8461703EF00D902E3B19520442` |

## Cierre

La prioridad no debería ser añadir más gráficos. Primero hay que garantizar que
cada cifra responde tres preguntas: de qué sucursal/tenant proviene, a qué
instante real corresponde y qué documento autorizado la respalda. Después de
cerrar esos límites, el módulo puede conservar buena parte de sus consultas y
componentes visuales actuales, pero sobre contratos financieros verificables.
