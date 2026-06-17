# Terraform Azure F3 - Platform y Prod

Estado: runbook de implementacion para Fase 3 DB-per-tenant.

## Modelo

`platform` administra recursos compartidos:

- PostgreSQL Flexible Server global.
- Output del ACR compartido temporal `posfifodevacr`.

`prod` administra runtime productivo:

- Resource Group prod.
- Observabilidad.
- Key Vault.
- Container App API.
- Container App Job de migraciones.
- Media storage si se activa despues del smoke de Fase 2.

`prod` no crea ACR propio en este MVP. Consume el registry desde el state de
`platform`.

## Remote State

Backend bootstrap existente:

```text
Resource Group: posfifo-tfstate-rg
Storage Account: posfifotfstatedev
Container: tfstate
```

Keys:

```text
platform: azure/platform.tfstate
prod:     azure/prod.tfstate
```

No crear ni commitear state local. `terraform.tfvars` sigue ignorado por git.

## Firewall del PostgreSQL platform

El servidor usa acceso publico controlado por firewall (sin VNET en el MVP). Las
reglas viven en `postgres_firewall_rules` (en `terraform.tfvars`, ignorado por
git; el `.example` documenta la forma):

- `allow-azure-services` (`0.0.0.0`): permite que las Container Apps de prod
  (egress Azure) lleguen a la BD. **Necesaria** mientras no haya VNET.
- IPs del operador: necesarias para correr `bootstrap_tenant`/`psql`
  **localmente** contra la BD; la regla de azure-services NO cubre tu laptop. Las
  reglas actuales se copiaron del `pos-fifo-pg` existente (rango ISP, IP de la
  ubicacion 152.166.130.95 y la subred Starlink `74.244.193.0/24`, que ya incluye
  los IPs sueltos `.4/.93/.231`). Si tu IP cambia (DHCP/Starlink), actualizar la
  regla y re-aplicar.

Nota de seguridad: `0.0.0.0` habilita el rango de **todo** Azure (no solo tu
suscripcion); la barrera real es la auth por password. Endurecer con VNET +
Private Endpoint cuando el cliente real lo amerite.

## Orden de apply

1. Preparar `platform`:

```powershell
cd C:\Proyectos\pos_fifo_system\infra\azure\environments\platform
copy terraform.tfvars.example terraform.tfvars
```

Editar:

- `subscription_id`
- nombres si Azure reporta colision global

No guardar el password del admin PostgreSQL en `terraform.tfvars`. Pasarlo por
variable de entorno antes del plan/apply:

```powershell
$env:TF_VAR_postgres_admin_password = "<POSTGRES_ADMIN_PASSWORD_PLATFORM>"
```

Validar:

```powershell
terraform init
terraform validate
terraform plan -out platform.tfplan
```

Aplicar solo cuando el plan cree el PostgreSQL esperado y lea el ACR existente:

```powershell
terraform apply platform.tfplan
```

2. Cargar secretos prod en Key Vault.

El primer `prod apply` puede crear solo foundation si `enable_api_container_app`
y `enable_migrate_job` estan en `false`. Luego cargar:

```powershell
az keyvault secret set `
  --vault-name posfifoprodkv `
  --name django-secret-key `
  --value "<DJANGO_SECRET_KEY_PROD>"
```

```powershell
az keyvault secret set `
  --vault-name posfifoprodkv `
  --name db-password `
  --value "<POSTGRES_ADMIN_PASSWORD_PLATFORM>"
```

```powershell
az keyvault secret set `
  --vault-name posfifoprodkv `
  --name db-user `
  --value "posadmin"
```

3. Preparar `prod`:

```powershell
cd C:\Proyectos\pos_fifo_system\infra\azure\environments\prod
copy terraform.tfvars.example terraform.tfvars
```

Editar:

- `subscription_id`
- `container_image_tag` con SHA o `prod-<shortsha>`
- `api_allowed_hosts` cuando haya dominio/FQDN definitivo
- `enable_api_container_app=true` despues de publicar imagen
- `enable_migrate_job=true` cuando el job de migraciones deba existir

Validar y aplicar:

```powershell
terraform init
terraform validate
terraform plan -out prod.tfplan
terraform apply prod.tfplan
```

## Smoke

Publicar imagen:

```powershell
az acr login --name posfifodevacr
docker push posfifodevacr.azurecr.io/pos-fifo-backend:<sha>
```

Verificar que prod usa el ACR compartido:

```powershell
terraform output container_registry
```

Ejecutar el migrate job manual desde Azure. El job usa:

```text
python manage.py migrate_cloud --settings=config.settings_cloud --noinput
```

Ese comando corre:

```text
manage.py migrate
manage.py migrate_tenants
```

Smoke minimo:

```powershell
python manage.py bootstrap_tenant --tenant demo --settings=config.settings_cloud
```

> Para correr `bootstrap_tenant` **localmente** tu IP publica debe estar en el
> firewall del PostgreSQL platform (ver "Firewall del PostgreSQL platform"); el
> rule `allow-azure-services` no cubre tu laptop. Alternativa sin abrir IP:
> ejecutarlo desde Azure como override del comando del migrate job.

Luego validar `/api/v1/health/`, login demo y `/api/v1/auth/me/`.

## Notas

- No usar `latest` en prod.
- Prod usa `api_min_replicas=0` para scale-to-zero MVP.
- ACR aislado por prod queda diferido. Si aparece requisito de compliance o
  mayor aislamiento, crear/importar un ACR en `platform` o uno exclusivo prod.
