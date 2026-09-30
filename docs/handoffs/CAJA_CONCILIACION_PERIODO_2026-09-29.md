# Caja: conteo compacto, ingresos diarios y cuadre por período

Base `7a4093f`, rama `codex/caja-dashboard-ingresos`. Implementación local;
sin publicación, despliegue ni modificación de instalaciones o datos operativos.

## Comportamiento

- Cierre: modal limitado al alto disponible, scroll vertical, denominaciones
  plegables en dos columnas compactas y Escape para salir. El botón de cierre
  consulta el estado actualizado antes de mostrar cifras.
- Panel y modal muestran ingresos del día local del **cajero de ese turno en
  esa sucursal**: pagos de ventas completadas (efectivo/transferencia/tarjeta)
  más CxC APLICADO. Abarca sus turnos de hoy, no solo el turno activo.
  Excluye CREDITO y fondos/reposiciones; la inicial vive en Pago y se suma una
  vez. El conteo ciego omite este agregado en el servidor.
- Reportes On-Demand ofrece **Cuadre semanal, quincenal o mensual / PDF**:
  `/reportes/conciliacion/`. Fechas inclusivas, rango personalizado hasta
  366 días, filtro por sucursal y accesos rápidos desde lunes, día 1/16 o
  principio de mes hasta hoy. Fechas futuras o invertidas se rechazan.
- Muestra ventas facturadas, crédito generado, ventas cobradas, cobros CxC,
  ingresos, reposiciones, gastos, retiros y flujo neto por día y en total.
  Días vacíos aparecen con cero. Desglose de ingresos por medio de pago.
- Arqueos: esperado, contado y diferencia **guardados** al cerrar, agrupados
  por fecha de cierre. No se reconstruye pertenencia por fechas ni se suman
  los fondos reutilizados como ingresos. Se informa cuántos turnos iniciados
  hasta el fin del período siguen abiertos al consultar.
- PDF Carta horizontal, encabezado del negocio, resumen, tablas con cabeceras
  repetidas, totales, paginación y firmas. Se genera en memoria y se abre para
  guardar/imprimir; no escribe en MEDIA ni almacenamiento público.

## Alcance y semántica

Mismo gate `reportes_ondemand` y alcance RBAC de reportes, tanto HTML como PDF;
sin permisos nuevos ni migraciones. Selección explícita de sucursal rechaza
IDs fuera de alcance y excluye filas sin sucursal. «Todas las permitidas»
mantiene la política legacy de `Alcance.filtrar`, que incluye filas sin sucursal.
Respuesta sin caché.

Ventas se agrupan por `fecha_venta`, siguiendo el contrato vigente; CxC por
`fecha_pago`; movimientos por `fecha`; arqueos por `fecha_cierre`. Se usa la
zona local activa. Importes vigentes al generar, con anulados excluidos: es
un reporte consultable, no un cierre FINAL congelado. El flujo neto incluye
todos los medios de pago, no equivale a efectivo físico ni utilidad.

## Validación

- PostgreSQL aislado, ejecución serial: 21 pruebas de conciliación/cuadre y
  56 de gates comerciales, PDF, resumen de notificaciones y kit común: 77 OK.
- Reejecución de las 12 pruebas de conciliación tras compactar el PDF.
- BDs descartables `test_pos_conciliacion_20260929`,
  `test_pos_conciliacion_gates_20260929`, `test_pos_conciliacion_final_20260929`;
  destruidas por el runner. Python aislado de `.venvs/pos_cierre_codex_a01_20260910`.
- Chromium/Playwright con templates Django y recursos locales: 1366×768,
  1024×600, 390×667 y 320×568; dialog dentro del viewport, sin desbordamiento
  horizontal, suma de denominaciones correcta y Confirmar Cierre accesible.
  Tres presets envían fechas correctas; sin errores JavaScript.
- PDF de 29 días con importes de millones: cuatro páginas, totales y firmas
  presentes, primera/última páginas revisadas visualmente. PDF vacío y
  multipágina cubierto por pruebas. No se acredita impresión física.
- Evidencia temporal: `C:/Windows/Temp/conciliacion_20260929/` y script
  `C:/Windows/Temp/verify_conciliacion_20260929.py`. La prueba de navegador
  usa estado/API simulados; endpoints, permisos y cálculos se prueban con
  Django/PostgreSQL. No consulta la instalación QA ni clientes reales.

Se preservan los cambios preexistentes de productos, `.codex/` y análisis
de costes. La publicación queda pendiente; QA fue actualizada según el acta
siguiente.

## QA local actualizada con autorización — 2026-09-29

- Aplicados los ocho archivos de runtime de `c7ff2e5` sobre
  `C:/Proyectos/pos_fifo_staging_qa/pos_fifo_system`. Antes de copiar se verificó
  contenido contra el padre del commit, incluidos los patches previos.
- Respaldo de archivos, dump, índice de `pg_restore --list` y manifiesto SHA-256
  en `C:/Proyectos/pos_fifo_staging_qa/patch_20260929_conciliacion/`.
  Dump SHA-256: `b973ab410857c3a00d98d945593ab9ce12bdb60bc9f9996f4e413fa0f54b5597`.
- Servicio web detenido/aplicado/reiniciado mediante PowerShell elevado.
  Configuración y gate Admin SYSADMIN preservados por hash. Web y sync Running;
  no se reinició ni modificó el servicio sync. Sin migraciones/dependencias nuevas.
- `verificar_instalacion`: sana, BD `pos_stage_qa`, sin migraciones pendientes.
  `verificar_sync --dias=7`: sin pérdida, ocho eventos confirmados.
- Chromium con login real local: acceso desde Reportes, tres accesos rápidos
  y rango personalizado HTTP 200; PDF HTTP 200, cuatro páginas con resumen,
  totales y firmas; primera página revisada. Sin errores JavaScript.
  Evidencias `smoke.json`, capturas y `cuadre_mensual.pdf` en el respaldo.
- No había turno activo: estado HTTP 200 y código del modal nuevo servido;
  no se abrió/cerró turno ni se registraron ventas, cobros o movimientos.
  La validación interactiva del modal conserva la evidencia aislada anterior.
- No se publicó código ni se actualizó API remota/portal cloud. Este patch solo
  cambia caja/reportes del POS local; no modifica su contrato de sync.
  Por indicación del usuario, esta acta queda pendiente de commit.

## Ajuste visual solicitado y aplicado en QA — 2026-09-29

- Acceso al cuadre integrado como tarjeta con el mismo icono, borde, hover y
  tipografía del selector; seis reportes en cuadrícula de tres columnas en
  escritorio. Pantalla del cuadre con formulario/card y tres tarjetas de
  resumen, reutilizando botones y colores existentes.
- PDF: valores del resumen, última columna de totales y filas TOTAL en
  negrita; filas TOTAL con fondo azul suave y separador. Kit común amplía
  `info_grid`/`standard_table` con opciones desactivadas por defecto para los
  demás documentos. No cambian cálculos ni permisos.
- Suites `test_conciliacion` y `test_auditoria_common` aprobadas en PostgreSQL
  aislado (`test_pos_conciliacion_estilo_20260929`, destruida por el runner).
  Templates/PDF renderizados y comprobados en Chromium; después smoke HTTP
  real en QA: tarjeta, tres rangos, PDF 200 y cero errores JavaScript.
  Extracción del PDF confirma tres importes de resumen y tres filas TOTAL
  con fuente Bold; capturas de tarjeta y PDF revisadas visualmente.
- Cuatro archivos de runtime aplicados con comparación previa y respaldo en
  `C:/Proyectos/pos_fifo_staging_qa/patch_20260929_conciliacion_estilos/`.
  Se conserva el dump de la actualización inmediatamente anterior. Reinicio
  solo del servicio web; sin cambios de datos, cloud, migraciones o estáticos.
  Manifiesto, smoke y capturas en esa carpeta. Código/documentación sin commit
  nuevo ni push, conforme a la preferencia indicada por el usuario.
