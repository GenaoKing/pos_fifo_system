# Terraform Azure remote state

Objetivo: mover `infra/azure/environments/dev/terraform.tfstate` desde disco local
a Azure Storage antes de crear `staging`.

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

## Permisos para CI futuro

Si GitHub Actions luego va a ejecutar Terraform, la Managed Identity necesitara:

- `Reader` sobre la subscription/RG segun alcance.
- `Contributor` o rol custom sobre los recursos que gestione.
- `Storage Blob Data Contributor` sobre el Storage Account de state.

Por ahora el workflow CI/CD no corre Terraform. Solo build/push/deploy de imagen.

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
