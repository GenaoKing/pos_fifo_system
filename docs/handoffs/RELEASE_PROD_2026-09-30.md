# Release de producción — preparación 2026-09-30

Despliegue ya ejecutado. El resultado operativo, la ventana real y los
pendientes están en [producción migrada](PRODUCCION_MIGRADA_2026-09-30.md).
Este documento conserva el corte de preparación del artefacto `3d5b2fe`.

El responsable autorizó implementar el plan completo e informó que los POS
locales están apagados. Se incluyen los cambios locales probados; las
notificaciones se preparan apagadas y los clientes se actualizarán después.
Este corte documenta la preparación; el resultado real del despliegue se
adjunta por separado con su SHA/digest y ledger, sin atribuirlo a una prueba.

## Integración

- Base de cloud staging `d24f24e`, cambios de `develop@c1ad5d9`, cuadre local
  `c7ff2e5` y ocho archivos validados por el operador, registrados en `1b5dc3b`.
- Cost controls y job productivo Manual con Web Push apagado: `5db92c9`.
- Gate de mantenimiento, health interno y ausencia de rollback automático de
  producción: `0b5d050`.
- Reparación dirigida de auditoría histórica del control plane: `5c95e5e`.
- Corrección de migración CONTADO con FKs diferidas: `fcd0048`.
- Portal consolidado: `9c8d4915eb29797782e251e9eb98d4960efc2d3b`, 146 pruebas,
  lint/build y CI/deploy staging aprobados. Main aún se promueve después de API.

Los cambios de caja, cuadre, PDF y productos forman parte de la misma fuente
backend que se empaqueta para Windows. Las configuraciones personales, secretos
y datos quedan fuera de los artefactos. El paquete conserva su manifiesto
original de construcción; la aceptación productiva se registra en el acta.

## Hallazgos comprobados en copias actuales

Se respaldaron control plane y cuatro tenants activos y se restauraron en un
PostgreSQL 16.15 aislado, conservando nombres y rutas de tenant. Las cinco bases
pasaron comparación de conteos contra los dumps. No se usó un dump histórico
como sustituto de las bases actuales.

1. El control plane registraba `sucursales.0001..0003` y `auditoria.0001..0004`
   sin sus tablas físicas. El reparador de sucursales existente y el nuevo
   `reparar_auditoria_dual_home --database default` materializan solamente los
   estados históricos faltantes. Ambos exigen dry-run revisado y `--apply`
   explícito; conservan `django_migrations`.
2. Royal Plast tiene dos placeholders CONTADO equivalentes. Su consolidación
   dejaba eventos de FK diferida pendientes y PostgreSQL rechazaba crear el
   índice único. `clientes.0006` ahora ejecuta `SET CONSTRAINTS ALL IMMEDIATE`
   después del DML y antes del índice, conservando la transacción completa y
   el rechazo de cualquier cliente CONTADO con identidad propia.
3. El ensayo terminó con control plane y **4/4 tenants migrados**, 155
   migraciones registradas por base. Conteos financieros y sumas de ventas,
   pagos y CxC se conservaron. Solo se consolidó un placeholder de Royal Plast,
   conforme a la regla ya existente de la migración.
4. La identidad técnica de routing se conserva. El diagnóstico estricto detecta
   metadatos previos divergentes: nombres del demo compartido, RNC de
   configuraciones RP/demo y `Tenant.plan_slug` vacío frente a suscripciones
   operativas Empresarial. No se cambiaron datos fiscales ni planes en este
   ensayo; su tratamiento debe quedar explícito en el acta de ejecución.

El primer ensayo en bases renombradas abortó correctamente en el preflight de
identidad de `tenancy.0004`. Se repitió en un servidor aislado con los nombres
originales, sin desactivar el guard ni modificar la migración para el laboratorio.

## Evidencia de código

- 122 pruebas focales de caja, reportes, permisos de reportes y PDF: PASS.
- 4 pruebas de health sin BD: PASS.
- 34 pruebas de política/gates en Linux, incluido PTY: PASS.
- 15 pruebas PostgreSQL de reparación histórica de auditoría: PASS.
- 27 pruebas de clientes, incluidas migraciones con FKs reales: PASS. Se
  reprodujo el error de índice antes del fix y se probó rollback completo.
- Terraform: validate/fmt y cinco pruebas HCL: PASS. Plan inicial de
  notificaciones: cuatro altas, una actualización de variables de API y cero
  destrucciones; requiere regenerar con el digest backend final antes de apply.

## Procedimiento de la ventana

La evidencia privada vive en `C:/Proyectos/_release_prod/20260930/`; no contiene
secretos versionados. Incluye inventario, dumps, hashes, conteos, sumas, logs de
ensayo y scripts operativos limitados al inventario de esta ventana.

El tenant demo usa `tnt_demo`, compartido con dev. Antes de los backups frozen
se cierran temporalmente ingress y revisiones de **dev y prod**. Staging usa
bases separadas. Dev se restaura con su misma imagen/configuración; no se
restaura ni migra su control plane como parte de una recuperación productiva.

Orden obligatorio:

1. Aprobar CI/manifiesto del SHA definitivo y preparar el job Manual con ese
   digest. El workflow corregido no ejecuta el procesador en prod.
2. Registrar snapshots de Azure y cerrar ambos escritores. Verificar cero
   réplicas antiguas, cero jobs activos y ausencia de sesiones sobre las bases.
3. Tomar backups frozen de las cinco bases y registrar `maintenance-state.json`
   con hashes, fecha UTC y la dependencia dev. Validar el plan de recuperación.
4. Ejecutar dry-run y apply dirigidos de sucursales y auditoría solo en el
   control plane, con el mismo código ensayado. No editar historial por SQL.
5. Fast-forward de main al SHA aprobado; dispatch con digest y migraciones
   obligatorias. Esperar ledger completo y health interno, conservando ingress
   cerrado. El workflow no vuelve a una API legacy ni abre tráfico.
6. Sincronizar catálogo de permisos sin presets y catálogo de módulos sin
   alterar planes/overrides. Comprobar datos, roles y estado apagado de
   notificaciones productivas antes de abrir.
7. Abrir exclusivamente API nueva y publicar portal. Restaurar dev y su
   escalado original. Observar 60 minutos; la prueba de los POS físicos queda
   para su encendido, porque el responsable informó que están apagados.

## Recuperación

Antes de migraciones se puede restaurar la configuración/imagen anterior.
Después del primer DDL no se permite rollback de imagen sola. El procedimiento
preparado exige tráfico cerrado en prod/dev, jobs detenidos, hashes de los
backups frozen y ausencia de escrituras operativas posteriores. Conserva las
bases fallidas mediante renombrado y restaura las cinco bases originales;
compara conteos, migraciones y sumas antes de permitir servicio antiguo.

Después de reabrir tráfico, restaurar el backup está bloqueado. Ante un fallo,
se conserva la evidencia y se corrige hacia adelante sin descartar operaciones.
