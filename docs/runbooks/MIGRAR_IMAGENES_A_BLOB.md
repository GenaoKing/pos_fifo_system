# Runbook — Subir las imágenes de un cliente a Blob Storage

Cuando un tenant se importó desde un dump, sus productos traen la **ruta** de la
imagen pero el **archivo** se quedó en la PC del cliente. El portal muestra
imágenes rotas hasta que se suben.

> **Estado medido el 2026-08-20 (Royal Plast):** 73 productos con imagen + el
> logo = **74 archivos faltantes**. El container `media-public` tenía un solo
> archivo (la prueba de humo de junio).
>
> **Ejecutado el 2026-08-23 (Royal Plast):** `uploaded: 73  updated: 73
> missing: 1  already_prefixed: 0  skipped: 0` — 237 MB, promedio 3.2 MB por
> imagen. El container pasó de 1 a 75 blobs. El `missing: 1` era el logo, y se
> resolvió aparte (ver más abajo). Estado final: **`already_prefixed: 74,
> missing: 0`**, con reconciliación blob↔BD sin diferencias en ninguna dirección.

---

## Lo que enseñó la corrida real de Royal Plast

**El zip vino sin la carpeta contenedora.** Lo recibido eran 306 `.jpg` sueltos
en la raíz, no un `media\` con sus subcarpetas. El comando busca
`<source-media-root>/productos/<archivo>`, así que hubo que extraer *dentro* de
una carpeta `productos`:

```bat
mkdir C:\temp\media_royalplast\productos
:: extraer el zip AHI dentro, no en media_royalplast
```

Si se extrae un nivel más arriba, el dry-run reporta `missing: 73` y parece que
faltan los archivos cuando en realidad están.

**Sobran archivos y no importa.** El zip traía 306 imágenes; la BD referencia
73. El comando sube solo lo referenciado — las otras 233 son huérfanas de
ediciones anteriores. No hay que depurar nada antes de correrlo.

**El logo ya estaba en Azure, en la ruta equivocada.** El único archivo que el
container tenía desde junio se llamaba `royalplast/productos/_smoke-logo-royal.jpeg`
— la prueba de humo había subido el logo real con un nombre de prueba. Al
reconciliar blobs contra BD apareció como "blob que ningún producto referencia",
y resultó ser justo el archivo que faltaba. Se copió a
`royalplast/config/logo-royal.jpeg` y se repuntó la BD, sin volver a la PC del
cliente.

> **Antes de pedir un archivo faltante, reconciliar.** El blob huérfano de hoy
> puede ser el archivo perdido de mañana:
>
> ```bash
> az storage blob list --account-name <cuenta> --container-name media-public --auth-mode login --query "[].name" -o tsv | sort > blobs.txt
> psql -t -A -d tnt_<tenant> -c "SELECT imagen FROM productos WHERE imagen<>'';" | sort > db.txt
> comm -23 blobs.txt db.txt   # blobs sin producto  <-- aqui aparecio el logo
> comm -13 blobs.txt db.txt   # productos sin blob  <-- debe quedar vacio
> ```

**El logo va en `media\config\`, no en `media\productos\`.** Es el archivo que
más se olvida porque vive fuera de la carpeta obvia. Al pedir el zip, pedir
**`media` completa**, no `media\productos`.

**Ritmo real:** ~5 s por imagen (≈3 MB cada una) sobre Starlink. 73 archivos =
unos 6 minutos. Conviene lanzarlo en segundo plano y no bloquear la terminal.

**Ruido en el log:** con `settings_cloud` el SDK de Azure loguea cada request
HTTP en INFO, así que la salida es enorme y las líneas `OK ...` quedan
sepultadas. Para seguir el avance:

```bash
grep -c 'Response status: 201' subida.log   # blobs creados
```

---

## Lo importante: casi todo se hace en remoto

Solo hay **un** paso que exige tocar la PC del cliente: **copiar la carpeta
`media`**. Todo lo demás —subida a Azure y actualización de la BD— corre desde
tu laptop.

```
   PC del cliente                    Tu laptop                    Azure
  ┌──────────────┐   copiar        ┌──────────────┐   subir     ┌────────────┐
  │ media\       │ ──carpeta────►  │ media_<cli>\ │ ──────────► │ Blob       │
  │  productos\  │  (una vez)      │              │             │ + BD tenant│
  │  config\     │                 └──────────────┘             └────────────┘
```

Y ese paso **no requiere que vayas**: cualquier persona en el negocio puede
comprimir la carpeta y mandártela. Ver el paso 1B.

---

## 1. Obtener la carpeta `media` del cliente

### 1A. Si estás en el sitio

```bat
xcopy /E /I C:\pos_fifo_system\media C:\temp\media_royalplast
```

Copiar esa carpeta a un USB o subirla a Drive.

### 1B. Si NO estás en el sitio (recomendado)

Pedirle a alguien del negocio que haga esto — no requiere conocimiento técnico:

1. Abrir la carpeta `C:\pos_fifo_system`
2. Click derecho sobre la carpeta `media` → **Enviar a** → **Carpeta comprimida**
3. Mandar el `media.zip` por WhatsApp, correo o Drive

> Si el archivo pesa demasiado para WhatsApp, que lo suban a Google Drive y
> compartan el enlace. Suelen ser pocos MB.

**Verificar lo recibido:** al descomprimir debe existir `media\productos\` con
archivos `.jpg` / `.png` / `.webp` adentro.

```bat
dir C:\temp\media_royalplast\productos
```

---

## 2. Ver qué haría, sin tocar nada

Desde tu laptop, con `az` logueado y el entorno `pos_fifo` activo.

**Dependencias:** el entorno local no las trae (solo la imagen del cloud las
instala). Sin esto, `settings_cloud` aborta con
`azure-identity must be installed when AZURE_BLOB_MEDIA_ENABLED=true`:

```bash
pip install "django-storages[azure]" "azure-identity"
```

**Variables de entorno** — las mismas que usa el Container App de prod, salvo
`AZURE_CLIENT_ID`, que **no** se define: en la laptop `DefaultAzureCredential`
debe usar tu sesión de `az login`, no la identidad administrada.

```bash
export AZURE_BLOB_MEDIA_ENABLED=true
export AZURE_STORAGE_ACCOUNT_NAME=posfifoprodmedia
export AZURE_STORAGE_MEDIA_CONTAINER=media-public
```


```bash
python manage.py migrar_media_tenant \
  --tenant royalplast \
  --source-media-root C:\temp\media_royalplast \
  --dry-run \
  --settings=<settings que apunte al control plane de prod>
```

**Salida esperada** — cada línea muestra el origen y su destino con prefijo de
tenant:

```
productos/image_kxAuWdp.jpg -> royalplast/productos/image_kxAuWdp.jpg
config/logo-royal.jpeg      -> royalplast/config/logo-royal.jpeg
uploaded: 0
updated: 0
missing: 0          <-- CLAVE: debe ser 0
already_prefixed: 0
skipped: 0
```

> **`missing` es el número que importa.** Si es mayor que 0, la carpeta que
> copiaste no tiene esos archivos. Corriendo el dry-run **sin** la carpeta del
> cliente, Royal Plast daba `missing: 74` — que fue exactamente cómo se detectó
> el problema.

Las credenciales de la BD de prod se sacan del Key Vault; ver
`docs/runbooks/PRUEBAS_SYNC_LOCAL.md` §5 para el procedimiento y el manejo del
firewall.

---

## 3. Aplicar

```bash
python manage.py migrar_media_tenant \
  --tenant royalplast \
  --source-media-root C:\temp\media_royalplast \
  --apply \
  --settings=<mismo settings>
```

El comando, por cada imagen:

1. Calcula el destino con el prefijo del tenant (`royalplast/productos/...`).
2. Sube el archivo al container `media-public`.
3. Actualiza el campo en la BD del tenant para que apunte a la ruta nueva.
4. **Genera la miniatura** en `royalplast/productos/thumbs/`, leyéndola del
   archivo local que ya tiene abierto — no del blob recién subido, que
   duplicaría el tráfico de toda la migración.

> Para un tenant cuya media ya se migró **antes** de que existieran las
> miniaturas, correr después:
> `python manage.py generar_miniaturas --tenant <key> --apply`
>
> Si **todas** fallan con `ResourceNotFoundError: The specified blob does not
> exist`, ese tenant nunca tuvo su media en Blob: sus rutas siguen crudas
> (`productos/foo.jpg` en vez de `<tenant>/productos/foo.jpg`). No es un
> problema de las miniaturas — el portal ya mostraba las imágenes rotas. Se
> arregla corriendo primero esta migración de media. Le pasa a
> `royalplastdemo`, medido el 2026-08-24.

Es **idempotente**: lo ya migrado se cuenta como `already_prefixed` y se salta.
Se puede correr de nuevo sin miedo.

**Salida esperada:** `uploaded` y `updated` iguales a la cantidad de archivos, y
`missing: 0`.

---

## 4. Verificar

**Los archivos están en Azure:**

```bash
az storage blob list --account-name posfifoprodmedia \
  --container-name media-public --auth-mode login \
  --query "length(@)" -o tsv
```

Debe pasar de 1 a ~75.

**La BD apunta a las rutas nuevas:**

```sql
SELECT imagen FROM productos WHERE imagen <> '' LIMIT 3;
```

Deben empezar con `royalplast/`.

**El portal las muestra:** entrar a
`https://red-bay-07331a710.7.azurestaticapps.net`, ir a Productos, y confirmar
que las miniaturas cargan.

---

## Por qué pasa esto

`upload_to` prefija con el tenant **solo las subidas nuevas**
(`apps/tenancy/media.py:tenant_media_name`). Un tenant creado por `pg_restore`
llega con las rutas crudas de la instalación local (`productos/xxx.jpg`) y sin
ningún archivo, porque un dump de PostgreSQL **no incluye media**.

Por eso todo import de un cliente existente necesita este paso. Está anotado
como parte del onboarding en `docs/ROADMAP_TENANCY_DBPERTENANT.md`.

---

## Tabla de fallos

| Síntoma | Causa | Arreglo |
|---|---|---|
| `missing` mayor que 0 | La carpeta copiada no tiene esos archivos | Verificar que se copió `media` completa, con su subcarpeta `productos` |
| `El directorio source-media-root no existe` | Ruta mal escrita o carpeta sin descomprimir | Usar ruta absoluta y confirmar con `dir` |
| `already_prefixed` igual al total | Ya se migró antes | No hay nada que hacer; está listo |
| Sube pero el portal sigue mostrando roto | El container no es público o falta el CDN | Verificar el acceso público del container en el Storage Account |
| `Tenant no encontrado` | El `--tenant` no coincide con `tenancy_tenants.tenant_key` | Listar los tenants del control plane y usar la clave exacta |

---

## Referencias

- Comando: `apps/tenancy/management/commands/migrar_media_tenant.py`
- Prefijo por tenant: `apps/tenancy/media.py`
- Credenciales y firewall de prod: `docs/runbooks/PRUEBAS_SYNC_LOCAL.md` §5
- Diseño de media por tenant: `docs/runbooks/AZURE_BLOB_MEDIA.md`
