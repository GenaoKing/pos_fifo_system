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
  --vault-name <key_vault_name> `
  --name django-secret-key `
  --value "<NUEVO-SECRETO-LARGO>"
```

```powershell
az keyvault secret set `
  --vault-name <key_vault_name> `
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
