# A09 — rig cloud/POS y base del acta de pase

Fecha: 2026-09-24. Estado: **preparación abierta; G1–G4 sin cerrar**.
Este registro aplica el [objetivo de release para RP y SK](../../planes/OBJETIVO_RELEASE_CLIENTES.md).
El alcance de trabajo nuevo excluye e-CF, etiquetas y periféricos accesorios;
se conserva la comprobación del comprobante habitual cuando se pruebe la venta.

## Candidato y evidencia ya recibida

| Componente | Referencia | Evidencia y límite |
| --- | --- | --- |
| Backend | Código `d69c73e`; rama documental integrada `integration/cierre-prod-A06-C04-C05` | Suite integrada 1.654 Django y 13 release PASS, según `INTEGRACION-TOTAL-2026-09-24.md`. Los commits posteriores a `d69c73e` son documentales; seleccionar y registrar el SHA exacto del RC antes del rig y de CI. |
| Portal | `integration/cierre-prod-C04-admin@a9d960e` | 143 tests, lint y build PASS; E2E real 10/10 anterior, no repetido en la integración. |
| Windows | ZIP `pos_fifo_system_C06.1_d69c73e791e5.zip`, SHA-256 `3ba925d4d120e47e730ee3959d7e8fd46925c98f363e0de05a1ba2b892a82f65` | C06.1 PASS: wheelhouse verificado offline, 32/32 migraciones y backup/restore antes y después sobre copia de desarrollo. Fuente: `C06.1-relevo-d69c73e-ensayo-completo.md`. Sigue siendo candidato local, no un artefacto aprobado para instalar. |
| Copias históricas RP/SK | Dumps de junio de 2026 | Claude reporta 78/78 migraciones RP y 115/115 SK sobre BDs descartables con el código `d69c73e`. Fuente: `C06.1-relevo-datos-reales-RP-SK.md`. SK no tenía fila de Sucursal en ese corte. Son fotos históricas; faltan copias actuales autorizadas y verificar identidad antes de actualizar. |

La incorporación del handoff de Claude está en la rama de integración. La
evidencia C06.1 corresponde al **código** `d69c73e`; no se atribuye
automáticamente a otro SHA de paquete, imagen o despliegue.

## Rig que A09 puede preparar y ejecutar localmente

1. Congelar el RC de código y el manifiesto que vincule backend, portal y
   Windows. Registrar hashes de artefactos y versiones de las instalaciones.
2. Seguir [PRUEBAS_SYNC_LOCAL](../../runbooks/PRUEBAS_SYNC_LOCAL.md) en BDs
   PostgreSQL **desechables y separadas**, con cloud local y POS unidos por HTTP
   y token de sucursal de laboratorio. Verificar los nombres de BD antes de
   crear, restaurar o limpiar. El catálogo debe superar 200 productos.
3. Hacer dos ciclos de pull/push y comprobar cardinalidad, último producto,
   cursor, diferidos, idempotencia y ausencia de hechos financieros perdidos.
   Repetir con el cloud anterior y el POS instalado real o su binario exacto;
   fixtures y dos procesos del código nuevo no cubren esa compatibilidad.
4. Registrar en el acta por escenario: versiones, BDs, hora, comando, conteos
   antes/después, respuesta HTTP, cursores, logs, resultado y archivo de
   evidencia. Toda anomalía debe tener responsable y decisión de bloqueo.

El ledger `INVENTARIO.md` marca `RM-SYNC-RIG` como operativo pendiente hasta
tener RC exacto, tokens autorizados y dos ciclos. Este documento prepara la
ejecución; no declara que el rig real haya pasado ni autoriza tocar `royalplast`
o `skperformance`. `royalplastdemo` es el único tenant remoto de prueba
admitido por el runbook; no se accedió a él para redactar esta base.

## Acta por gates

| Gate | Estado al 2026-09-24 | Evidencia que falta para firmarlo |
| --- | --- | --- |
| G1, entrada a staging | **Pendiente** | Corte de bloqueadores de este release, rig cloud/POS real y compatibilidad con POS instalado, copias actuales autorizadas por POS/tenant con identidad y restore, CI remoto y manifiesto/digest del RC. C06.1 cubre copia de desarrollo y migración sobre dumps históricos de RP/SK, no el estado presente de las tiendas. |
| G2, pase a producción | **No iniciado** | Ese RC en staging, al menos 24 h con arranque en frío y conciliación, catálogo >200, matriz operativa del alcance, backups restaurados por BD, plan de infraestructura y acta con responsables/rollback. Requiere autorización del responsable antes de operar producción. |
| G3, cloud sano | **No iniciado** | Despliegue autorizado del digest aprobado, migraciones por BD, portal posterior, 60 min de observación y dos ciclos por POS existente. |
| G4, tiendas | **No iniciado** | RP actualizada y conciliada, un día real de observación, luego SK; aceptación de operadores, versiones efectivas y soporte. |

Esta es la **base del acta**, no el acta final. Cada gate se actualiza con
evidencia nueva y decisión fechada. Un test verde o el PASS de C06.1 no
autoriza por sí solo staging, producción ni una visita a clientes.
