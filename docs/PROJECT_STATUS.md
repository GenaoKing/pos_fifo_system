# Estado maestro del proyecto

Ultima revision: **2026-09-18** (`develop@fffd02b` permanece intacto; el
candidato local `integration/cierre-prod-A06-C04-C05@2df0749` reúne A05/C03,
A06, C05/CT-04, C05 p6, SUS-014 y CT-03 sync SUS-007/CFG-007). El frontend C04
integró p6 en `claude/cierre-prod-C04@f0e6c2d`; build/lint/120 tests y su
evidencia HTTP a 201 filas (24/24) están verdes localmente. El responsable
aceptó ambas puntas y A07 repitió 605 pruebas backend en un worktree aislado.
El candidato backend C04 p5.2
`codex/cierre-prod-C04-p5-backend-admin@5790ec2` añade las escrituras portal de
usuarios, sucursales y configuración, aún sin integrar. Sin push, despliegue ni
lectura o escritura de datos operativos.

Este documento es la puerta de entrada para leer el proyecto sin perderse entre
roadmaps, runbooks y bitacoras historicas. **Verifica la fecha de cada fila
antes de accionar:** lo que toca infraestructura de Azure (prod, imagenes,
Terraform) se verifico por ultima vez contra la nube el 2026-09-08 durante la
promocion a staging; el resto de esta revision se contrasto contra el codigo.

## Si sos un agente, empeza por el mapa de tu app

El punto de entrada para entender **codigo** no es este documento ni las
auditorias: es `apps/<app>/AGENTS.md`. Las 21 apps tienen el suyo -- que hace,
entrypoints ("necesito -> voy a"), modelos clave e invariantes -- y cada uno
lleva su `Ultima revision`. El contrato completo esta en `AGENTS.md` (raiz) y en
`CLAUDE.md`.

**Las auditorias de `docs/exploracion/` NO son base de conocimiento.** Son
snapshots historicos de hallazgos del 2026-08-20, muchos ya mitigados; cada una
abre con un banner que lo dice. Sirven para entender *por que* algo esta escrito
como esta, nunca como estado actual. Lo vivo de cada app es su `AGENTS.md`, y lo
vivo de las auditorias es `ESTADO_AUDITORIAS.md` (estado) + `TODO_AUDITORIAS.md`
(pendientes accionables).

## Como leer estos docs

- **Fuente viva**: documento que se debe actualizar cuando cambia el plan.
- **Runbook**: tutorial operativo para repetir una tarea.
- **Handoff**: fotografia de una fase, util para contexto pero no siempre es la
  fuente viva.
- **Historico**: bitacora o incidente conservado por trazabilidad.

Regla de organizacion: la raiz de `docs/` queda reservada para fuentes vivas
con decisiones pendientes o lectura operativa diaria. Tutoriales, handoffs,
bitacoras y exploraciones viven en subcarpetas.

### Plan activo de cierre a produccion

El [plan conjunto Codex / Claude](PLAN_CIERRE_PROD.md) define el alcance de cierre,
propiedad de archivos, dependencias y gates para integrar `develop`, validar un
nuevo candidato en staging y preparar cloud -> Royal Plast -> SK Performance.
Encargos: [Codex](planes/CIERRE_PROD_CODEX.md) y
[Claude](planes/CIERRE_PROD_CLAUDE.md). Estado: **el `develop` local integra
A00-A04, C01-C03 y C05 partes 1-2; el candidato aislado
`integration/cierre-prod-A05-C03@609c98f` suma A05.1-A05.4. El candidato
consolidado `integration/cierre-prod-A06-C04-C05@2df0749` integra A06,
selectores C05/CT-04, doce productores C05 p6 CT-01, SUS-014 y CT-03 sync.
C04 p6 se integró en `claude/cierre-prod-C04@f0e6c2d`, con evidencia local a
  201 filas. Ambas puntas fueron aceptadas y A07 dejó evidencia local de 605
  pruebas; C04 p5.2 backend debe integrarse antes de abrir el consumidor
  frontend. G1-G4 siguen
  pendientes de preflight y release; nada está aprobado
ni publicado**.
Inventario,
contratos y handoffs:
[`docs/handoffs/cierre_prod/`](handoffs/cierre_prod/). No autoriza despliegues.
Sus decisiones de alcance prevalecen sobre
recomendaciones historicas de este indice; no prueban el estado actual de Azure.

## Resumen ejecutivo

| Area | Estado | Fuente viva | Siguiente accion |
| --- | --- | --- | --- |
| Vision/producto | En progreso | `VISION_PRODUCTO_2026.md` | Elegir la proxima apuesta de producto luego de cerrar deploy dev/staging. |
| POS local | En produccion en 2 clientes | `ROADMAP_CLOUD.md` | Desplegar Fases 1-4 de sync (visita RP sabado, SK semana siguiente). Nuevo (2026-09-08, sin desplegar): comprobante de venta formal en PDF, `apps/ventas/pdf_comprobante.py` + migracion `auditoria.0007`. |
| Deploy POS local | Update in-place + `.env` (Fase 4) | `docs/runbooks/INSTALACION_CLIENTE_NUEVO.md` | Desplegar el paquete nuevo; rotar SECRET_KEY (#9) en la misma ventana. |
| Portal cloud | **En produccion** | `ROADMAP_PORTAL.md` | ASWA prod vivo (`red-bay-07331a710`) apuntando a la API de prod. Imagenes de RP subidas (2026-08-23); grilla de productos pinta miniatura, no el original (2026-08-24, falta desplegar a prod). |
| Deploy Azure backend | dev/staging/prod vivos | `ROADMAP_DEPLOY_AZURE.md` | Prod corre imagen de junio: promover `develop`->`main` + job de migraciones. |
| Tenancy cloud | **Fases 1-5 CERRADAS** | `TENANCY_DB_PER_TENANT.md` (diseno) + `apps/tenancy/AGENTS.md`; el roadmap se archivo en `docs/historico/` | Royal Plast (2026-06-20) y SK (2026-06-23) en prod, sincronizando. Media de RP subida (2026-08-23). BUG-F (login caido ~5h por migracion fantasma) resuelto (2026-08-23), con guard `migrate_tenants` nuevo. |
| Terraform/Azure | platform/dev/staging/prod aplicados | `ROADMAP_DEPLOY_AZURE.md` | Deuda: un solo Flexible Server B1ms aloja todo, sin HA y backup 7 dias. |
| RBAC/permisos | CT-02 y consumidores C05 integrados/validados localmente; producción aún legacy | `RBAC_PERMISOS.md` + `docs/handoffs/cierre_prod/CONTRATOS.md` | PER-013 ya cubre anulación/reimpresión por sucursal; migración 0011 y retiro del bypass ADMIN requieren preflight verde por tenant. |
| Notificaciones portal | **V1 validada en staging; fase cerrada** | `docs/runbooks/NOTIFICACIONES_WEB_PUSH.md` | Preparar la evaluación staging → producción. La matriz y sus casos físicos diferidos están en `docs/handoffs/STAGING_NOTIFICACIONES_2026-09-07.md`; eventos nuevos, en `docs/runbooks/EXTENDER_NOTIFICACIONES.md`. |
| Modulos vendibles | Fundación completa; SUS-006, SUS-014 y SUS-007/CFG-007 integrados localmente | `ARQUITECTURA_MODULOS.md` | CxC/reportes on-demand ya tienen gate HTML/API; C05/CT-04 y CT-03 sync están en el candidato. SUS-016 continúa pendiente. |
| e-CF | Fase inicial/MSeller implementada | `docs/handoffs/HANDOFF_ECF.md` + `apps/facturacion_electronica/AGENTS.md`; el roadmap de la Fase Inicial se archivo en `docs/historico/` | Mantener MSeller operativo; nativa/certificacion DGII quedan fase futura. |
| Testing | CT-03/C04 p6 aceptados; A07 y backend C04 p5.2 validados localmente | `TESTING.md` | 398 backend focales, A07: 605 backend OK; C04 p5.2: 7 HTTP + 1 gate físico tenant + 58 CT-01/tenancy/API OK. Faltan consumidor C04, e-CF, Linux, preflights autorizados y artefacto en A09. |
| Auditorias de codigo | 191 hallazgos en 18 modulos | `ESTADO_AUDITORIAS.md` (estado) + `TODO_AUDITORIAS.md` (accionable) | C05 p6 registra 12 productores CT-01 en el candidato; CT-04/C04 operativo y deuda residual siguen abiertos. |
| KB para agentes | 21/21 apps mapeadas | `AGENTS.md` (raiz) + `apps/<app>/AGENTS.md` | Convencion cerrada el 2026-09-08. Al tocar una app, actualizar la linea `Ultima revision` de su mapa en el mismo commit. |
| Sync confiable | **Fases 0/1/2/4 desplegadas previamente; A04 durable integrado localmente** | `ROADMAP_SYNC_CONFIABLE.md` + handoff A04 | Compatibilidad HTTP real, dry-run de clientes y despliegue quedan para A09 con autorización. |
| Bugs/hallazgos | 13 bugs etiquetados (BUG-A..M) | `BUGS.md` | BUG-K tiene sonda/plan dirigido integrado localmente desde A04; no se ejecutó contra clientes y producción/historia siguen pendientes de A09. |
| Innovacion | Exploracion | `docs/exploracion/OPORTUNIDADES_INNOVACION.md` | Releer despues de estabilizar SaaS/dev cloud. |

## Cloud, portal y deploy

La fuente de verdad para deploy es `ROADMAP_DEPLOY_AZURE.md`.

Estado actual contrastado con el repo:

- `config/settings_cloud.py` existe y es el contrato cloud para Azure Container
  Apps.
- `Dockerfile`, `.dockerignore` y `requirements_cloud.txt` existen.
- `.github/workflows/backend-ci.yml` existe y cubre checks, build, push a ACR,
  deploy a Container Apps y smoke `/api/v1/health/`.
- `infra/azure/environments/dev` contiene Terraform para RG, ACR, Container
  Apps, Container App Job, Key Vault, observabilidad, identities/RBAC y remote
  state.
- `apps/notificaciones` y el portal React implementan bandeja, reglas RBAC,
  Web Push y un job programado por minuto. Un code review (2026-09-05) endurecio
  8 puntos antes de desplegar. El backend, portal, VAPID y tenant descartable
  `demo` estan en dev. El motor se activo con corte temporal solo para `demo` y
  el job `posfifo-dev-notifications` corre cada minuto con identidad propia y
  secretos desde Key Vault. El smoke manual confirmo bandeja y aceptacion Web
  Push para apertura/cierre/reapertura; un segundo cierre confirmo el circuito
  automatico en 98 segundos, sin errores ni reintentos. La matriz física se
  completó después en staging con las excepciones documentadas abajo.
- El 2026-09-07 se desplegaron en dev las correcciones BUG-I/J/L, el pipeline
  con rollback independiente y los iconos PWA. Terraform agrego el Action Group
  y la alerta Kusto del job; la prueba de Azure termino `Succeeded`.
- El 2026-09-08 se promovio el backend completo a staging: las 13 migraciones
  pendientes terminaron en control plane y `staging_royalplast`, y API/job
  quedaron en la imagen inmutable `bb37b2f`. Terraform aplico las 6 altas y el
  unico cambio esperados, sin destrucciones; el plan posterior quedo sin drift.
- El portal staging quedo publicado en
  `https://salmon-rock-01cc45c10.7.azurestaticapps.net` con el backend correcto,
  service worker, manifest e iconos PNG. El tenant realmente aislado para la
  matriz es `staging_demo` (sucursal `01`), con motor activado desde el corte y
  el POS local dedicado sincronizando cada 60 segundos.
- El 2026-09-09 se cerró la validación staging de notificaciones por decisión
  del responsable del producto. Pasaron físicamente iPhone PWA, Android y
  Windows; apertura, cierres, diferencia, movimientos, umbral inferior/borde
  y regla apagada. El cierre agregado final, usuario fuera de sucursal,
  suscripción caducada y la reverificación visual de BUG-I/J no se ejecutaron:
  quedan como riesgos aceptados y casos explícitos para la preparación a
  producción, no como pruebas aprobadas.
- `docs/runbooks/AZURE_DEV_RESOURCES.md` lista recursos reales de Azure dev.

Discrepancias resueltas o visibles:

- `ROADMAP_PORTAL.md` tenia el bloque deploy 5.F mas atrasado que la realidad.
  Debe delegar detalles operativos a `ROADMAP_DEPLOY_AZURE.md`.
- Azure Static Web Apps dev ya existe:
  `https://agreeable-moss-051bc0010.7.azurestaticapps.net`. El frontend tiene
  preparacion y runbook operativo
  (`docs/runbooks/FRONTEND_DEPLOY_AZURE_STATIC_WEB_APPS.md`). El deploy del
  2026-09-07 valido OIDC, `VITE_API_URL` y la publicacion de manifest, service
  worker e iconos PNG.
- Floci sigue siendo laboratorio opcional, no staging.

## RBAC y modulos

Fuentes vivas:

- `RBAC_PERMISOS.md`
- `RBAC_LOCAL_CUTOVER_PENDIENTE.md`
- `ARQUITECTURA_MODULOS.md`

Estado contrastado con el repo:

- `apps/permisos` existe y contiene catalogo, motor, seed, decoradores, admin,
  migrations y tests.
- `apps/suscripciones` existe y contiene registro de modulos, planes,
  resolutor, admin, commands, migrations y tests.
- La arquitectura separa permisos de seguridad (`apps/permisos`) de
  entitlements comerciales (`apps/suscripciones`).

Discrepancia clave:

- Hay infraestructura real, pero el cutover local y algunas fronteras de
  enforcement/gating siguen pendientes. No tratar RBAC/modulos como "cerrado"
  hasta validar POS local y contrato portal/backend.

## e-CF

Fuentes:

- `docs/historico/ROADMAP_ECF_FASE_INICIAL.md` como roadmap.
- `docs/handoffs/HANDOFF_ECF.md` como handoff profundo.
- `docs/historico/TESTING_ECF_2026-05-09.md` y `docs/historico/TESTING_ECF_AUTOMATIZADO_2026-05-18.md` como
  bitacoras historicas de validacion.

Estado contrastado con el repo:

- `apps/facturacion_electronica` existe con modelos, interfaz neutral,
  integracion MSeller, payload mapper, procesador, command y tests.
- `ConfiguracionNegocio` contiene seleccion de proveedor `mseller/nativo` y
  `modo_contingencia`.

Frontera actual:

- La fase operativa es MSeller/PSFE. La libreria nativa y certificacion DGII
  completa siguen como fase futura.

## Testing

Fuente viva: `TESTING.md`.

Estado (2026-09-11):

- **1.509 metodos de test en 128 archivos.** La unica app sin ningun archivo de
  test es `apps/sucursales` (su cobertura vive en las apps que la consumen).
  Con un solo archivo, y por lo tanto candidatas a reforzar:
  `auditoria`, `clientes`, `negocios`, `notificaciones` y `usuarios`.
- Corrida de integración C05 parte 2 en Windows 3.11/Django 5.2.17: **95/95
  focales en 23,794 s**. El discovery completo ejecutó 1.453 casos: no reportó
  fallos de comportamiento y terminó con tres errores de importación porque
  intentó cargar los tres módulos e-CF bajo `manage.py test`, combinación que
  `TESTING.md` prohíbe; hubo 3 skips. Separada con su runner correcto, la suite
  e-CF quedó **72/72 en 104,27 s**. La repetición Linux 3.12 vive en el handoff A01 y se
  repite sobre el RC en A09. El conteo estático de métodos no es directamente
  comparable con casos parametrizados y descubrimiento del runner.
- `apps/facturacion_electronica` corre con **pytest** (`pytest.ini` ->
  `testpaths`), no con `manage.py test`.
- Baseline: CPython 3.11.14 x64 en Windows y CPython 3.12.14 en cloud/CI. Los locks
  y comandos reproducibles están en `requirements/README.md`; no actualizar el
  conda compartido en sitio.
- `--parallel` da ruido falso en Windows: medir en serial.

Siguiente foco:

- Tests criticos antes de promover a prod: auth/API, sync, CxC, reportes cloud,
  RBAC/modulos y smoke contra backend dev.

## Roadmaps: estado y prioridad

Auditados uno por uno el 2026-09-08, contrastando sus checkboxes contra el
codigo. **Dos se archivaron** por no tener trabajo pendiente real, y los cuatro
vivos quedan en este orden.

| # | Roadmap | Que queda | Por que ese lugar |
| --- | --- | --- | --- |
| **P1** | `ROADMAP_SYNC_CONFIABLE.md` | 7 items, **todos de despliegue**: desplegar Fase 3 (cloud primero), correr el rig contra `royalplastdemo`, `verificar_sync` en RP y SK, y el despliegue conjunto Fase 1+2 en una sola visita por cliente | Es lo unico donde el codigo ya esta escrito y probado y lo que falta es **ejecutar**. Ademas BUG-K (ACK falso del sync) sigue fuera de produccion |
| **P2** | `ROADMAP_DEPLOY_AZURE.md` | La promocion a produccion: prod corre imagen de junio y exige `workflow_dispatch` + job de migraciones aparte | Es la puerta por la que pasa todo lo demas. **Necesita poda**: su Fase D2 sigue diciendo que Static Web Apps esta "bloqueado por Azure for Students" y hoy hay ASWA vivo en dev, staging y prod |
| **P3** | `ROADMAP_PORTAL.md` | B11b (escrituras locales de maestros hacia el cloud), B12 (`inventario_consolidado` multi-sucursal real), UI de asignacion usuario->rol/sucursal, smoke RBAC completo | **Es el mas desfasado**: de sus 26 pendientes, al menos 6 ya estan hechos (`token_blacklist`, endpoint de logout, rate limiting de login, `SECURE_HSTS_SECONDS`, indice `EventoSync(sucursal, estado)`) y D8/D11 quedaron superados por el deploy real de ASWA. Podarlo antes de planificar sobre el |
| **P4** | `ROADMAP_CLOUD.md` | Solo la Fase 6 sigue viva: segunda sucursal de prueba, `instalar.bat` con modo sucursal/nodo, heartbeat de sucursal y alerta por email si una lleva >1h sin sincronizar | El propio documento se declara superado como estado operativo desde 2026-06-09. Es vision de producto, no trabajo en curso. **Candidato a archivar** en cuanto la Fase 6 se extraiga a su propio roadmap |

Archivados el 2026-09-08 (con banner explicando por que, en `docs/historico/`):

- `ROADMAP_TENANCY_DBPERTENANT.md` -- Fases 1-5 cerradas; RP y SK en produccion
  desde junio y media de RP en Blob desde el 2026-08-23. El unico pendiente real
  era rotar `SECRET_KEY` (#9), que sigue trackeado en la fila "Deploy POS local".
- `ROADMAP_ECF_FASE_INICIAL.md` -- la Fase Inicial (MSeller/PSFE) esta
  implementada. Sus 6 checkboxes sin marcar no eran tareas: eran campos en
  blanco de una plantilla de decisiones para una Fase 2 que hoy no existe como
  proyecto.

**Deuda transversal de estos documentos:** ninguno mantiene sus checkboxes al
dia -- `ROADMAP_SYNC_CONFIABLE` y el de e-CF tienen 0 marcados pese a estar
implementados, porque su estado real vive en prosa en la cabecera. Al tocar un
roadmap, marcar los checkboxes de lo que ya se hizo en el mismo commit.

## Clasificacion de documentos

Inventario base verificado contra el arbol de `docs/` el 2026-09-08; ampliado el
2026-09-09 con el plan de cierre y los encargos de ambos agentes.

### Fuentes vivas en la raiz de `docs/` (17)

- `PROJECT_STATUS.md` -- este documento
- `PLAN_CIERRE_PROD.md` -- plan de cierre, reparto Codex/Claude y gates; encargos
  operativos por agente en `docs/planes/`
- `VISION_PRODUCTO_2026.md`
- `DEPLOY_POS_LOCAL.md`
- Los 4 roadmaps vivos, priorizados en [Roadmaps: estado y prioridad](#roadmaps-estado-y-prioridad):
  `ROADMAP_SYNC_CONFIABLE.md`, `ROADMAP_DEPLOY_AZURE.md`, `ROADMAP_PORTAL.md`,
  `ROADMAP_CLOUD.md`
- `RBAC_PERMISOS.md`
- `RBAC_LOCAL_CUTOVER_PENDIENTE.md`
- `ARQUITECTURA_MODULOS.md`
- `TESTING.md`
- `BUGS.md`
- `TENANCY_DB_PER_TENANT.md`
- `TENANCY_CLOUD_DESIGN.md`
- `ESTADO_AUDITORIAS.md` -- punto unico del estado de las auditorias
- `TODO_AUDITORIAS.md` -- checklist accionable de lo que queda abierto

### Runbooks operativos (20)

Instalacion y operacion de clientes:

- `docs/runbooks/INSTALACION_CLIENTE_NUEVO.md`
- `docs/runbooks/ACTUALIZACION_CLIENTE_EXISTENTE.md`
- `docs/runbooks/MIGRAR_IMAGENES_A_BLOB.md`
- `docs/runbooks/ROYAL_PLAST_IMPORT_DB_PER_TENANT.md`

Sync:

- `docs/runbooks/PRUEBAS_SYNC_LOCAL.md`
- `docs/runbooks/SYNC_EMULACION_SUCURSAL_PROD.md`

Cloud, deploy y notificaciones:

- `docs/runbooks/DOCKER_BACKEND_AZURE.md`
- `docs/runbooks/GITHUB_ACTIONS_BACKEND_AZURE.md`
- `docs/runbooks/FRONTEND_DEPLOY_AZURE_STATIC_WEB_APPS.md`
- `docs/runbooks/NOTIFICACIONES_WEB_PUSH.md`
- `docs/runbooks/EXTENDER_NOTIFICACIONES.md`
- `docs/runbooks/AZURE_DEV_RESOURCES.md`
- `docs/runbooks/AZURE_BLOB_MEDIA.md`
- `docs/runbooks/D0_SECRET_ROTATION.md`

Terraform:

- `docs/runbooks/TERRAFORM_PRIMER.md`
- `docs/runbooks/TERRAFORM_AZURE_D2_FOUNDATION.md`
- `docs/runbooks/TERRAFORM_AZURE_D2_CONTAINER_APPS.md`
- `docs/runbooks/TERRAFORM_AZURE_D3_KEY_VAULT.md`
- `docs/runbooks/TERRAFORM_AZURE_F3_PLATFORM_PROD.md`
- `docs/runbooks/TERRAFORM_AZURE_REMOTE_STATE.md`

### Handoffs y deuda (4)

- `docs/handoffs/D2_DEV_HANDOFF_DEBT.md`
- `docs/handoffs/D3_CICD_MVP_HANDOFF.md`
- `docs/handoffs/HANDOFF_ECF.md`
- `docs/handoffs/STAGING_NOTIFICACIONES_2026-09-07.md` -- promoción completa a
  staging, matriz física y cierre de fase con riesgos diferidos

### Historicos / bitacoras (6)

- `docs/historico/ROADMAP_TENANCY_DBPERTENANT.md` -- **archivado 2026-09-08**:
  Fases 1-5 cerradas, dos clientes en produccion desde junio
- `docs/historico/ROADMAP_ECF_FASE_INICIAL.md` -- **archivado 2026-09-08**: la
  Fase Inicial (MSeller/PSFE) esta implementada
- `docs/historico/TESTING_ECF_2026-05-09.md`
- `docs/historico/TESTING_ECF_AUTOMATIZADO_2026-05-18.md`
- `docs/historico/TERRAFORM_AZURE_D2_REGION_DESTROY_NOTES.md`
- `docs/historico/latency_results_azure_pg_20260419_1941.json`

### Exploracion (20 archivos)

- `docs/exploracion/OPORTUNIDADES_INNOVACION.md` -- unica exploracion viva.
- `docs/exploracion/AUDITORIA_CODIGO_APPS_*.md` (19): **snapshots historicos del
  2026-08-20, no estado actual.** Cada uno abre con su banner. No usarlos como
  KB: para eso estan los `apps/<app>/AGENTS.md`.

## Nota historica 2026-08-20 (parcialmente superada)

Estado verificado contra Azure y las BDs de produccion **en esa fecha**. Se
conserva por trazabilidad, pero tres puntos ya no son ciertos y estan marcados
abajo. Lo que manda hoy es el resumen ejecutivo y la seccion de cloud/deploy.

- **Royal Plast y SK Performance estan EN PRODUCCION cloud y sincronizando a
  diario** (tenants `royalplast` desde 2026-06-20 y `skperformance` desde
  2026-06-23). Las Fases 4 y 5 de `docs/historico/ROADMAP_TENANCY_DBPERTENANT.md` estan
  cerradas de hecho.
- Se detectaron 2 bugs de sync (BUG-A perdida silenciosa de eventos, BUG-B
  cursor de pull) documentados en `BUGS.md` y planificados en
  `ROADMAP_SYNC_CONFIABLE.md`.
- Prod NO se auto-deploya: requiere `workflow_dispatch` manual + job de
  migraciones aparte (`PROD_RUN_MIGRATIONS_ON_DEPLOY=false`).
- **Deuda de despliegue:** prod corre una imagen del 19 de junio. Sin desplegar
  hay 5 commits de junio + las Fases 0-4 de sync. Mientras tanto: BUG-A sigue
  perdiendo eventos, y **RD$240,435 de cuentas por cobrar de Royal Plast siguen
  invisibles** en el portal.
- **Portal de produccion vivo:** `red-bay-07331a710.7.azurestaticapps.net`,
  apuntando a la API de prod. ~~73 imagenes de productos de RP salen rotas~~ ->
  **SUPERADO:** la media de RP se subio a Blob el 2026-08-23.
- ~~Suite de tests: 407, todos verdes.~~ -> **SUPERADO:** 1.167 pruebas Django
  y 72 de facturación verdes en CI (2026-09-09).
- ~~Deuda de despliegue: prod corre una imagen del 19 de junio~~ -> **PARCIAL:**
  el 2026-09-08 se promovio el backend completo a **staging** (imagen `bb37b2f`,
  13 migraciones aplicadas). **Produccion sigue fuera de ese despliegue** y las
  migraciones de la tabla de `ESTADO_AUDITORIAS.md` siguen siendo obligatorias
  antes de tocarla.

## Proximo orden recomendado

Revisado 2026-09-09. Los cinco puntos anteriores (commit de docs/infra, merge a
`develop`, crear `infra/azure/environments/staging`, y la estrategia de frontend
dev) estan **hechos**: staging existe con su backend remoto, el frontend dev y
staging estan publicados en Azure Static Web Apps, y el backend completo se
promovio a staging el 2026-09-08.

1. **Consumir el baseline A01 sin mezclar entornos:** Claude/C01 usa su worktree,
   venv/BD/puertos propios y los locks publicados; las suites tenant siguen en
   serial hasta TEN-016. Seguir el [plan activo](PLAN_CIERRE_PROD.md).
2. **Cerrar bugs y deuda del alcance en develop:** Codex lleva nucleo, RBAC,
   auditoria, sync y maestros; Claude lleva Windows/dotenv, documentos,
   configuracion/modulos, operacion comercial y portal. PER-006/007 siguen
   pendientes hasta que haya evidencia de cierre, pero no son el unico criterio
   del nuevo gate. Cotizaciones vencidas no bloquean; sus bugs de codigo si.
3. **Validar un nuevo candidato completo en staging:** suites, migraciones sobre
   copias, contratos POS viejo/nuevo, restauracion, matriz fisica y observacion.
   No heredar automaticamente la aprobacion de una version anterior.
4. **Solicitar pase y operar cloud -> RP -> SK:** backend antes que portal,
   notificaciones graduales y aceptacion por tienda. Redis, WORM y separacion/HA
   de PostgreSQL quedan fuera de este release; la consistencia entre workers
   debe resolverse sin introducir Redis.
