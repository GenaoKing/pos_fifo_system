# GitHub Actions - Backend Django a Azure Container Apps

Este documento cubre la fase D3 del roadmap: validar el backend en PR y desplegar
dev desde GitHub Actions.

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

Job `deploy-dev`:

- Login a Azure con OIDC.
- Login a ACR.
- Build de Docker image.
- Tag con SHA de commit.
- Push a ACR.
- Actualiza imagen del job `migrate`.
- Opcionalmente ejecuta migraciones.
- Actualiza imagen de la API.
- Smoke test de `/api/v1/health/`.

## Secrets de GitHub

Crear estos como **Repository secrets**:

```text
AZURE_CLIENT_ID
AZURE_TENANT_ID
AZURE_SUBSCRIPTION_ID
```

Estos no son secretos de la app Django. Son datos para que GitHub haga login a
Azure con OIDC.

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

## Crear identidad OIDC en Azure

La forma recomendada es crear un App Registration o Managed Identity federada
para GitHub Actions. Debe confiar en tu repo y rama.

Permisos minimos sugeridos para dev:

- Sobre ACR `posfifodevacr`: `AcrPush`.
- Sobre Resource Group `posfifo-dev-rg`: `Container Apps Contributor`.

Si el workflow necesita leer otros recursos, ajustar con permisos mas finos. No
usar Owner salvo para pruebas muy cortas.

## Probar manualmente

Desde GitHub:

1. Ir a `Actions`.
2. Seleccionar `Backend CI/CD`.
3. `Run workflow`.
4. `deploy_dev = true`.
5. `run_migrations = false` para primer test.

Si el deploy pasa, probar de nuevo con:

```text
run_migrations = true
```

## Deploy automatico desde develop

El workflow corre `deploy-dev` en push a `develop`.

El tag de imagen sera:

```text
<commit-sha>
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

En dev, la frontera queda asi:

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
- Crear workflows separados para staging/prod con aprobacion.
- Agregar rollback documentado por revision/imagen anterior.
