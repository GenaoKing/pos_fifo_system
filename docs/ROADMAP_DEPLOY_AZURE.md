# Roadmap Deploy Azure / CI-CD

Documento guia para convertir el portal cloud en un despliegue repetible, primero en **dev/staging** y luego en produccion.

> Estado maestro del proyecto: `docs/PROJECT_STATUS.md`.
> Inventario Azure dev actual: `docs/runbooks/AZURE_DEV_RESOURCES.md`.

## Decision base

**Elegimos Docker + Azure Container Apps para el backend Django.**

Razon:

- El artefacto de backend sera una imagen Docker versionada, igual para local, CI y Azure.
- Azure Container Apps encaja mejor con la vision futura: API HTTP, jobs de migracion, workers/eventos, escalado, revisiones y despliegues blue/green.
- Azure App Service Linux sin Docker queda como plan B para demos rapidas, no como arquitectura objetivo.
- Azure Static Web Apps sigue siendo el destino natural del portal React.
- Azure PostgreSQL Flexible Server sigue siendo la base de datos cloud.


Arquitectura objetivo v1:

```text
GitHub backend repo
  -> GitHub Actions
  -> Docker build
  -> Azure Container Registry
  -> Azure Container Apps: api
  -> Azure Container Apps Job: migrate / comandos operativos
  -> Azure PostgreSQL Flexible Server
  -> Log Analytics / Application Insights

GitHub frontend repo
  -> GitHub Actions
  -> Azure Static Web Apps
  -> consume API HTTPS
```

## Principios

- **Dev real antes que prod.** Primero un ambiente `dev` real en Azure, luego `staging`, luego produccion.
- **Migraciones explicitas.** No correr `manage.py migrate` automaticamente en cada arranque del contenedor. Usar pipeline o Container Apps Job.
- **Imagen inmutable.** Taggear imagenes con SHA de commit; evitar `latest` como referencia de despliegue.
- **Secretos fuera del repo.** Nada de claves, tokens, DB passwords ni valores reales en `.bat`, docs o settings versionados.
- **IaC primero, portal despues.** Crear recursos con Terraform antes de depender del click manual del portal Azure.
- **Rollback posible.** Mantener revisiones y smoke tests para volver a una imagen anterior.
- **POS local sigue operativo.** La nube agrega visibilidad y administracion; no debe romper el flujo local si esta offline.

## Fase D0 - Limpieza y readiness antes de cloud

Objetivo: evitar subir a Azure con deuda de secretos/config.

- [ ] Rotar cualquier secreto que haya estado versionado o pegado en scripts/docs.
- [x] Reemplazar secretos en scripts por variables de entorno o placeholders.
- [x] Crear `config/settings_cloud.py` o endurecer `config/settings_production.py` para Azure:
  - `DEBUG=False`
  - `SECRET_KEY` desde env/secret
  - `ALLOWED_HOSTS` desde env
  - `CSRF_TRUSTED_ORIGINS` desde env si aplica
  - `CORS_ALLOWED_ORIGINS` desde env
  - DB desde env
  - logging a stdout/stderr
- [x] Definir `APP_VERSION` o exponer SHA de commit en health/version.
- [x] Crear o endurecer `/api/v1/health/` con:
  - estado app
  - conexion DB
  - version/commit
  - ambiente
- [x] Confirmar que `manage.py check` corre con settings cloud.

Notas de implementacion D0:

- `settings_azure_pg.py` queda como settings de desarrollo contra Azure DB.
- `config/settings_cloud.py` es el contrato de deploy para Azure Container Apps.
- `requirements_cloud.txt` es el contrato de dependencias cloud hasta D1; Docker
  debe instalarlo o fusionarlo con `requirements.txt`.
- Rotacion pendiente: ver `docs/runbooks/D0_SECRET_ROTATION.md`.

DoD:

- No quedan secretos reales necesarios para deploy en archivos versionados.
- Settings cloud arranca localmente con variables simuladas.
- Health endpoint responde sin depender del frontend.

## Fase D1 - Dockerizar backend

Objetivo: tener una unidad de despliegue reproducible.

- [x] Crear `Dockerfile` multi-stage o slim:
  - base Python estable
  - instalar dependencias del proyecto
  - copiar codigo
  - `collectstatic`
  - comando de arranque con Gunicorn
- [x] Crear `.dockerignore`.
- [x] Definir entrypoint sin migraciones automaticas.
- [~] Validar build local:
  - [x] `docker build`
  - [ ] `docker run`
  - [ ] health endpoint
  - [ ] static files servidos por WhiteNoise
- [x] Documentar variables minimas requeridas.

Notas de implementacion D1:

- Tutorial Docker: `docs/runbooks/DOCKER_BACKEND_AZURE.md`.
- Variables ejemplo: `deploy/env_cloud.example`; copiar a `deploy/env_cloud.local`.
- `requirements_cloud.txt` es Linux/cloud-only y no instala dependencias Windows
  de impresoras.
- El contenedor no corre migraciones en startup; migraciones van por comando
  explicito o Container Apps Job.
- Build Docker local validado; el contexto quedo limpio de `.terraform/` y state
  local. Validacion `docker run` local contra DB queda como smoke manual cuando
  se necesite reproducir un bug fuera de Azure.

DoD:

- `docker run` levanta la API contra una DB configurada.
- El contenedor no requiere estado en disco local.
- Logs salen por stdout/stderr.

## Fase D2 - Terraform foundation

Objetivo: crear infraestructura con codigo.

> Modelo mental antes de escribir HCL: ver `docs/runbooks/TERRAFORM_PRIMER.md` (tres mundos
> config/state/real, providers, modulos, ciclo de comandos, gotchas).

Estructura recomendada:

```text
infra/
  azure/
    environments/
      dev/
      staging/
      prod/
    modules/
      container-apps/
      postgres/
      static-web-app/
      observability/
      key-vault/
  floci-lab/
```

Recursos dev minimos:

- [x] Resource Group.
- [x] Azure Container Registry.
- [x] Container Apps Environment.
- [x] Container App `api`.
- [x] Container App Job `migrate`.
- [x] Azure PostgreSQL Flexible Server o referencia al existente.
- [x] Log Analytics Workspace.
- [x] Application Insights.
- [x] Key Vault o secretos de Container Apps para el primer corte.
- [ ] Static Web App para frontend dev. (bloqueado por policy/regiones de Azure for Students; modulo HCL existe y queda deshabilitado en dev)

State:

- [x] Local state solo para aprendizaje inicial.
- [x] Remote state en Azure Storage antes de trabajo compartido o prod.
- [x] Separar state por ambiente: `dev`, `staging`, `prod`. (`dev` usa `azure/dev.tfstate`; `staging/prod` deben nacer con keys propias)

DoD:

- `terraform plan` es revisable.
- `terraform apply` crea ambiente dev desde cero.
- Outputs incluyen URLs, nombres de recursos y registry.

Notas de avance D2:

- Foundation aplicada en Azure for Students con `static-web-app` deshabilitado
  por policy regional.
- Agregado scaffold incremental para ACR + Container Apps Environment + API/job
  opcionales. Tutorial: `docs/runbooks/TERRAFORM_AZURE_D2_CONTAINER_APPS.md`.
- ACR, Container Apps Environment, Container App `api` y Container App Job
  `migrate` creados en dev usando imagen
  `posfifodevacr.azurecr.io/pos-fifo-backend:dev`.
- Health cloud validado: `/api/v1/health/` responde `200 OK` con `status=ok`
  y `db=ok` desde Azure Container Apps.
- Remote state dev migrado a Azure Storage con lock:
  `posfifo-tfstate-rg/posfifotfstatedev/tfstate/azure/dev.tfstate`.
- Dev API ajustada a scale-to-zero para ahorrar credito:
  `api_min_replicas=0`, `api_max_replicas=1`. Tradeoff esperado: primer request
  tras inactividad puede tener cold start.
- Inventario actual de recursos dev: `docs/runbooks/AZURE_DEV_RESOURCES.md`.
- Deuda dev documentada en `docs/handoffs/D2_DEV_HANDOFF_DEBT.md`: secrets actuales en
  Container Apps/Terraform state, ASWA apagado, versionado manual y contrato
  de health/probes para Container Apps.

Notas de avance D3:

- Agregado modulo `key-vault` y wiring dev opcional con `enable_key_vault`.
- Tutorial D3 creado en `docs/runbooks/TERRAFORM_AZURE_D3_KEY_VAULT.md`.
- Decision D3A: crear Key Vault primero y cargar secrets con Azure CLI para no
  guardar valores en Terraform state.
- Agregado `use_key_vault_secrets` para que Container Apps use referencias a
  Key Vault con Managed Identity + `Key Vault Secrets User`.
- Deuda D3 pendiente: limpiar valores directos de `terraform.tfvars`, tratar el
  state local historico como sensible y rotar secretos antes de staging/prod si
  fueron expuestos.

Notas de implementacion D2:

- Primer tutorial: `docs/runbooks/TERRAFORM_AZURE_D2_FOUNDATION.md`.
- Primer root module: `infra/azure/environments/dev`.
- Modulos iniciales: `observability` y `static-web-app`.
- Region dev por defecto ajustada a `canadacentral` por policy de suscripcion.
- En esta maquina aun falta instalar Terraform CLI y Azure CLI para correr
  `init/validate/plan/apply`.

## Fase D2b - Floci/Terraform lab opcional

Objetivo: aprender y practicar sin costo ni riesgo.

- [ ] Crear `infra/floci-lab/`.
- [ ] Levantar `floci-az` con Docker Compose.
- [ ] Practicar Terraform con recursos soportados por el emulador:
  - Storage
  - Key Vault
  - App Configuration
  - Queue/Event Hubs si entran en pruebas futuras
- [ ] Mantener variables parecidas al Terraform real para transferir aprendizaje.

Reglas:

- Floci es **laboratorio**, no staging.
- No usar Floci para validar Container Apps, ACR, PostgreSQL Flexible Server, dominios, HTTPS ni RBAC real.
- Todo cambio de `infra/floci-lab/` debe poder borrarse sin impacto en el producto.

DoD:

- Se entiende el flujo `terraform init/plan/apply/destroy`.
- Se documentan diferencias entre emulador y Azure real.

## Fase D3 - CI backend

Objetivo: validar cada PR antes de construir/deployar.

Workflow PR:

- [x] Checkout.
- [x] Setup Python.
- [x] Instalar dependencias.
- [x] `python manage.py check`.
- [ ] Tests criticos:
  - API auth/maestros
  - sync
  - CxC
  - reportes cloud
  - e-CF/MSeller si aplica al branch
- [ ] Lint/formato si se introduce herramienta (`ruff` recomendado, pero no bloquear hasta configurarlo).

Workflow merge a `develop`:

- [x] Build Docker image.
- [x] Tag con SHA.
- [x] Push a ACR.
- [x] Deploy a Container Apps dev.
- [x] Ejecutar job de migracion manual/controlado.
- [x] Smoke test automatico:
  - [x] `/api/v1/health/`
  - login admin
  - `/api/v1/reportes/ventas-hoy/`
  - `/api/v1/sucursales/status/`

DoD:

- PR falla antes de deploy si rompe checks/tests.
- Merge a `develop` produce una revision dev comprobable.

Notas de avance D3 CI:

- Workflow creado en `.github/workflows/backend-ci.yml`.
- Tutorial creado en `docs/runbooks/GITHUB_ACTIONS_BACKEND_AZURE.md`.
- Deploy dev usa OIDC hacia Azure, build Docker, tag SHA, push a ACR,
  `az containerapp update` y smoke test `/api/v1/health/`.
- Migraciones quedan controladas por `workflow_dispatch.run_migrations` o por
  `RUN_MIGRATIONS_ON_DEPLOY=true`; no se fuerzan sin opt-in.
- Frontera dev: Terraform gestiona infra/config/secrets y GitHub Actions
  gestiona imagen desplegada; el modulo ignora drift de `container.image` para
  evitar rollback accidental.
- Alternativa Azure for Students documentada/implementada: crear User Assigned
  Managed Identity + federated credential con Terraform cuando Entra bloquea
  App registrations.
- RBAC dev para GitHub Actions: `AcrPush` en ACR y `Contributor`/`Reader` en
  `posfifo-dev-rg`; deuda futura: role custom minimo para staging/prod.
- CI/CD dev validado desde `develop`: GitHub Actions hizo build Docker, push a
  ACR, update de Container App, smoke test `/api/v1/health/` y update del job
  `posfifo-dev-migrate`.
- Warning pendiente no bloqueante: GitHub Actions avisa de deprecacion Node.js
  20 en actions externas.
- D3 CI/CD queda en MVP funcional para dev. Handoff:
  `docs/handoffs/D3_CICD_MVP_HANDOFF.md`.
- Bloqueo previo a staging resuelto: Terraform dev usa remote state en Azure
  Storage con lock. Runbook: `docs/runbooks/TERRAFORM_AZURE_REMOTE_STATE.md`.

## Fase D4 - CI frontend

Objetivo: desplegar portal React contra API dev.

- [ ] Workflow PR:
  - `npm ci`
  - lint
  - build
- [ ] Workflow merge a `develop`:
  - deploy a Azure Static Web Apps dev
  - `VITE_API_URL` apunta al backend dev
- [ ] Configurar `staticwebapp.config.json` para SPA routing.
- [ ] Validar CORS contra API dev.

**[Estado may 2026] Prep frontend ya hecho en `pos-cloud-dashboard`:**

- `staticwebapp.config.json` con SPA fallback + headers de seguridad (CSP `connect-src 'self' https:`, `X-Frame-Options: DENY`, `X-Content-Type-Options`, `Referrer-Policy`). En prod, endurecer CSP `connect-src` al host de API exacto.
- `.github/workflows/ci.yml`: `npm ci` + lint + `test:run` + build en PR/push (sin secretos). El deploy lo agrega ASWA con su token al crear el recurso (D8 infra).
- `.env.example` + `README.md` con setup/scripts/env/deploy.
- Resolución de backend centralizada en `src/lib/config.ts` (ver D8).
- **CORS:** el origen del portal debe coincidir EXACTO con `CORS_ALLOWED_ORIGINS` del backend (sin slash final, esquema https en cloud).

DoD:

- Portal dev abre por URL de Static Web Apps.
- Login y dashboard funcionan contra API dev.

## Fase D5 - Staging y promocion

Objetivo: separar "probado en dev" de "candidato a prod".

- [x] Scaffold `infra/azure/environments/staging` con backend remoto
  `azure/staging.tfstate`.
- [x] `infra/azure/environments/staging/terraform.tfvars` local creado sin
  secretos inline, con Key Vault, API/job apagados y DB `pos_fifo_staging`.
- [x] Crear foundation `staging` con Terraform:
  - Resource Group `posfifo-staging-rg`
  - ACR `posfifostagingacr`
  - Log Analytics `posfifo-staging-law`
  - Application Insights `posfifo-staging-appi`
  - Key Vault `posfifostagingkv`
- [x] Resolver limite Azure for Students: staging reutiliza el Container Apps
  Environment dev `posfifo-dev-aca-env` porque la suscripcion no permite mas de
  un Container Apps Environment en `canadacentral`.
- [ ] Crear DB `pos_fifo_staging` en la misma instancia Azure PostgreSQL dev/free
  o definir el nombre final en `terraform.tfvars`.
- [x] Primer `terraform plan/apply` de staging con API/job apagados para crear
  foundation, ACR, observabilidad y Key Vault.
- [ ] Cargar secrets staging (`django-secret-key`, `db-password`) en el Key
  Vault de staging antes de encender API/job con `use_key_vault_secrets=true`.
  - Nombre esperado si `key_vault_name=null`: `posfifostagingkv`.
  - `django-secret-key` debe ser distinto por ambiente.
  - `db-password` puede ser el mismo solo si staging usa el mismo usuario
    PostgreSQL; preferible usuario/password separado cuando sea practico.
- [ ] Mantener `api_min_replicas=0`, `api_max_replicas=1` mientras staging sea
  apagable/on-demand.
- [ ] Publicar imagen Docker `staging` en ACR staging o decidir reutilizar ACR
  dev para el primer smoke.
- [ ] Deploy desde rama `main` o tags release.
- [ ] Ejecutar migraciones de staging via Container Apps Job.
- [ ] Smoke E2E:
  - auth
  - dashboard
  - maestros
  - CxC
  - reportes
  - sync desde una sucursal de prueba
- [ ] Habilitar revisiones en Container Apps para rollback/blue-green.

DoD:

- Staging representa la forma final de produccion.
- Una release puede validarse sin tocar prod.

## Fase D6 - Produccion controlada

Objetivo: primer deploy real sin acoplarlo a todos los clientes.

- [ ] Crear ambiente `prod` con Terraform.
- [ ] Configurar dominios:
  - `api.<dominio>`
  - `portal.<dominio>`
- [ ] Configurar HTTPS.
- [ ] Configurar secretos prod.
- [ ] Ejecutar migraciones prod via job con aprobacion manual.
- [ ] Smoke manual post-deploy.
- [ ] Documentar rollback:
  - revision anterior
  - imagen anterior
  - backups DB

DoD:

- Produccion responde con dominios reales.
- Portal y API operan con HTTPS.
- Hay forma clara de volver atras.

## Fase D7 - Observabilidad y operaciones

Objetivo: saber que esta vivo antes de que el usuario avise.

- [ ] Logs estructurados JSON o formato estable.
- [ ] Application Insights para requests, errores y latencia.
- [ ] Alertas:
  - API 5xx
  - health DB falla
  - sucursal sin sync > umbral
  - job de migracion falla
- [ ] Dashboard operativo:
  - version desplegada
  - ultimo deploy
  - estado sync
  - eventos sync en error
- [ ] Backups y restore drill para Azure PostgreSQL.

DoD:

- Se puede diagnosticar si el problema es frontend, API, DB o sync.
- Hay alertas antes de que el owner lo note.

## Fase D8 - Multi-tenant / futuro SaaS

Objetivo: preparar el salto de "portal por cliente" a plataforma.

- [ ] Decidir estrategia tenant:
  - deploy por cliente al inicio
  - `django-tenants` cuando haya 2+ clientes pagos o necesidad real
- [ ] Separar variables por cliente/ambiente.
- [ ] Definir naming convention:
  - `posfifo-dev-*`
  - `posfifo-stg-*`
  - `posfifo-prod-*`
- [ ] Definir estrategia de dominios por cliente.
- [ ] Definir migraciones por tenant.

**Frontend multi-tenant — recomendación (seam ya listo):**

- El portal hornea `VITE_API_URL` en build (single-tenant). Para "agregar un segundo cliente sin rediseñar deploy", el frontend ya expone un seam de **runtime config** en `src/lib/config.ts`: prioriza `window.__APP_CONFIG__.apiUrl` sobre la env de build.
- Al activar multi-tenant: servir un `config.js`/`config.json` por host (ASWA por ambiente, o un `index.html` que inyecte `window.__APP_CONFIG__` según el dominio del tenant) → **un solo bundle inmutable** sirve a `portal.clienteA.com → api.clienteA.com` y `portal.clienteB.com → api.clienteB.com` sin rebuild.
- Implicación de seguridad: la CSP `connect-src` debe incluir el/los host(s) de API del tenant; con runtime config conviene generar la CSP por ambiente/tenant en vez de hornearla.
- Contrato ya tenant-aware: `User.tenant_id` viaja en el login del portal; el filtro `sucursal` de reportes/CxC viaja por `{ params }` (base del aislamiento). No reintroducir supuestos single-tenant en endpoints nuevos.

DoD:

- Agregar un segundo cliente no requiere redisenar deploy desde cero.

## Orden recomendado inmediato

1. D0 - Readiness y settings cloud.
2. D1 - Dockerfile backend.
3. D2 - Terraform dev minimo.
4. D3 - GitHub Actions backend a Container Apps dev.
5. D4 - Static Web Apps dev.
6. D5 - Staging.
7. D6 - Produccion.

## Referencias

- Azure Container Apps GitHub Actions: https://learn.microsoft.com/en-us/azure/container-apps/github-actions
- Azure Container Apps Jobs: https://learn.microsoft.com/en-us/azure/container-apps/jobs
- Azure Container Apps revisions/traffic: https://learn.microsoft.com/en-ca/azure/container-apps/revisions
- Azure Static Web Apps build configuration: https://learn.microsoft.com/en-us/azure/static-web-apps/build-configuration
