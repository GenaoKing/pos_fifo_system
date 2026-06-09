# Terraform D2 - Regiones y destroy en Azure dev

> Estado documental: nota historica de incidente/aprendizaje D2. Para el estado
> Azure dev actual usar `docs/runbooks/AZURE_DEV_RESOURCES.md`; para remote state usar
> `docs/runbooks/TERRAFORM_AZURE_REMOTE_STATE.md`.

Esta nota acompana `docs/runbooks/TERRAFORM_AZURE_D2_FOUNDATION.md` durante las primeras
pruebas de `terraform apply/destroy` en Azure.

## Error: region no permitida por policy

Si Azure devuelve `RequestDisallowedByAzure`, Terraform no esta fallando por HCL
ni por credenciales. La suscripcion tiene una politica que limita las regiones
donde puede crear recursos.

En esta suscripcion ya vimos que `canadacentral` permite avanzar con recursos
como Resource Group y Log Analytics, pero Static Web Apps puede tener una lista
de regiones admitidas distinta. Para el primer laboratorio conviene separar las
regiones:

```hcl
location                = "canadacentral"
observability_location  = null
static_web_app_location = "centralus"
```

Lectura mental:

- `location`: region base del Resource Group.
- `observability_location`: si queda en `null`, usa `location`.
- `static_web_app_location`: region especifica de Azure Static Web Apps.

## Error: Resource Group still contains Resources

Durante un `apply` parcial puede quedar un recurso vivo dentro del Resource
Group. En este caso fue:

```text
Microsoft.OperationalInsights/workspaces/posfifo-dev-law
```

El provider AzureRM intenta proteger contra borrados accidentales y por defecto
verifica que el Resource Group este vacio antes de borrarlo. Para este ambiente
`dev`/lab se configuro:

```hcl
provider "azurerm" {
  features {
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }
}
```

Con eso, `terraform destroy` le pide a Azure borrar el Resource Group completo,
incluyendo recursos anidados.

No copiar esta decision a produccion sin revisarla. En prod normalmente se
quiere que Terraform sea mas conservador.

## Secuencia recomendada ahora

Desde:

```powershell
cd C:\Proyectos\pos_fifo_system\infra\azure\environments\dev
```

Para completar primero la foundation sin quedar bloqueados por Static Web Apps,
deja `terraform.tfvars` asi:

```hcl
location                = "canadacentral"
observability_location  = null
enable_static_web_app   = false
static_web_app_location = "centralus"
```

Con esa configuracion Terraform crea Resource Group, Log Analytics y Application
Insights, pero salta el modulo de Static Web Apps.

Primero confirma que Terraform ve el workspace creado:

```powershell
terraform state list
```

Luego intenta limpiar otra vez:

```powershell
terraform destroy
```

Si el destroy termina limpio, ajusta `terraform.tfvars`:

```hcl
location                = "canadacentral"
observability_location  = null
enable_static_web_app   = false
static_web_app_location = "centralus"
```

Y prueba de nuevo:

```powershell
terraform plan
terraform apply
```

Cuando la foundation ya este creada, podemos probar Static Web Apps como cambio
separado:

```hcl
enable_static_web_app   = true
static_web_app_location = "centralus"
```

Si esa region tambien falla con `RequestDisallowedByAzure`, no destruimos la
foundation: solo volvemos a `enable_static_web_app = false` o probamos otra
region permitida para ese servicio.

## Si destroy vuelve a fallar

Si falla otra vez por el mismo workspace, hay dos caminos:

1. Revisar si el recurso sigue en state:

```powershell
terraform state list
```

2. Borrarlo manualmente desde Azure Portal o Azure CLI y repetir `terraform destroy`.

Para Azure CLI, la forma conceptual seria:

```powershell
az monitor log-analytics workspace delete `
  --resource-group posfifo-dev-rg `
  --workspace-name posfifo-dev-law `
  --yes
```

Despues:

```powershell
terraform destroy
```

La regla de oro: antes de borrar manualmente, confirmar que estamos en el
Resource Group de laboratorio `posfifo-dev-rg`, no en un ambiente compartido ni
productivo.

## Decision actual: no crear otra suscripcion todavia

Con `enable_static_web_app = false`, Terraform pudo reconciliar la foundation:

- Resource Group.
- Log Analytics Workspace.
- Application Insights.

Eso significa que D2 puede seguir avanzando sin Azure Static Web Apps. El bloqueo
de ASWA es una combinacion entre policy regional de la suscripcion y regiones
admitidas por el servicio. No necesitamos resolverlo antes de trabajar ACR,
Container Apps, job de migraciones, Key Vault/secrets y PostgreSQL.

Crear una suscripcion nueva con tarjeta podria liberar algunas restricciones si
la suscripcion actual es de tipo estudiante/free/beneficio limitado, pero no es
necesario para esta fase. Conviene posponer esa decision hasta que el backend
cloud este desplegado y sepamos si el frontend realmente necesita Static Web
Apps o si nos conviene Storage static website, App Service, Container App o un
frontend servido por otro canal.
