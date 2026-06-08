# Terraform D2 - ACR y Azure Container Apps

Esta fase extiende la foundation ya creada:

- Resource Group.
- Log Analytics.
- Application Insights.

Y agrega la plataforma donde vivira el backend Docker:

- Azure Container Registry, ACR.
- Azure Container Apps Environment.
- Container App `api`, opcional hasta publicar la imagen.
- Container App Job `migrate`, opcional y manual.

## Modelo mental

Si vienes de on-prem:

- ACR es tu repositorio de imagenes, parecido a guardar plantillas versionadas.
- Container Apps Environment es el runtime administrado donde corren servicios.
- Container App `api` es el proceso web Gunicorn/Django.
- Container App Job `migrate` es una tarea manual, no un servicio permanente.
- Las variables de entorno son la configuracion del deploy.
- Los logs salen a stdout/stderr y Azure los manda a Log Analytics.

La regla importante: las migraciones no corren al arrancar el contenedor. Se
ejecutan como job explicito.

## Primer apply: crear plataforma, no desplegar API todavia

En `infra/azure/environments/dev/terraform.tfvars` deja:

```hcl
location               = "canadacentral"
observability_location = null

enable_static_web_app = false

container_apps_location = null
acr_name                = null
acr_sku                 = "Basic"

enable_api_container_app = false
enable_migrate_job       = false

container_image_repository = "pos-fifo-backend"
container_image_tag        = "dev"
```

Con eso Terraform debe crear:

- ACR.
- Container Apps Environment.

Pero no intenta crear la API ni el job, porque todavia no existe una imagen en
ACR.

```powershell
cd C:\Proyectos\pos_fifo_system\infra\azure\environments\dev

terraform fmt
terraform plan
terraform apply
```

Despues revisa los outputs:

```powershell
terraform output container_registry
terraform output container_apps
```

El output de ACR debe mostrar algo como:

```text
login_server = "posfifodevacr.azurecr.io"
```

## Publicar la imagen Docker al ACR

Desde la raiz del repo:

```powershell
cd C:\Proyectos\pos_fifo_system
```

Login contra el registry:

```powershell
az acr login --name <acr_name>
```

Ejemplo:

```powershell
az acr login --name posfifodevacr
```

Construir imagen local:

```powershell
docker build -t pos-fifo-backend:dev .
```

Etiquetarla para ACR:

```powershell
docker tag pos-fifo-backend:dev <login_server>/pos-fifo-backend:dev
```

Ejemplo:

```powershell
docker tag pos-fifo-backend:dev posfifodevacr.azurecr.io/pos-fifo-backend:dev
```

Subirla:

```powershell
docker push <login_server>/pos-fifo-backend:dev
```

Ejemplo:

```powershell
docker push posfifodevacr.azurecr.io/pos-fifo-backend:dev
```

## Configurar DB y secretos para API/job

En `terraform.tfvars`, completar los valores reales:

```hcl
django_secret_key = "REEMPLAZAR-POR-UN-SECRETO-LARGO"

db_name     = "REEMPLAZAR"
db_user     = "REEMPLAZAR"
db_password = "REEMPLAZAR"
db_host     = "REEMPLAZAR.postgres.database.azure.com"
db_port     = "5432"
db_sslmode  = "require"

api_allowed_hosts        = ".azurecontainerapps.io,localhost,127.0.0.1"
api_cors_allowed_origins = ""
api_csrf_trusted_origins = ""
api_min_replicas         = 0
api_max_replicas         = 1

app_version    = "dev"
git_commit_sha = "unknown"
```

Nota de seguridad: este primer corte guarda secretos en `terraform.tfvars` local
y en Terraform state. `terraform.tfvars` esta ignorado por git, pero el state
tambien debe tratarse como sensible. En D3 movemos esto a Key Vault o secrets
manejados con mas disciplina.

## Crear el job de migraciones

Primero crear solo el job:

```hcl
enable_migrate_job       = true
enable_api_container_app = false
```

Aplicar:

```powershell
cd C:\Proyectos\pos_fifo_system\infra\azure\environments\dev

terraform plan
terraform apply
```

El job queda creado pero no corre automaticamente.

Para ejecutarlo:

```powershell
az containerapp job start `
  --resource-group posfifo-dev-rg `
  --name posfifo-dev-migrate
```

Ver ejecuciones:

```powershell
az containerapp job execution list `
  --resource-group posfifo-dev-rg `
  --name posfifo-dev-migrate `
  --output table
```

## Crear la API

Cuando las migraciones ya pasaron:

```hcl
enable_api_container_app = true
enable_migrate_job       = true
```

Aplicar:

```powershell
terraform plan
terraform apply
```

Revisar el FQDN:

```powershell
terraform output container_apps
```

Probar health:

```powershell
curl https://<api_fqdn>/api/v1/health/
```

Esperado:

```json
{
  "status": "ok",
  "db": "ok",
  "environment": "dev"
}
```

## Hito alcanzado en dev

En Azure for Students, con Static Web Apps deshabilitado, el backend pudo
desplegarse en Azure Container Apps:

```text
ACR: posfifodevacr.azurecr.io
Container Apps Environment: posfifo-dev-aca-env
API: posfifo-dev-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io
Imagen: posfifodevacr.azurecr.io/pos-fifo-backend:dev
Job migraciones: posfifo-dev-migrate
```

Health validado:

```text
GET /api/v1/health/ -> 200 OK
status: ok
db: ok
environment: dev
version: dev
commit: unknown
```

`commit: unknown` no indica falla de runtime. Significa que todavia no estamos
inyectando `GIT_COMMIT_SHA` desde pipeline/build. Se resolvera cuando el build y
push de imagen sean automatizados o cuando se establezca el valor manualmente en
`terraform.tfvars`.

Validaciones recomendadas despues del apply:

```powershell
curl https://posfifo-dev-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io/api/v1/health/
```

Ver logs de la API:

```powershell
az containerapp logs show `
  --resource-group posfifo-dev-rg `
  --name posfifo-dev-api `
  --follow
```

Ejecutar job de migraciones manualmente:

```powershell
az containerapp job start `
  --resource-group posfifo-dev-rg `
  --name posfifo-dev-migrate
```

Listar ejecuciones del job:

```powershell
az containerapp job execution list `
  --resource-group posfifo-dev-rg `
  --name posfifo-dev-migrate `
  --output table
```

## Si falla por region

Si Azure for Students bloquea Container Apps o ACR en `canadacentral`, no cambies
todo al azar. Cambia solo:

```hcl
container_apps_location = "REGION-PERMITIDA"
```

Y vuelve a correr:

```powershell
terraform plan
```

El Resource Group y observability pueden quedarse en Canada Central; lo que se
mueve es la plataforma de contenedores.

## Si falla porque la imagen no existe

Verifica que:

- `enable_api_container_app = false` antes del primer apply de plataforma.
- `enable_migrate_job = false` antes del primer apply de plataforma.
- `docker push` haya subido exactamente la imagen esperada por Terraform.

El nombre esperado esta en:

```powershell
terraform output container_apps
```

Campo:

```text
image
```

## Si Azure Portal avisa que ACR admin esta deshabilitado

Mensaje tipico:

```text
Cannot perform credential operations ... as admin user is disabled.
```

Esto es esperado para este deploy. El ACR se creo con:

```hcl
admin_enabled = false
```

La API no debe usar usuario/password del registry. Azure Container Apps hace pull
de la imagen con Managed Identity y permiso `AcrPull`.

No habilites el admin user solo por ese aviso del portal. Si el contenedor no
puede descargar la imagen, el sintoma real aparecera en logs/revision status.

## Si health queda cargando

Prueba con timeout local para no quedarte esperando:

```powershell
curl -v --max-time 20 https://<api_fqdn>/api/v1/health/
```

Revisa estado de la app:

```powershell
az containerapp show `
  --resource-group posfifo-dev-rg `
  --name posfifo-dev-api `
  --query "{provisioningState:properties.provisioningState,runningStatus:properties.runningStatus,latestRevision:properties.latestRevisionName,fqdn:properties.configuration.ingress.fqdn}" `
  --output table
```

Revisa revisions:

```powershell
az containerapp revision list `
  --resource-group posfifo-dev-rg `
  --name posfifo-dev-api `
  --output table
```

Logs en vivo:

```powershell
az containerapp logs show `
  --resource-group posfifo-dev-rg `
  --name posfifo-dev-api `
  --follow
```

Si el comando responde:

```text
Could not find a replica for this app
```

probablemente la app esta en scale-to-zero porque `api_min_replicas = 0`. Para
depurar, cambia temporalmente:

```hcl
api_min_replicas = 1
```

Aplica y revisa logs otra vez:

```powershell
terraform plan
terraform apply

az containerapp logs show `
  --resource-group posfifo-dev-rg `
  --name posfifo-dev-api `
  --follow
```

Cuando el health este estable, puedes volver a `api_min_replicas = 0` para
permitir scale-to-zero en dev.

Si los logs muestran espera o error conectando a PostgreSQL, revisar:

- `db_host`, `db_name`, `db_user`, `db_password`.
- Firewall/networking del PostgreSQL existente.
- Que el servidor PostgreSQL permita conexiones desde Azure services o desde la
  salida de Container Apps.
- Que `db_sslmode = "require"`.

Para evitar esperas largas, el contenedor define `PGCONNECT_TIMEOUT=5`.

### Si los logs muestran DisallowedHost con IP interna

Ejemplo:

```text
Invalid HTTP_HOST header: '100.100.0.153:8000'
User-Agent: kube-probe
```

Eso significa que el probe interno de Azure Container Apps esta llamando el
health check con un host interno que Django no acepta. El modulo configura los
probes con un host permitido:

```hcl
host = "localhost"
```

Los probes de plataforma apuntan al health liviano:

```text
/api/v1/health/live/
```

Ese endpoint no consulta PostgreSQL. Sirve para decirle a Azure que el proceso
HTTP/Django esta vivo.

La validacion manual queda:

```powershell
curl -v --max-time 20 https://<api_fqdn>/api/v1/health/live/
curl -v --max-time 20 https://<api_fqdn>/api/v1/health/
```

El health completo con DB sigue siendo:

```text
/api/v1/health/
```

`ALLOWED_HOSTS` debe incluir:

```hcl
api_allowed_hosts = ".azurecontainerapps.io,localhost,127.0.0.1"
```

Despues de ajustar:

```powershell
terraform plan
terraform apply
```

## Si falla por Microsoft.App no registrado

Error tipico:

```text
MissingSubscriptionRegistration: The subscription is not registered to use namespace 'Microsoft.App'
```

Azure Container Apps usa el resource provider `Microsoft.App`. En algunas
suscripciones, especialmente nuevas, free o Azure for Students, hay que
registrarlo manualmente antes del primer deploy.

Verifica que estas en la suscripcion correcta:

```powershell
az account show --query "{name:name,id:id}" --output table
```

Revisa el estado:

```powershell
az provider show `
  --namespace Microsoft.App `
  --query registrationState `
  --output tsv
```

Registra el provider:

```powershell
az provider register --namespace Microsoft.App
```

Container Apps tambien suele requerir estos providers, que normalmente ya estan
registrados si observability y ACR fueron creados, pero se pueden confirmar:

```powershell
az provider register --namespace Microsoft.OperationalInsights
az provider register --namespace Microsoft.ContainerRegistry
```

Espera hasta que `Microsoft.App` diga `Registered`:

```powershell
az provider show `
  --namespace Microsoft.App `
  --query registrationState `
  --output tsv
```

Cuando este registrado, reintenta:

```powershell
terraform plan
terraform apply
```

Si Azure for Students no permite registrar `Microsoft.App`, entonces la
suscripcion no puede usar Azure Container Apps y habria que evaluar una
suscripcion Pay-As-You-Go o cambiar temporalmente el runtime del backend.
