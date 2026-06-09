# D2 Terraform Azure Foundation

Tutorial para el primer `plan/apply/destroy` real en Azure. Este corte crea algo
simple y entendible: Resource Group, Log Analytics, Application Insights y Static
Web App dev.

No crea todavia Container Apps, ACR, Key Vault ni PostgreSQL. Es intencional:
primero queremos que el ciclo Terraform sea natural antes de sumar recursos con
mas knobs y costo.

## Que se creo en el repo

```text
infra/
  azure/
    environments/
      dev/                 # root module: aqui corres terraform
    modules/
      observability/       # Log Analytics + Application Insights
      static-web-app/      # Azure Static Web App
```

## Instalar herramientas

En Windows, instalar Terraform y Azure CLI:

```powershell
winget install Hashicorp.Terraform
winget install Microsoft.AzureCLI
```

Cerrar y abrir la terminal despues de instalar para refrescar el `PATH`.

Verificar:

```powershell
terraform version
az version
```

## Login y subscription

```powershell
az login
az account list --output table
az account set --subscription "<SUBSCRIPTION_ID>"
```

Terraform con AzureRM v4 necesita `subscription_id` para `plan/apply`. En este
repo se pasa por `terraform.tfvars`, no hardcodeado.

## Preparar variables

```powershell
cd C:\Proyectos\pos_fifo_system\infra\azure\environments\dev
Copy-Item terraform.tfvars.example terraform.tfvars
notepad terraform.tfvars
```

Completar:

```hcl
subscription_id = "tu-subscription-id-real"
location        = "canadacentral"
```

`terraform.tfvars` esta ignorado por git. No guardar secretos reales ni IDs
privados en archivos versionados si no hace falta.

## Ciclo de comandos

Inicializar provider y modulos:

```powershell
terraform init
```

Formatear:

```powershell
terraform fmt -recursive
```

Validar sintaxis:

```powershell
terraform validate
```

Ver el plan:

```powershell
terraform plan
```

El primer plan deberia mostrar recursos `+ create` para:

- Resource Group
- Log Analytics Workspace
- Application Insights
- Static Web App

Aplicar:

```powershell
terraform apply
```

Leer el plan, confirmar con `yes`, y al final revisar outputs:

```powershell
terraform output
```

Destruir el laboratorio dev si quieres volver costo a cero:

```powershell
terraform destroy
```

## Como leer lo creado

- `azurerm_resource_group.main`: el contenedor logico del ambiente dev.
- `module.observability`: crea logs/telemetry que luego usara Container Apps.
- `module.static_web_app`: crea el recurso ASWA, pero no sube el frontend React.

Importante: Terraform crea el recurso Static Web App. El contenido del portal se
despliega despues con el workflow de Azure Static Web Apps o CI/CD.

## State local

En este primer corte, el state queda local en:

```text
infra/azure/environments/dev/terraform.tfstate
```

Ese archivo no se commitea. Para trabajo compartido/staging/prod, D2 siguiente
debe mover state a Azure Storage con locking.

Despues de `terraform init`, Terraform tambien crea `.terraform.lock.hcl`.
Ese archivo **si se commitea**: fija la version exacta del provider para que otra
maquina instale lo mismo.

## Troubleshooting

- **`terraform` no se reconoce**: instalar Terraform y reabrir terminal.
- **`az` no se reconoce**: instalar Azure CLI y reabrir terminal.
- **`subscription_id is required`**: revisar `terraform.tfvars` o definir
  `ARM_SUBSCRIPTION_ID`.
- **Provider registration/RBAC error**: tu usuario puede no tener permisos para
  registrar providers o crear recursos. Revisar rol en la subscription.
- **Static Web App region no soportada**: cambiar `static_web_app_location` en
  `terraform.tfvars`.
- **`RequestDisallowedByAzure` por region**: tu suscripcion tiene una policy que
  limita regiones. En este proyecto el default dev queda en `canadacentral`.
  Si ya aplicaste y solo se creo el Resource Group en otra region, lo mas limpio
  para este laboratorio inicial es `terraform destroy`, ajustar regiones en
  `terraform.tfvars`, y volver a `terraform plan/apply`.
- **Plan propone destroy inesperado**: parar, no aplicar; revisar nombres y state.

## Siguiente incremento

Cuando este `plan/apply/destroy` salga limpio, el proximo corte D2 puede agregar:

- Azure Container Registry.
- Container Apps Environment.
- Container App `api`.
- Container Apps Job `migrate`.
- Key Vault o secrets de Container Apps.
- Data source para PostgreSQL Flexible Server existente.
> Nota de laboratorio: en suscripciones con policy regional restrictiva, crea
> primero la foundation con `enable_static_web_app = false`. Luego prueba Static
> Web Apps como cambio separado cambiando solo `enable_static_web_app` y
> `static_web_app_location`.
