# D3 CI/CD MVP handoff

Estado: MVP dev funcional.

## Lo que ya funciona

Circuito validado:

```text
develop -> GitHub Actions -> Docker build -> ACR -> Container Apps -> health OK
```

Evidencia de la corrida:

- GitHub Actions autentica contra Azure con OIDC.
- Subject correcto:

```text
repo:GenaoKing/pos_fifo_system:ref:refs/heads/develop
```

- ACR login OK.
- Docker build OK.
- Docker push OK.
- Container App `posfifo-dev-api` actualizada con tag SHA.
- `/api/v1/health/` responde `status=ok`, `db=ok`.
- Container App Job `posfifo-dev-migrate` actualizado con la misma imagen.

## Recursos dev involucrados

```text
Resource Group: posfifo-dev-rg
ACR: posfifodevacr.azurecr.io
Container App: posfifo-dev-api
Container App Job: posfifo-dev-migrate
Key Vault: posfifodevkv
```

## GitHub Actions

Workflow:

```text
.github/workflows/backend-ci.yml
```

Variables requeridas:

```text
AZURE_RESOURCE_GROUP=posfifo-dev-rg
AZURE_ACR_NAME=posfifodevacr
AZURE_ACR_LOGIN_SERVER=posfifodevacr.azurecr.io
AZURE_CONTAINER_APP_NAME=posfifo-dev-api
AZURE_MIGRATE_JOB_NAME=posfifo-dev-migrate
AZURE_API_BASE_URL=https://posfifo-dev-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io
RUN_MIGRATIONS_ON_DEPLOY=false
```

Secrets requeridos:

```text
AZURE_CLIENT_ID
AZURE_TENANT_ID
AZURE_SUBSCRIPTION_ID
```

## RBAC dev

Managed Identity:

```text
posfifo-dev-github-actions-id
```

Permisos actuales:

- `AcrPush` sobre `posfifodevacr`.
- `Reader` sobre `posfifo-dev-rg`.
- `Contributor` sobre `posfifo-dev-rg`.

Decision dev:

- `Contributor` en RG dev es aceptable para cerrar MVP.
- Para staging/prod, crear rol custom minimo.

## Deuda aceptada

- Tests criticos aun no estan completos:
  - auth/API,
  - sync,
  - CxC,
  - reportes cloud.
- Smoke test automatico solo valida `/api/v1/health/`.
- Migraciones no se ejecutan automaticamente salvo opt-in.
- `APP_VERSION`/`GIT_COMMIT_SHA` en health siguen gestionados por Terraform/env,
  no por el workflow.
- Warning GitHub Actions Node.js 20 no bloqueante.
- Terraform state historico local sigue sensible si quedan backups en disco, pero
  dev ya usa remote state en Azure Storage.

## Remote state

Dev ya fue migrado:

```text
posfifo-tfstate-rg
  -> posfifotfstatedev
  -> tfstate
  -> azure/dev.tfstate
```

Runbook: `docs/TERRAFORM_AZURE_REMOTE_STATE.md`.

## Proximo paso recomendado

Crear scaffold de `staging` usando backend remoto desde el primer commit, con
una key separada (`azure/staging.tfstate`) y sin copiar `terraform.tfvars` de dev
con secretos o endpoints reales.
