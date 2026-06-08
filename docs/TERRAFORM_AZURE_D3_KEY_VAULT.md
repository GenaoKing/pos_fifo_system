# Terraform D3 - Key Vault y secretos

D2 dejo el backend cloud funcionando, pero todavia hay una deuda importante:
`terraform.tfvars` y Terraform state contienen secretos usados por la Container
App.

D3 empieza creando Key Vault como foundation de secretos. El objetivo no es
cambiar todo de golpe, sino movernos en pasos pequenos:

1. Crear Key Vault.
2. Confirmar que podemos cargar secrets sin guardarlos en Terraform.
3. Dar permiso de lectura a la Managed Identity de la API/job.
4. Cambiar Container Apps para referenciar Key Vault.
5. Quitar secretos reales de `terraform.tfvars`.

## Modelo mental

Si vienes de on-prem:

- Key Vault es la caja fuerte central.
- Container App secrets son el mecanismo de inyeccion al proceso.
- Managed Identity es la credencial sin password que usa Azure para leer la caja
  fuerte.
- Terraform state es sensible si contiene valores secretos.

En dev hoy existen secretos en Container Apps:

```text
django-secret-key
db-password
```

Eso es esperado, pero no es el destino final de produccion.

## Primer apply: crear Key Vault

En `infra/azure/environments/dev/terraform.tfvars`:

```hcl
enable_key_vault                             = true
key_vault_name                               = null
key_vault_location                           = null
key_vault_purge_protection_enabled           = false
grant_current_user_key_vault_secrets_officer = true
```

Notas:

- `key_vault_name = null` usa la convencion local, por ejemplo
  `posfifodevkv`.
- Si Azure dice que el nombre ya existe, define uno globalmente unico:

```hcl
key_vault_name = "posfifo<algo>devkv"
```

- En dev dejamos `purge_protection=false` para poder destruir el lab.
- En prod debe ser `true`.

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
terraform output key_vault
```

## Cargar secrets sin Terraform

No usar `azurerm_key_vault_secret` para estos secrets en esta fase, porque eso
meteria el valor en Terraform state.

Usar Azure CLI:

```powershell
az keyvault secret set `
  --vault-name posfifodevkv `
  --name django-secret-key `
  --value "<NUEVO-SECRETO-LARGO>"
```

```powershell
az keyvault secret set `
  --vault-name posfifodevkv `
  --name db-password `
  --value "<PASSWORD-POSTGRES>"
```

Validar que existen, sin mostrar valores:

```powershell
az keyvault secret list `
  --vault-name <key_vault_name> `
  --query "[].name" `
  --output table
```

## Por que no hacemos el cambio completo en el mismo apply

La API ya funciona. Cambiar simultaneamente:

- origen de secretos,
- permisos RBAC,
- revision de Container Apps,
- y posiblemente tags de imagen,

haria mas dificil diagnosticar si algo falla.

Por eso D3 se divide:

- D3A: crear Key Vault y cargar secrets.
- D3B: asignar `Key Vault Secrets User` a las identities de API/job.
- D3C: cambiar Container Apps a Key Vault references.
- D3D: limpiar `terraform.tfvars` y documentar rotacion.

## Deuda hasta completar D3

Mientras D3 no este completo:

- `terraform.tfvars` local sigue teniendo secretos reales.
- Terraform state local sigue siendo sensible.
- Container Apps tiene secretos internos creados por Terraform.
- No compartir state ni tfvars.
- No subir capturas donde se vean valores de secrets.

## Siguiente decision

Cuando Key Vault este creado y puedas cargar secrets, el siguiente cambio sera
dar permisos de lectura a las Managed Identities:

```text
posfifo-dev-api-id
posfifo-dev-migrate-id
```

Rol:

```text
Key Vault Secrets User
```

Luego Container Apps podra referenciar:

```text
https://<vault>.vault.azure.net/secrets/django-secret-key
https://<vault>.vault.azure.net/secrets/db-password
```

## D3B/D3C: conectar Container Apps a Key Vault

Una vez creados los secrets en Key Vault, activar:

```hcl
use_key_vault_secrets = true

django_secret_key_secret_name = "django-secret-key"
db_password_secret_name       = "db-password"
```

El modulo hace tres cosas:

- Asigna `Key Vault Secrets User` a `posfifo-dev-api-id`.
- Asigna `Key Vault Secrets User` a `posfifo-dev-migrate-id`.
- Cambia los secrets de Container Apps para que sean referencias a Key Vault.

Ejecutar:

```powershell
cd C:\Proyectos\pos_fifo_system\infra\azure\environments\dev

terraform fmt
terraform validate
terraform plan
terraform apply
```

Si falla por permisos inmediatamente despues de crear el rol, espera 1-3 minutos
y repite `terraform apply`. RBAC en Azure a veces tarda en propagarse.

Validar:

```powershell
curl -v --max-time 20 https://posfifo-dev-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io/api/v1/health/live/
curl -v --max-time 20 https://posfifo-dev-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io/api/v1/health/
```

Tambien puedes revisar en Azure Portal que los secrets de la Container App ahora
aparezcan como referencias a Key Vault.

## Limpiar valores directos en tfvars

Cuando la API responda con Key Vault:

```hcl
django_secret_key = null
db_password       = null
```

Mantener:

```hcl
use_key_vault_secrets = true
```

Aplicar otra vez:

```powershell
terraform plan
terraform apply
```

Esto evita que futuros planes dependan de secretos directos en `terraform.tfvars`.

Importante: si esos secretos estuvieron antes en Terraform state local, considera
el state como sensible aunque el recurso haya cambiado. Para prod/staging:

- migrar a remote state protegido,
- limitar acceso al storage de state,
- rotar secretos que hayan quedado expuestos en pruebas,
- no compartir `terraform.tfstate` ni backups locales.

## Si Container Apps no puede leer Key Vault

Sintomas posibles:

- revision no saludable,
- error de secret reference,
- logs del sistema indicando acceso denegado a Key Vault.

Revisar:

```powershell
az containerapp logs show `
  --resource-group posfifo-dev-rg `
  --name posfifo-dev-api `
  --type system `
  --follow
```

Confirmar roles:

```powershell
az role assignment list `
  --assignee <principal-id-de-la-identity> `
  --scope <key-vault-id> `
  --output table
```

La identity debe tener:

```text
Key Vault Secrets User
```
