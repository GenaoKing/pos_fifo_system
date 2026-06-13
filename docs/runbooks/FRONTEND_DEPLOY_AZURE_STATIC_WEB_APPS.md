# Deploy Frontend En Azure Static Web Apps

Guia practica para publicar `pos-cloud-dashboard` como portal cloud usando
Azure Static Web Apps (ASWA), preferiblemente en una suscripcion Pay-As-You-Go
si Azure for Students mantiene bloqueado el recurso por policy/regiones.

## Estado actual

- Backend dev: `https://posfifo-dev-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io`
- Backend staging: `https://posfifo-staging-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io`
- Frontend repo: `C:\Proyectos\pos-cloud-dashboard`
- Build frontend: Vite/React, salida `dist/`.
- Config SPA: `staticwebapp.config.json` ya existe.
- CORS local Vite validado en dev/staging para `http://localhost:5173`.
- ASWA en Azure for Students esta bloqueado por policy/regiones; se evalua
  crear ASWA Free desde una suscripcion Pay-As-You-Go.
- ASWA dev creado el 2026-06-13:
  `https://agreeable-moss-051bc0010.7.azurestaticapps.net`
  (`posfifo-dev-portal-swa`, RG `posfifo-dev-frontend-rg`, Free, branch
  `develop`).
- CORS dev aplicado para el origen ASWA dev.
- Pendiente: redeployar ASWA con `VITE_API_URL` en el workflow para que el
  bundle apunte al backend dev.

## Modelo mental

ASWA hace tres trabajos:

1. Construye el bundle React desde GitHub Actions.
2. Sirve los archivos estaticos (`dist/`) por HTTPS/CDN.
3. Reescribe rutas internas del SPA hacia `index.html`.

El backend Django sigue viviendo en Azure Container Apps. El frontend solo
necesita conocer la URL publica del API con `VITE_API_URL`.

```text
GitHub pos-cloud-dashboard
  -> GitHub Actions / Azure Static Web Apps action
  -> npm run build
  -> dist/
  -> Azure Static Web Apps
  -> navegador del usuario
  -> HTTPS API Azure Container Apps
```

## Decision recomendada

Primero probar ASWA Free en Pay-As-You-Go:

- mantiene el frontend dentro del ecosistema Azure,
- conserva el roadmap original,
- evita Dockerizar un frontend estatico,
- da HTTPS y routing SPA sin trabajo extra.

Si ASWA vuelve a bloquearse o toma mas de 1-2 horas, usar Vercel como plan B
para no frenar el portal.

## Campos al crear ASWA

En Azure Portal:

1. Buscar **Static Web Apps**.
2. `Create`.
3. Subscription: Pay-As-You-Go.
4. Resource group:
   - dev: `posfifo-dev-frontend-rg`
   - staging: `posfifo-staging-frontend-rg`
5. Name:
   - dev: `posfifo-dev-portal-swa`
   - staging: `posfifo-staging-portal-swa`
6. Plan type: `Free` para el primer corte.
7. Region: elegir una region permitida por la suscripcion. Si Azure ofrece una
   lista corta, usar la mas cercana/estable disponible; el API esta en
   `canadacentral`, pero ASWA se sirve globalmente.
8. Deployment source: `GitHub`.
9. Organization: `GenaoKing`.
10. Repository: `pos-cloud-dashboard`.
11. Branch:
    - dev: `develop`
    - staging: `main` o rama/tag release cuando formalicemos promocion.
12. Build preset: React/Vite o Custom.
13. App location: `/`
14. API location: dejar vacio.
15. Output location: `dist`
16. Build command: `npm run build`

## Variables por ambiente

`VITE_API_URL` no es secreto. Es una URL publica del API.

Valores actuales:

```text
dev:
VITE_API_URL=https://posfifo-dev-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io

staging:
VITE_API_URL=https://posfifo-staging-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io
```

Importante para Vite:

- Las variables `VITE_*` se hornean durante `npm run build`.
- Las **Environment variables** del portal de ASWA aplican principalmente al
  backend API/Functions de Static Web Apps, no son el punto correcto para
  inyectar `VITE_API_URL` al bundle ya construido.
- Para Vite, poner `VITE_API_URL` en el workflow de GitHub Actions o como
  GitHub Actions Variable usada por el workflow.

## Workflow ASWA esperado

Si Azure genera automaticamente el workflow, revisar que tenga estos valores:

```yaml
app_location: "/"
api_location: ""
output_location: "dist"
app_build_command: "npm run build"
```

Y agregar env vars al paso de deploy/build:

```yaml
env:
  VITE_API_URL: ${{ vars.VITE_API_URL_DEV }}
  VITE_APP_VERSION: ${{ github.sha }}
```

Para staging, usar otra variable:

```yaml
env:
  VITE_API_URL: ${{ vars.VITE_API_URL_STAGING }}
  VITE_APP_VERSION: ${{ github.sha }}
```

Variables sugeridas en GitHub:

```text
Settings -> Secrets and variables -> Actions -> Variables

VITE_API_URL_DEV=https://posfifo-dev-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io
VITE_API_URL_STAGING=https://posfifo-staging-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io
```

ASWA normalmente crea un secret/token de deploy. Si lo crea el portal, no
renombrarlo sin actualizar el workflow.

## Node/Oryx

`pos-cloud-dashboard/package.json` declara:

```json
"engines": {
  "node": ">=20.19.0",
  "npm": ">=10.0.0"
}
```

Esto ayuda a que Oryx/GitHub Actions no use una version de Node incompatible
con Vite 8.

## CORS despues de crear ASWA

Cuando Azure entregue una URL como:

```text
https://<nombre-generado>.azurestaticapps.net
```

hay que agregarla al backend:

```hcl
api_cors_allowed_origins = "http://localhost:5173,http://127.0.0.1:5173,https://<nombre-generado>.azurestaticapps.net"
```

Dev actual:

```text
https://agreeable-moss-051bc0010.7.azurestaticapps.net
```

Ya fue agregado a `infra/azure/environments/dev/terraform.tfvars` y aplicado
con Terraform el 2026-06-13.

Ambientes:

- dev: `infra/azure/environments/dev/terraform.tfvars`
- staging: `infra/azure/environments/staging/terraform.tfvars`

Luego:

```powershell
cd C:\Proyectos\pos_fifo_system\infra\azure\environments\dev
terraform plan
terraform apply
```

Repetir para staging si corresponde.

## Smoke test

Despues de deploy y CORS:

1. Abrir la URL de ASWA.
2. Login con usuario real del ambiente.
3. Confirmar `/dashboard`.
4. Probar:
   - `/productos`
   - `/categorias`
   - `/clientes`
   - `/cuentas`
   - `/reportes`
   - `/comparativo`
5. Refrescar el navegador en una ruta interna, por ejemplo `/cuentas`.
   Debe cargar la app y no devolver 404. Eso valida SPA routing.
6. Revisar DevTools -> Network:
   - API responde desde `posfifo-*-api...azurecontainerapps.io`.
   - No hay error CORS.
7. Confirmar backend health:

```powershell
curl.exe https://posfifo-dev-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io/api/v1/health/
curl.exe https://posfifo-staging-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io/api/v1/health/
```

## Troubleshooting

### La app abre, pero login falla por CORS

Sintoma en navegador:

```text
Access to XMLHttpRequest ... has been blocked by CORS policy
```

Solucion:

- copiar el origen exacto de ASWA, sin slash final,
- agregarlo a `api_cors_allowed_origins`,
- `terraform apply`,
- esperar nueva revision de Container App.

### La app abre, pero apunta al API incorrecto

Sintoma:

- DevTools muestra requests hacia `api.example.com`,
- o hacia local,
- o hacia el ambiente equivocado.

Solucion:

- revisar el workflow ASWA,
- confirmar `VITE_API_URL` en el env del paso que ejecuta build/deploy,
- redeployar.

### Refrescar `/dashboard` o `/cuentas` devuelve 404

El hosting no esta aplicando SPA routing.

Verificar que `staticwebapp.config.json` este en la raiz del frontend y que el
workflow use:

```yaml
app_location: "/"
output_location: "dist"
```

### Build falla por version de Node

Verificar que `package.json` tenga `engines` y que el log del workflow no use
una version de Node menor a `20.19`.

## Fuentes oficiales

- Build configuration ASWA:
  https://learn.microsoft.com/en-us/azure/static-web-apps/build-configuration
- Application settings ASWA:
  https://learn.microsoft.com/en-us/azure/static-web-apps/application-settings
- Runtimes soportados / `package.json engines`:
  https://learn.microsoft.com/en-us/azure/static-web-apps/languages-runtimes
- GitHub Actions para ASWA:
  https://docs.github.com/en/actions/how-tos/deploy/deploy-to-third-party-platforms/azure-static-web-app
