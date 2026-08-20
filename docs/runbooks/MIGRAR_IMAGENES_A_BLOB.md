# Runbook — Subir las imágenes de un cliente a Blob Storage

Cuando un tenant se importó desde un dump, sus productos traen la **ruta** de la
imagen pero el **archivo** se quedó en la PC del cliente. El portal muestra
imágenes rotas hasta que se suben.

> **Estado medido el 2026-08-20 (Royal Plast):** 73 productos con imagen + el
> logo = **74 archivos faltantes**. El container `media-public` tenía un solo
> archivo (la prueba de humo de junio).

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

Desde tu laptop, con `az` logueado y el entorno `pos_fifo` activo:

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
