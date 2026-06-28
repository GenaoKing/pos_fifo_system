# Azure Blob Media para imagenes publicas

Estado: MVP economico para cloud. Prod aplicado y smoke directo validado
2026-06-20.

Este runbook cubre imagenes no sensibles: productos y logos. No usar este
container para reportes, cierres, XML/e-CF ni documentos privados.

## Modelo mental

- El POS/local sigue usando `media/` en disco.
- Azure Container Apps no debe depender de disco local persistente.
- Azure Blob Storage guarda archivos de media publica con URLs estables.
- Django cloud usa Managed Identity, no account keys.

## Recursos creados por Terraform

Cuando `enable_media_storage=true`, el ambiente crea:

- Storage Account `StorageV2`
- `Standard_LRS`
- access tier `Hot`
- shared access keys deshabilitadas (`shared_access_key_enabled=false`)
- container publico `media-public`
- rol `Storage Blob Data Contributor` para:
  - Managed Identity de la API
  - Managed Identity del migrate job, si existe
  - usuario/principal actual, para migracion manual con Azure CLI

No se crean CDN, private endpoints, file shares, queues ni redundancia
geo-replicada.

## Autenticacion de Terraform contra Storage (keys deshabilitadas)

**Decision de arquitectura:** los Storage Accounts se crean con
`shared_access_key_enabled = false` (solo Managed Identity / Azure AD, sin account
keys). Esto endurece la cuenta pero rompe el comportamiento por defecto del provider
azurerm: al crear la cuenta hace un poll del Blob Service usando la account key y
falla con `403 KeyBasedAuthenticationNotPermitted`.

**Solucion (estandar del proyecto):** el provider azurerm usa Azure AD para el
data-plane de Storage. Ya esta en `environments/dev/provider.tf`,
`environments/staging/provider.tf` y `environments/prod/provider.tf`:

```hcl
provider "azurerm" {
  # ...
  storage_use_azuread = true
}
```

**Prerrequisito de quien corre `terraform apply`:** como el data-plane ahora se
autentica por AAD, el principal que ejecuta el apply (tu usuario con `az login`, o
el service principal de CI) necesita un rol de datos de Storage en el scope del RG
(o superior) ANTES del apply. El role assignment `current_user_blob_contributor` del
modulo se crea DESPUES de la cuenta, asi que no cubre el poll de creacion; hay que
otorgarlo una vez, fuera de banda:

```powershell
az role assignment create `
  --assignee (az ad signed-in-user show --query id -o tsv) `
  --role "Storage Blob Data Contributor" `
  --scope /subscriptions/<sub-id>/resourceGroups/posfifo-dev-rg
# esperar ~5 min a que propague el RBAC, luego: terraform apply
```

Alternativa descartada (documentada por completitud): poner
`shared_access_key_enabled = true` desbloquea el apply sin AAD pero deja keys
activas; se prefirio MI-only.

## Activar en dev

En `infra/azure/environments/dev/terraform.tfvars`:

```hcl
enable_media_storage                      = true
media_storage_account_name                = null
media_storage_container_name              = "media-public"
grant_current_user_media_blob_contributor = true
```

`media_storage_account_name = null` usa la convencion:

```text
posfifodevmedia
```

Luego:

```powershell
cd C:\Proyectos\pos_fifo_system\infra\azure\environments\dev
terraform init
terraform fmt
terraform validate
terraform plan
terraform apply
```

Ver outputs:

```powershell
terraform output media_storage
```

## Activar en prod

Prod usa la misma arquitectura economica de dev, con un Storage Account por
ambiente y un solo container publico para imagenes/logos no sensibles.

En `infra/azure/environments/prod/terraform.tfvars`:

```hcl
enable_media_storage                      = true
media_storage_account_name                = null
media_storage_container_name              = "media-public"
grant_current_user_media_blob_contributor = true
```

`media_storage_account_name = null` usa la convencion:

```text
posfifoprodmedia
```

Antes del primer apply, confirmar que el usuario que ejecuta Terraform tiene rol
de datos sobre el Resource Group de prod:

```powershell
az role assignment create `
  --assignee (az ad signed-in-user show --query id -o tsv) `
  --role "Storage Blob Data Contributor" `
  --scope /subscriptions/e88372f6-b224-4d73-bf17-c61f32559c45/resourceGroups/posfifo-prod-rg
```

Si el rol se acaba de crear, esperar unos minutos por propagacion de RBAC.

Luego:

```powershell
cd C:\Proyectos\pos_fifo_system\infra\azure\environments\prod
terraform validate
terraform plan
terraform apply
terraform output media_storage
```

El plan esperado crea/actualiza:

- Storage Account `posfifoprodmedia`.
- Container publico `media-public`.
- RBAC `Storage Blob Data Contributor` para API, migrate job y usuario actual.
- Nueva revision de la API/job con:
  - `AZURE_BLOB_MEDIA_ENABLED=true`
  - `AZURE_STORAGE_ACCOUNT_NAME=posfifoprodmedia`
  - `AZURE_STORAGE_MEDIA_CONTAINER=media-public`

Smoke basico:

```powershell
curl https://posfifo-prod-api.greenglacier-6158bae1.canadacentral.azurecontainerapps.io/api/v1/health/

az storage blob list `
  --auth-mode login `
  --account-name posfifoprodmedia `
  --container-name media-public `
  --prefix royalplast/ `
  --output table
```

Smoke prod validado 2026-06-20:

```text
Storage Account: posfifoprodmedia
Container: media-public
Blob: royalplast/productos/_smoke-logo-royal.jpeg
URL: https://posfifoprodmedia.blob.core.windows.net/media-public/royalplast/productos/_smoke-logo-royal.jpeg
Resultado: HTTP 200, Content-Type image/jpeg
```

## Rebuild/deploy de la API

Este cambio agrega dependencias cloud:

- `django-storages[azure]`
- `azure-identity`

Despues de aplicar Terraform, hay que publicar una imagen Docker nueva para que
Container Apps tenga esas librerias.

Flujo esperado:

```powershell
docker build -t pos-fifo-backend:dev .
az acr login --name posfifodevacr
docker tag pos-fifo-backend:dev posfifodevacr.azurecr.io/pos-fifo-backend:dev
docker push posfifodevacr.azurecr.io/pos-fifo-backend:dev
az containerapp update `
  --resource-group posfifo-dev-rg `
  --name posfifo-dev-api `
  --image posfifodevacr.azurecr.io/pos-fifo-backend:dev
```

Si GitHub Actions ya esta configurado para dev, hacer merge/push al branch de
deploy tambien puede construir y desplegar la imagen.

## Subir imagenes existentes

En modo DB-per-tenant, la BD guarda rutas relativas del `ImageField` con
prefijo por `tenant_key`, por ejemplo:

```text
demo/productos/mi-producto.jpg
demo/config/logo.png
```

El modo mono-tenant local conserva rutas legacy (`productos/...`, `config/...`)
mientras no haya tenant activo. Para tenants, usar el comando Django porque
sube/copia el archivo y actualiza la ruta en BD de forma idempotente:

Desde la raiz del backend:

```powershell
python manage.py migrar_media_tenant `
  --settings=config.settings_development `
  --tenant demo `
  --source-media-root .\media `
  --apply
```

Para Royal Plast en prod, ejecutar el mismo comando con `settings_cloud` despues
de restaurar/registrar el tenant:

```powershell
python manage.py migrar_media_tenant `
  --settings=config.settings_cloud `
  --tenant royalplast `
  --source-media-root <ruta-media-local-royal> `
  --dry-run
```

Si el dry-run cuadra:

```powershell
python manage.py migrar_media_tenant `
  --settings=config.settings_cloud `
  --tenant royalplast `
  --source-media-root <ruta-media-local-royal> `
  --apply
```

Para revisar antes de escribir:

```powershell
python manage.py migrar_media_tenant `
  --settings=config.settings_development `
  --tenant demo `
  --source-media-root .\media `
  --dry-run
```

## Smoke test

1. Confirmar health:

```powershell
curl https://posfifo-dev-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io/api/v1/health/
```

2. Subir o actualizar una imagen de producto desde el admin/API.

3. Confirmar que el blob existe:

```powershell
az storage blob list `
  --auth-mode login `
  --account-name posfifodevmedia `
  --container-name media-public `
  --prefix demo/productos/ `
  --output table
```

4. Confirmar que `/api/v1/maestros/productos/` devuelve `imagen_url` apuntando a:

```text
https://posfifodevmedia.blob.core.windows.net/media-public/demo/productos/...
```

5. Abrir el portal y confirmar que la imagen renderiza.

## Limpieza simple

Si una imagen se elimina desde Django, `ImageField.delete()` debe borrar el blob
actual cuando el storage cloud esta activo.

Para revisar blobs huerfanos manualmente:

```powershell
az storage blob list `
  --auth-mode login `
  --account-name posfifodevmedia `
  --container-name media-public `
  --prefix demo/productos/ `
  --output table
```

No borrar en lote sin comparar contra la BD.

## Deuda futura

- Media privada: reportes, cierres, PDFs, XML/e-CF y adjuntos sensibles deben ir
  en container privado.
- Descargas privadas: usar backend o URLs SAS temporales.
- CDN: agregar solo si el trafico de imagenes lo justifica.
- Lifecycle policies: agregar solo cuando haya volumen real de blobs.
