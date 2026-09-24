# A00 — inventario de cierre de producción

Actualización vigente **2026-09-24**: SUS-007/CFG-007 y SUS-016 están
incorporados al candidato consolidado. También `dec46a3` y C06.1/2/3;
el ensayo completo del paquete nuevo pasó sobre `d69c73e` en copia de
desarrollo; no cubre las copias de RP/SK ni G1–G4. Las capturas fechadas
anteriores se conservan como historia. Ver `INTEGRACION-TOTAL-2026-09-24.md`.

Captura: **2026-09-10 America/Santo_Domingo**. Base funcional auditada:
`origin/develop@45ca23afcdc5811d9e4d94556c1c05fc99949890`. Base documental
común local: `c4af604f48a9334cbf3cc35a9b866046e2ed052e`.

Este ledger consolida los hallazgos vigentes de `TODO_AUDITORIAS.md`,
`ESTADO_AUDITORIAS.md`, `BUGS.md` y los roadmaps vivos. Los snapshots de
`docs/exploracion/` aportan título, severidad y reproducción, pero no sustituyen
la revalidación contra el código. Para filas aún pendientes, `commit = —` y la
prueba indicada es el criterio que debe cerrar el bloque; no significa que ya
haya pasado.

Estados usados: `PENDIENTE`, `DECIDIDO` (política fijada, falta o no código),
`ACREDITADO` (código/evidencia verificable), `OPERATIVO_PENDIENTE`, `EXCLUIDO`
y `DIFERIDO_EXPLICITO`. Un despliegue anterior nunca equivale a validación del
nuevo candidato.

Actualización A03: **2026-09-11**; implementación `3e6cec1`, merge local
`b7147fb` y matriz combinada validada. No modifica la captura base A00 ni
acredita despliegue.

Actualización A04+C05: **2026-09-11**; A04 (`be15ea0`) y C05 parte 1
(`60c6dbc`) se integraron primero en `9ff61c2`. C05 parte 2 fue revisada sobre
esa base y sus hallazgos de integración se cerraron en `18e0898`. La evidencia
acredita solo código local: CT-04, C04, la compatibilidad HTTP real y cualquier
sonda o reparación de clientes permanecen pendientes en sus bloques.

Conciliación Claude/Codex: **2026-09-18**. El candidato consolidado
`integration/cierre-prod-A06-C04-C05@dfb1dfc` integra A06, C05/CT-04, C05 p6
(12 acciones CT-01), SUS-014 y fixture/test CT-03; la matriz backend focal dio
363 OK. C04 frontend `e319058` sigue en su repositorio: su smoke HTTP reportó
23/23 y resta UI a >200 filas. Ver
`CONCILIACION-CLAUDE-CODEX-2026-09-18.md`.

Actualización A07: **2026-09-18**. El responsable aceptó las dos puntas
locales CT-03/C04 p6 descritas en
`INTEGRACION-CT03-C04P6-2026-09-18.md`; por tanto se abrió esta reconciliación
sobre `integration/cierre-prod-A06-C04-C05@1357cd7`. La matriz de familias A
(`auditoria`, `usuarios`, `negocios`, `permisos`, `tenancy`, `sync`,
`productos`, `clientes`) terminó con **605 pruebas OK**, incluyendo las dos
bases físicas namespaced de TEN-016. No hubo migración, push, despliegue,
lectura ni escritura de datos operativos. El detalle reproducible, los estados
revisados y los diferidos queda en `A07-INVENTARIO-2026-09-18.md`.

Durante esa corrida se reparó una falsa falla del *fixture* de clientes: cuatro
casos de escritura de API no activaban el flag exclusivo del runner de tests
`API_MAESTROS_PERMITE_ESCRITURA_LOCAL_TEST`. No se relajó la protección del POS:
el test que la niega sigue cubierto; el cambio sólo declara el modo cloud
simulado donde esos cuatro casos fueron diseñados para ejecutar.

Reconciliación de ledger C02/C03: **2026-09-16** (Claude, read-only + doc). La
sección "Configuración, suscripciones y documentos" arrastraba `PENDIENTE` en
filas ya cerradas por C02/C03 en `develop`: el ledger se actualizó para
A03/A04/C05 pero no a nivel-ítem de C03. Revalidado contra código+test en
`develop`, se pasan a `ACREDITADO` con su commit: COM-005/006/007/008/009/014
(`d937db5`), CFG-006/011 (`38e5647`), CFG-009 (`b19c4a5`), CFG-013/014/015
(`fe5c8de`), CFG-017 + SUS-015 (`7201043`), SUS-008/009 (`1ca1688`), SUS-010/012/018
(`d968e3f`), SUS-011 (`506edf2`), SUS-013 (`6e3d551`), SUS-017 (`3018ce8`). Siguen
`PENDIENTE` a propósito: CFG-012 (resuelto solo en `integration/cierre-prod-A05-C03`,
no en `develop`), SUS-007 y SUS-016 (C+A: mitad C hecha, falta la mitad de Codex),
y el resto sin código todavía. No hubo cambios de código ni de rama en esta pasada.

Trabajo self-contained de la sesión **2026-09-16** (Claude): cinco ramas sobre
`develop`, **sin push ni merge**, listas para el checkpoint que integra sobre
`integration/cierre-prod-A05-C03`. Estado consolidado y orden de integración en
**[ESTADO_SESION_2026-09-16.md](ESTADO_SESION_2026-09-16.md)** conserva la
fotografía previa a la integración. El 2026-09-17 el checkpoint
`integration/cierre-prod-A05-C03` integró y revalidó COM-012 (`78bec9a`),
CFG-010 patas 1+2 (`0db8f57`), CFG-018/019/020 (`1a7b767`) y SUS-019
(`0548384`), junto con A05.3. El candidato sigue local: no es `develop`, no fue
publicado ni desplegado. `REPORTES-CHART-CDN` (Chart.js offline, `1009ba3`)
continúa parte del mismo checkpoint.

## Base, ramas, worktrees y aislamiento

**Actualización A05.4 (2026-09-17).** CT-04 queda publicado sobre
`integration/cierre-prod-A05-C03@695b36c`: el transporte implementado es
`master.mutation.v1` y el fixture canónico
`fixtures/ct04_master_offline_v1.json` reserva listado y acciones para A06.
Esto habilita el trabajo contractual de C04, no una integración portal, un
despliegue ni resoluciones backend.

| Elemento | Resultado verificado | Evidencia / acción |
| --- | --- | --- |
| Backend remoto | `origin/develop@45ca23a`; local no divergía antes del bootstrap | `git fetch --prune`; `rev-list develop...origin/develop = 0/0` |
| Base documental | `c4af604` sobre `develop`, un commit local por delante | Commit aislado de los cuatro documentos de coordinación. No se hizo push: `develop` despliega dev por CI y este encargo prohíbe desplegar. |
| Backend Claude | `C:/Proyectos/pos_fifo_system_cierre_claude`, rama `claude/cierre-prod-C01@c4af604` | Worktree propio; no usa staging. |
| Backend Codex | `C:/Proyectos/pos_fifo_system_cierre_codex`, rama `codex/cierre-prod-A00@c4af604` | Worktree propio. |
| Frontend Claude | `C:/Proyectos/pos_cloud_dashboard_cierre_claude`, rama `claude/cierre-prod-C01-frontend@239da82` | Parte de `origin/develop`, no de la feature abierta ni del `develop` local atrasado. |
| Staging protegido | `C:/Proyectos/pos_fifo_system_notifications_staging@89b30c4`, detached y limpio | No se usa para desarrollo. |
| Venvs | `.venv` independiente en ambos worktrees backend, Python 3.11.14 | Creados sin modificar el conda compartido. Python 3.12 no está instalado en el host; se validará mediante la imagen cloud. |
| BDs dev | `pos_cierre_codex` y `pos_cierre_claude`, vacías, en PostgreSQL local | Creadas con el usuario de desarrollo documentado. Los tests derivan `test_pos_cierre_*`. |
| BDs tenant de tests | TEN-016 usa `TENANT_TEST_DB_NAMESPACE` y dos BDs físicas derivadas por corrida (`583863f`) | Cada runner crea/destruye solo su namespace. Sin la variable, el gate se salta y no registra aliases extra. |
| Puertos reservados | Codex 8101/8102; Claude 8201/8202 | Backend `.env` ignorado usa 8101/8201; segundo puerto reservado para rig/frontend. |
| Servicios | No existen `POSFifoSystem`/`POSFifoSync` en este host | Los ensayos NSSM quedan para servicios de laboratorio de C01/C06. |

Los `.env` aislados son ignorados, contienen solo valores locales de desarrollo,
`SYNC_ENABLED=false` y no se versionan. No se copiaron configuraciones reales.

### `tfvars` canónicos protegidos

El worktree de staging conserva cuatro `terraform.tfvars` ignorados. El archivo
canónico de staging es
`infra/azure/environments/staging/terraform.tfvars`, 4031 bytes, SHA-256
`87F8E9D1AF0D605D67C8BEECDFEFE8F52EBE6D845E2693D2069A3DECD461E6EE`,
última escritura UTC `2026-09-07T17:01:36.6022366Z`. No se leyó ni copió su
contenido, no se ejecutó Terraform y no se tocó state.

## Versiones instaladas observables sin mutar entornos

| Entorno | Backend / job | Portal | Estado de certeza |
| --- | --- | --- | --- |
| dev | API revisión `posfifo-dev-api--0000065`, imagen `45ca23a`; jobs migrate/notificaciones en la misma imagen | SWA `develop`; workflow exitoso para `239da82` | Consultado con Azure CLI y GitHub Actions el 2026-09-10. |
| staging | API revisión `posfifo-staging-api--0000016`, imagen `89b30c4`; migrate/notificaciones en la misma imagen | SWA `staging`; workflow exitoso para `e0a2302` | Consultado con Azure CLI y GitHub Actions. |
| prod | API revisión `posfifo-prod-api--0000008`, imagen `bcb8621`; migrate en la misma imagen; no existe job de notificaciones | SWA `main`; último workflow prod exitoso para `c9116f5` | Esto corrige la nota vieja que decía “imagen de junio”: el digest observado corresponde al commit del 2026-08-24. |
| Royal Plast POS | SHA/paquete exacto no observable desde este host | N/A | `OPERATIVO_PENDIENTE`: obtener en preflight autorizado; no inferirlo por la imagen cloud. |
| SK Performance POS | SHA/paquete exacto no observable; documentación confirma paquete viejo y riesgo de doble impresión | N/A | `OPERATIVO_PENDIENTE`: obtener en preflight autorizado. |

No se iniciaron jobs, no se cambiaron recursos y no se consultaron ni alteraron
filas operativas.

## Diferencia `origin/staging..origin/develop`

La cifra se volvió a calcular: **20 commits de develop no están en staging** y
staging tiene **2 merge commits propios** (`bb37b2f`, `89b30c4`). Las ramas no
son ancestro directo una de otra; no se debe “alinear” con reset ni force-push.

| Clase | Cantidad | Commits | Tratamiento |
| --- | ---: | --- | --- |
| Funcional | 2 | `446d29c` comprobante de venta PDF; `fbcbaf4` UX de cuadre | Ya forman parte de la base; no reimplementar. Revalidar en C02/C05. |
| CI | 1 | `2b48259` PostgreSQL de pruebas | Ya forma parte de la base; A01 lo adapta al baseline. |
| Tests | 1 | `45ca23a` regresiones caja/configuración | Ya forma parte de la base. |
| Documentación/mapas/evidencia | 12 | `68fbf61`, `c72fdfb`, `f970553`, `2288d43`, `2de1e23`, `cb0b767`, `3f4e971`, `5b455e3`, `f750822`, `782d3ee`, `982d891`, `215369e` | Antecedente, no aprobación del RC nuevo. |
| Merges | 4 | `a99476a`, `21fe8e2`, `bf2f8de`, `cb42757` | Historia de integración; no cherry-pick duplicado. |

## Decisiones del cierre que sustituyen documentación vieja

| ID | Decisión vigente | Dueño / bloque | Estado | Evidencia de cierre requerida |
| --- | --- | --- | --- | --- |
| DEC-MAESTROS-OFFLINE | El POS puede editar maestros con RBAC aun offline; cola durable, pendiente visible y conflicto explícito. | A A05/A06; C C04/C05 | DECIDIDO | Reinicio offline, dos escritores, rechazo por revocación y resolución CAS. |
| DEC-INACTIVOS | Operación solo usa producto y categoría activos; administración ofrece Activos/Inactivos/Todos y reactivación. | A A06; C C04/C05 | DECIDIDO | Búsqueda, escaneo, checkout y >200 filas. |
| CAJA-002 | Efectivo sin caja abierta se mantiene permitido y queda sin turno. | C C05 | ACREDITADO; revalidado C05 | Tests de venta/cobro sin turno y arqueo distinguible. |
| CXC-006 | Venta con abonos aplicados no se anula hasta revertirlos manualmente. | C C05 | ACREDITADO; revalidado C05 | 409, reversa LIFO auditada y anulación posterior. |
| RPT-004 | Cierre nace BORRADOR y se finaliza manualmente. | C C05 | ACREDITADO; revalidado C05 | Recalcular borrador y congelar solo con `--finalizar`. |
| RPT-005 | No habrá cierre automático; el launcher roto se retira y la operación queda manual. | C C01/C05 | ACREDITADO | `ed47249`: launcher retirado, runbook manual y comando probado. |
| DEC-BACKFILL-HIST | No atribuir sucursal/turno ni corregir duplicados financieros a ciegas. | A09/C06 | DECIDIDO | Toda reparación real empieza en dry-run y lista aprobada. |
| DEC-COT-VENCIDAS | Cotizaciones vencidas existentes no bloquean; los bugs de cotizaciones sí. | C C05 | DECIDIDO | Reportar conteo en preflight, sin mutarlo. |
| DEC-CACHE | Sin Redis en este release; BD + memo por request, consistentes entre workers. | A01/A03; C03 | DECIDIDO | Cambio visible en request siguiente de otro worker. |
| DEC-AUDITORIA | Consulta local rápida >=90 días, sin purga/WORM nuevos ni promesa de inviolabilidad total. | A A02 | DECIDIDO | Índices/consultas 90 días y limitación documentada. |
| DEC-ADMIN | `/admin/` cerrado en cloud; admin local permanece con autorización y alternativas cloud. | A A02; C C04 | DECIDIDO | Cloud 403/404 para principal tenant y operación equivalente autorizada. |
| DEC-NOTIF | Dormidas durante migración y activación gradual demo/piloto/tenants; no se eliminan. | A08/A09; C06 | DECIDIDO | Jobs/config listos y activación solo con autorización. |
| EXC-ARQUITECTURA | Redis, WORM, HA/separación PostgreSQL y migración de cuenta/suscripción Azure quedan fuera. | A08 | EXCLUIDO | Registrar riesgo; no implementar ni ocultar el pendiente. |
| EXC-PRODUCTO | Nuevas funciones multi-sucursal, updater remoto y e-CF nativo/certificación DGII quedan fuera; MSeller se conserva. | A/C | EXCLUIDO | No mezclar con deuda obligatoria del release. |

## Hallazgos de código vigentes por ID

`Fuente` abrevia el checklist vivo y el snapshot técnico correspondiente.
`Prueba` remite a la reproducción/aceptación detallada bajo el mismo ID en ese
snapshot; el bloque debe convertirla en regresión automatizada antes de cerrar.

### Permisos — dueño A, A03/B04

| ID | Fuente | Reproducción actual | Dueño | Bloque | Severidad | Estado | Commit | Prueba |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PER-006 | TODO + AUD-PER | Mover la terna natural deja activa la asignación anterior en el POS. | A | A03 | Crítica | RESUELTO | `3e6cec1` | Identidad UUID + revoke/create atómico y prueba API/sync. |
| PER-007 | TODO + AUD-PER | El borrado físico de rol custom no baja a POS. | A | A03 | Crítica | RESUELTO | `3e6cec1` | Baja lógica versionada, tombstone y reconciliación completa scoped. |
| PER-012 | TODO + AUD-PER | Cambiar M2M de rol no siempre avanza cursor. | A | A03 | Media-alta | RESUELTO | `3e6cec1` | Add/remove/clear/set directo y reverso avanzan revisión/timestamp. |
| PER-013 | TODO + AUD-PER | Catálogo y enforcement real no coinciden por completo. | A+C | A03/C02/C05 | Media-alta | ACREDITADO | `3e6cec1`, `60c6dbc` | Contrato/helper y consumidores de anulación/reimpresión por sucursal validados juntos. |
| PER-014 | TODO + AUD-PER | Mutaciones RBAC sin auditoría durable. | A | A03 | Media-alta | RESUELTO | `3e6cec1` | Servicios emiten exactamente un evento CT-01 por mutación. |
| PER-015 | TODO + AUD-PER | `sync_permisos` mezcla catálogo, presets y transporte. | A | A03 | Media | RESUELTO | `3e6cec1` | Catálogo por defecto; presets solo con opción explícita. |
| PER-016 | TODO + AUD-PER | Bootstrap no es idempotente/seguro multi-negocio. | A | A03 | Media-alta | RESUELTO | `3e6cec1` | Transacción/alias, negocio explícito y revocación preservada. |
| PER-017 | TODO + AUD-PER | Escrituras API de rol/asignación no son atómicas bajo carrera. | A | A03 | Media-alta | RESUELTO | `3e6cec1` | Servicios con atomic/locks/constraints y revisión 409. |
| PER-018 | TODO + AUD-PER | Admin/comandos no comparten garantías API/tenant. | A | A03 | Media-alta | RESUELTO | `3e6cec1` | Admin read-only; comandos tenant-aware y alias explícito. |
| PER-019 | TODO + AUD-PER | Data migrations dependen de código vivo y sin reversa semántica. | A | A03 | Baja-media | RESUELTO | `3e6cec1` | Datos/helpers históricos congelados; migración desde cero verde. |
| PER-020 | TODO + AUD-PER | Fallbacks ocultan errores de configuración. | A | A03 | Baja-media | RESUELTO | `3e6cec1` | DRF deny-by-default; template deniega y registra solo tipo. |
| PER-021 | TODO + AUD-PER | Suite/documentación omiten fronteras adversariales. | A | A03 | Media | RESUELTO | `3e6cec1` | Matriz CT-02 y documentos vivos actualizados. |

### Auditoría, identidad, negocios y tenancy — dueño A, A02/B02-B03

| ID | Fuente | Reproducción actual | Dueño | Bloque | Severidad | Estado | Commit | Prueba |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AUD-008 | TODO + AUD-AUD | Política de fallo contradictoria puede impedir sesión. | A | A02 | Alta | RESUELTO | `cd8a3b4` | Logout invalida aunque falle el sink; dominio exitoso revierte si falla CT-01. |
| AUD-009 | TODO + AUD-AUD | Anulación registra estado nuevo como anterior. | A | A02 | Alta | RESUELTO | `cd8a3b4` | Snapshot previo probado en anulación. |
| AUD-010 | TODO + AUD-AUD | Acción, nivel y resultado admiten combinaciones incoherentes. | A | A02 | Alta | RESUELTO | `cd8a3b4` | Constraints/validación rechazan combinaciones imposibles. |
| AUD-013 | TODO + AUD-AUD | Excepciones completas/duplicadas sin redacción. | A | A02 | Alta | RESUELTO | `cd8a3b4` | Fixture recursivo de password/token/DSN/excepción. |
| AUD-016 | TODO + AUD-AUD | `registrar_compra()` no serializa payload documentado. | A | A02 | Alta al activar | RESUELTO | `cd8a3b4` | Payload de proveedor serializable probado. |
| AUD-018 | TODO + AUD-AUD | Taxonomía promete productores que no existen. | A | A02 | Alta | RESUELTO | `583863f`, `bb7f774` | `productores.py` mapea cada tipo legacy o declara `SIN_PRODUCTOR`; V1 enumera acciones válidas sin comodines. |
| AUD-019 | TODO + AUD-AUD | Visor oculta datos necesarios para investigar. | A | A02 | Media | RESUELTO | `cd8a3b4` | Consulta scoped expone envelope redactado y rango temporal. |
| AUD-020 | TODO + AUD-AUD | Sin lifecycle/índices suficientes para 90 días. | A | A02 | Media | RESUELTO | `cd8a3b4` | Índices tenant/sucursal/entidad/fecha; ventana consultable >=90 d, sin purga automática. |
| AUD-021 | TODO + AUD-AUD | Relación genérica no conserva identidad histórica estable. | A | A02 | Media-baja | RESUELTO | `cd8a3b4` | Referencia opaca y snapshot sobreviven renombre/borrado. |
| USR-007 | TODO + AUD-USR | Sin flujo autoritativo de provisioning tenant. | A | A02 | Media-alta | RESUELTO | `583863f` | Servicio/comando crean usuario+RBAC+CT-01 o revierten. |
| USR-010 | TODO + AUD-USR | `Identity` y `Usuario` son credenciales independientes. | A | A02 | Media-alta | RESUELTO | `583863f` | Secretos/rotaciones local y portal separados; igualdad rechazada. |
| USR-011 | TODO + AUD-USR | Manager omite validación de credencial/email/rol. | A | A02 | Media | RESUELTO | `583863f` | Alta humana soportada usa `create_human_user`, validadores y `full_clean`; `create_user` queda compat ORM. |
| USR-012 | TODO + AUD-USR | Tres fuentes de privilegio sin invariantes comunes. | A | A02/A03 | Media-alta | RESUELTO | `583863f`, `3e6cec1` | Identidad/tenant validados; RBAC versionado y revocación común. |
| USR-013 | TODO + AUD-USR | Mutaciones de usuario sin auditoría de dominio. | A | A02 | Media | RESUELTO | `583863f` | Alta/actualización/estado con CT-01 en la transacción tenant. |
| USR-014 | TODO + AUD-USR | IP confía en cualquier `X-Forwarded-For`. | A | A08 | Media | PENDIENTE | — | Proxy real + spoof adversarial. |
| USR-015 | TODO + AUD-USR | `last_login` y `ultimo_acceso` divergen. | A | A02 | Media-baja | RESUELTO | `583863f` | Login local/portal persiste el mismo instante. |
| USR-016 | TODO + AUD-USR | Username/email únicos dependen de mayúsculas. | A | A02 | Media | RESUELTO | `583863f` | `Lower()` único y migración con preflight sin renombre. |
| USR-017 | TODO + AUD-USR | Sesión deslizante sin máximo absoluto de 12 h. | A | A02 | Baja-media | RESUELTO | `583863f` | Middleware local y access/refresh JWT vencen al máximo absoluto. |
| USR-019 | TODO + AUD-USR | Rutas dev/redirecciones no comparten política. | A | A02 | Baja | RESUELTO | `583863f` | Home por rol único; styleguide staff+DEBUG; rutas POS ausentes en cloud. |
| NEG-006 | TODO + AUD-NEG | Tres fuentes de identidad comercial divergen. | A | A02 | No indicada | RESUELTO_CODIGO | `583863f` | Autoridad control-plane y verificador read-only de proyecciones. |
| NEG-007 | TODO + AUD-NEG | Ciclo tenant sin auditoría de dominio. | A | A02 | No indicada | RESUELTO | `583863f` | Preparación/checkpoints auditados y reanudables. |
| NEG-008 | TODO + AUD-NEG | Borrado de negocio cascada seguridad/entitlements. | A | A02 | No indicada | RESUELTO | `583863f` | Delete Admin cerrado; lifecycle soportado desactiva y Usuario usa PROTECT. |
| NEG-009 | TODO + AUD-NEG | `slug` mutable participa en identidad legacy. | A | A02 | No indicada | RESUELTO | `583863f` | Slug lógico/físico inmutable y preflight sin reapuntar. |
| NEG-011 | TODO + AUD-NEG | RNC sin canon ni política de unicidad. | A | A02 | No indicada | RESUELTO_CODIGO | `583863f` | Canon de 9 dígitos único; migraciones abortan colisión. |
| NEG-012 | TODO + AUD-NEG | Escrituras directas evitan validadores. | A | A02 | No indicada | RESUELTO | `583863f` | `save()`/servicios ejecutan `full_clean` y constraints. |
| NEG-013 | TODO + AUD-NEG | Autogeneración de slug omitible/solo memoria. | A | A02 | No indicada | RESUELTO | `583863f` | Slug se incluye aunque `update_fields` lo omitiera. |
| NEG-014 | TODO + AUD-NEG | Generación de slug tiene carrera TOCTOU. | A | A02 | No indicada | RESUELTO | `583863f` | Constraint + savepoint y retry acotado. |
| NEG-016 | TODO + AUD-NEG | Documentación de modelo describe arquitectura retirada. | A | A02/A07 | No indicada | RESUELTO_A02 | `583863f` | Docstring/mapa distinguen tenant lógico de routing físico. |
| NEG-017 | TODO + AUD-NEG | Lifecycle disperso fuera de la app. | A | A02 | No indicada | RESUELTO | `583863f` | `apps.negocios.services` concentra alta/actualización/estado. |
| TEN-016 | TODO + AUD-TEN | CI no prueba aislamiento PostgreSQL multi-DB real. | A | A02/A08 | Media-alta | RESUELTO | `583863f` | Dos BDs físicas namespaced, mismo PK y filas/refs aisladas. |

### Productos y clientes — dueño A, A05-A06/B07-B08

| ID | Fuente | Reproducción actual | Dueño | Bloque | Severidad | Estado | Commit | Prueba |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PRO-002 | TODO + AUD-PRO | Escritura local no se propaga autoritativamente. | A | A05/A06 | Crítica | ACREDITADO_LOCAL | `3c6c0e7`, `7e5535d`, `b25a3b7` | Cola durable y conflicto explícito repetidos por A07 en `test_mutaciones_maestro_a052a`; ACK/reintento/CAS del receptor A05.3 quedaron acreditados en la matriz integrada de 398. |
| PRO-003 | TODO + AUD-PRO | SKU editable rompe identidad del pull. | A | A05 | Crítica | ACREDITADO_LOCAL | `cfbd507` | SKU y `origen_cloud_id` inmutables incluso por `QuerySet.update`; adopción única cubierta en `test_identidad_a05`. |
| PRO-004 | TODO + AUD-PRO | Borrado API no deja tombstone. | A | A05/A06 | Crítica | ACREDITADO_LOCAL | `b25a3b7` | Baja lógica con motivo; el pull conserva inactivos y no borra historia (`test_producto_viewset`). |
| PRO-009 | TODO + AUD-PRO | HTML/modelo omiten validaciones de API. | A | A06 | Alta | PENDIENTE | — | Matriz UI/API/modelo. |
| PRO-010 | TODO + AUD-PRO | Precios/catálogo sin auditoría de dominio. | A | A06 | Alta | PENDIENTE | — | CT-01 before/after. |
| PRO-011 | TODO + AUD-PRO | Admin masivo oculta fecha real de cambio. | A | A06 | Media-alta | PENDIENTE | — | Aceptación PRO-011. |
| PRO-012 | TODO + AUD-PRO | Lifecycle de imágenes no atómico/no converge. | A+C | A06/C02 | Media-alta | PENDIENTE | — | Commit/cleanup/retry. |
| PRO-013 | TODO + AUD-PRO | Atributos configurables no forman esquema aplicado. | A | A06 | Media-alta | PENDIENTE | — | Aceptación PRO-013. |
| PRO-014 | TODO + AUD-PRO | SKU/código de barras tienen carreras. | A | A06 | Media-alta | PENDIENTE | — | N altas concurrentes. |
| PRO-015 | TODO + AUD-PRO | Errores/borrados protegidos sin contrato. | A | A06 | Media-alta | PENDIENTE | — | Errores API/UI estables. |
| PRO-016 | TODO + AUD-PRO | Chequeo cloud ocurre antes de autenticar. | A | A06 | Media | PENDIENTE | — | Usuario anónimo no provoca I/O cloud. |
| PRO-017 | TODO + AUD-PRO | Impresión sin permiso, cuota o trazabilidad. | A+C | A06/C02 | Media | PENDIENTE | — | CT-01/02 + resultado incierto. |
| PRO-019 | TODO + AUD-PRO | Lista completa + N+1. | A | A06 | Media | ACREDITADO_LOCAL | `b25a3b7` | Paginación operativa conserva más de 200 inactivos (`test_paginacion_operativa_mantiene_mas_de_doscientos_inactivos`). |
| PRO-020 | TODO + AUD-PRO | Admin muestra indicadores inconsistentes/vacíos. | A | A06 | Baja-media | PENDIENTE | — | Aceptación PRO-020. |
| PRO-021 | TODO + AUD-PRO | Formato de código de barras configurado no se respeta. | A+C | A06/C02 | Baja-media | PENDIENTE | — | Gramática/históricos. |
| PRO-022 | TODO + AUD-PRO | Deuda de admin, índices y portabilidad. | A | A06 | Baja | PENDIENTE | — | Aceptación PRO-022. |
| CLI-004 | TODO + AUD-CLI | Escritura local contradice autoridad cloud y se pisa. | A | Bloque posterior explícito | Crítica funcional | DIFERIDO_EXPLICITO | `eaefc1b` (contención) | Cliente adoptado devuelve 409 y no confirma un cambio que el pull pisaría; la cola offline/CAS de clientes no existe aún y no queda habilitada por A07. |
| CLI-006 | TODO + AUD-CLI | Catálogo no aislado en base compartida. | A | A06 | No indicada | PENDIENTE | — | Dos negocios sin fuga. |
| CLI-008 | TODO + AUD-CLI | Escrituras locales omiten `full_clean`. | A | A06 | No indicada | PENDIENTE | — | Validación negativa. |
| CLI-009 | TODO + AUD-CLI | Cédula/RNC sin canon real. | A | A06 | No indicada | PENDIENTE | — | Normalización/colisión. |
| CLI-010 | TODO + AUD-CLI | Identidad origen parcial/degrada al borrar sucursal. | A | A05 | No indicada | PENDIENTE | — | Identidad estable/adopción. |
| CLI-011 | TODO + AUD-CLI | API/alta local sin auditoría. | A | A06 | No indicada | PENDIENTE | — | CT-01 transaccional. |
| CLI-012 | TODO + AUD-CLI | Edición de límite no atribuye sucursal. | A | A06 | No indicada | PENDIENTE | — | CT-01 scope. |
| CLI-013 | TODO + AUD-CLI | DELETE físico da 500 con referencias. | A | A06 | No indicada | PENDIENTE | — | Baja lógica/409 estable. |
| CLI-015 | TODO + AUD-CLI | Detalle apunta a plantilla inexistente. | A | A06 | No indicada | PENDIENTE | — | GET detalle 200/404. |
| CLI-016 | TODO + AUD-CLI | Listado/búsqueda con N+1 financiero. | A | A06 | No indicada | PENDIENTE | — | Presupuesto de queries. |
| CLI-017 | TODO + AUD-CLI | Admin muta internos fuera del dominio. | A | A06 | No indicada | PENDIENTE | — | Aceptación CLI-017. |
| CLI-018 | TODO + AUD-CLI | UI muestra acciones no autorizadas. | A | A06 | No indicada | PENDIENTE | — | CT-02 UI + servidor 403. |
| CLI-019 | TODO + AUD-CLI | Dos CRUD mantienen contratos distintos. | A | A06 | No indicada | PENDIENTE | — | Matriz local/API. |
| CLI-021 | TODO + AUD-CLI | Índices/código dispersan responsabilidades. | A | A06 | No indicada | PENDIENTE | — | Aceptación CLI-021. |

### Configuración, suscripciones y documentos — dueño C, C02-C03

| ID | Fuente | Reproducción actual | Dueño | Bloque | Severidad | Estado | Commit | Prueba |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| COM-005 | TODO + AUD-COM | Tabla mal formada desborda página sin error. | C | C02 | P2 | ACREDITADO | `d937db5` | `_validar_forma_tabla` rechaza geometría/columnas inválidas; test en `apps/common/tests`. |
| COM-006 | TODO + AUD-COM | Vacíos/dimensiones propagan excepciones crudas. | C | C02 | P2 | ACREDITADO | `d937db5` | Vacíos/dimensiones degradan con aviso; test en `apps/common/tests`. |
| COM-007 | TODO + AUD-COM | Logo corrupto rompe documento. | C | C02 | P2 | ACREDITADO | `d937db5` | `_logo_es_valido` degrada logo corrupto con warning; test en `apps/common/tests`. |
| COM-008 | TODO + AUD-COM | Fallo storage se oculta y quita logo. | C | C02 | P2 | ACREDITADO | `d937db5` | Fallo de storage se registra distinto de "no hay logo"; test en `apps/common/tests`. |
| COM-009 | TODO + AUD-COM | Logo remoto ilimitado agota memoria. | C | C02 | P2 urgente | ACREDITADO | `d937db5` | `_leer_acotado` lee por chunks con tope `LOGO_MAX_BYTES` + pre-check de `size`; test en `apps/common/tests`. |
| COM-012 | TODO + AUD-COM | Tabla materializa todos los registros. | C | C02 | P2 | ACREDITADO | `78bec9a` | `standard_table` recorre perezoso y corta en `TABLA_MAX_FILAS=5000` con fila de aviso; test. **Integrado y revalidado localmente en `integration/cierre-prod-A05-C03` el 2026-09-17.** |
| COM-013 | TODO + AUD-COM | ReportLab/Pillow no fijados en build. | A+C | A01/C02 | P2 | PENDIENTE | — | Locks Windows/cloud. |
| COM-014 | TODO + AUD-COM | Logo se deforma a cuadrado fijo. | C | C02 | P3 | ACREDITADO | `d937db5` | `_logo_flowable` escala manteniendo proporción dentro de la caja; test en `apps/common/tests`. |
| CFG-006 | TODO + AUD-CFG | Combinaciones operativas/fiscales inseguras. | C | C03 | Alta | ACREDITADO | `38e5647` | `full_clean()` rechaza combinaciones inseguras; test `test_auditoria_configuracion`. |
| CFG-007 | TODO + AUD-CFG | Pull omite validadores/choices. | C+A | C03/A sync hook | Alta | ACREDITADO | `1019500` | Validación antes de guardar; payload inválido congela cursor sin mutación ni diferido. |
| CFG-008 | TODO + AUD-CFG | Controles e-CF no forman unidad. | C | C03 | Alta diferida | PENDIENTE | — | Matriz MSeller; nativo no activo. |
| CFG-009 | TODO + AUD-CFG | Templates/gates leen dos verdades. | C | C03 | Media-alta | ACREDITADO | `b19c4a5` | UI/menús leen `modulos_efectivos()`, no `config.modulo_*`; test de configuración. Nota: la mitad de SUS-007 (sync) sigue en Codex. |
| CFG-010 | TODO + AUD-CFG | Acceso rápido sin scope/invariantes DB. | C | C03 | Media-alta | ACREDITADO | `0db8f57` | Pata 1: `save()` valida (full_clean). Pata 2: FK `sucursal` (mig. 0011, null=legacy) + consumidor filtra por sucursal. **Integrado y revalidado localmente en `integration/cierre-prod-A05-C03` el 2026-09-17.** Preflight: backfill legacy + CheckConstraint. |
| CFG-011 | TODO + AUD-CFG | `QuerySet.delete()` salta protección. | C | C03 | Media-alta | ACREDITADO | `38e5647` | `delete()` de instancia y de QuerySet levantan `ConfiguracionProtegidaError`; test de configuración. |
| CFG-012 | TODO + AUD-CFG | Leer configuración puede crearla. | C | C03 | Media | ACREDITADO_LOCAL | `23dc805`, `a865af3` | Lectura pura y bootstrap explícito/inequívoco integrados en el candidato; `develop` conserva la versión anterior. |
| CFG-013 | TODO + AUD-CFG | Verificador acepta legacy con módulos apagados. | C | C03 | Media-alta | ACREDITADO | `fe5c8de` | `verificar_instalacion` sale distinto de cero ante config incompleta. |
| CFG-014 | TODO + AUD-CFG | Diagnóstico muestra otra sucursal y sale 0. | C | C03 | Media | ACREDITADO | `fe5c8de` | Diagnóstico usa la sucursal actual y `--strict` sale no-cero; test `test_verificar_instalacion`. |
| CFG-015 | TODO + AUD-CFG | `crear_config_inicial` sin sucursal pisa primera. | C | C03/C01 | Media-alta | ACREDITADO | `fe5c8de` | `crear_config_inicial` sin sucursal ya no pisa la primera fila. |
| CFG-016 | TODO + AUD-CFG | Conversión BAT→env no garantiza round-trip/ACL. | C | C01 | Media-alta | PENDIENTE | — | Matriz caracteres/idempotencia. |
| CFG-017 | TODO + AUD-CFG | Cambios de config sin auditoría uniforme. | C | C03 | Media-alta | ACREDITADO | `7201043` | Admin registra evento CT-01 en la misma transacción; test de configuración. |
| CFG-018 | TODO + AUD-CFG | Logo sin lifecycle/propagación. | C | C02/C03 | Media-baja | ACREDITADO | `1a7b767` | `save()` borra el logo anterior al reemplazar; test. Propagación/authoritativeness siguen abiertas. **Integrado y revalidado localmente en `integration/cierre-prod-A05-C03` el 2026-09-17.** |
| CFG-019 | TODO + AUD-CFG | Superficies declaradas sin flujo soportado. | C | C03 | Baja-media | ACREDITADO | `1a7b767` | Retirados `requiere_sysadmin`/`requiere_admin_o_sysadmin` (sin uso) + AGENTS.md. **Integrado y revalidado localmente en `integration/cierre-prod-A05-C03` el 2026-09-17.** |
| CFG-020 | TODO + AUD-CFG | Formato barcode promete más que generador. | C | C02/C03 | Baja-media | ACREDITADO | `1a7b767` | `clean()` valida `[A-Z0-9]{1,13}-XXXXXX`; test. Lado generador (productos) sigue en A. **Integrado y revalidado localmente en `integration/cierre-prod-A05-C03` el 2026-09-17.** |
| CFG-021 | TODO + AUD-CFG | Suite no cubre fronteras críticas. | C | C03 | Media | ACREDITADO | — | Cubierto por tests de C03 (caché, fallback, RBAC, validación cruzada, borrado) + AccesoRapido; pull inválido cubierto por CFG-007/CT-03. Multiworker conserva su gate de infraestructura. |
| SUS-006 | TODO + AUD-SUS | CxC/reportes HTML sin gate de módulo. | C | C05 | P1 | ACREDITADO | `60c6dbc` | Gates HTML/API responden 404 con módulo apagado y conservan permiso ortogonal. |
| SUS-007 | TODO + AUD-SUS | Templates, sync y servicio leen fuentes distintas. | C+A | C03/A hook | P1 | ACREDITADO | `1019500` | Pull legacy derivado del resolutor efectivo; cursor incluye cambios oficiales de plan/override. |
| SUS-008 | TODO + AUD-SUS | Bootstrap une flags entre sucursales. | C | C03 | P1 | ACREDITADO | `1ca1688` | Bootstrap preserva flags por sucursal; test `test_auditoria_suscripciones`. |
| SUS-009 | TODO + AUD-SUS | Config legacy sin sucursal se pierde al migrar. | C | C03 | P1 | ACREDITADO | `1ca1688` | Config legacy `sucursal=NULL` ya no se ignora en silencio; test de suscripciones. |
| SUS-010 | TODO + AUD-SUS | Error DB se interpreta como permiso de baja. | C | C03 | P1 crítico | ACREDITADO | `d968e3f` | Hook de datos en vuelo es fail-closed: la excepción bloquea la baja; test de suscripciones. |
| SUS-011 | TODO + AUD-SUS | Señales invalidan antes del commit. | C | C03 | P2 | ACREDITADO | `506edf2` | Invalidación de cache diferida a `transaction.on_commit`; test de suscripciones. |
| SUS-012 | TODO + AUD-SUS | Registro código y espejo DB divergen. | C | C03 | P2 | ACREDITADO | `d968e3f` | `checks.py` falla ruidoso si el catálogo en código y el espejo DB divergen; test de suscripciones. |
| SUS-013 | TODO + AUD-SUS | `activo`/estados sin semántica efectiva. | C | C03 | P2 | ACREDITADO | `6e3d551` | `Plan.activo` bloquea nuevas altas sin suspender el core; test de suscripciones. |
| SUS-014 | TODO + AUD-SUS | Plan control-plane diverge del operativo. | C+A | C03/A07 | P2 | ACREDITADO_LOCAL | `b318681`, `c003af8`, `3a16e38`, `dfb1dfc` | Prevención en `bootstrap_tenant`; `PLAN_DRIFT` read-only se reporta desde `verificar_identidad_tenant`, sin autocorrección. |
| SUS-015 | TODO + AUD-SUS | Cambios comerciales sin auditoría. | C | C03 | P2 | ACREDITADO | `7201043` | `GuardDegradacionMixin` registra evento CT-01 en la misma transacción; productor de auditoría. |
| SUS-016 | TODO + AUD-SUS | Bootstrap/sync parcial reporta éxito pobre. | C+A | C03/A hook | P2 | ACREDITADO | `1ca1688`, `f83f67d` | Bootstrap atómico/dry-run más checkpoint determinista read-only; JSON/strict detectan estado parcial sin repararlo. |
| SUS-017 | TODO + AUD-SUS | `sync_modulos` no sincroniza planes default. | C | C03 | P2 | ACREDITADO | `3018ce8` | `Plan.preset_version` + `sync_modulos` resincroniza planes gestionados desactualizados; test `test_sync_modulos`. |
| SUS-018 | TODO + AUD-SUS | Key desconocida puede aprobarse fail-open. | C | C03 | P3 | ACREDITADO | `d968e3f` | Una key fuera del catálogo deniega aun en el camino fail-open; test de suscripciones. |
| SUS-019 | TODO + AUD-SUS | Suite omite fronteras contractuales. | C | C03 | P3 | ACREDITADO | `0548384` | Regresiones del guard por plan (downgrade) y `activa` (suspensión): auditadas + rollback sin evento. **Integrado y revalidado localmente en `integration/cierre-prod-A05-C03` el 2026-09-17.** |

### Cotizaciones y operación comercial — dueño C, C05/B09

| ID | Fuente | Reproducción actual | Dueño | Bloque | Severidad | Estado | Commit | Prueba |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| COT-008 | TODO + AUD-COT | Cantidades/importes imposibles persistibles. | C | C05 | Alta | ACREDITADO | `b6e898a`, `18e0898` | Constraints, preflight y 400 para valores inválidos. |
| COT-009 | TODO + AUD-COT | Acepta cliente/producto inactivo. | C | C05 | Media-alta | ACREDITADO | `460e05e`, `18e0898` + `productos_vendibles()`/`es_vendible` (PRO-007, A05.2a, ya integrado en esta rama) + tests en `claude/cierre-prod-C05-ct04-selectores` | Cliente inactivo (18e0898) + producto/categoría inactivos y `MutacionMaestro` en `CONFLICTO` (producto o categoría) revalidados server-side en `guardar_cotizacion`; `PENDIENTE` sigue cotizable. Accesos rápidos de categoría alineados a la misma regla. |
| COT-010 | TODO + AUD-COT | Numeración `count()+1` colisiona. | C | C05 | Media-alta | ACREDITADO | `460e05e`, `18e0898` | Máximo sufijo, unicidad legacy y retry/savepoint. |
| COT-011 | TODO + AUD-COT | Cabecera/detalle quedan con totales distintos. | C | C05 | Media-alta | ACREDITADO | `b6e898a`, `18e0898` | Servicio atómico y reconciliación al editar/borrar líneas. |
| COT-012 | TODO + AUD-COT | Ciclo sin auditoría de negocio. | C | C05 | Media-alta | ACREDITADO_LOCAL | `bbb5246`, `51946ef` | Crear/convertir persisten `audit.event.v1` dentro de la transacción; candidato local, sin publicar. |
| COT-013 | TODO + AUD-COT | Borrado no converge local/cloud. | C+A | C05/A sync hook | Media-alta | PENDIENTE | — | Tombstone/replay. |
| COT-014 | TODO + AUD-COT | Errores filtran internals/estatus incorrectos. | C | C05 | Media | ACREDITADO | `460e05e`, `18e0898` | Errores 4xx/5xx seguros y casos cliente/producto ausente. |
| COT-015 | TODO + AUD-COT | Estado/vínculo venta sin invariante DB. | C | C05 | Media | ACREDITADO | `b6e898a`, `18e0898` | Invariante bidireccional estado/venta + `PROTECT`. |
| COT-017 | TODO + AUD-COT | Listados/stock sin límites. | C | C05 | Media-baja | PARCIAL_C05 | `460e05e` | Lista paginada; stock al convertir y presupuesto de queries siguen pendientes. |
| COT-018 | TODO + AUD-COT | Vistas conservan caminos ambiguos/floats. | C | C05 | Baja | PENDIENTE | `18e0898` (parcial) | Cantidad ya usa Decimal; consolidar rutas y eliminar floats restantes. |

## Deuda transversal vigente sin ID único

| ID local | Fuente / reproducción actual | Dueño | Bloque | Severidad | Estado | Commit | Prueba de cierre |
| --- | --- | --- | --- | --- | --- | --- | --- |
| OPS-RD-MONEDA | Cambio visible `$`→`RD$` aún requiere comunicación. | C | C02/C06 | Operativa | OPERATIVO_PENDIENTE | — | Matriz física/documentos. |
| OPS-COM-001 | Sucursal sin config propia usa fallback y warning. | C | C03/C06 | Alta legal | OPERATIVO_PENDIENTE | — | Preflight de solo lectura por instalación. |
| OPS-COT-007 | Pendientes >15 días no convierten; no bloquean release. | C | C05/C06 | Operativa | DIFERIDO_EXPLICITO | — | Conteo/aviso, sin mutación. |
| OPS-COT-002 | `cotizaciones.precio_negociado` no está en roles default. | C | C05/C06 | Financiera | OPERATIVO_PENDIENTE | — | Matriz rol/403. |
| OPS-SUS-001 | Suscripción suspendida/null cambia de fail-open a módulos efectivos. | C | C03/C06 | Alta | OPERATIVO_PENDIENTE | — | Preflight sin editar datos. |
| OPS-CFG-002 | `SUCURSAL_CODIGO` inválido debe abortar, no cruzar identidad. | C | C01/C03 | Alta | OPERATIVO_PENDIENTE | — | Preflight aislado. |
| OPS-CFG-003 | Asignar `configuracion.administrar` a roles legítimos. | C | C03/C06 | Alta acceso | OPERATIVO_PENDIENTE | — | Sin lockout/403 correcto. |
| DOC-RBAC-WORKERS | RBAC_PERMISOS aún dice single-worker; Docker usa 3. | A | A03/A07 | Baja | RESUELTO | `3e6cec1` | Documento alineado con caché/namespace vigente. |
| USR-002-CLOUD-ADMIN | Cerrar `/admin/` cloud y cubrir alternativas; MFA/red queda fuera del código inmediato. | A+C | A02/C04 | Alta | PARCIAL_A02 | `583863f` | `/admin/` no se monta en cloud; alternativa visual sigue C04 y MFA/red queda diferido. |
| OPS-RESTORE | Dumps verificados pero sin restauración end-to-end. | A+C | A08/C06 | Alta | PENDIENTE | — | Restaurar control plane + tenants aislados. |
| OPS-PRO-007 | Categoría inactiva con producto activo cambia visibilidad efectiva. | A+C | A06/C04/C05 | Alta | CÓDIGO_LOCAL_INTEGRADO; OPERATIVO_PENDIENTE | `integration/cierre-prod-A06-C04-C05` | Backend/POS/portal cubiertos; falta conteo preflight aprobado con datos reales. |
| OPS-CLI-CONTADO | `clientes.0006` aborta ante cliente real marcado CONTADO. | A | A06/A08 | Alta migración | OPERATIVO_PENDIENTE | — | Preflight copia; no reasignar historia. |
| OPS-NEG-SELF | Más de una fila negocio en tenant bloquea provisioning. | A | A02/A08 | Alta | OPERATIVO_PENDIENTE | — | Preflight por copia. |
| OPS-USR-HUERFANOS | Usuario activo sin negocio pierde scope al cerrar fail-open. | A | A02/A08 | Alta | OPERATIVO_PENDIENTE | — | Lista read-only + plan manual. |
| OPS-PER-ROLES | Roles custom no reciben permisos por data migration. | A+C | A03/C06 | Alta | OPERATIVO_PENDIENTE | — | Matriz custom/system. |
| SYNC-CLAIM | Claim push no sobrevive crash; falta lease de 5 min. | A | A04 | Alta | ACREDITADO | `be15ea0` | Dos procesos, crash, lease vencido y ACK tardío verdes. |
| SYNC-DIFERIDOS | Diferido congela cursor; falta cola durable/estado PARCIAL. | A | A04 | Alta | ACREDITADO | `be15ea0` | Reinicio, rollback entre pasos, fallo al persistir y cursor cubiertos. |
| SYNC-LEGACY | `_pull_legacy` sigue por compatibilidad cloud viejo. | A | A04 | Media | DIFERIDO_EXPLICITO | `be15ea0` | Se conserva; matriz viejo/nuevo real queda A09 antes de retirarlo. |
| DB-CONSTRAINTS | Ventas/inventario validan cantidades/importes solo en app. | C | C05 | Alta | ACREDITADO | `b6e898a`, `18e0898` | Escritura directa rechazada y preflight identifica PKs. |
| CXC-MIG-ALIAS | `cuentas_por_cobrar.0002` usaba el manager sin `.using(schema_editor.connection.alias)` y podía sembrar `default`. | C | C05/A08 | Alta migración | ACREDITADO | `95dddb3` | Grafo tenant desde cero conserva el alias de `schema_editor`. |
| CXC-IDEMP-CONC | Faltaba prueba N reintentos de cobro con misma clave. | C | C05 | Alta | ACREDITADO | `b066636` | Seis reintentos producen exactamente un efecto financiero. |
| VEN-ANULAR-LEGACY | `_puede_anular` usa rol legacy. | C | C05 | Alta auth | ACREDITADO | `60c6dbc` | CT-02 contra sucursal de la venta; roles custom A/B y venta legacy cubiertos. |
| INV-RBAC-SCOPE | Gates inventario llamaban permiso sin sucursal. | C | C05 | Alta auth | ACREDITADO | `95dddb3` | Servicio reautoriza contra sucursal del lote; asignaciones A/B cubiertas. |
| SYNC-VENTA-ID | Handler venta cloud carece identidad compuesta robusta. | A | A04 | Alta | ACREDITADO | `be15ea0` | Identidad/hash scopeados; replay cross-branch no adopta el hecho ajeno. |
| TEN-API-AUDIT | Impersonación registra sesión, no cada mutación. | A | A02 | Alta | RESUELTO_CONTRATO | `583863f` | CT-01 conserva actor operativo e `impersonator_ref`; cada bloque acredita sus productores. |
| PER-ADMIN-BYPASS | `ADMIN` conserva bypass transitorio. | A | A03/A08 | Alta | OPERATIVO_PENDIENTE | `3e6cec1` | Flag y preflight listos; ejecutar por tenant antes de retirarlo. |
| NOTIF-RBAC-GUARD | Notificaciones duplica filtros de `permisos.engine`. | A | A03 | Media | RESUELTO | `3e6cec1` | `asignaciones_efectivas` es el helper único compartido. |
| PAG-CXC-CAJA | Cartera limita 300 con aviso e historial cortaba 50 sin aviso. | C | C05 | Media | ACREDITADO | `eb72f69` | Historial paginado; cartera conserva tope explícito y metadatos. |
| AUD-002-ULTIMA | Borrar última fila no es detectable sin WORM/cadena. WORM excluido; se documenta el límite. | A | A02 | Riesgo aceptado | DIFERIDO_EXPLICITO | — | No prometer garantía total. |
| REPORTES-CHART-CDN | Chart.js depende de CDN en POS offline. | C | C02/C05 | Media | ACREDITADO | `1009ba3` | Asset local `static/js/chart.min.js`; revisado PASS (`f085d77`) e **integrado por Codex en `integration/cierre-prod-A05-C03`**. |
| REPO-TEMP-SETTINGS | TODO citaba `settings_auditoria_sucursales_temp.py` sin trackear; ya no existe. | A | A00 | Baja | ACREDITADO | `c4af604` base | `Test-Path`/`git status` limpios. |

## Bugs BUG-A…M

| ID | Fuente / reproducción | Dueño | Bloque | Severidad | Estado actual | Commit/evidencia | Prueba pendiente |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BUG-A | Eventos no nacían si web carecía `SYNC_ENABLED`. | A+C | A04/C01/C06 | Alta | Código y cloud/RP desplegados; SK pendiente | BUGS + Fase 1 | Preflight SK y baseline final. |
| BUG-B | Cursor temporal/orden podía saltar maestros. | A | A04/A09 | Media | Corregido/desplegado previo | BUGS + Fase 2 | Compat viejo/nuevo y >200. |
| BUG-C | CxC sin cédula no resolvía cliente. | A+C | A04/C05/C06 | Alta | Corregido; historia/visita final a revalidar | BUGS | Replay y conciliación por tienda. |
| BUG-D | Bootstrap/config dejaba módulos e impresión apagados. | C | C01/C03 | Alta | Causa y fail-open corregidos; procedimiento final pendiente | BUGS | Instalación limpia baseline A01. |
| BUG-E | Refresh sin contexto tenant daba 500. | A | A02 | Baja | RESUELTO | `583863f` + BUGS | Refresh tenant-aware revalidado y límite absoluto cubierto. |
| BUG-F | Migraciones figuraban aplicadas sin tablas tenant. | A | A02/A08 | Crítica | Corregido y reparado en prod | `migrate_tenants` guard | Migración limpia/copia y tablas reales. |
| BUG-G | Update RP: backup locale, secret, orden y NSSM. | C | C01/C06 | Crítica | Correcciones previas; paquete final no ensayado | BUGS | Matriz interrupción/rollback. |
| BUG-H | SKU local faltante tumbaba venta cloud. | A+C | A04/A06/C04/C06 | Alta | Prod backend `bcb8621` contiene fix; POS/foto y RC final pendientes | BUGS | Stub→portal→pull/foto. |
| BUG-I | Chrome autocompleta credenciales en caja. | C | C05/C06 | Baja | Código + test revalidados; visual diferida | `45ca23a`, matriz C05 | Chrome con credenciales reales de laboratorio. |
| BUG-J | Comentario Django aparecía en sidebar. | C | C06 | Baja | Código + test revalidados; visual diferida | BUGS + matriz C05 | Inspección visual RC. |
| BUG-K | `IntegrityError` falso se ACKeaba duplicado. | A | A04 | Alta | Herramienta/reconciliación A04 listas; prod y falsos ACK históricos pendientes | `d831535`, `be15ea0`; dev/staging previos | Ejecutar primero dry-run y revisar lista en A09; no replay ciego. |
| BUG-L | Portal mostraba HTML 500 y confundía alta push. | C | C04/C06 | Media | Corregido dev/staging | Frontend staging `e0a2302` | RC integrado/backend real. |
| BUG-M | Safari no instalado tocaba `pushManager`. | C | C04/C06 | Media | Corregido y validado físicamente en staging | Frontend staging `e0a2302` | Repetir RC sin asumir evidencia vieja. |

## Deuda vigente de roadmaps y alcance

| ID local | Fuente | Dueño / bloque | Estado | Evidencia de cierre |
| --- | --- | --- | --- | --- |
| RM-SYNC-RIG | `ROADMAP_SYNC_CONFIABLE`: rig `royalplastdemo`/compatibilidad. | A A09 + C06 | OPERATIVO_PENDIENTE; no ejecutar ahora | RC exacto, tokens autorizados, dos ciclos. |
| RM-SYNC-PREFLIGHT | `verificar_sync` y versión exacta en RP/SK. | A09 + C06 | OPERATIVO_PENDIENTE | Preflight read-only autorizado por tienda. |
| RM-SYNC-ROLLOUT | Cloud primero, luego RP y SK con reparación dirigida. | A09 + C06 | OPERATIVO_PENDIENTE | G2/G3/G4 y autorización explícita. |
| RM-AZURE-PROD | Promoción prod, migraciones por BD, rollback/observabilidad/restore. | A08/A09 | PENDIENTE; despliegue no autorizado | Digest, plan sin destrucción, restore y acta. |
| RM-PORTAL-B11B | Escrituras locales de maestros al cloud. | A05/A06 + C04 | PENDIENTE | Cubierto por PRO-002/003/004 y CLI-004. |
| RM-PORTAL-RBAC-UI | UI de asignación rol/sucursal y smoke 403. | A03 + C04 | PENDIENTE | CT-02 y backend real. |
| RM-PORTAL-PASSWORD | Cambio de password desde perfil. | A02 + C04 | PENDIENTE | Identidad correcta, revocación de sesiones y auditoría. |
| RM-PORTAL-PAGINACION | Listas grandes sin contrato uniforme. | A06 + C04/C05 | PENDIENTE | >200, errores/parciales. |
| RM-OBSERVABILIDAD | Logs estables, alertas, dashboard y Sentry/equivalente. | A08 + C04 | PENDIENTE según gate | Alertas/job/API sin secretos. |
| RM-PORTAL-B12 | Inventario consolidado multi-sucursal nuevo. | — | EXCLUIDO por DEC-PRODUCTO | No implementar en este release. |
| RM-CLOUD-F6 | Segunda PC, modo nodo, heartbeat/alerta >1 h. | — | EXCLUIDO como nueva función multi-sucursal | Extraer después del cierre. |
| RM-REMOTE-UPDATER | Actualizador remoto POS. | — | EXCLUIDO explícito | Mantener updater manual C01/C06. |
| RM-NEON-FLOCI | Neon/Floci lab. | — | EXCLUIDO/no ruta objetivo | No consume el gate. |
| RM-HA-DB | Separar servidor/HA/geo-backup. | — | EXCLUIDO explícito; riesgo registrado | Topología compartida/min replicas 0. |

## Cobertura y regla de actualización

Este inventario contiene **124 hallazgos con ID de auditoría**, 30 deudas o
preflights sin ID único, 13 bugs etiquetados y 14 filas de roadmap/alcance.
Cuando un bloque cierre una fila debe reemplazar `—` por SHA, enlazar el test y
cambiar el estado solo con evidencia. A07 revalidó las familias asignadas a A:
un `PENDIENTE` restante es deuda identificada con criterio de cierre; un
`OPERATIVO_PENDIENTE` requiere entorno autorizado y un
`DIFERIDO_EXPLICITO` no se puede presentar como listo para release. Los handoffs
de Claude proponen deltas y Codex mantiene este archivo.
