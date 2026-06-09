# Estado maestro del proyecto

Estado consolidado al 2026-06-09. Este documento es la puerta de entrada para
leer el proyecto sin perderse entre roadmaps, runbooks y bitacoras historicas.

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
| POS local | MVP funcional en produccion local | `ROADMAP_CLOUD.md` | Limpiar usos legacy de settings y hacer smoke operativo final. |
| Portal cloud | MVP funcional parcial | `ROADMAP_PORTAL.md` | Smoke E2E contra backend dev desplegado y decidir Static Web Apps bajo limitaciones Azure Students. |
| Deploy Azure backend | MVP dev funcional | `ROADMAP_DEPLOY_AZURE.md` | Crear `staging` con remote state propio y roles menos amplios. |
| Terraform/Azure | Dev funcional con state remoto | `ROADMAP_DEPLOY_AZURE.md` + `docs/runbooks/TERRAFORM_*` | Scaffold de `staging`; no crear prod hasta validar staging. |
| RBAC/permisos | En progreso avanzado | `RBAC_PERMISOS.md` | Completar cutover POS local y enforcement/gating pendiente. |
| Modulos vendibles | Fundacion implementada | `ARQUITECTURA_MODULOS.md` | Fases 2-4: enforcement, admin/React y hooks de datos bloqueantes. |
| e-CF | Fase inicial/MSeller implementada | `ROADMAP_ECF_FASE_INICIAL.md` + `docs/handoffs/HANDOFF_ECF.md` | Mantener MSeller operativo; nativa/certificacion DGII quedan fase futura. |
| Testing | Convenciones activas | `TESTING.md` | Subir cobertura critica cloud/RBAC/sync antes de staging. |
| Bugs/hallazgos | Registro liviano | `BUGS.md` | Convertir pendientes repetidos en issues o tareas de roadmap. |
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
- `docs/runbooks/AZURE_DEV_RESOURCES.md` lista recursos reales de Azure dev.

Discrepancias resueltas o visibles:

- `ROADMAP_PORTAL.md` tenia el bloque deploy 5.F mas atrasado que la realidad.
  Debe delegar detalles operativos a `ROADMAP_DEPLOY_AZURE.md`.
- Azure Static Web Apps esta bloqueado/no aplicado en dev por restricciones de
  Azure for Students/regiones. El frontend tiene preparacion, pero no recurso
  ASWA operativo dentro de este stack.
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
- `ROADMAP_PORTAL.md`
- `ROADMAP_DEPLOY_AZURE.md`
- `ROADMAP_ECF_FASE_INICIAL.md`
- `RBAC_PERMISOS.md`
- `RBAC_LOCAL_CUTOVER_PENDIENTE.md`
- `ARQUITECTURA_MODULOS.md`
- `TESTING.md`
- `BUGS.md`

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

## Proximo orden recomendado

1. Cerrar commit de docs/infra dev actual.
2. Merge a `develop` para validar GitHub Actions y deploy dev con imagen nueva.
3. Crear `infra/azure/environments/staging` con backend remoto
   `azure/staging.tfstate`.
4. Aumentar tests criticos cloud/RBAC antes de promover staging.
5. Decidir estrategia frontend dev si Azure Static Web Apps sigue bloqueado en
   Azure for Students.
