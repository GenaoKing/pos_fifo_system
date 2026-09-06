# Estado maestro del proyecto

Estado consolidado al 2026-06-09, con parches puntuales al 2026-09-05 (ver notas
inline). Este documento es la puerta de entrada para leer el proyecto sin
perderse entre roadmaps, runbooks y bitacoras historicas -- pero varias filas
de la tabla llevan semanas sin una revision completa; verificar contra el
roadmap especifico antes de accionar algo con fecha vieja.

## Como leer estos docs

- **Fuente viva**: documento que se debe actualizar cuando cambia el plan.
- **Runbook**: tutorial operativo para repetir una tarea.
- **Handoff**: fotografia de una fase, util para contexto pero no siempre es la
  fuente viva.
- **Historico**: bitacora o incidente conservado por trazabilidad.

Regla de organizacion: la raiz de `docs/` queda reservada para fuentes vivas
con decisiones pendientes o lectura operativa diaria. Tutoriales, handoffs,
bitacoras y exploraciones viven en subcarpetas.

## Resumen ejecutivo

| Area | Estado | Fuente viva | Siguiente accion |
| --- | --- | --- | --- |
| Vision/producto | En progreso | `VISION_PRODUCTO_2026.md` | Elegir la proxima apuesta de producto luego de cerrar deploy dev/staging. |
| POS local | En produccion en 2 clientes | `ROADMAP_CLOUD.md` | Desplegar Fases 1-4 de sync (visita RP sabado, SK semana siguiente). |
| Deploy POS local | Update in-place + `.env` (Fase 4) | `docs/runbooks/INSTALACION_CLIENTE_NUEVO.md` | Desplegar el paquete nuevo; rotar SECRET_KEY (#9) en la misma ventana. |
| Portal cloud | **En produccion** | `ROADMAP_PORTAL.md` | ASWA prod vivo (`red-bay-07331a710`) apuntando a la API de prod. Imagenes de RP subidas (2026-08-23); grilla de productos pinta miniatura, no el original (2026-08-24, falta desplegar a prod). |
| Deploy Azure backend | dev/staging/prod vivos | `ROADMAP_DEPLOY_AZURE.md` | Prod corre imagen de junio: promover `develop`->`main` + job de migraciones. |
| Tenancy cloud | **Fases 1-5 CERRADAS** | `ROADMAP_TENANCY_DBPERTENANT.md` | Royal Plast (2026-06-20) y SK (2026-06-23) en prod, sincronizando. Media de RP subida (2026-08-23). BUG-F (login caido ~5h por migracion fantasma) resuelto (2026-08-23), con guard `migrate_tenants` nuevo. |
| Terraform/Azure | platform/dev/staging/prod aplicados | `ROADMAP_DEPLOY_AZURE.md` | Deuda: un solo Flexible Server B1ms aloja todo, sin HA y backup 7 dias. |
| RBAC/permisos | En produccion | `RBAC_PERMISOS.md` | 2 roles y 3 asignaciones activas por tenant. Sin pendientes bloqueantes. |
| Notificaciones portal | **V1 activa en dev; piloto en curso** | `docs/runbooks/NOTIFICACIONES_WEB_PUSH.md` | Motor activo solo para `demo`, un dispositivo Win32 real y job por minuto habilitado (2026-09-06). Cuatro operaciones produjeron una fila de bandeja y una entrega `ENVIADA` cada una; el ultimo cierre recorrio POS→sync→job en 98 s. Falta confirmacion visual y ampliar el smoke a movimientos, otros dispositivos y reglas negativas. |
| Modulos vendibles | Fundacion completa | `ARQUITECTURA_MODULOS.md` | BUG-D corregido (2026-08-24): negocio sin aprovisionar falla abierto, ya no apaga la impresion en silencio. Sin pendientes. |
| e-CF | Fase inicial/MSeller implementada | `ROADMAP_ECF_FASE_INICIAL.md` + `docs/handoffs/HANDOFF_ECF.md` | Mantener MSeller operativo; nativa/certificacion DGII quedan fase futura. |
| Testing | Convenciones activas | `TESTING.md` | Subir cobertura critica cloud/RBAC/sync antes de staging. |
| Sync confiable | **Fases 0/1/2/4 desplegadas (2026-08-22); Fase 3 implementada (2026-08-24)** | `ROADMAP_SYNC_CONFIABLE.md` | Desplegar Fase 3 (conciliacion diaria): cloud primero. Visita a SK Performance pendiente. |
| Bugs/hallazgos | 11 bugs etiquetados (BUG-A..K) | `BUGS.md` | BUG-I/J siguen pendientes de UX. BUG-K (ACK falso del sync) esta corregido y desplegado en dev; no promovido a produccion. |
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
  automatico en 98 segundos, sin errores ni reintentos. Falta confirmar la
  presentacion visual y completar la matriz de dispositivos/movimientos/reglas
  del runbook.
- `docs/runbooks/AZURE_DEV_RESOURCES.md` lista recursos reales de Azure dev.

Discrepancias resueltas o visibles:

- `ROADMAP_PORTAL.md` tenia el bloque deploy 5.F mas atrasado que la realidad.
  Debe delegar detalles operativos a `ROADMAP_DEPLOY_AZURE.md`.
- Azure Static Web Apps dev ya existe:
  `https://agreeable-moss-051bc0010.7.azurestaticapps.net`. El frontend tiene
  preparacion y runbook operativo
  (`docs/runbooks/FRONTEND_DEPLOY_AZURE_STATIC_WEB_APPS.md`). Pendiente:
  redeployar con `VITE_API_URL` en el workflow ASWA y smoke de login publicado.
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

- `ROADMAP_ECF_FASE_INICIAL.md` como roadmap.
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

Estado:

- Hay tests activos en `apps/api`, `apps/permisos`, `apps/suscripciones`,
  `apps/sync`, `apps/cuentas_por_cobrar`, `apps/facturacion_electronica` y
  `apps/ventas`.
- Para este repo, el interprete probado historicamente es
  `C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe`.

Siguiente foco:

- Tests criticos antes de staging: auth/API, sync, CxC, reportes cloud,
  RBAC/modulos y smoke contra backend dev.

## Clasificacion de documentos

### Fuentes vivas en la raiz de `docs/`

- `PROJECT_STATUS.md`
- `VISION_PRODUCTO_2026.md`
- `ROADMAP_CLOUD.md`
- `DEPLOY_POS_LOCAL.md`
- `ROADMAP_PORTAL.md`
- `ROADMAP_DEPLOY_AZURE.md`
- `ROADMAP_ECF_FASE_INICIAL.md`
- `RBAC_PERMISOS.md`
- `RBAC_LOCAL_CUTOVER_PENDIENTE.md`
- `ARQUITECTURA_MODULOS.md`
- `TESTING.md`
- `BUGS.md`
- `TENANCY_DB_PER_TENANT.md`
- `ROADMAP_SYNC_CONFIABLE.md`

### Runbooks operativos

- `docs/runbooks/DOCKER_BACKEND_AZURE.md`
- `docs/runbooks/GITHUB_ACTIONS_BACKEND_AZURE.md`
- `docs/runbooks/TERRAFORM_PRIMER.md`
- `docs/runbooks/TERRAFORM_AZURE_D2_FOUNDATION.md`
- `docs/runbooks/TERRAFORM_AZURE_D2_CONTAINER_APPS.md`
- `docs/runbooks/TERRAFORM_AZURE_D3_KEY_VAULT.md`
- `docs/runbooks/TERRAFORM_AZURE_REMOTE_STATE.md`
- `docs/runbooks/AZURE_DEV_RESOURCES.md`
- `docs/runbooks/D0_SECRET_ROTATION.md`
- `docs/runbooks/PRUEBAS_SYNC_LOCAL.md`
- `docs/runbooks/INSTALACION_CLIENTE_NUEVO.md`
- `docs/runbooks/MIGRAR_IMAGENES_A_BLOB.md`

### Handoffs y deuda

- `docs/handoffs/D2_DEV_HANDOFF_DEBT.md`
- `docs/handoffs/D3_CICD_MVP_HANDOFF.md`
- `docs/handoffs/HANDOFF_ECF.md`

### Historicos / bitacoras

- `docs/historico/TESTING_ECF_2026-05-09.md`
- `docs/historico/TESTING_ECF_AUTOMATIZADO_2026-05-18.md`
- `docs/historico/TERRAFORM_AZURE_D2_REGION_DESTROY_NOTES.md`
- `docs/historico/latency_results_azure_pg_20260419_1941.json`

### Exploracion

- `docs/exploracion/OPORTUNIDADES_INNOVACION.md`

## Nota de actualizacion 2026-08-20

Estado **verificado contra Azure y las BDs de produccion**, no contra los docs.
Lo que sigue reemplaza lo que digan las secciones de abajo:

- **Royal Plast y SK Performance estan EN PRODUCCION cloud y sincronizando a
  diario** (tenants `royalplast` desde 2026-06-20 y `skperformance` desde
  2026-06-23). Las Fases 4 y 5 de `ROADMAP_TENANCY_DBPERTENANT.md` estan
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
  apuntando a la API de prod. Pero **73 imagenes de productos de RP salen rotas**:
  los archivos nunca se subieron a Blob (ver
  `docs/runbooks/MIGRAR_IMAGENES_A_BLOB.md`).
- Suite de tests: **407, todos verdes**.

## Proximo orden recomendado

1. Cerrar commit de docs/infra dev actual.
2. Merge a `develop` para validar GitHub Actions y deploy dev con imagen nueva.
3. Crear `infra/azure/environments/staging` con backend remoto
   `azure/staging.tfstate`.
4. Aumentar tests criticos cloud/RBAC antes de promover staging.
5. Decidir estrategia frontend dev si Azure Static Web Apps sigue bloqueado en
   Azure for Students.
