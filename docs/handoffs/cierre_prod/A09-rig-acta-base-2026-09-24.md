# A09 — rig cloud/POS y base del acta de pase

Fecha: 2026-09-24. Estado: **rig local parcial acreditado; G1–G4 sin cerrar**.
Resultados y límites: [ensayo A09](A09-rig-local-2026-09-24.md).
Este registro aplica el [objetivo de release para RP y SK](../../planes/OBJETIVO_RELEASE_CLIENTES.md).
El alcance de trabajo nuevo excluye e-CF, etiquetas y periféricos accesorios;
se conserva la comprobación del comprobante habitual cuando se pruebe la venta.

## Candidato y evidencia ya recibida

| Componente | Referencia | Evidencia y límite |
| --- | --- | --- |
| Backend | Código `639d3b4` en `integration/cierre-prod-A06-C04-C05` | Suite integrada 1.654 Django sobre la base anterior; después del cambio Windows: 59 tests focales, 13 de release y lint `.bat` PASS. Falta CI Linux y congelar el SHA de promoción. |
| Portal | `integration/cierre-prod-C04-admin@a9d960e` | 143 tests, lint y build PASS; E2E real 10/10 anterior, no repetido en la integración. |
| Windows | Código `639d3b4`; ZIP SHA-256 `43e57726a4cce8f82bd65ee1301f0bf752fceb70832925866db14a969a89856a` | Dos construcciones bit a bit iguales, 36 wheels; procedimiento de ocho fases ensayado por Claude sobre RP con `settings_production` antes del pequeño cierre offline de Codex. C06.1 anterior probó 32/32 y backup/restore en copia de desarrollo. Sigue siendo candidato local. |
| Copias históricas RP/SK | Dumps de junio de 2026 | Claude reporta 78/78 migraciones RP y 115/115 SK sobre BDs descartables con el código `d69c73e`. Fuente: `C06.1-relevo-datos-reales-RP-SK.md`. SK no tenía fila de Sucursal en ese corte. Son fotos históricas; faltan copias actuales autorizadas y verificar identidad antes de actualizar. |

La incorporación del handoff de Claude está en la rama de integración.
Cada ensayo conserva su SHA: la evidencia C06.1 inicial corresponde a
`d69c73e`; el paquete reconstruido y el rig local corresponden al código
`639d3b4`. Ninguno se atribuye automáticamente a una imagen remota.

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
| G1, entrada a staging | **Pendiente** | Corte de bloqueadores de este release, compatibilidad con el SHA/binario realmente instalado, copias actuales autorizadas por POS/tenant con identidad y restore, flujo comercial completo, CI remoto y manifiesto/digest del RC. El rig local ejercitó HTTP/paginación/replay y una versión anterior aproximada, no el estado presente de las tiendas. |
| G2, pase a producción | **No iniciado** | Ese RC en staging, al menos 24 h con arranque en frío y conciliación, catálogo >200, matriz operativa del alcance, backups restaurados por BD, plan de infraestructura y acta con responsables/rollback. Requiere autorización del responsable antes de operar producción. |
| G3, cloud sano | **No iniciado** | Despliegue autorizado del digest aprobado, migraciones por BD, portal posterior, 60 min de observación y dos ciclos por POS existente. |
| G4, tiendas | **No iniciado** | RP actualizada y conciliada, un día real de observación, luego SK; aceptación de operadores, versiones efectivas y soporte. |

Esta es la **base del acta**, no el acta final. Cada gate se actualiza con
evidencia nueva y decisión fechada. Un test verde o el PASS de C06.1 no
autoriza por sí solo staging, producción ni una visita a clientes.
