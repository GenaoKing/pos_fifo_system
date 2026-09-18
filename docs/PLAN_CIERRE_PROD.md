# Cierre del gate de producción — plan conjunto Codex / Claude

Fecha: **2026-09-18**. Estado: **en ejecución; CT-03 sync y C04 p6 aceptados
localmente; A07 backend revalidado y el candidato backend C04 p5.2 está listo
para integrar antes de abrir el consumidor frontend**.

Este documento coordina el cierre de bugs y deuda técnica antes de promover el
`develop` corregido a `staging`, luego a producción y, finalmente, a los POS de
Royal Plast (RP) y SK Performance (SK). **Repartir o aprobar el plan no autoriza
desplegar, ejecutar Terraform, modificar datos de clientes ni activar servicios.**

## 1. Punto de partida y alcance

Referencias obligatorias: [CLAUDE.md](../CLAUDE.md),
[estado del proyecto](PROJECT_STATUS.md), [estado de auditorías](ESTADO_AUDITORIAS.md),
[pendientes](TODO_AUDITORIAS.md), [bugs](BUGS.md) y los `AGENTS.md` de las apps
que se vayan a modificar. Antes de operar, leer el runbook correspondiente.

Inventario de planificación, que A00 debe volver a comprobar antes de integrar:

- Backend `develop`: `45ca23afcdc5811d9e4d94556c1c05fc99949890`, worktree
  `C:/Proyectos/pos_fifo_system`, limpio al preparar este reparto.
- Worktree de staging: `C:/Proyectos/pos_fifo_system_notifications_staging`,
  detached en `89b30c4c03bcdbfbbdeece47bdab9b5d026c1181`.
  **Sus `terraform.tfvars` ignorados contienen la configuración de staging que
  hay que preservar. No usarlo como worktree de desarrollo.**
- La comparación anterior identificó 20 commits de `develop` no presentes en
  `origin/staging`; volver a contar y clasificar, no usar «20» como constante.
- Frontend: repositorio separado `C:/Proyectos/pos-cloud-dashboard`. Su base de
  trabajo será `origin/develop`, no una feature branch encontrada abierta.
- Los SHAs de Git no prueban qué corre ahora en Azure o en cada tienda. El
  inventario operativo de A00/A08 debe registrar también imágenes y versiones
  instaladas; las cifras antiguas de migraciones no sustituyen ese inventario.

**Alcance de cierre:** todos los hallazgos vigentes de bugs y deuda técnica del
inventario de A00, más las regresiones de este release. Revalidar cada hallazgo
histórico: se corrige o se acredita que ya está resuelto. No se reimplementan
arreglos existentes ni se cierra un hallazgo solo porque su PR fue fusionado.
Los proyectos futuros expresamente excluidos no entran por llamarles «deuda».

### Decisiones que ambos agentes deben respetar

1. Corregir sobre ramas basadas en `develop`; integrar por bloques en `develop`.
   Crear un **nuevo candidato de staging con todo el código integrado**. Las
   pruebas del staging anterior son antecedente, no aprobación del nuevo SHA.
2. Productos y categorías inactivos no aparecen en selección operativa,
   búsqueda de venta ni escaneo. En administración se reutiliza el filtro
   **Activos / Inactivos / Todos**, con activos por defecto y reactivación
   autorizada. Un producto es operativamente activo solo si él y su categoría
   lo están. Desactivar la categoría no cambia el flag individual del producto.
   Conservar históricos y visibilidad administrativa de existencias.
3. Los maestros se podrán editar desde el POS con RBAC, incluso sin internet:
   cola durable de mutaciones, estado pendiente visible y resolución explícita
   de conflictos con el portal. No sustituirlo por un proxy que exija conexión
   ni por una política silenciosa de «última escritura gana».
4. Se mantiene efectivo sin caja abierta; no se anulan ventas con abonos
   aplicados hasta revertirlos manualmente; el cierre contable se finaliza
   manualmente. No instalar un cierre automático. No inventar atribuciones
   históricas de sucursal/turno ni corregir duplicados financieros a ciegas.
5. Las cotizaciones vencidas existentes **no bloquean el despliegue**.
   Los bugs del código de cotizaciones sí forman parte del cierre.
6. Se mantiene la topología PostgreSQL compartida actual, sin introducir HA ni
   separación de servidor en este release, y `min_replicas=0`. Redis queda fuera:
   permisos/configuración/módulos deben ser consistentes entre workers usando
   lectura de BD y memoización acotada a la petición.
7. Auditoría local consultable rápidamente durante al menos 90 días; sin purga
   ni almacenamiento WORM nuevo en esta fase. No presentar esto como garantía
   completa contra manipulación de la última fila. Cerrar `/admin/` en cloud;
   mantener el admin local, con autorización y operaciones cloud alternativas.
8. Notificaciones dormidas durante migración y luego activación gradual: demo,
   piloto y tenants reales tras sus verificaciones. **No quedan deshabilitadas
   permanentemente ni se elimina su preparación de infraestructura.**
9. Se excluyen Redis, WORM, separación/HA de PostgreSQL, migración de cuenta o
   suscripción Azure, nuevas funciones comerciales multi-sucursal, actualizador
   remoto de POS y e-CF nativo/certificación DGII. Conservar MSeller operativo.
   Las pruebas de aislamiento multi-tenant/multi-sucursal sí son obligatorias.

### Diferencias que se conservan frente al plan inicial de Claude

El plan externo `C:/Users/Santiago/.claude/plans/planear-y-evaluar-el-sparkling-teacup.md`
es un antecedente útil de despliegue; este reparto incorpora el alcance ampliado.
No trasladar sin corregir estas simplificaciones:

- `.env` se interpreta con Python/dotenv, **no con `for /f` de BAT**. Además hay
  que retirar las variables antiguas que NSSM haya guardado para ambos servicios.
- Los ACK falsos históricos de BUG-K no se recuperan por instalar el fix:
  reconciliar eventos locales `CONFIRMADO` con existencia real en cloud y generar
  una reparación dirigida. El backfill de eventos inexistentes es otro caso.
- Un rol diferido por permisos desconocidos no es automáticamente seguro:
  probar que la revocación llegue también a POS antiguos. Si no hay contrato
  compatible probado, la ventana cloud nuevo / POS viejo sigue bloqueada.
- `pg_restore --list` no es un ensayo de restauración. Restaurar un dump anterior
  después de nuevas ventas perdería esas operaciones sin recuperación explícita.
- No copiar `tfvars` de staging a prod ni resetear ramas por rutina. Un push
  normal desde una rama atrasada se rechaza; no usar force-push para «alinearla».

## 2. Reparto y propiedad de archivos

- **Agente A — Codex:** núcleo backend, identidad, permisos, auditoría, sync,
  maestros, dependencias, infraestructura, integración y custodia del gate.
  Encargo: [CIERRE_PROD_CODEX.md](planes/CIERRE_PROD_CODEX.md).
- **Agente B — Claude:** despliegue Windows/dotenv, documentos e impresión,
  configuración/módulos, operación comercial y portal React.
  Encargo: [CIERRE_PROD_CLAUDE.md](planes/CIERRE_PROD_CLAUDE.md).

**Un archivo tiene un solo escritor.** Las excepciones explícitas de esta tabla
prevalecen sobre una carpeta general. Compartir una app no autoriza modificar el
archivo del otro agente. El propietario integra los hooks solicitados por el
consumidor; cualquier transferencia se registra antes de editar.

| Superficie | Escritor | Frontera / consumidor |
| --- | --- | --- |
| `apps/auditoria`, `usuarios`, `negocios`, `tenancy`, `sucursales`, `permisos`, `notificaciones`, `sync` | A | B consume los contratos y solicita hooks; no modifica el motor de sync. |
| `apps/productos`, `apps/clientes`, sus templates/static específicos y API de maestros | A | B modifica los selectores comerciales en ventas/cotizaciones y el portal React. |
| `apps/configuracion`, `apps/suscripciones` y sus comandos | B | A conecta sus contratos al motor sync/settings; B mantiene conversión/verificación de `.env`. Excepción temporal registrada: A implementa solo `apps/configuracion/services.py` para C04 p5.2; ver `handoffs/cierre_prod/A-C04P5-BACKEND-ADMIN-2026-09-18.md`. |
| `apps/ventas`, `inventario`, `caja`, `cuentas_por_cobrar`, `cotizaciones`, `reportes`, `facturacion_electronica` | B | A integra handlers de sus eventos dentro de `apps/sync` y API sync. |
| `apps/api/**` | A | Excepciones de B: views/serializers/tests de configuración, suscripciones, CxC y reportes, y `views/reportes_urls.py`. Para C04 p5.2 A implementa por transferencia `views/serializers/tests administracion`; A conserva auth, permisos, maestros, notificaciones, sucursales y todo sync. |
| `apps/api/urls.py`, `config/**`, `manage.py`, routers/settings globales | A | B propone rutas y requisitos del loader; A los integra. |
| `apps/common/**` | A | Excepciones de B: `apps/common/pdf/**`, sus tests `test_pdf_*` y futuros módulos exclusivamente PDF. `AGENTS.md` de common lo integra A. |
| `utils/imagenes.py`, `utils/impresoras/**`, PDF de las apps de B | B | A modifica los hooks de modelos de productos/clientes, no las utilidades. |
| `templates/base.html`, navegación/estáticos globales compartidos | B | A solicita enlaces/flags; B no redefine RBAC ni entitlements en JavaScript. |
| `deploy/**`, launchers Windows, `.env.example`, documentación de instalación local | B | Excepción: configs reales ignoradas son datos operativos protegidos, no editables por lote. |
| `requirements*`, locks backend, Docker, CI/CD, `infra/**` | A | B pide dependencias para PDF/Windows y usa el baseline publicado. No cambia Azure ni workflows. |
| Repositorio frontend React, package/lock y documentación propia | B | Excepción: workflows de despliegue/CI son de A. Coordinar scripts/contratos antes de tocarlos. |
| Tests y migraciones | Propietario del dominio | Un test compartido existente queda con su escritor; crear módulos separados o solicitar integración. |
| Este plan, `PROJECT_STATUS`, `ESTADO_AUDITORIAS`, `TODO_AUDITORIAS`, `BUGS`, `CLAUDE.md`, mapas de API/common | A | B entrega deltas documentales en su handoff. Mapas de sus otras apps los actualiza B. |
| Encargo y handoffs `Axx-*` / `Cxx-*` | A / B respectivamente | No editar simultáneamente un mismo registro. |

Archivo no contemplado o con dos dominios: se asigna antes de editar; no se
interpreta como permiso para que ambos lo cambien. Esta regla incluye fixtures,
factories, scripts de prueba, routers y archivos `__init__.py` compartidos.

## 3. Arranque seguro y protocolo de integración

### Preparación común (A00)

1. Revisar Git en ambos repos y worktrees; conservar trabajo ajeno. Commitear
   únicamente estos documentos de coordinación para obtener un SHA base común
   antes de que cada agente cree sus ramas. Registrar SHAs backend y frontend.
   Los comandos de creación de ramas/worktrees se ejecutarán en la fase de
   trabajo autorizada, no como efecto de redactar el plan.
2. Crear worktrees separados desde la base común de `develop`:
   `C:/Proyectos/pos_fifo_system_cierre_codex` y
   `C:/Proyectos/pos_fifo_system_cierre_claude`. No usar el worktree de staging.
   Usar ramas por bloque: `codex/cierre-prod-Axx` y `claude/cierre-prod-Cxx`.
3. Claude tendrá un worktree frontend independiente, por ejemplo
   `C:/Proyectos/pos_cloud_dashboard_cierre_claude`, con base `origin/develop`.
   A debe coordinar cualquier cambio a sus workflows en una rama separada.
4. Cada agente usa su propio venv, `.env`, puertos, BD de desarrollo **y BD de
   tests**. Nombres sugeridos: `pos_cierre_codex` / `pos_cierre_claude`, puertos
   A 8101/8102 y B 8201/8202 para cloud/POS. No reutilizar la BD habitual.
   Verificar también aliases y nombres de BDs tenant: cambiar solo `DB_NAME`
   no aísla un test que cree tenants con nombres fijos. Si es necesario, usar
   instancias PostgreSQL separadas. No correr suites paralelas en una BD común.
5. Ensayos NSSM en VM/instalación desechable con servicios propios de prueba.
   No detener ni reconfigurar `POSFifoSystem`/sync reales para probar scripts.
   Dumps de clientes solo en entornos autorizados, con acceso restringido y
   fuera de Git; no enviar secretos o datos personales en evidencia.
6. Inventariar y proteger los `tfvars` canónicos del otro worktree sin volcar
   valores sensibles. Una sola persona opera cada state; prod usa sus propios
   valores. No hacer `apply`, cambiar state ni copiar secretos en esta fase.

### Contratos que desbloquean el trabajo paralelo

A registra los contratos en `docs/handoffs/cierre_prod/CONTRATOS.md`; B propone
su parte mediante su handoff. No implementar nombres de endpoints/campos
inventados independientemente. Publicar esquema, ejemplos, errores, ownership,
versión, compatibilidad, fixtures de contrato y un commit consumible.

| Contrato | Define | Consumidor / condición de entrada |
| --- | --- | --- |
| CT-01 — auditoría e identidad | A02: actor, tenant, sucursal, canal, objeto estable, before/after, resultado y correlación; transacciones y redacción | B puede empezar PDF/Windows antes; productores de auditoría se integran después de este commit. |
| CT-02 — permisos y capacidades | A03: autorización, revocaciones, catálogo/presets, capacidades POS viejo/nuevo | B aplica gates en C02/C03/C04/C05; ningún bypass paralelo. |
| CT-03 — configuración efectiva | B/C03 publicó fixture/test `capacidades.efectivas.v1`; A integró SUS-007/CFG-007 en el candidato local | A consume desde sync/settings; B desde UI/API/servicios. Lecturas consistentes sin Redis. |
| CT-04 — maestros offline | A05/A06: identidad, revisiones, cola, ACK/retry, conflictos y visibilidad efectiva | B/C04 integró p6: UI a 201 filas, contratos HTTP vivos y cobertura de pantalla; pendiente aceptación técnica, no publicación. |
| CT-05 — artefacto y actualización | A01/A08: runtimes, locks, manifiesto/digest y migraciones; C01/C06: paquete y preflight Windows | Ambos prueban la misma versión congelada, sin recompilar el artefacto aprobado para prod. |

### Ciclo de cada bloque

1. A registra dependencias listas y el SHA base; el propietario toma un bloque
   acotado. No desarrollar durante semanas contra una base antigua sin integrar.
2. Implementar cambio, migraciones y pruebas del dominio; actualizar su mapa
   cuando cambie un contrato. El PR incluye solo ese bloque y su evidencia.
3. Las migraciones las genera el dueño de la app sobre su último head integrado.
   Registrar padres y orden entre apps. Si hace falta una migración del otro,
   integrar primero su bloque; no reservar números a ciegas ni reescribir una
   migración ya aplicada. A verifica el grafo conjunto desde cero y desde copia.
4. Entregar commit(s)/PR y handoff. El otro agente revisa el diff, riesgos y tests.
   A integra serialmente en `develop` y publica el nuevo SHA verde. Ni Claude ni
   una automatización hacen merges/pushes de staging/main para «desbloquearse».
5. Consumir el bloque integrado mediante actualización coordinada de la rama.
   No hacer force-push a ramas compartidas ni cherry-pick duplicado en ambos lados.
6. Si un cambio invalida un contrato aprobado, crear una nueva revisión,
   notificar al consumidor y repetir las pruebas afectadas. No cambiar la
   semántica silenciosamente para pasar un test local.

**Handoff por bloque:** `docs/handoffs/cierre_prod/<Axx-o-Cxx>-<tema>.md`.
Contenido mínimo: estado, SHA base/resultado, IDs de hallazgos, archivos, contrato,
migraciones/BDs, comandos exactos, entorno y resultado de tests, pruebas omitidas,
riesgos, rollback, dependencias del otro y siguiente tarea desbloqueada. No
marcar «probado» por inspección; distinguir automatizado, simulado y físico.

Estados: `PENDIENTE → EN_CURSO → REVISION → INTEGRADO → VALIDADO`.
`BLOQUEADO` indica dependencia concreta y responsable; no significa que el
agente deba tocar archivos ajenos. Solo A actualiza el estado global.

## 4. Bloques completos y asignación

Todos los bloques parten **PENDIENTES**. La tabla es la matriz de cobertura del
plan original; el detalle ejecutable está en los encargos de cada agente.

| Bloque | Resultado exigido | Dueño / encargo | Dependencia |
| --- | --- | --- | --- |
| B00 — inventario y alcance | Ledger exhaustivo de hallazgos, ramas, instalaciones, decisiones y responsables | A / A00 | Ninguna |
| B01 — dependencias | Baseline reproducible Windows/cloud/CI y artefactos sin secretos | A / A01; B consume en C01/C06 | B00 |
| B02 — auditoría | Contrato común y mutaciones auditadas, consultas de 90 días | A / A02; B integra productores de sus apps | B01 |
| B03 — usuarios/tenancy | Identidad estable, provisioning recuperable, aislamiento y admin cloud cerrado | A / A02 | B02 para aceptación |
| B04 — RBAC | PER-006/007 y deuda vigente; revocación compatible y sin lockout | A / A03; B aplica gates en sus superficies | B02/B03 |
| B05 — módulos/config | Un resolutor efectivo, validaciones y cache por petición | B / C03; A adapta consumidor sync | CT-01/02/03 |
| B06 — transporte | Lease, diferidos durables, cursores e idempotencia; recuperación BUG-K dirigida | A / A04 | B01, contrato de identidad |
| B07 — maestros offline | Identidad/adopción, cola local, receptor versionado, conflictos y hechos preservados | A / A05/A06; portal B / C04 | B04/B06, CT-03 |
| B08 — maestros/UI | Activos por defecto, reactivación, filtros/paginación y estado pendiente/conflicto | A / A06 en backend/POS maestros; B / C04 en React y C05 en selección comercial | CT-04 y B07 |
| B09 — operación comercial | Ventas, CxC, inventario, caja y cotizaciones consistentes y auditados | B / C05; A integra handlers sync | CT-01/02/03, CT-04 para selectores |
| B10 — documentos | PDF, imágenes, impresión, permisos y evidencia física | B / C02; A integra hooks de maestros | CT-01/02 para aceptación |
| B11 — POS Windows | Instalador/updater dotenv, NSSM, backups y recuperación ensayados | B / C01/C06; A integra settings/deps | CT-03/05 |
| B12 — cloud/release | CI multi-BD, migraciones obligatorias, digest y Terraform de prod revisado | A / A08 | B01; resto para cierre |
| B13 — staging integrado | RC congelado, suites, copias migradas, compatibilidad y matriz física | A / A09 dirige; B / C06 verifica Windows/portal/negocio | B00–B12 integrados |
| B14 — prod cloud | Backend/migraciones primero; portal después; piloto y observación | A / A09, operador único; B verifica | Gate firmado y autorización |
| B15 — POS RP → SK | Ensayo, respaldo, update, operación física, conciliación y observación | A / A09 coordina; B / C06 responsable del procedimiento local | B14 sano y autorización por tienda |

### Orden para aprovechar a los dos agentes

| Oleada | Codex | Claude | Punto de encuentro |
| --- | --- | --- | --- |
| 0 | A00: base común, inventario y aislamiento; A01: baseline | Tras base común, C01: updater/dotenv en laboratorio | CT-03/05; Claude solicita cambios de settings/requirements a Codex. |
| 1 | A02: auditoría/identidad; A03: RBAC | C02: PDF/impresión; terminar C01 | CT-01/02; integrar hooks y repetir tests con baseline final. |
| 2 | A04: transporte; A05: identidad y cola | C03: configuración/módulos; parte financiera de C05 | CT-03; gates únicos y eventos auditados. |
| 3 | A06: conflictos/API y maestros/POS | C04: portal sobre contrato; terminar C05 y selectores | CT-04; pruebas reales offline/portal/POS e inactivos. |
| 4 | A07: cierre del inventario; A08: pipeline/infra | C06: ensayos updater, migraciones y revisión cruzada | Todos los bloques integrados; no quedan hooks simulados. |
| 5 | A09: staging y acta de gate | C06: aceptación Windows, portal y negocio | Firma técnica conjunta + aprobación del responsable. |
| 6 | Operación cloud, luego RP y SK, autorizada | Verificador y soporte del update | Un operador muta cada entorno; no despliegues concurrentes. |

Las oleadas orientan el orden, no obligan a esperar un bloque entero si su
contrato ya está integrado. El trabajo físico requiere al operador de la tienda;
no se puede cerrar solo con dos agentes de software.

## 5. Gates y evidencia de pase

### G0 — listo para desarrollar en paralelo

- Base documental común, worktrees/BDs/servicios de prueba separados.
- Cada hallazgo activo tiene ID, dueño, reproducción/estado, bloque y evidencia
  de cierre requerida. Cada excepción futura está explicada, no oculta como done.
- Contratos pendientes tienen productor/consumidor; tareas iniciales independientes
  A01 y C01 disponibles. Ninguna credencial real en commits o handoffs.

### G1 — listo para nuevo staging

- B00–B12 integrados y revisados. Todos los hallazgos del alcance corregidos o
  acreditados como ya resueltos; ninguna revisión cruzada bloqueante abierta.
- Pruebas Django según `TESTING.md`, e-CF por pytest separado, frontend
  build/lint/tests y migraciones sin cambios sin generar. Windows en serial.
- Pruebas de concurrencia/idempotencia, aislamiento multi-BD y compatibilidad
  cloud nuevo/POS instalado reales; no solo fixtures del código nuevo.
- Migración desde instalación limpia y copias representativas de **cada** POS
  y tenant; dependencias/paquete offline reproducibles en runtimes destino.
- Manifiesto de candidato: backend SHA + digest, frontend SHA/artefacto,
  paquete Windows + hash, locks, migraciones y configuración requerida sin valores.

### G2 — listo para autorizar producción

- G1 cumplido; ese candidato pasó staging. Cambiar código después invalida la
  aprobación del candidato anterior y exige reverificar lo afectado.
- Al menos 24 h de observación que incluyan arranque en frío y conciliación
  diaria; prueba con más de 200 productos para atravesar páginas; sin cursores
  atascados, diferidos inexplicados ni hechos financieros perdidos.
- Matriz: permisos/revocación POS viejo, roles custom, cierre con ventas/abonos,
  usuario fuera de sucursal, BUG-I/J, impresión física, offline/reinicio,
  conflictos portal/POS, categoría inactiva y reactivación. Web Push caducado
  se prueba con respuesta 410 controlada; fallo de proveedor también simulado.
- Backups de todas las BDs activas y control plane restaurados en aislamiento;
  comprobar integridad y tiempos, no únicamente que el dump sea listable.
- Plan prod Terraform revisado sin destrucciones inesperadas, VAPID/job/alertas
  preparados, migraciones obligatorias y resultado por BD antes de mover la API.
- Acta de pase con evidencia, ventanas, responsable, criterios de abortar y
  recuperación de escrituras posteriores al backup. El usuario autoriza operar.

### G3 — cloud sano antes de tocar un POS

- Repetir inventario/preflight/backups en la fecha real: no asumir 11 migraciones
  ni un número fijo de tenants. Migrar control plane y todos los tenants activos;
  si uno falla, no dar por finalizado el rollout ni continuar con el portal.
- Backend aprobado y su digest primero; comprobar login, API, RBAC y sync de
  versiones instaladas. Portal `main` después: su push puede desplegarlo solo.
- Activar notificaciones primero en tenant demo con corte temporal y job probado;
  tenants reales según piloto, no habilitación masiva durante migraciones.
- Al menos 60 minutos de observación cloud y dos ciclos de sync por POS existente;
  investigar discrepancias y falsos ACK antes de declarar sano el sistema.

### G4 — cierre por tienda y del release

- RP fuera de horario: respaldo/ensayo final, update, `.env`, servicios, seeds,
  identidad/adopción y pull verificados antes de habilitar edición offline.
- Comparar antes/después conteos, saldos y hechos; reconciliar primero en dry-run.
  Toda reparación aplica únicamente la lista validada, con trazabilidad.
- Operador prueba venta, crédito/abono/reversa, anulación, impresión/reimpresión,
  cierre/PDF, offline/reinicio y recuperación. Verificar impresión doble en SK.
- Observar RP un día real de operación antes de actualizar SK; repetir la matriz
  y activar notificaciones del tenant solo tras el smoke correspondiente.
- Rollback de código no implica rollback de esquema. Restauración completa al
  backup es opción segura sin escrituras posteriores; si ya las hubo, preservar,
  recuperar/reconciliar y preferir fix-forward. No perder ventas por restaurar.
- Cerrar el release solo con evidencia de ambos POS, cloud y portal; actualizar
  fuentes vivas y entregar el handoff final con versiones realmente instaladas.

## 6. Qué podemos adelantar sin desplegar

Código/tests por bloques; contratos; preflight de solo lectura; inventario de
versiones y servicios; paquete y wheelhouse offline; validación de dependencias;
ensayo de migraciones/restauración en copias autorizadas; preparación de variables
requeridas y revisión del plan Terraform; runbooks y agenda con las tiendas.

No adelantar como efecto secundario: `apply`, cambios de secretos de producción,
reset de cursores/estados, reparaciones financieras, migraciones de instalaciones
reales, push a ramas que autodespliegan o activación de notificaciones reales.

## 7. Registro inicial

| Elemento | Estado | Evidencia |
| --- | --- | --- |
| Reparto documental A/B | Preparado | Este plan y los dos encargos enlazados |
| Bootstrap/base común A00 | **Integrado localmente (2026-09-10)** | Base `c4af604`; inventario/CT `eb5f6b0`; handoff `docs/handoffs/cierre_prod/A00-base-inventario.md`. Sin push/deploy. |
| Implementación A01–A08 / C01–C06 | **A01-A04, C01-C03 y C05 partes 1-2 en `develop`; A05/A06, C05 CT-04/p6, SUS-014 y CT-03 sync están integrados localmente y aceptados** | `integration/cierre-prod-A06-C04-C05@2df0749` integra A06, selectores, 12 productores CT-01, SUS-014, CT-03 sync y el checkpoint A07. C04 p6 está en `claude/cierre-prod-C04@f0e6c2d`; el backend C04 p5.2 es `codex/cierre-prod-C04-p5-backend-admin@5790ec2` y espera integración local antes del frontend. C06 sigue pendiente. |
| G0 / G1 / G2 / G3 / G4 | **G0 completado; CT-03/C04 p6 aceptados y A07 backend revalidado; G1-G4 siguen pendientes de preflight/release** | Backend: 398 focales y A07 605 OK. Frontend C04: build/lint 120 tests y evidencia HTTP a 201 filas 24/24. Faltan preflight read-only autorizado, C04 p5, C06 y gates de release. |
| Despliegue cloud / RP / SK | No autorizado por este documento | Requiere autorización operativa explícita |
