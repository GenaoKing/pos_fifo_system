# Objetivo de release: actualizar Royal Plast y SK Performance

Fecha: 2026-09-24. Documento de alcance y secuencia; no ejecuta ni autoriza
por sí mismo una publicación o intervención en las tiendas.

El responsable pide concentrar el cierre en valor operativo y dejar fuera
e-CF, impresoras de etiquetas y trabajos accesorios. La propuesta de corte
del backlog que sigue permite convertir esa prioridad en un release finito.
Los cambios de alcance propuestos no convierten pendientes antiguos en resueltos.

## Resultado final

Royal Plast y SK Performance operan con una versión identificada y comprobada
del POS, backend cloud y portal. Pueden vender, cobrar, registrar crédito y
abonos, administrar caja e inventario y consultar su información, con permisos
correctos. Una caída de internet o un reinicio no pierde ni duplica operaciones;
al recuperarse la conexión, POS y cloud convergen o muestran un problema
identificable. La actualización conserva los datos y tiene recuperación ensayada.

El release termina cuando ambas tiendas están actualizadas, sus resultados
antes/después están conciliados y completaron su observación operativa.
Desplegar el backend o completar una suite no equivale a ese resultado.

## Mapa actual verificado

Base funcional backend `d69c73e`, frontend `a9d960e`. Evidencia en
`../handoffs/cierre_prod/INTEGRACION-TOTAL-2026-09-24.md`: 1.654 Django,
143 frontend y 13 de release aprobadas; lint/build verdes. Las pruebas e-CF
previas son evidencia conservada, no un frente nuevo de este release.

| Área | Estado real | Lo que falta para esta entrega |
| --- | --- | --- |
| Venta, caja, crédito, abonos, compras e inventario | Correcciones integradas y pruebas locales | Recorrido operativo completo en staging y por tienda, con saldos y movimientos conciliados. |
| Sincronización POS/cloud | Transporte durable, reintentos, deduplicación y diagnóstico integrados | Compatibilidad con POS instalado, offline/reinicio, recuperación y revisión de pendientes históricos. |
| Productos/categorías | Mutaciones offline, conflictos, inactivos y administración integrados | Validación del flujo completo y clasificación de deuda PRO todavía abierta. |
| Clientes | CRUD y contención de edición local de clientes adoptados | Clasificar deuda CLI de aislamiento, validación, auditoría y borrado. CLI-004 no entrega edición offline con resolución de conflictos. |
| Usuarios, roles, sucursales y configuración | Backend/portal integrados; pruebas de aislamiento y permisos | Identidad inicial, adopción, usuarios/roles reales y ausencia de bloqueos de acceso. |
| Distribución y actualización | Tooling, locks, paquete y fix de permisos.0011 integrados | Ensayo C06.1 completo, copias representativas RP/SK, restore y artefactos remotos identificados. |
| Entornos | Candidatos locales consolidados; develop aún no los contiene | Recorrer dev → staging → producción; verificar versiones desplegadas en cada paso. |

No se verificó Azure ni las instalaciones de clientes para redactar este mapa.
El relevo C06.1 ya tiene un worktree desde `d69c73e`; su existencia no acredita
que el ensayo haya terminado. Este documento no cambia ese código base.

## Alcance funcional y regla para detener la expansión

Entran operación comercial, integridad de datos/dinero, seguridad de acceso,
aislamiento de negocios, sincronización y actualización recuperable.
Se conserva el ticket/comprobante habitual que necesita la venta; no se abre
una nueva matriz de dispositivos ni desarrollo de impresión.

Fuera de trabajo nuevo: e-CF/certificación, etiquetas, más periféricos,
inventario consolidado multi-sucursal nuevo, segundo nodo/PC, actualizador
remoto, rediseños, optimizaciones cosméticas y nuevas funciones de notificaciones.
Se preserva el comportamiento existente; excluir un área no significa quitarla
del producto ni permitir regresiones en clientes que la utilizan.

Propuesta para este release: mantener diferida la edición offline con CAS de
clientes (CLI-004), con su contención y limitación visibles. No anunciarla como
una capacidad entregada. Productos/categorías mantienen su flujo ya integrado.

Antes de abrir dev, hacer **una revisión acotada del inventario existente**:

- Corregir o acreditar con evidencia los fallos reproducibles que pierdan o
  dupliquen operaciones, alteren precios/saldos/stock, permitan acceso indebido,
  mezclen negocios, bloqueen un flujo diario o impidan actualizar/restaurar.
- Revisar especialmente los pendientes PRO/CLI, borrado de clientes,
  validación de catálogo, auditoría de mutaciones comerciales, cotizaciones y
  las precondiciones de identidad/configuración/RBAC. Los estados viejos del
  ledger se contrastan con el código; no se presupone que todos siguen rotos.
- Registrar el resto como diferido con motivo, limitación y siguiente destino.
  La aprobación del corte resuelve las discrepancias con el alcance amplio
  anterior de PLAN_CIERRE_PROD; no se declara G1 completo antes de ese corte.
- Después, admitir al release solo bloqueadores o regresiones de sus pruebas.
  Evitar nuevos frentes de producto mientras se recorre la promoción.

## Ruta de entrega

| Etapa | Trabajo | Criterio de salida |
| --- | --- | --- |
| 0. Cierre del candidato | Corte de pendientes; C06.1 completo desde copia limpia; paquete y restore; comprobar impacto de SEC-001 y preparar corrección de credenciales si corresponde. | Sin bloqueadores del alcance; versiones y limitaciones declaradas. |
| 1. Dev | Integrar candidatos en las ramas correspondientes y publicar en dev cuando se autorice. Ejecutar CI/Linux, migraciones y recorrido POS/API/portal real. | Flujos esenciales funcionando juntos, sin mocks que sustituyan la integración; imágenes/paquetes identificados. |
| 2. Staging | Ensayar el candidato exacto con copias recientes autorizadas de RP/SK, aislamiento de tenants, POS instalado/nuevo, offline/reinicio, backups y restore. | G1/G2 del alcance cumplidos; mínimo 24 h con arranque en frío y conciliación diaria; acta y recuperación preparadas. |
| 3. Producción cloud | Con pase explícito: inventario y respaldos actuales; migraciones por BD; backend por digest aprobado, después portal. | G3: al menos 60 min y dos ciclos por POS instalado, con login, acceso y sync sanos. |
| 4. Royal Plast | Actualizar fuera de horario con paquete aprobado; revisar identidad, servicios, venta/cobro/caja, saldos y sincronización. | Aceptación del operador y un día real de operación sin incidentes bloqueantes. |
| 5. SK Performance | Repetir actualización y aceptación después de RP; comprobar los problemas conocidos propios de SK. | Ambas tiendas conciliadas y observadas; versiones instaladas y soporte documentados; G4 cerrado. |

Los tiempos de observación son mínimos de aceptación, no una fecha prometida
de finalización. Un hallazgo bloqueante devuelve el candidato al punto afectado.

La imagen aprobada para producción debe ser la misma probada en staging por
digest; el SHA seleccionado debe satisfacer el gate de labels de CI. El
manifiesto común vincula backend, frontend, Windows y sus migraciones.
Ver `../runbooks/RELEASE_PROMOTION_A08_2.md` y los runbooks de actualización.

## Reparto para la continuación

- Codex: consolidar el corte de bloqueadores, corregir backend de su dominio,
  coordinar CI/dev/staging/cloud, manifiesto y acta de pase.
- Claude: completar el ensayo C06.1 desde el SHA acordado, procedimiento Windows,
  backup/restore y validación de los recorridos POS/portal del alcance.
- Responsable y operadores: concretar ventanas, autorizar pasos sobre entornos
  reales y aceptar la operación en cada tienda.

El siguiente trabajo útil es terminar C06.1 y la clasificación acotada de
bloqueadores para preparar dev. No se inicia otro bloque de funciones accesorias.
