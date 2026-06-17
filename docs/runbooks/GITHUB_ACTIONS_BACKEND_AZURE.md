# GitHub Actions - Backend Django a Azure Container Apps

Este documento cubre la fase D3/F3 del roadmap: validar el backend en PR y
desplegar dev, staging y prod desde GitHub Actions.

Workflow creado:

```text
.github/workflows/backend-ci.yml
```

## Modelo mental

Si vienes de operaciones on-prem:

- GitHub Actions es el runner de automatizacion.
- ACR es el repositorio de artefactos.
- La imagen Docker es el paquete versionado.
- Container App es el servicio.
- Container App Job es el comando operativo para migraciones.
- OIDC evita guardar passwords de Azure en GitHub.

## Que hace el workflow

Job `checks`:

- Checkout.
- Python 3.12.
- Instala `requirements_cloud.txt`.
- Ejecuta `python manage.py check --settings=config.settings_cloud`.
- Ejecuta `collectstatic --dry-run`.

Job `deploy-backend`:

- Login a Azure con OIDC.
- Imprime contexto Azure no sensible: subscription, resource group, Container
  Apps y jobs.
- Login a ACR.
- Build de Docker image.
- Tags con SHA de commit, `<ambiente>-<sha>` y tag estable del ambiente.
- Push a ACR.
- Actualiza imagen de la API.
- Smoke test de `/api/v1/health/`.
- Actualiza imagen del job `migrate` si existe.
- Opcionalmente ejecuta migraciones.

El deploy se decide por branch:

```text
develop -> dev
staging -> staging
main    -> prod
```

## Secrets de GitHub

Crear estos como **Repository secrets** para dev:

```text
AZURE_CLIENT_ID
AZURE_TENANT_ID
AZURE_SUBSCRIPTION_ID
```

Crear los equivalentes por ambiente para staging/prod:

```text
STAGING_AZURE_CLIENT_ID
STAGING_AZURE_TENANT_ID
STAGING_AZURE_SUBSCRIPTION_ID
PROD_AZURE_CLIENT_ID
PROD_AZURE_TENANT_ID
PROD_AZURE_SUBSCRIPTION_ID
```

Estos no son secretos de la app Django. Son datos para que GitHub haga login a
Azure con OIDC.

### Crear los secrets en la UI de GitHub

Ruta:

```text
GitHub repo -> Settings -> Secrets and variables -> Actions -> Secrets
```

Crear cada uno con `New repository secret`:

```text
AZURE_CLIENT_ID=<Application client ID de la identidad/app registration>
AZURE_TENANT_ID=<Directory tenant ID>
AZURE_SUBSCRIPTION_ID=<Subscription ID de Azure for Students>
```

No crear aqui:

```text
DJANGO_SECRET_KEY
DB_PASSWORD
connection strings
tokens e-CF/MSeller
```

Esos secretos ya viven en Azure Key Vault.

## Variables de GitHub

Crear estos como **Repository variables**:

```text
AZURE_RESOURCE_GROUP=posfifo-dev-rg
AZURE_ACR_NAME=posfifodevacr
AZURE_ACR_LOGIN_SERVER=posfifodevacr.azurecr.io
AZURE_CONTAINER_APP_NAME=posfifo-dev-api
AZURE_MIGRATE_JOB_NAME=posfifo-dev-migrate
AZURE_API_BASE_URL=https://posfifo-dev-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io
RUN_MIGRATIONS_ON_DEPLOY=false
```

`RUN_MIGRATIONS_ON_DEPLOY=false` mantiene las migraciones bajo control manual.
Si quieres que cada push a `develop` dispare el job de migraciones, cambiar a
`true`.

### Crear las variables en la UI de GitHub

Ruta:

```text
GitHub repo -> Settings -> Secrets and variables -> Actions -> Variables
```

Crear cada una con `New repository variable`:

```text
AZURE_RESOURCE_GROUP=posfifo-dev-rg
AZURE_ACR_NAME=posfifodevacr
AZURE_ACR_LOGIN_SERVER=posfifodevacr.azurecr.io
AZURE_CONTAINER_APP_NAME=posfifo-dev-api
AZURE_MIGRATE_JOB_NAME=posfifo-dev-migrate
AZURE_API_BASE_URL=https://posfifo-dev-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io
RUN_MIGRATIONS_ON_DEPLOY=false
```

Variables no son secretas. Son nombres de recursos y URLs operativas.

## Crear identidad OIDC en Azure

La forma recomendada es crear un App Registration o Managed Identity federada
para GitHub Actions. Debe confiar en tu repo y rama.

Permisos minimos sugeridos para dev:

- Sobre ACR `posfifodevacr`: `AcrPush`.
- Sobre Resource Group `posfifo-dev-rg`: `Contributor`.
- Sobre Resource Group `posfifo-dev-rg`: `Reader`.

Nota: `Container Apps Contributor` puede no cubrir todas las operaciones de
`Microsoft.App/jobs/write` necesarias para actualizar Container App Jobs desde
Azure CLI. Para dev usamos `Contributor` limitado al Resource Group, no a toda la
suscripcion.

Si el workflow necesita leer otros recursos, ajustar con permisos mas finos. No
usar Owner salvo para pruebas muy cortas.

## Branches: main, staging, develop y features

Flujo recomendado para este repo:

```text
features/* -> PR -> develop -> deploy dev
develop probado -> PR -> staging -> deploy staging
staging probado -> PR -> main -> deploy prod
```

Como `main` es la default branch, GitHub necesita que el archivo del workflow
exista tambien en `main` para que aparezca normalmente en la pestana Actions y
para poder usar `Run workflow`.

Para deploy real, los branches autorizados por OIDC son:

```text
develop
staging
main
```

Por eso el federated credential usa:

```text
repo:GenaoKing/pos_fifo_system:ref:refs/heads/develop
repo:GenaoKing/pos_fifo_system:ref:refs/heads/staging
repo:GenaoKing/pos_fifo_system:ref:refs/heads/main
```

No autorizar ramas `features/*` para deploy cloud salvo una prueba muy puntual.
Las ramas feature deberian validar por PR checks; el deploy ocurre al hacer
merge al branch de ambiente correspondiente.

## Si Azure for Students bloquea App registrations

Si al entrar a:

```text
Microsoft Entra ID -> App registrations
```

Azure muestra:

```text
You don't have access
Insufficient privileges to complete the operation
```

no significa que el backend cloud este mal. Significa que tu usuario no tiene
permiso en el tenant de Microsoft Entra para crear aplicaciones.

Esto puede pasar aunque seas owner/contributor de la suscripcion Azure for
Students. Son planos de permisos distintos:

- Azure Subscription/RG: recursos como ACR, Container Apps, Key Vault.
- Microsoft Entra ID: identidades, app registrations, service principals.

La alternativa recomendada para este repo es crear una **User Assigned Managed
Identity** con federated credential desde Terraform.

### Crear Managed Identity OIDC con Terraform

En `infra/azure/environments/dev/terraform.tfvars`:

```hcl
enable_github_actions_identity = true

github_repository_owner = "TU-USUARIO-U-ORG"
github_repository_name  = "TU-REPO"
github_deploy_branch    = "develop"
```

Ejemplo para este repo:

```hcl
github_repository_owner = "GenaoKing"
github_repository_name  = "pos_fifo_system"
github_deploy_branch    = "develop"
```

No usar la URL completa:

```hcl
# Incorrecto
github_repository_name = "https://github.com/GenaoKing/pos_fifo_system"
```

El `subject` esperado debe verse asi:

```text
repo:GenaoKing/pos_fifo_system:ref:refs/heads/develop
```

Si accidentalmente creaste el credential con owner/repo incorrecto, el plan debe
mostrar un update in-place parecido a:

```text
subject = "repo:valor-viejo:ref:refs/heads/develop" -> "repo:GenaoKing/pos_fifo_system:ref:refs/heads/develop"
Plan: 0 to add, 1 to change, 0 to destroy.
```

Eso es seguro de aplicar.

Si Terraform dice que el recurso ya existe pero no esta en state, importar:

```powershell
terraform import `
  'azurerm_federated_identity_credential.github_actions_develop[0]' `
  '/subscriptions/e88372f6-b224-4d73-bf17-c61f32559c45/resourceGroups/posfifo-dev-rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/posfifo-dev-github-actions-id/federatedIdentityCredentials/github-develop'
```

Despues repetir:

```powershell
terraform plan
terraform apply
```

Opcionalmente puedes fijar nombre:

```hcl
github_actions_identity_name = "posfifo-dev-github-actions-id"
```

Ejecutar:

```powershell
cd C:\Proyectos\pos_fifo_system\infra\azure\environments\dev

terraform init
terraform fmt
terraform validate
terraform plan
terraform apply
```

Ver output:

```powershell
terraform output github_actions_identity
```

El output devuelve:

```text
client_id
principal_id
subject
```

Usa `client_id` como:

```text
AZURE_CLIENT_ID
```

Los otros secrets siguen igual:

```text
AZURE_TENANT_ID
AZURE_SUBSCRIPTION_ID
```

Terraform tambien asigna:

- `AcrPush` sobre `posfifodevacr`.
- `Container Apps Contributor` sobre `posfifo-dev-rg`.
- `Contributor` sobre `posfifo-dev-rg`.
- `Reader` sobre `posfifo-dev-rg`.

Este camino evita crear App Registration manualmente.

### Crear App Registration por Azure Portal

Usa esta ruta solo si tienes acceso a Microsoft Entra ID/App registrations.

1. Ir a:

```text
Azure Portal -> Microsoft Entra ID -> App registrations -> New registration
```

2. Nombre sugerido:

```text
posfifo-github-actions-dev
```

3. Supported account types:

```text
Accounts in this organizational directory only
```

4. Crear y copiar:

```text
Application (client) ID -> AZURE_CLIENT_ID
Directory (tenant) ID   -> AZURE_TENANT_ID
```

5. Copiar tambien el Subscription ID:

```text
Azure Portal -> Subscriptions -> Azure for Students -> Subscription ID
```

Ese valor va en:

```text
AZURE_SUBSCRIPTION_ID
```

### Agregar federated credential para GitHub

En el App Registration:

```text
Certificates & secrets -> Federated credentials -> Add credential
```

Usar:

```text
Federated credential scenario: GitHub Actions deploying Azure resources
Organization: <tu usuario u organizacion GitHub>
Repository: <nombre del repo>
Entity type: Branch
Branch: develop
Name: github-develop
```

Esto permite que el workflow en la rama `develop` haga login sin password.

### Asignar roles en Azure

Dar permisos minimos a la App Registration.

En ACR:

```text
Azure Portal -> posfifodevacr -> Access control (IAM) -> Add role assignment
Role: AcrPush
Members: posfifo-github-actions-dev
```

En Resource Group:

```text
Azure Portal -> posfifo-dev-rg -> Access control (IAM) -> Add role assignment
Role: Container Apps Contributor
Members: posfifo-github-actions-dev
```

Si Azure tarda en reconocer los permisos, esperar 1-3 minutos y repetir el
workflow.

## Probar manualmente

Desde GitHub:

1. Ir a `Actions`.
2. Seleccionar `Backend CI/CD`.
3. `Run workflow`.
4. Seleccionar el branch del ambiente: `develop`, `staging` o `main`.
5. `target_environment = dev`, `staging` o `prod`.
6. `deploy_backend = true`.
7. `run_migrations = false` para primer test.

Si el deploy pasa, probar de nuevo con:

```text
run_migrations = true
```

Si `Backend CI/CD` no aparece en Actions, primero hay que subir al repo el
archivo:

```text
.github/workflows/backend-ci.yml
```

GitHub no muestra workflows que solo existen localmente.

## Deploy automatico por ambiente

El workflow corre `deploy-backend` en push al branch de ambiente:

```text
develop -> dev
staging -> staging
main    -> prod
```

Los tags de imagen seran:

```text
<commit-sha>
<ambiente>-<commit-sha>
<ambiente>
```

En este primer corte, el SHA queda en el tag de la imagen. La deuda de
`commit: unknown` en `/api/v1/health/` queda pendiente hasta que el deploy sea
controlado por Terraform remote state o hasta que definamos una forma estable de
inyectar version/commit sin generar drift.

Hoy no actualizamos por CLI estas variables:

```text
APP_VERSION=<commit-sha>
GIT_COMMIT_SHA=<commit-sha>
```

porque Terraform todavia gestiona la configuracion de la app y podria detectar
drift en un `terraform plan`.

## Frontera Terraform vs CI

En cada ambiente, la frontera queda asi:

- Terraform gestiona infraestructura, env vars, secrets, identities, RBAC y
  probes.
- GitHub Actions gestiona la imagen desplegada en API/job.

Por eso el modulo de Container Apps ignora cambios en:

```text
template.container.image
```

Esto evita que un `terraform apply` posterior haga rollback accidental a la
imagen definida en `terraform.tfvars`.

## Smoke test

El workflow valida:

```text
/api/v1/health/
```

Ese endpoint toca DB. Si falla:

- revisar revision de Container Apps,
- revisar logs de API,
- revisar Key Vault references,
- revisar PostgreSQL/firewall,
- revisar que migraciones hayan corrido si hubo cambios de schema.

## Primera corrida dev validada

Resultado observado:

- OIDC login correcto con subject:

```text
repo:GenaoKing/pos_fifo_system:ref:refs/heads/develop
```

- Contexto Azure correcto:

```text
Resource Group: posfifo-dev-rg
Container App: posfifo-dev-api
Container App Job: posfifo-dev-migrate
```

- Build Docker completado.
- Push a ACR completado.
- API actualizada con imagen taggeada por SHA.
- Smoke test respondio:

```json
{"status":"ok","db":"ok","environment":"dev"}
```

- Job `posfifo-dev-migrate` actualizado con la misma imagen.

Esto valida el circuito D3 dev:

```text
develop -> GitHub Actions -> Docker build -> ACR -> Container Apps -> health
```

## Warning Node.js 20 actions

GitHub puede mostrar:

```text
Node.js 20 actions are deprecated.
```

En esta fase es warning, no fallo. La corrida puede terminar bien aunque aparezca
la advertencia.

Deuda:

- Revisar cuando `actions/checkout`, `actions/setup-python` y `azure/login`
  publiquen versiones que corran sobre Node.js 24.
- Alternativamente probar `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true` en una rama
  antes de activarlo en `develop`.

## Error: migrate job does not exist

Si GitHub Actions falla con:

```text
ERROR: The containerapps job 'posfifo-dev-migrate' does not exist
```

pero localmente este comando si lo ve:

```powershell
az containerapp job list `
  --resource-group posfifo-dev-rg `
  --output table
```

entonces el runner de GitHub probablemente esta usando otro contexto:

- `AZURE_SUBSCRIPTION_ID` incorrecto.
- `AZURE_RESOURCE_GROUP` incorrecto.
- OIDC entrando a otro tenant/subscription.
- Falta rol `Reader` sobre `posfifo-dev-rg`.
- Falta rol `Contributor` sobre `posfifo-dev-rg` para actualizar jobs.
- El job fue borrado en Azure pero sigue en Terraform state local.

Valores esperados para dev:

```text
AZURE_SUBSCRIPTION_ID=e88372f6-b224-4d73-bf17-c61f32559c45
AZURE_RESOURCE_GROUP=posfifo-dev-rg
AZURE_MIGRATE_JOB_NAME=posfifo-dev-migrate
```

El workflow ahora imprime:

- `az account show`
- `az group show`
- `az containerapp list`
- `az containerapp job list`

Usa esa salida para comparar GitHub vs local.

Para no bloquear el deploy de API, el workflow actualiza primero la API y luego
actualiza el job de migraciones solo si existe. Si `run_migrations=true`, el job
si debe existir y el workflow fallara si no lo encuentra.

## Deuda pendiente antes de prod

- Agregar tests criticos reales al job `checks`:
  - auth/API,
  - sync,
  - CxC,
  - reportes cloud.
- Decidir si migraciones corren automaticas en dev o solo manuales.
- Agregar espera/verificacion formal del job de migraciones.
- Configurar environments de GitHub (`dev`, `staging`, `prod`) con approvals.
- Migrar Terraform state local a remote state protegido.
- Agregar rollback documentado por revision/imagen anterior.
