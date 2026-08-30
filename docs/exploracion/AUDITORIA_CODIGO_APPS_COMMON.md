# Auditoría profunda de código - `apps/common`

Fecha: 2026-08-28  
Revisión inicial: `cc103df`  
Revisión al cierre: `8564e3f`  
Modo: lectura, pruebas y documentación; no se aplicaron correcciones funcionales.

Nota de concurrencia: `apps/common` estaba limpio al comenzar la revisión. Durante
la auditoría el usuario confirmó sus correcciones pendientes y `HEAD` avanzó de
`cc103df` a `8564e3f`. Se compararon ambas revisiones: no cambió la biblioteca PDF,
sus cuatro consumidores, `get_config()`, los requirements ni el Dockerfile. Las
ediciones del usuario se preservaron y la validación final se ejecutó sobre la
revisión de cierre. Después de esa ejecución aparecieron nuevas correcciones no
confirmadas en `apps/negocios/utils.py` y endpoints API de reporting; no modifican
`apps/common/pdf/standard.py` ni los cuatro generadores PDF trazados, pero se
consideran posteriores y no revalidadas por esta suite.

## Resumen ejecutivo

`apps/common` no es una Django app registrada: es una biblioteca transversal de
390 líneas que define el formato común de PDFs. La consumen cotizaciones, estados
de cuenta, cierres diarios y facturas de financiación. Por eso un defecto pequeño
puede repetirse en documentos comerciales y financieros distintos.

El hallazgo más grave es de identidad multi-sucursal: todos los generadores
conocen, directa o indirectamente, la sucursal del documento, pero resuelven el
encabezado mediante `get_config()`, que usa `settings.SUCURSAL_CODIGO`. Una
cotización originada en B puede imprimirse con nombre, RNC, dirección, teléfono y
logo de A.

Los demás riesgos principales son:

- Un importe corrupto se representa silenciosamente como `$0.00`; `NaN` e
  infinitos producen cadenas que parecen campos monetarios, pero no son importes
  válidos.
- Textos sin límite pueden hacer que ReportLab lance `LayoutError`. Los datos que
  llegan al helper incluyen notas y direcciones almacenadas en `TextField`.
- Filas y vectores de ancho inconsistentes no se validan. Una columna adicional
  expandió la tabla a 777.6 puntos sobre un área disponible de 518.4, por lo que
  contenido financiero puede quedar fuera de página sin error.
- Un logo corrupto rompe el encabezado; un fallo de storage, en cambio, se traga
  y genera silenciosamente un documento sin logo. Los logos remotos se leen
  completos en memoria y sin límite.
- El pie usa la hora local del host y coordenadas Carta globales aunque el
  documento declare otro tamaño/orientación.
- Las dependencias de producción se instalan con rangos abiertos, aunque existen
  snapshots exactos que el Dockerfile no utiliza.

Se documentan **15 hallazgos**:

| Prioridad | Cantidad | Criterio |
| --- | ---: | --- |
| P1 | 1 | Puede atribuir un documento a la sucursal o identidad fiscal incorrecta. |
| P2 | 12 | Puede falsear presentación financiera, truncar contenido, provocar 500, agotar recursos o producir builds no reproducibles. |
| P3 | 2 | Deteriora identidad visual y deja contratos críticos sin cobertura suficiente. |

> **Estado (2026-08-30): P1 MITIGADO (1/1) + 5 P2.** Los 15 hallazgos se
> verificaron contra el código. El P1 está corregido, junto con COM-002,
> COM-003, COM-004, COM-010, COM-011 y COM-015. Ver
> [Estado de mitigación](#estado-de-mitigación) al final.
> **Sin migraciones.** Incluye dos cambios visibles en todos los documentos:
> el símbolo de moneda pasa a `RD$` y un importe corrupto ya no se imprime
> como cero.

La suite seleccionada terminó con **19/19 pruebas existentes aprobadas**. La app
aporta **2 pruebas propias**. Una batería adversarial temporal terminó con
**18/18 reproducciones confirmadas** y se retiró del workspace. También pasó
`manage.py check`; `apps/common` no tiene modelos, no está en `INSTALLED_APPS` y
no le aplica una comprobación de migraciones.

## Alcance

Se inspeccionaron completamente:

- `apps/common/pdf/standard.py`
- `apps/common/tests/test_pdf_standard.py`
- inicializadores del paquete

Se trazaron todos los consumidores directos:

- `apps/cotizaciones/pdf_generator.py`
- `apps/cuentas_por_cobrar/pdf_generator.py`
- `apps/reportes/pdf_generator.py`
- `apps/ventas/pdf_financiacion.py`
- pruebas de logos remotos en `apps/tenancy/tests/test_media.py`
- configuración por sucursal y almacenamiento de logos
- requirements locales/cloud y la instalación del Dockerfile

La revisión cubrió escape de texto, dinero, fechas, zona horaria, geometría,
tablas, logos locales/remotos, memoria, errores de maquetación, identidad de
sucursal y cobertura. No se auditó otra vez el control de acceso de los endpoints;
ese contrato pertenece a las auditorías de sus apps consumidoras.

## Hallazgo P1

### COM-001 - El encabezado puede pertenecer a otra sucursal

- Tipo: aislamiento multi-sucursal / identidad legal / integridad documental.
- Evidencia:
  - `get_config()` resuelve exclusivamente desde `settings.SUCURSAL_CODIGO`; sin
    código cae a la primera configuración (`apps/configuracion/utils.py:14-47`).
  - `CotizacionPDF` recibe una cotización con FK `sucursal`, pero en el constructor
    llama `get_config()` sin usarla (`apps/cotizaciones/pdf_generator.py:25-35`).
  - `EstadoCuentaPDF` repite el patrón, aunque sus cuentas conocen sucursal
    (`apps/cuentas_por_cobrar/pdf_generator.py:24-32`).
  - El cierre conoce `cierre.sucursal`, pero también usa el config global
    (`apps/reportes/pdf_generator.py:43-60`).
  - La factura de financiación conoce `venta.sucursal` y hace lo mismo
    (`apps/ventas/pdf_financiacion.py:25-35`).
  - `business_header()` imprime nombre, RNC, teléfono, dirección y logo del config
    recibido (`apps/common/pdf/standard.py:233-273`).
- Reproducción validada:
  - Se crearon sucursales A y B con configuraciones `Identidad A / RNC 101` e
    `Identidad B / RNC 202`.
  - Con `SUCURSAL_CODIGO=PDF-A`, se construyó `CotizacionPDF` para una cotización
    cuya sucursal era B. El generador seleccionó la configuración A.
- Impacto:
  - Una cotización, estado de cuenta o factura puede mostrar datos fiscales y
    marca de una tienda distinta al hecho que documenta.
  - En una disputa, el PDF no representa de forma confiable quién emitió el
    documento.
- Recomendación:
  - Hacer obligatorio pasar al encabezado la configuración derivada de la
    sucursal del objeto/snapshot. No resolver contexto global dentro del helper.
  - Para documentos consolidados, definir una identidad explícita de negocio, no
    elegir accidentalmente la instalación actual.
- Prueba de aceptación sugerida:
  - Generar alternadamente documentos A/B en el mismo proceso siempre imprime
    nombre, RNC, dirección, teléfono y logo de la sucursal del objeto, sin depender
    de settings, caché o request previo.

## Hallazgos P2

### COM-002 - Un importe inválido se convierte silenciosamente en cero

- Tipo: integridad financiera / manejo de errores.
- Evidencia:
  - `money()` captura `InvalidOperation`, `TypeError` y `ValueError`, asigna
    `Decimal('0.00')` y no informa el fallo
    (`apps/common/pdf/standard.py:170-175`).
- Reproducción validada:
  - `money('importe-corrupto')` devolvió exactamente `$0.00`.
- Impacto:
  - Un dato derivado/importado corrupto puede presentarse como ausencia real de
    deuda, descuento o pago. El PDF queda bien formado pero materialmente falso.
- Recomendación:
  - Fallar explícitamente o devolver un marcador inequívoco de dato inválido y
    registrar contexto. La capa de dominio debe entregar `Decimal` finito.
- Prueba de aceptación sugerida:
  - Texto, objetos incompatibles y errores de cálculo impiden emitir el documento
    como válido y generan una señal observable; cero real sigue imprimiéndose como
    cero.

### COM-003 - Se aceptan no-finitos y la moneda queda ambigua

- Tipo: representación financiera / contrato monetario.
- Evidencia:
  - `Decimal(str(value))` admite `NaN` e infinitos; no se comprueba `is_finite()`
    (`apps/common/pdf/standard.py:170-175`).
  - El formato fija `$` y separadores estadounidenses sin código de moneda.
- Reproducción validada:
  - Se obtuvieron `$NaN`, `$Infinity` y `$-Infinity`.
  - RD$1,234.50 se presenta como `$1,234.50`, sin distinguir DOP de USD.
- Impacto:
  - Un documento puede contener un “importe” no computable o una moneda
    interpretable como dólares estadounidenses.
- Recomendación:
  - Rechazar todo Decimal no finito. Definir moneda en configuración/contrato y
    presentar `RD$` o `DOP` de manera inequívoca.
- Prueba de aceptación sugerida:
  - NaN/infinito fallan antes del build; cada documento declara moneda y mantiene
    un formato consistente en totales, líneas y notas.

### COM-004 - Texto sin límite puede impedir generar el PDF

- Tipo: disponibilidad / agotamiento de recursos / contenido no acotado.
- Evidencia:
  - `clean()` escapa el texto, pero no limita longitud ni estrategia de corte
    (`apps/common/pdf/standard.py:150-167`).
  - `info_grid()` coloca el contenido completo dentro de una fila de `Table`
    (`:288-317`). ReportLab no puede dividir una única fila más alta que el frame.
  - Consumidores pasan notas/direcciones provenientes de `TextField`, por ejemplo
    notas de cotización (`apps/cotizaciones/pdf_generator.py:92-99`).
- Reproducción validada:
  - Una nota de 200,000 caracteres produjo una fila de 27,291 puntos y
    `LayoutError`; el documento no fue generado.
- Impacto:
  - Un dato válido para la base puede convertir el endpoint PDF en 500 y bloquear
    una operación o descarga financiera.
- Recomendación:
  - Definir límites de dominio y una política de truncado/anexo/paginación. No
    colocar texto arbitrario completo en una fila indivisible.
- Prueba de aceptación sugerida:
  - El máximo permitido se genera y pagina; entradas superiores se rechazan al
    capturarse o se presentan con una indicación explícita de truncado.

### COM-005 - La forma de las tablas no se valida y puede desbordar la página

- Tipo: integridad visual / truncamiento silencioso.
- Evidencia:
  - `standard_table()` calcula columnas desde `headers`, pero agrega cada fila con
    su longitud real sin comprobar igualdad (`apps/common/pdf/standard.py:341-365`).
  - ReportLab amplía `_ncols` al máximo encontrado y replica/ajusta anchos sin que
    el helper lo detecte.
- Reproducción validada:
  - Dos headers y una fila de tres valores produjeron tres columnas de 259.2
    puntos: ancho total 777.6 sobre `CONTENT_WIDTH=518.4`.
  - Tres columnas con solo dos anchos normalizados produjeron el mismo desborde.
- Impacto:
  - La última columna puede quedar fuera de página o cortada mientras el build
    termina exitosamente, ocultando importes/estados sin aviso.
- Recomendación:
  - Exigir longitudes exactas para headers, filas, aligns y col_widths; comprobar
    ancho final contra el frame.
- Prueba de aceptación sugerida:
  - Cualquier inconsistencia produce un error de contrato antes de ReportLab;
    tablas válidas envuelven al ancho declarado en todas las páginas.

### COM-006 - Estructuras vacías y dimensiones inválidas propagan excepciones crudas

- Tipo: robustez de API interna / disponibilidad.
- Evidencia:
  - `info_grid([[]])` calcula `max_pairs=0` y divide `width / max_pairs`
    (`apps/common/pdf/standard.py:288-295`).
  - `totals_table([])` construye una `Table` sin filas (`:389-410`).
  - `_normalize_widths()` acepta ceros, negativos, NaN, cantidad incorrecta y
    anchos no positivos (`:320-329`).
  - `business_header()` resta un ancho fijo de logo sin comprobar el ancho total
    (`:252-259`).
- Reproducción validada:
  - Una fila vacía lanzó `ZeroDivisionError`.
  - Totales vacíos lanzaron `ValueError` de ReportLab.
  - Anchos `[0, 0]` fallaron durante build por ancho disponible negativo.
  - Un header con logo y ancho de 0.5 pulgadas creó una segunda columna negativa.
- Impacto:
  - Errores normales de composición llegan como excepciones internas heterogéneas
    y difíciles de mapear/observar.
- Recomendación:
  - Validar precondiciones con una excepción propia y mensajes accionables;
    definir representación consistente de colecciones vacías.
- Prueba de aceptación sugerida:
  - Matriz de vacío/cero/negativo/NaN/mismatch falla de forma determinista antes
    de construir flowables.

### COM-007 - Un logo corrupto rompe todo el documento

- Tipo: disponibilidad / validación de recurso.
- Evidencia:
  - `_logo_source()` considera válido cualquier bloque leído
    (`apps/common/pdf/standard.py:207-230`).
  - `business_header()` lo entrega directamente a `Image` (`:252-255`).
- Reproducción validada:
  - Un blob `b'no-es-una-imagen'` fue devuelto como `BytesIO`; al construir el
    encabezado, Pillow/ReportLab lanzó `UnidentifiedImageError`.
- Impacto:
  - Corrupción de blob, migración defectuosa o contenido reemplazado impide emitir
    todos los PDFs que usan ese config.
- Recomendación:
  - Validar tipo, dimensiones y decodificación al subir y nuevamente de forma
    defensiva al renderizar. Decidir si el documento debe fallar de forma
    observable o usar un placeholder explícito.
- Prueba de aceptación sugerida:
  - Logo corrupto produce una respuesta controlada/alerta o un placeholder
    visible; nunca un 500 opaco.

### COM-008 - Un fallo de storage se oculta y genera un documento sin logo

- Tipo: observabilidad / integridad de marca.
- Evidencia:
  - `_logo_source()` captura cualquier `Exception` al abrir/leer y devuelve
    `None`; tampoco registra el error (`apps/common/pdf/standard.py:221-230`).
  - `business_header()` interpreta `None` exactamente igual que “no hay logo”.
- Reproducción validada:
  - Un `TimeoutError('blob no disponible')` produjo `_logo_source=None` y el PDF
    se generó exitosamente sin logo.
- Impacto:
  - Una interrupción de Azure Blob puede degradar documentos oficiales sin que
    operador ni monitoreo sepan que faltó un recurso configurado.
- Recomendación:
  - Diferenciar “no configurado” de “falló la lectura”; registrar tenant, objeto y
    causa. Definir política por tipo de documento.
- Prueba de aceptación sugerida:
  - Ausencia voluntaria no alerta; timeout/403/404/corrupción sí dejan métrica/log
    correlacionable y una salida explícita.

### COM-009 - El logo remoto se carga completo y sin límite en memoria

- Tipo: recursos / disponibilidad.
- Evidencia:
  - La lectura es `BytesIO(logo.read())`, sin size check, streaming ni timeout
    propio (`apps/common/pdf/standard.py:221-224`).
  - `ConfiguracionNegocio.logo` no declara validator de tamaño
    (`apps/configuracion/models.py:66-71`).
- Reproducción validada:
  - Un blob simulado de 5 MiB se materializó completo en un `BytesIO` de 5 MiB
    antes de que ReportLab intentara decodificarlo.
- Impacto:
  - Varias descargas concurrentes multiplican memoria por worker; una imagen muy
    grande o bomba de descompresión puede agotar recursos.
- Recomendación:
  - Limitar bytes y píxeles al upload, almacenar una variante PDF optimizada y
    rechazar recursos fuera de contrato antes de leerlos completos.
- Prueba de aceptación sugerida:
  - Archivos/dimensiones superiores al máximo se rechazan; concurrencia medida no
    supera el presupuesto de memoria del worker.

### COM-010 - El sello de generación usa la zona horaria del host

- Tipo: trazabilidad temporal / configuración regional.
- Evidencia:
  - `date()` sí usa `timezone.localtime()` para datetimes aware
    (`apps/common/pdf/standard.py:178-186`).
  - El footer usa `datetime.now()` importado de la librería estándar
    (`:434-444`).
- Reproducción validada:
  - Al instrumentar el footer, consumió directamente la hora del host y nunca
    llamó `django.utils.timezone.localtime`.
- Impacto:
  - En contenedores UTC el documento puede mostrar una hora cuatro horas distinta
    a Santo Domingo, mientras el cuerpo usa la zona Django.
- Recomendación:
  - Usar `timezone.localtime()`/`timezone.now()` y declarar zona si el documento
    puede circular entre regiones.
- Prueba de aceptación sugerida:
  - Con host UTC y `TIME_ZONE=America/Santo_Domingo`, cuerpo y footer muestran el
    mismo instante local esperado, incluso cerca de cambio de fecha.

### COM-011 - El footer ignora el tamaño real del documento

- Tipo: geometría / compatibilidad de layout.
- Evidencia:
  - `document()` acepta cualquier `pagesize` (`apps/common/pdf/standard.py:189-197`).
  - `footer_canvas()` dibuja con `PAGE_WIDTH` global Carta para línea, centro y
    página (`:434-444`), sin leer `doc.pagesize` ni canvas.
- Reproducción validada:
  - En A4 landscape (841.89 pt), el número de página se dibujó en x=565.2, la
    coordenada de Carta portrait, no en el margen derecho real.
- Impacto:
  - El pie queda descentrado, corto o fuera de la composición al reutilizar la API
    que ella misma expone para otros tamaños.
- Recomendación:
  - Derivar ancho/alto del documento/canvas en cada página.
- Prueba de aceptación sugerida:
  - Carta, A4 y ambas orientaciones colocan línea, etiqueta y numeración en sus
    márgenes reales.

### COM-012 - Las tablas materializan todos los registros antes de maquetar

- Tipo: memoria / escalabilidad.
- Evidencia:
  - `standard_table()` convierte incondicionalmente el iterable con `list(rows)`
    (`apps/common/pdf/standard.py:341-352`).
  - CxC también reúne cuentas, cuotas y pagos antes del build
    (`apps/cuentas_por_cobrar/pdf_generator.py:27-30`, `:71-108`).
- Reproducción validada:
  - Un generador de 1,000 filas quedó consumido por completo al llamar
    `standard_table()`, antes de `doc.build()`.
- Impacto:
  - Estados de cuenta o reportes grandes duplican estructuras en memoria y pueden
    bloquear un worker síncrono.
- Recomendación:
  - Definir máximos/paginación por documento y evitar copias redundantes. Para
    volúmenes mayores, usar exportación asíncrona/streaming o anexos.
- Prueba de aceptación sugerida:
  - Pruebas con cardinalidad máxima miden memoria y tiempo dentro del presupuesto;
    el exceso produce una respuesta controlada.

### COM-013 - Los builds productivos no fijan versiones de ReportLab/Pillow

- Tipo: reproducibilidad / cadena de suministro / compatibilidad.
- Evidencia:
  - `requirements.txt` declara `Pillow>=10.2` y `reportlab>=4.0`
    (`requirements.txt:18-19`).
  - Cloud repite rangos abiertos (`requirements_cloud.txt:25-26`).
  - El Dockerfile instala `requirements_cloud.txt` en cada build
    (`Dockerfile:13-15`).
  - Existen snapshots con ReportLab 4.4.9/Pillow 12.1.0, pero no son los usados por
    el contenedor (`requirements_actual.txt:15,28`).
- Estado observado:
  - El entorno auditado ejecutó ReportLab 4.4.9 y Pillow 12.1.0.
- Impacto:
  - Dos despliegues del mismo commit pueden cambiar parser, maquetación o seguridad
    de imágenes sin diff del repositorio.
- Recomendación:
  - Generar un lock/hash reproducible para local y cloud, con proceso explícito de
    actualización y regresión visual/funcional.
- Prueba de aceptación sugerida:
  - El mismo commit resuelve hashes idénticos; actualizar una dependencia exige PR
    visible y ejecuta PDFs de referencia/adversariales.

## Hallazgos P3

### COM-014 - Todos los logos se deforman a un cuadrado fijo

- Tipo: identidad visual / presentación.
- Evidencia:
  - `Image` fuerza ancho y alto a 0.9 pulgadas, sin preservar relación de aspecto
    (`apps/common/pdf/standard.py:252-255`).
- Reproducción validada:
  - Un logo de 1000x100 píxeles conservó esas dimensiones intrínsecas, pero se
    dibujó a 64.8x64.8 puntos.
- Impacto:
  - Marcas horizontales aparecen comprimidas y poco profesionales en todos los
    documentos.
- Recomendación:
  - Calcular dimensiones con `preserveAspectRatio`/thumbnail y encajar dentro de
    un bounding box sin estirar.
- Prueba de aceptación sugerida:
  - Logos horizontal, vertical y cuadrado mantienen proporción y no desplazan el
    texto fuera del header.

### COM-015 - La cobertura propia valida solo el camino feliz mínimo

- Tipo: pruebas / regresión.
- Evidencia:
  - `apps/common/tests/test_pdf_standard.py` contiene dos pruebas: formato básico y
    un PDF sin datos (`:25-46`).
  - Logos remotos tienen dos casos positivos adicionales en tenancy
    (`apps/tenancy/tests/test_media.py:141-202`).
  - No había casos para sucursal incorrecta, importes inválidos, no-finitos,
    geometría, filas incompatibles, textos grandes o fallos de storage.
- Impacto:
  - Cambios visuales pueden conservar `%PDF` al inicio y aun perder columnas,
    identidad o exactitud financiera.
- Recomendación:
  - Convertir las reproducciones en contratos permanentes y añadir inspección de
    texto/posición o snapshots visuales controlados donde aporte valor.
- Prueba de aceptación sugerida:
  - La suite cubre cada helper, errores, límites, configuraciones A/B y los cuatro
    generadores completos.

## Controles que sí funcionaron

- `clean()` escapa `<`, `>` y `&` antes de construir `Paragraph`; no se confirmó
  inyección de markup desde nombres/notas.
- Las fechas aware del cuerpo se convierten con la zona horaria de Django.
- Las tablas repiten la fila de headers al paginar.
- El fallback remoto permite leer logos de backends sin `.path`, incluido Azure.
- Los cuatro PDFs básicos se generaron correctamente en la suite existente.

Estos controles reducen riesgo, pero no compensan el contexto de sucursal
implícito ni la ausencia de límites/validación estructural.

## Validación ejecutada

Entorno:

- Python: `C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe`
- settings temporal con base aislada:
  `test_pos_fifo_auditoria_common_20260828`
- ReportLab: 4.4.9
- Pillow: 12.1.0
- Django system check: sin incidencias

Suite existente seleccionada (**19/19 OK**, 3.087 s):

- `apps.common`
- PDF de cotizaciones
- PDF/Excel de estados de cuenta
- PDF de cierres diarios
- factura PDF de financiación
- media/lectura de logos remotos bajo tenancy

Suite adversarial temporal (**18/18 OK**, 1.286 s):

- encabezado de sucursal A en objeto de sucursal B
- importe inválido, NaN e infinitos
- símbolo de moneda ambiguo
- filas vacías, totales vacíos y anchos inválidos
- columnas/anchos incompatibles y desborde silencioso
- texto que supera el frame
- logo corrupto, storage caído y lectura remota sin límite
- deformación de relación de aspecto
- footer con ancho incorrecto y hora del host
- materialización completa de iterables

La revalidación combinada sobre `8564e3f` terminó con **37/37 OK**
(19 existentes + 18 adversariales, 4.057 s).

Las pruebas y settings temporales se retiraron después de capturar la evidencia.
No se modificó código funcional, requirements, templates ni tests permanentes.

## Orden de remediación sugerido

1. Corregir COM-001: hacer explícita e inmutable la identidad del emisor del
   documento y probar A/B en el mismo proceso.
2. Endurecer el contrato financiero y estructural (COM-002 a COM-006) antes de
   seguir reutilizando los helpers.
3. Definir política de logos y límites de recursos (COM-007 a COM-009 y COM-012).
4. Corregir zona horaria/geometría del footer (COM-010/COM-011).
5. Bloquear dependencias de producción y ejecutar regresión PDF en sus upgrades
   (COM-013).
6. Preservar proporciones y convertir las 18 reproducciones en regresiones
   permanentes (COM-014/COM-015).

Este orden no implica corregir dentro de esta auditoría. Cada bloque debe tener
su plan separado y revalidarse con documentos representativos del negocio.

---

# Estado de mitigación

Fecha: 2026-08-30. Verificación previa: se releyó cada hallazgo contra el código
citado. **Los 15 son reales.**

## Resumen por hallazgo

| ID | Real | Estado | Dónde quedó la corrección |
|---|---|---|---|
| COM-001 | Sí | Corregido | `config_para_documento(sucursal)` en `apps/configuracion/utils.py`. Los **cuatro** generadores —cotización, estado de cuenta, cierre y factura de financiación— lo usan; ninguno resuelve ya por `settings`. |
| COM-002 | Sí | Corregido | `money()` levanta `ImporteInvalido` en vez de devolver `$0.00`. Un cero real se sigue imprimiendo como cero. |
| COM-003 | Sí | Corregido | Se rechazan `NaN` e infinitos, y el símbolo pasa a `RD$`. |
| COM-004 | Sí | Corregido | `clean()` acota a `MAX_TEXTO` (4000) con marca `[...]` visible. |
| COM-005 | Sí | **Abierto** | Validación de forma de tablas. |
| COM-006 | Sí | **Abierto** | Estructuras vacías y dimensiones inválidas. |
| COM-007 | Sí | **Abierto** | Un logo corrupto rompe el documento. |
| COM-008 | Sí | **Abierto** | Un fallo de storage se oculta. |
| COM-009 | Sí | **Abierto** | El logo remoto se carga completo y sin límite. |
| COM-010 | Sí | Corregido | El sello usa `timezone.localtime`. |
| COM-011 | Sí | Corregido | El pie usa `doc.pagesize`, no la constante Carta. |
| COM-012 | Sí | **Abierto** | Las tablas materializan todos los registros. |
| COM-013 | Sí | **Abierto** | Los builds no fijan versiones de ReportLab/Pillow. |
| COM-014 | Sí | **Abierto** | Los logos se deforman a un cuadrado. |
| COM-015 | Sí | Corregido | La app tenía 2 pruebas. Ahora 22. |

## COM-001: un documento que no dice quién lo emitió

Todos los generadores conocen la sucursal del objeto que documentan —la
cotización tiene FK, las cuentas también, el cierre y la venta igual— pero
resolvían el encabezado con `get_config()`, que sale de `settings.SUCURSAL_CODIGO`.

Con `SUCURSAL_CODIGO=A`, una cotización de B se imprimía con el nombre, el RNC,
la dirección, el teléfono y el logo de A. **En una disputa, el PDF no representa
de forma confiable quién emitió el documento** — y es el único artefacto que
queda del hecho.

`config_para_documento(sucursal)` resuelve la configuración de esa sucursal, y
cae al contexto actual solo cuando no hay ninguna —instalación sin migrar, o un
resumen consolidado que legítimamente no documenta una tienda sino todas—,
dejando un `warning` en el log. Un encabezado que no corresponde al hecho
documentado conviene que se note antes en el log que después en una factura.

Un test estructural verifica que ninguno de los cuatro generadores pueda volver
a llamar `get_config()`.

## COM-002: la falsedad bien formada

`money()` capturaba el error y devolvía `$0.00` sin informar nada. Un importe
corrupto se presentaba como **ausencia real de deuda, descuento o pago**: el PDF
quedaba bien formado y materialmente falso, que es la peor combinación posible
en un documento que alguien usa para cobrar.

Ahora levanta `ImporteInvalido`. **Es un cambio de contrato**: un dato corrupto
pasa de producir un documento engañoso a impedir su emisión. Es deliberado —
entre no emitir y emitir algo falso, la segunda es peor— pero conviene saberlo:
si aparece un `ImporteInvalido` en producción, el problema no es el PDF sino el
dato que llega hasta él.

## Cambios de conducta observables

1. **Todos los importes pasan de `$` a `RD$`** en todos los documentos:
   cotizaciones, estados de cuenta, cierres, facturas y tickets que usen estos
   helpers. `$1,234.50` no distinguía DOP de USD.
2. **Un importe corrupto o no finito impide emitir el documento**, con
   `ImporteInvalido`.
3. **Los documentos se encabezan con la identidad fiscal de su propia
   sucursal.** Si una instalación venía imprimiendo todo con la identidad de la
   sucursal configurada en `settings`, los documentos de otras sucursales
   cambian de encabezado — correctamente.
4. **Los textos muy largos se truncan con `[...]`** en vez de impedir generar el
   PDF.
5. **El sello del pie usa la hora del negocio.** En un contenedor en UTC, un
   cierre de las 8 PM en Santo Domingo se sellaba a medianoche del día
   siguiente.

## Despliegue

**Sin migraciones.**

> **Revisar antes de desplegar:**
> 1. **El cambio de `$` a `RD$` es visible para el cliente final.** Si hay
>    plantillas, capturas o material impreso que lo referencien, conviene
>    avisarlo. La constante es `SIMBOLO_MONEDA` en `apps/common/pdf/standard.py`.
> 2. **Sucursales sin configuración propia.**
>    `Sucursal.objects.filter(configuracionnegocio__isnull=True)`. Esas caen al
>    contexto actual y dejan un `warning`; conviene darles la suya.

## Lo que no se tocó, y por qué

Los seis P2 abiertos son de **robustez del renderizado**, no de corrección del
contenido: logo corrupto (COM-007), fallo de storage silenciado (COM-008), logo
remoto sin límite de memoria (COM-009), forma de tablas sin validar (COM-005),
estructuras vacías (COM-006) y materialización completa de registros (COM-012).
Se dejaron fuera porque cada uno necesita decidir **qué hacer cuando falla** —
emitir sin logo, fallar, degradar— y esa decisión conviene tomarla con el
comportamiento observado en producción, no a ciegas.

**COM-009 es el más urgente de los seis**: un logo remoto se lee completo en
memoria sin límite, así que el tamaño del archivo que alguien suba determina
cuánta memoria consume el worker al generar cualquier documento.

**COM-013 es de infraestructura**: los builds productivos instalan ReportLab y
Pillow con rangos abiertos aunque existan snapshots exactos que el `Dockerfile`
no usa. Un cambio menor de esas librerías puede alterar la maquetación de todos
los documentos sin que nada lo anuncie.

**COM-014** (los logos se deforman a un cuadrado de 0.9") es cosmético pero
visible en cada documento.

## Pruebas

Suite completa, serial: **1098 tests, OK.**

Módulo de regresión nuevo: `apps/common/tests/test_auditoria_common.py`
(20 pruebas), sobre las 2 que la app tenía.

**Verificación por mutación.** Revertidos cuatro hallazgos, seis pruebas fallan:
el importe corrupto vuelve a imprimirse como cero, los no finitos vuelven a
pasar, y el generador de cotizaciones vuelve a resolver el encabezado por
`settings`.

**Una prueba mía que no servía, y su corrección.** El test del sello comparaba
el texto del pie con `timezone.localtime`, pero en un host que ya está en la
zona del negocio **coinciden por accidente**: la mutación a `datetime.now()` no
lo hacía fallar. Se agregó una aserción estructural sobre el código de
`footer_canvas`. Y esa aserción falló al principio porque **el propio docstring
de la función contenía el literal `datetime.now()`** que el test busca —el mismo
tropiezo que con el comentario del instalador en USR-005—.
