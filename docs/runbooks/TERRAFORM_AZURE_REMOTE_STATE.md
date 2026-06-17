# Terraform Azure remote state

Estado dev: completado.

Objetivo: mover `infra/azure/environments/dev/terraform.tfstate` desde disco
local a Azure Storage antes de crear `staging`.

## Estado actual dev

Remote state ya esta activo para `dev`:

```text
Resource Group: posfifo-tfstate-rg
Storage Account: posfifotfstatedev
Container: tfstate
Blob key dev: azure/dev.tfstate
Backend config: infra/azure/environments/dev/backend.tf
```

## State keys actuales

Todos los ambientes Terraform nuevos usan el mismo backend bootstrap:

```text
Resource Group: posfifo-tfstate-rg
Storage Account: posfifotfstatedev
Container: tfstate
```

Keys canonicas:

```text
dev:      azure/dev.tfstate
staging:  azure/staging.tfstate
platform: azure/platform.tfstate
prod:     azure/prod.tfstate
```

`platform` y `prod` no deben arrancar con state local. El Storage Account de
state sigue siendo bootstrap manual y no se administra desde esos states.

Validaciones ya ejecutadas:

```powershell
terraform init -migrate-state -force-copy
terraform validate
terraform plan -compact-warnings
```

Resultado esperado actual:

```text
No changes.
```

Terraform ya adquiere y libera lock remoto durante `plan/apply`.

## Por que hacerlo ahora

El backend dev ya funciona y CI/CD ya despliega. Si seguimos creando ambientes
con state local:

- otra maquina podria arrancar con state vacio,
- un CI podria no conocer recursos existentes,
- dos applies podrian pisarse,
- `terraform.tfstate` local sigue conteniendo historial sensible.

Azure Storage como backend resuelve:

- state remoto,
- locking por blob lease,
- acceso controlado por RBAC,
- separacion por ambiente.

## Modelo mental

Si vienes de on-prem:

- Storage Account es el file server del state.
- Blob Container es la carpeta de states.
- Blob `dev.tfstate` es el archivo de estado del ambiente dev.
- Locking evita que dos operadores editen la misma "configuracion real" al mismo
  tiempo.

## Naming recomendado

Para Azure for Students/dev:

```text
Resource Group: posfifo-tfstate-rg
Storage Account: posfifotfstatedev
Container: tfstate
Blob key dev: azure/dev.tfstate
```

Nota: el nombre del Storage Account debe ser globalmente unico, minusculas y sin
guiones. Si `posfifotfstatedev` ya existe, usa un sufijo corto:

```text
posfifotfstatesg
posfifotfstate2026
```

## Paso 1 - Crear storage de state

Desde PowerShell:

```powershell
az group create `
  --name posfifo-tfstate-rg `
  --location canadacentral
```

```powershell
az storage account create `
  --name posfifotfstatedev `
  --resource-group posfifo-tfstate-rg `
  --location canadacentral `
  --sku Standard_LRS `
  --kind StorageV2 `
  --min-tls-version TLS1_2 `
  --allow-blob-public-access false
```

Crear container:

```powershell
az storage container create `
  --name tfstate `
  --account-name posfifotfstatedev `
  --auth-mode login
```

Si `--auth-mode login` falla por RBAC, asigna a tu usuario:

```text
Storage Blob Data Contributor
```

Scope:

```text
Storage Account posfifotfstatedev
```

Luego espera 1-3 minutos y repite el comando del container.

## Paso 2 - Agregar backend config

En:

```text
infra/azure/environments/dev/backend.tf
```

usar:

```hcl
terraform {
  backend "azurerm" {
    resource_group_name  = "posfifo-tfstate-rg"
    storage_account_name = "posfifotfstatedev"
    container_name       = "tfstate"
    key                  = "azure/dev.tfstate"
    use_azuread_auth     = true
  }
}
```

Este repo incluye un ejemplo en:

```text
infra/azure/environments/dev/backend.tf.example
```

No se activa hasta copiarlo/renombrarlo a `backend.tf`.

## Paso 3 - Migrar state local

Desde:

```powershell
cd C:\Proyectos\pos_fifo_system\infra\azure\environments\dev
```

Primero backup local:

```powershell
Copy-Item terraform.tfstate terraform.tfstate.pre-remote-backend.backup
```

Luego:

```powershell
terraform init -migrate-state
```

Terraform debe preguntar si quieres copiar el state local al backend remoto.
Responder:

```text
yes
```

## Paso 4 - Validar

```powershell
terraform state list
terraform plan
```

Esperado:

```text
No changes.
```

O cambios menores conocidos y revisables, pero no recreacion masiva.

Validar en Azure Portal:

```text
posfifo-tfstate-rg
  -> posfifotfstatedev
  -> Containers
  -> tfstate
  -> azure/dev.tfstate
```

## Paso 5 - Despues de migrar

No borrar inmediatamente:

```text
terraform.tfstate.pre-remote-backend.backup
```

Guardarlo temporalmente como backup sensible.

No compartir:

```text
terraform.tfstate
terraform.tfstate.backup
*.backup
```

Cuando confirmemos que remote state esta estable, borrar backups locales o
guardarlos en un lugar seguro.

En este repo, estos archivos siguen ignorados por git:

```text
infra/azure/environments/dev/.terraform/
infra/azure/environments/dev/terraform.tfstate
infra/azure/environments/dev/terraform.tfstate.backup
infra/azure/environments/dev/terraform.tfstate.pre-remote-backend.backup
infra/azure/environments/dev/terraform.tfvars
```

Mantener el backup local solo mientras confirmamos estabilidad. Tratarlo como
sensible: puede contener IDs, nombres de recursos y referencias historicas.

## Permisos para CI futuro

Si GitHub Actions luego va a ejecutar Terraform, la Managed Identity necesitara:

- `Reader` sobre la subscription/RG segun alcance.
- `Contributor` o rol custom sobre los recursos que gestione.
- `Storage Blob Data Contributor` sobre el Storage Account de state.

Por ahora el workflow CI/CD no corre Terraform. Solo build/push/deploy de imagen.
Cuando CI ejecute Terraform, agregar `Storage Blob Data Contributor` sobre
`posfifotfstatedev`.

## Importante

No crear `staging` antes de remote state.

Primero:

```text
dev local state -> Azure Storage backend
```

Luego:

```text
infra/azure/environments/staging
```

Asi staging nace con la disciplina correcta.

## Staging con el mismo PostgreSQL

Para la escala actual no necesitamos otro PostgreSQL Flexible Server para
staging. El patron recomendado es:

- mismo server cubierto por la suscripcion/free tier,
- **otra base de datos** para staging, por ejemplo `pos_fifo_staging`,
- secrets distintos para Django y DB password,
- Container App staging con `api_min_replicas=0`.
- En Azure for Students, si aparece la cuota `MaxNumberOfRegionalEnvironmentsInSubExceeded`,
  reutilizar el Container Apps Environment dev existente y crear solo la
  Container App/job de staging cuando toque encenderlos.

Esto mantiene costos bajos y permite probar migraciones/smoke tests sin tocar
la base de dev ni una futura base de produccion.

Flujo inicial:

```powershell
cd C:\Proyectos\pos_fifo_system\infra\azure\environments\staging
copy terraform.tfvars.example terraform.tfvars
```

Editar `terraform.tfvars`:

```hcl
environment = "staging"
db_name     = "pos_fifo_staging"

enable_api_container_app = false
enable_migrate_job       = false
api_min_replicas         = 0
api_max_replicas         = 1

existing_container_apps_environment_id   = "/subscriptions/e88372f6-b224-4d73-bf17-c61f32559c45/resourceGroups/posfifo-dev-rg/providers/Microsoft.App/managedEnvironments/posfifo-dev-aca-env"
existing_container_apps_environment_name = "posfifo-dev-aca-env"
```

Primer apply crea foundation/ACR/observabilidad/Key Vault sin prender la API:

```powershell
terraform init
terraform plan
terraform apply
```

Luego crear/cargar secrets en Key Vault, publicar una imagen con tag
`staging`, activar `enable_api_container_app=true` y, cuando corresponda,
`enable_migrate_job=true`.

Nota de cuota Azure for Students:

- `staging` mantiene state, RG, ACR, Key Vault, DB y nombres logicos separados.
- El runtime fisico de Container Apps puede ser compartido con dev por limite de
  suscripcion.
- La consecuencia practica es que logs/plataforma de las Container Apps pueden
  quedar asociados al Environment dev; para produccion se debe crear un
  Environment propio en una suscripcion/cuota adecuada.

### Key Vault y rotacion en staging

El archivo `infra/azure/environments/staging/terraform.tfvars` esta preparado
para no guardar secretos:

```hcl
django_secret_key = null
db_password       = null
use_key_vault_secrets = true
```

Con `key_vault_name = null`, el nombre esperado del Key Vault de staging es:

```text
posfifostagingkv
```

Ese Key Vault **no existe antes del primer `terraform apply` de staging**. El
primer apply, con `enable_api_container_app=false` y `enable_migrate_job=false`,
crea foundation, ACR, observabilidad y Key Vault. Luego cargas secrets:

```powershell
az keyvault secret set `
  --vault-name posfifostagingkv `
  --name django-secret-key `
  --value "<secret-key-staging>"
```

```powershell
az keyvault secret set `
  --vault-name posfifostagingkv `
  --name db-password `
  --value "<password-db-staging>"
```

Recomendacion:

- `django-secret-key`: **siempre distinto** entre dev, staging y prod.
- `db-password`: puede ser el mismo si staging usa el mismo usuario PostgreSQL
  (`posadmin`), pero eso reduce aislamiento. Mejor practica: crear un usuario
  staging con password propio cuando el esfuerzo sea razonable.
- No copiar secretos de dev a staging por comodidad salvo que sea una prueba
  temporal y quede documentado para rotacion.
