# Docker backend Django para Azure

Guia practica para D1. La idea es producir una imagen Linux reproducible del
backend cloud, lista para correr localmente y luego subir a Azure Container
Apps.

## Modelo mental si vienes de on-prem

- **Imagen Docker**: parecida a una plantilla dorada de VM, pero mucho mas
  pequena. Incluye Python, dependencias, codigo y static files ya recolectados.
- **Contenedor**: una instancia efimera de esa imagen. Si se borra, no deberia
  perder nada importante. La DB, archivos de cliente y secretos viven fuera.
- **Variables de entorno**: equivalen al archivo de configuracion del ambiente.
  La misma imagen sirve para dev/staging/prod cambiando env vars.
- **Logs stdout/stderr**: en vez de escribir logs a disco, el proceso imprime.
  Docker, Azure Container Apps y Log Analytics capturan esa salida.
- **Migraciones**: no corren al arrancar el contenedor. Se ejecutan como paso
  explicito de pipeline o Container Apps Job.

## Archivos creados

- `Dockerfile`: receta de la imagen backend.
- `.dockerignore`: evita copiar cache, logs, media, secretos y `node_modules`.
- `requirements_cloud.txt`: dependencias Linux/cloud, sin librerias Windows de
  impresoras.
- `deploy/env_cloud.example`: plantilla de variables minimas sin secretos reales.

## Preparar variables locales

Copiar la plantilla y completar valores reales locales:

```powershell
Copy-Item deploy\env_cloud.example deploy\env_cloud.local
notepad deploy\env_cloud.local
```

Para PostgreSQL corriendo en Windows desde Docker Desktop, usa:

```text
DB_HOST=host.docker.internal
DB_PORT=5432
DB_SSLMODE=disable
SECURE_SSL_REDIRECT=false
```

`SECURE_SSL_REDIRECT=false` es solo para Docker local por HTTP. Si queda en
`true`, Django responde `301` hacia `https://localhost:8000/...`; Gunicorn no
sirve TLS dentro del contenedor y el navegador/curl parecen quedarse sin
respuesta util.

Si usas Azure PostgreSQL dev, cambia `DB_HOST`, `DB_USER`, `DB_PASSWORD` y deja:

```text
DB_SSLMODE=require
```

## Build

```powershell
docker build -t pos-fifo-backend:dev .
```

Que hace el build:

1. Usa `python:3.12-slim-bookworm`.
2. Instala `requirements_cloud.txt`.
3. Copia el repo.
4. Ejecuta `collectstatic` con variables dummy de build.
5. Deja como comando final `gunicorn config.wsgi:application`.

El build no conecta a la DB y no corre migraciones.

## Run

```powershell
docker run --rm --name pos-fifo-backend-dev `
  -p 8000:8000 `
  --env-file deploy\env_cloud.local `
  pos-fifo-backend:dev
```

En otra terminal:

```powershell
curl http://localhost:8000/api/v1/health/
```

Respuesta esperada con DB disponible:

```json
{
  "status": "ok",
  "db": "ok",
  "version": "local-docker",
  "commit": "local",
  "environment": "dev",
  "timestamp": "..."
}
```

Si la app responde pero la DB no esta disponible, health devuelve HTTP `503`
con `status: degraded` y `db: error`.

## Static files

WhiteNoise sirve los archivos recolectados dentro de la imagen. Probar una URL
real:

```powershell
curl -I http://localhost:8000/static/js/pos/punto_venta.js
```

Debe responder `200` si el archivo existe en `static/` y fue recolectado.

## Logs

Ver logs del contenedor:

```powershell
docker logs pos-fifo-backend-dev
```

Gunicorn escribe access/error logs a stdout/stderr. Esa es la misma forma en que
Azure Container Apps los captura.

## Parar y limpiar

Si corriste sin `--rm`:

```powershell
docker stop pos-fifo-backend-dev
docker rm pos-fifo-backend-dev
```

Listar imagenes:

```powershell
docker images pos-fifo-backend
```

Eliminar imagen local:

```powershell
docker rmi pos-fifo-backend:dev
```

## Migraciones

No ejecutar migraciones en el `CMD` del contenedor. Para local se pueden correr
manual, usando la misma imagen:

```powershell
docker run --rm --env-file deploy\env_cloud.local pos-fifo-backend:dev `
  python manage.py migrate --settings=config.settings_cloud
```

En Azure, esto debe convertirse en Container Apps Job o paso explicito de CI/CD.

## Troubleshooting

- **`Access is denied` leyendo `C:\Users\...\docker\config.json`**: Docker puede
  mostrar ese warning si el archivo de config local tiene permisos raros. Solo
  bloquea D1 si `docker build` o `docker run` fallan.
- **`health` devuelve `503`**: el contenedor arranco, pero no llega a la DB.
  Revisar `DB_HOST`, firewall, usuario/password, puerto y `DB_SSLMODE`.
- **`health` devuelve `301` hacia HTTPS**: falta `SECURE_SSL_REDIRECT=false` en
  `deploy/env_cloud.local` para pruebas locales por HTTP. En Azure/prod se usa
  HTTPS real y puede quedar en `true`.
- **`ModuleNotFoundError: win32print`**: no deberia pasar en cloud; el driver
  Zebra ahora tolera Linux. Si aparece, buscar otro import Windows top-level.
- **Static devuelve 404**: confirmar que el archivo existe en `static/` y que el
  build corrio `collectstatic`.
- **Cambios de codigo no aparecen**: reconstruir imagen. Un contenedor no toma
  codigo nuevo automaticamente.

## Variables minimas

```text
DJANGO_SETTINGS_MODULE=config.settings_cloud
DJANGO_SECRET_KEY=...
ALLOWED_HOSTS=...
DB_NAME=...
DB_USER=...
DB_PASSWORD=...
DB_HOST=...
DB_PORT=5432
DB_SSLMODE=require|disable
CORS_ALLOWED_ORIGINS=...
SECURE_SSL_REDIRECT=false   # solo local Docker por HTTP
CLOUD_ENVIRONMENT=dev
APP_VERSION=...
GIT_COMMIT_SHA=...
```
