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
de costes. La publicación y actualización de QA quedan pendientes.
