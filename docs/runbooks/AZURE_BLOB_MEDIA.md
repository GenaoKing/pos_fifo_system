# Azure Blob Media para imagenes publicas

Estado: MVP economico para cloud.

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
