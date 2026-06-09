# Azure dev resources inventory

Estado capturado para el ambiente `dev`.

## Resumen mental

```text
GitHub Actions
  -> build Docker image
  -> push a Azure Container Registry
  -> update Azure Container App
  -> smoke test /api/v1/health/

Terraform
  -> crea y mantiene la infraestructura dev
  -> guarda state remoto en Azure Storage

Azure PostgreSQL existente
  -> vive fuera del stack Terraform dev actual
```

## Recursos principales

| Recurso | Tipo | Resource Group | Terraform | Para que sirve |
| --- | --- | --- | --- | --- |
| `posfifo-dev-rg` | Resource Group | subscription | Si | Agrupa la infraestructura cloud dev del backend. |
| `posfifodevacr` | Azure Container Registry | `posfifo-dev-rg` | Si | Almacena imagenes Docker del backend. Es el equivalente a un repositorio privado de imagenes. |
| `posfifo-dev-aca-env` | Container Apps Environment | `posfifo-dev-rg` | Si | Ambiente compartido donde corren Container Apps y Jobs. Conecta con Log Analytics. |
| `posfifo-dev-api` | Container App | `posfifo-dev-rg` | Si | Backend Django/Gunicorn publicado por HTTPS. |
| `posfifo-dev-migrate` | Container App Job | `posfifo-dev-rg` | Si | Job manual para ejecutar migraciones/comandos operativos sin meterlos en el arranque de la API. |
| `posfifo-dev-api-id` | Managed Identity | `posfifo-dev-rg` | Si | Identidad de la API para leer ACR y Key Vault sin usuario/password embebido. |
| `posfifo-dev-migrate-id` | Managed Identity | `posfifo-dev-rg` | Si | Identidad del job de migraciones para leer ACR y Key Vault. |
| `posfifodevkv` | Key Vault | `posfifo-dev-rg` | Si | Guarda secretos cloud como `django-secret-key` y `db-password`. |
| `posfifo-dev-law` | Log Analytics Workspace | `posfifo-dev-rg` | Si | Recibe logs de Container Apps y observabilidad de plataforma. |
| `posfifo-dev-appi` | Application Insights | `posfifo-dev-rg` | Si | Telemetria de aplicacion/diagnostico para dev. |
| `posfifo-dev-github-actions-id` | Managed Identity | `posfifo-dev-rg` | Si | Identidad federada usada por GitHub Actions via OIDC. |
| `Application Insights Smart Detection` | Action Group | `posfifo-dev-rg` | No explicito | Recurso auxiliar creado por Azure/App Insights para alertas inteligentes. No lo tocamos manualmente. |
| `posfifotfstatedev` | Storage Account | `posfifo-tfstate-rg` | Bootstrap manual | Guarda el Terraform remote state: `tfstate/azure/dev.tfstate`. |
| `pos-fifo-pg` | PostgreSQL Flexible Server | `rg-pos-fifo` | No | Base PostgreSQL existente que usa el backend cloud dev. |
| `pos-fifo-sql` | Azure SQL Server | `rg-pos-fifo` | No | Recurso SQL existente fuera del stack cloud Django actual. No gestionarlo desde este Terraform. |
| `pos-fifo-sql/pos_fifo_db` | Azure SQL Database | `rg-pos-fifo` | No | Base SQL existente fuera del stack cloud Django actual. |

## Fronteras de administracion

Terraform dev gestiona:

- Resource Group `posfifo-dev-rg`.
- ACR.
- Container Apps Environment.
- Container App `api`.
- Container App Job `migrate`.
- Managed Identities de API, migrate y GitHub Actions.
- RBAC dev necesario para ACR, Key Vault, Container Apps y GitHub Actions.
- Key Vault.
- Log Analytics y Application Insights.

Terraform dev no gestiona:

- PostgreSQL `pos-fifo-pg`.
- Azure SQL `pos-fifo-sql` y `pos_fifo_db`.
- Storage Account de remote state como recurso del mismo stack. Ese storage es
  bootstrap: se creo antes para que Terraform pudiera guardar su state remoto.
- Recursos auxiliares creados automaticamente por Azure, como Smart Detection.

## Estado runtime actual

API:

```text
URL: https://posfifo-dev-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io
Health: /api/v1/health/
Scale dev: min=0, max=1
```

Nota: Azure puede mostrar `minReplicas=null`; en este contexto equivale a
scale-to-zero (`0`). El primer request tras inactividad puede tardar por cold
start.

Imagen activa al momento de esta captura:

```text
posfifodevacr.azurecr.io/pos-fifo-backend:b56a3e05de0dd8478589c80882ae2fb08e48ccd4
```

Job de migraciones:

```text
Nombre: posfifo-dev-migrate
Trigger: Manual
Imagen: misma familia que la API
```

## ACR actual

Registry:

```text
posfifodevacr.azurecr.io
```

Repositorio:

```text
pos-fifo-backend
```

Tags vistos recientemente:

```text
b56a3e05de0dd8478589c80882ae2fb08e48ccd4
72ecebf31ac9d9f611711487091f858f09d3dfc0
dev-live-health
dev
```

Regla de trabajo: preferir tags inmutables por SHA de commit para deploy. Evitar
`latest`.

## Remote state

Backend Terraform dev:

```text
Resource Group: posfifo-tfstate-rg
Storage Account: posfifotfstatedev
Container: tfstate
Blob key: azure/dev.tfstate
```

Archivo de configuracion:

```text
infra/azure/environments/dev/backend.tf
```

Comandos de verificacion:

```powershell
cd C:\Proyectos\pos_fifo_system\infra\azure\environments\dev

terraform validate
terraform plan -compact-warnings
```

Esperado:

```text
No changes.
```

## Comandos utiles de inventario

Listar recursos:

```powershell
az resource list `
  --query "[].{name:name,type:type,resourceGroup:resourceGroup,location:location}" `
  --output table
```

Ver imagen activa de la API:

```powershell
az containerapp show `
  --resource-group posfifo-dev-rg `
  --name posfifo-dev-api `
  --query "{fqdn:properties.configuration.ingress.fqdn,image:properties.template.containers[0].image,min:properties.template.scale.minReplicas,max:properties.template.scale.maxReplicas,status:properties.runningStatus}" `
  --output json
```

Ver tags publicados en ACR:

```powershell
az acr repository show-tags `
  --name posfifodevacr `
  --repository pos-fifo-backend `
  --orderby time_desc `
  --top 10 `
  --output table
```

Ver recursos que Terraform conoce:

```powershell
cd C:\Proyectos\pos_fifo_system\infra\azure\environments\dev
terraform state list
```

## Precauciones

- No borrar `rg-pos-fifo` desde este roadmap: contiene bases existentes fuera
  del stack Terraform dev.
- No borrar `posfifo-tfstate-rg` sin una migracion planificada: ahi vive el
  state remoto.
- No editar secretos cloud desde Terraform con valores planos. Usar Key Vault.
- Un `docker push` a ACR no despliega por si solo. Para desplegar hay que
  actualizar Container App o dejar que GitHub Actions lo haga.
