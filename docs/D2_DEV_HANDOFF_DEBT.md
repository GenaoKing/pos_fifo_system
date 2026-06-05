# D2 dev handoff y deuda pendiente

Estado actual:

- Backend Django desplegado en Azure Container Apps.
- API publica responde `/api/v1/health/` con `200 OK`, `status=ok`, `db=ok`.
- ACR privado con `admin_enabled=false`.
- Container App usa Managed Identity + rol `AcrPull`.
- Static Web Apps queda deshabilitado por policy/regiones de Azure for Students.

## Decision: dos health checks

Para produccion no conviene que los probes de plataforma dependan de PostgreSQL.
Si la DB tiene un corte breve, Azure podria matar contenedores sanos.

Se definen dos contratos:

```text
/api/v1/health/live/
```

Health liviano para Azure Container Apps. No consulta DB. Responde si Django
esta vivo.

```text
/api/v1/health/
```

Health completo. Consulta DB y expone `status`, `db`, `version`, `commit` y
`environment`.

Container Apps debe usar `/api/v1/health/live/` para `startup_probe` y
`liveness_probe`. Monitoreo externo puede usar `/api/v1/health/`.

## Secrets vistos en Azure Portal

Los secrets `django-secret-key` y `db-password` que aparecen en la Container App
son esperados. Terraform los crea con bloques `secret` y luego las variables de
entorno los referencian por nombre.

Esto evita imprimir el secreto como env var visible, pero no resuelve todo el
ciclo de vida de secretos.

Deuda D3:

- `terraform.tfvars` local contiene secretos reales.
- Terraform state contiene valores sensibles.
- No hay Key Vault todavia.
- No hay rotacion automatizada.
- No hay pipeline inyectando secretos/version/commit.

Decision actual para dev:

- Aceptable temporalmente porque `terraform.tfvars` esta ignorado por git.
- Tratar el state local como archivo sensible.
- No compartir `terraform.tfstate`.
- Rotar secretos antes de staging/prod si fueron expuestos durante pruebas.

Decision production-ready:

- Crear Key Vault.
- Mover `DJANGO_SECRET_KEY` y password DB a Key Vault o references seguras.
- Usar identities/RBAC en vez de copiar secretos manualmente.
- Remote state en Azure Storage con acceso restringido.

## Deuda operativa dev

- `commit` aparece como `unknown` porque `GIT_COMMIT_SHA` aun no se inyecta desde
  pipeline.
- `version` aparece como `dev` porque `APP_VERSION` esta manual.
- `api_min_replicas = 1` es util para debugging, pero consume credito. En dev se
  puede volver a `0` cuando los probes queden estables.
- Static Web Apps sigue apagado con `enable_static_web_app = false`.
- PostgreSQL existente vive en otro Resource Group (`rg-pos-fifo`), fuera de este
  state Terraform.
- El job de migraciones existe, pero debe ejecutarse explicitamente cuando hay
  cambios de schema.

## Runbook para publicar el cambio de health live

Este cambio toca codigo Django y Terraform. Requiere rebuild/push de imagen y
despues `terraform apply`.

Usa un tag nuevo para evitar ambiguedad con imagenes cacheadas. Ejemplo:
`dev-live-health`.

En `terraform.tfvars`:

```hcl
container_image_tag = "dev-live-health"
```

Desde la raiz del repo:

```powershell
cd C:\Proyectos\pos_fifo_system

docker build -t pos-fifo-backend:dev-live-health .
docker tag pos-fifo-backend:dev-live-health posfifodevacr.azurecr.io/pos-fifo-backend:dev-live-health
docker push posfifodevacr.azurecr.io/pos-fifo-backend:dev-live-health
```

Luego desde Terraform:

```powershell
cd C:\Proyectos\pos_fifo_system\infra\azure\environments\dev

terraform fmt
terraform plan
terraform apply
```

Forzar una revision si Azure no detecta cambio de imagen por usar el mismo tag:

```powershell
az containerapp revision restart `
  --resource-group posfifo-dev-rg `
  --name posfifo-dev-api `
  --revision <revision-name>
```

Validar:

```powershell
curl -v --max-time 20 https://posfifo-dev-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io/api/v1/health/live/
curl -v --max-time 20 https://posfifo-dev-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io/api/v1/health/
```
