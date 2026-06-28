# Runbook — Import Royal Plast a DB-per-tenant

Estado: runbook operativo para dry-run prod descartable. Fecha: 2026-06-18.

## Estado Actual 2026-06-19

- Dry-run prod descartable cerrado correctamente y luego limpiado:
  `royalplastdryrun`/`tnt_royalplastdryrun` ya no son parte del control plane.
- Prod esta vivo contra `pos_fifo_prod`; `/api/v1/health/` responde `ok`.
- El control plane prod solo tiene `demo`; el tenant real `royalplast` todavia
  no existe.
- CI/CD prod ya tiene identidad OIDC y GitHub `PROD_*`; el deploy de prod es
  manual desde branch `main`.
- La API prod corre una imagen anterior. Antes del cutover real conviene
  promover el codigo aprobado a `main` y ejecutar el workflow prod manual.
- Media publica prod aplicada: Storage Account `posfifoprodmedia`, container
  `media-public`, API/job con `AZURE_BLOB_MEDIA_ENABLED=true`. Smoke directo
  validado con `royalplast/productos/_smoke-logo-royal.jpeg` respondiendo HTTP
  200 como `image/jpeg`.

Siguiente gate para cutover real:

1. Tener dump fresco final de Royal Plast.
2. Desplegar a prod la version aprobada del codigo.
3. Crear/restaurar `tnt_royalplast`.
4. Registrar `Tenant royalplast` en `pos_fifo_prod`.
5. Ejecutar `migrate_tenants` y `normalizar_import_tenant`.
6. Importar imagenes reales a Blob y validar `imagen_url` en portal.
7. Validar totales, login, sync token e imagenes antes de activar sync.

Objetivo: validar el dump real de Royal Plast contra el contrato DB-per-tenant
sin tocar produccion ni activar sync. El primer ensayo debe restaurar en una BD
temporal, comparar totales y documentar diferencias antes de promover el flujo a
Fase 4.

Autorizacion operativa: Royal Plast autorizo inspeccionar y manipular los datos
del dump en un ambiente de prueba. Aun asi, los documentos deben registrar
conteos/agregados y no copiar datos sensibles puntuales salvo que sean necesarios
para diagnostico.

## Responsabilidades

**Santiago**

- Indicar ruta local del dump y formato (`custom`, `plain sql`, `tar`, comprimido).
- Confirmar si el dump fue tomado con la misma version/commit de migraciones que
  este repo, o al menos la fecha exacta del codigo instalado.
- Proveer totales esperados desde Royal Plast local:
  - cantidad de categorias, productos activos/inactivos y clientes;
  - total de ventas historicas;
  - total vendido bruto/neto segun reporte usado por el dueno;
  - inventario valorizado y/o unidades por SKU criticos;
  - CxC abierta/parcial/vencida y saldo total;
  - cantidad de usuarios/cajeras y sucursales.
- Confirmar ventana donde se podria rotar `DJANGO_SECRET_KEY` y encender sync
  mas adelante. No se hace en este dry-run.

**Codex**

- Restaurar el dump en una BD temporal.
- Ejecutar migraciones si aplica y detectar drift de schema.
- Crear/validar control plane `Tenant`, `Identity`, `Membership` y `SyncToken`.
- Ejecutar queries de validacion y documentar diferencias.
- Dejar comandos reproducibles para repetir el import.

## Preflight

No usar `tnt_royalplast` en el primer ensayo. Usar una BD descartable:

```powershell
$env:TENANCY_DB_PER_TENANT_ENABLED='true'
$env:DJANGO_SETTINGS_MODULE='config.settings_development'
```

Para Royal Plast no se usa `bootstrap_tenant` como primer paso. Ese comando es
el camino clean para tenants nuevos (`demo`, SK futuro). RP trae data historica,
asi que el camino aprobado es: crear BD descartable, restaurar dump completo,
registrar `Tenant`, correr `migrate_tenants` y luego `normalizar_import_tenant`.

## Identificar Formato Del Dump

```powershell
pg_restore --list "RUTA\royal_plast.dump"
```

- Si lista objetos: formato custom/tar apto para `pg_restore`.
- Si falla y el archivo abre como SQL: usar `psql -f`.
- Si esta comprimido (`.gz`, `.zip`), extraer a una carpeta temporal fuera del
  repo o a una ruta ignorada.

## Camino A — Dump Custom/Tar Completo

Restaurar en BD temporal nueva:

```powershell
dropdb -h localhost -U pos_user --if-exists tnt_royalplast_import_test
createdb -h localhost -U pos_user tnt_royalplast_import_test
pg_restore -h localhost -U pos_user -d tnt_royalplast_import_test `
  --clean --if-exists --no-owner --no-privileges `
  "RUTA\royal_plast.dump"
```

Luego registrar temporalmente esa BD como tenant si se quiere validarla desde
Django:

```powershell
C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py shell --settings=config.settings_development
```

```python
from apps.tenancy.models import Tenant
Tenant.objects.update_or_create(
    tenant_key='royalplastimport',
    defaults={
        'slug': 'royal-plast-import',
        'nombre': 'Royal Plast Import Test',
        'db_name': 'tnt_royalplast_import_test',
        'media_prefix': 'royalplastimport/',
        'activo': True,
    },
)
```

## Camino B — Data-Only Sobre BD Migrada (no usado para RP actual)

Usar si el dump viene de la misma version de schema o si queremos que Django cree
primero las tablas. No es el camino elegido para el dump actual de Royal Plast,
porque el dry-run validado fue restore completo + migraciones. Para RP no crear
la BD con `bootstrap_tenant` antes del restore.

```powershell
pg_restore -h localhost -U pos_user -d tnt_royalplastimport `
  --data-only --disable-triggers --no-owner --no-privileges `
  "RUTA\royal_plast.dump"
```

Riesgo: si el dump incluye secuencias o datos ya sembrados por migrations, puede
haber conflictos. En ese caso se documenta y se decide si conviene restore
completo + migraciones o un proceso de transformacion.

## Validaciones Django

Ejecutar con tenant activo:

```powershell
$env:TENANCY_DB_PER_TENANT_ENABLED='true'
C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py shell --settings=config.settings_development
```

```python
from apps.tenancy.context import force_tenancy, tenant_context
from apps.tenancy.models import Tenant

with force_tenancy(True):
    tenant = Tenant.objects.get(tenant_key='royalplastimport')
    with tenant_context(tenant):
        from apps.negocios.models import Negocio
        from apps.sucursales.models import Sucursal
        from apps.usuarios.models import Usuario
        from apps.productos.models import Categoria, Producto
        from apps.clientes.models import Cliente
        from apps.ventas.models import Venta
        from apps.cuentas_por_cobrar.models import CuentaPorCobrar

        print('negocios', Negocio.objects.count())
        print('sucursales', list(Sucursal.objects.values_list('codigo', 'nombre')))
        print('usuarios', Usuario.objects.count())
        print('categorias', Categoria.objects.count())
        print('productos', Producto.objects.count())
        print('clientes', Cliente.objects.count())
        print('ventas', Venta.objects.count())
        print('cxc', CuentaPorCobrar.objects.count())
```

Validar login/control plane:

```powershell
$env:TENANCY_DB_PER_TENANT_ENABLED='true'
C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py migrate_tenants `
  --tenant royalplastimport `
  --settings=config.settings_development `
  --noinput
```

## Camino Prod Descartable — Dump Actual

Este es el camino aprobado para probar contra el PostgreSQL platform/prod con el
dump actual, sin tocar el tenant final `royalplast`.

Contrato:

```text
tenant_key: royalplastdryrun
db_name: tnt_royalplastdryrun
slug: royal-plast-dryrun
media_prefix: royalplastdryrun/
sucursal_codigo: 01
admin_email: storibio57+dryrun@gmail.com
```

Preparar variables locales contra prod leyendo secretos desde Key Vault, no desde
archivos planos:

```powershell
$env:TENANCY_DB_PER_TENANT_ENABLED='true'
$env:DJANGO_SETTINGS_MODULE='config.settings_cloud'
$env:DB_NAME='pos_fifo_prod'
$env:DB_SSLMODE='require'
$env:ALLOWED_HOSTS='localhost,127.0.0.1'
$env:DB_HOST='<platform-postgres-fqdn>'
$env:DB_USER='<keyvault:db-user>'
$env:DB_PASSWORD='<keyvault:db-password>'
$env:DJANGO_SECRET_KEY='<keyvault:django-secret-key>'
```

Restaurar y registrar el tenant descartable:

```powershell
dropdb -h $env:DB_HOST -U $env:DB_USER --if-exists tnt_royalplastdryrun
createdb -h $env:DB_HOST -U $env:DB_USER tnt_royalplastdryrun
pg_restore -h $env:DB_HOST -U $env:DB_USER -d tnt_royalplastdryrun `
  --clean --if-exists --no-owner --no-privileges `
  docs\dumps\royal_backup.dump
```

```powershell
C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py shell --settings=config.settings_cloud
```

```python
from apps.tenancy.models import Tenant
Tenant.objects.update_or_create(
    tenant_key='royalplastdryrun',
    defaults={
        'slug': 'royal-plast-dryrun',
        'nombre': 'Royal Plast Dry Run',
        'db_name': 'tnt_royalplastdryrun',
        'media_prefix': 'royalplastdryrun/',
        'activo': True,
    },
)
```

Migrar y normalizar:

```powershell
C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py migrate_tenants `
  --tenant royalplastdryrun `
  --settings=config.settings_cloud `
  --noinput

C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py normalizar_import_tenant `
  --tenant royalplastdryrun `
  --nombre "Royal Plast EIRL" `
  --slug royal-plast-dryrun `
  --sucursal-codigo 01 `
  --sucursal-nombre "Royal Plast - Principal" `
  --admin-email storibio57+dryrun@gmail.com `
  --admin-password "<GENERAR_PASSWORD_DRYRUN>" `
  --show-sync-token `
  --settings=config.settings_cloud
```

Gate del dry-run prod:

- Restore, migraciones y normalizacion terminan sin errores.
- Ventas, compras y lotes quedan con `sucursal_id` asignado.
- Login con `storibio57+dryrun@gmail.com` devuelve `tenant_id=royalplastdryrun`.
- Token sync dry-run lee maestros; `demo` no ve datos de RP.
- Totales agregados quedan documentados. El dump actual debe rondar los 440k
  pesos en ventas; el valor exacto se registra al cierre.

## Checklist De Aceptacion

- El restore termina sin errores no explicados.
- `migrate_tenants --tenant <tenant_key_descartable>` no deja migraciones pendientes.
- Existe exactamente un `Negocio` self-row o se define el arreglo necesario.
- Todas las `Sucursal` tienen `negocio_id`.
- Usuarios operativos existen y las ventas conservan FK a usuario.
- Ventas, caja, inventario y CxC cuadran contra los totales dados por Santiago.
- El token de sync queda registrado en `SyncToken`, pero `SYNC_ENABLED` sigue
  apagado.
- No se activa Royal Plast cloud real hasta cerrar diferencias.

## Decisiones Que Saldran Del Dry-Run

- Restore completo vs data-only sobre BD migrada.
- Si hace falta script de normalizacion post-restore para self-row `Negocio`.
- Si hay drift de migraciones entre la instalacion local de Royal Plast y este
  repo.
- Estrategia para imagenes/media: copiar rutas existentes, migrar a Blob en Fase
  2 o dejar placeholder hasta validar catalogo.

## Dry-Run 2026-06-16 — `docs/dumps/royal_backup.dump`

Archivo inspeccionado:

```text
docs/dumps/royal_backup.dump
Formato: PostgreSQL custom dump, gzip
Origen: royal_plastic_pos
Dump creado: 2026-06-13 14:30:54
PostgreSQL origen: 15.17
```

Hallazgo de schema:

- El dump es anterior al contrato actual: no trae `negocios`, `sucursales`,
  `permisos`, `suscripciones`, `sync`, `tenancy`, ni tablas de CxC.
- Restaurar completo y luego correr `migrate_tenants` funciono sobre una BD
  temporal.
- Despues de migrar, las migraciones crean `Negocio` y asignan `Usuario.negocio`,
  pero no crean `Sucursal`; por eso ventas/compras/lotes quedan con
  `sucursal_id=NULL` hasta normalizar.

Comandos ejecutados:

```powershell
$env:PGPASSWORD='Prueba123'
dropdb -h localhost -U pos_user --if-exists tnt_royalplast_import_test
createdb -h localhost -U pos_user tnt_royalplast_import_test
pg_restore -h localhost -U pos_user -d tnt_royalplast_import_test `
  --clean --if-exists --no-owner --no-privileges `
  docs\dumps\royal_backup.dump
```

Registro temporal en control plane:

```python
from apps.tenancy.models import Tenant
Tenant.objects.update_or_create(
    tenant_key='royalplastimport',
    defaults={
        'slug': 'royal-plast-import',
        'nombre': 'Royal Plast Import Test',
        'db_name': 'tnt_royalplast_import_test',
        'media_prefix': 'royalplastimport/',
        'activo': True,
    },
)
```

Migracion y normalizacion:

```powershell
C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py migrate_tenants `
  --tenant royalplastimport `
  --settings=config.settings_development `
  --noinput

C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py normalizar_import_tenant `
  --tenant royalplastimport `
  --nombre "Royal Plast EIRL" `
  --slug royal-plast `
  --sucursal-codigo RP-001 `
  --sucursal-nombre "Royal Plast - Principal" `
  --admin-email admin@royalplast.local `
  --admin-password Admin123! `
  --settings=config.settings_development
```

Resultado despues de normalizar:

| Metrica | Valor |
| --- | ---: |
| Negocios | 1 |
| Sucursales | 1 (`RP-001`) |
| Usuarios | 3 (2 originales + usuario de servicio sync) |
| Categorias | 20 |
| Productos | 273 |
| Clientes | 3 |
| Ventas | 320 |
| Ventas completadas | 320 |
| Total ventas | 447,530.00 |
| Pagos efectivo | 320 / 447,530.00 |
| Compras | 4 |
| Lotes | 4 |
| Unidades actuales en lotes | 387 |
| Valor costo en lotes | 86,555.00 |
| Ventas sin sucursal | 0 |
| Compras sin sucursal | 0 |
| Lotes sin sucursal | 0 |

Smokes:

- Login `admin@royalplast.local` / `Admin123!`: HTTP 200,
  `tenant_id=royalplastimport`, usuario operativo `Santiago`.
- Token de sync `RP-001`: `/api/v1/maestros/productos/` devuelve HTTP 200 y
  `count=273`.

Pendiente de decision:

- Decision 2026-06-18: el codigo real de sucursal para el import es `01`.
- Decision 2026-06-18: el email real del dueno/admin para el cutover final es
  `storibio57@gmail.com`; el dry-run prod usa `storibio57+dryrun@gmail.com`.
- Confirmado por Santiago: los totales 320 ventas y 447,530.00 cuadran con este
  dump del 2026-06-13.
- Antes del go-live vendra un dump mas fresco; este dry-run valida el camino
  tecnico, no es el dataset final para produccion.

## Dry-Run Prod Descartable 2026-06-18 — `royalplastdryrun`

Ambiente: PostgreSQL platform/prod `posfifoplatformpg`, control plane
`pos_fifo_prod`, tenant descartable `royalplastdryrun` en BD
`tnt_royalplastdryrun`.

Comandos efectivos:

1. Cleanup de `Tenant royalplastdryrun` si existia.
2. `dropdb --if-exists --force tnt_royalplastdryrun`.
3. `createdb tnt_royalplastdryrun`.
4. `pg_restore --clean --if-exists --no-owner --no-privileges docs\dumps\royal_backup.dump`.
5. Registrar `Tenant royalplastdryrun` con slug `royal-plast-dryrun`.
6. `migrate_tenants --tenant royalplastdryrun --settings=config.settings_cloud --noinput`.
7. `normalizar_import_tenant --tenant royalplastdryrun --sucursal-codigo 01 --admin-email storibio57+dryrun@gmail.com`.

Resultado despues de normalizar en prod:

| Metrica | Valor |
| --- | ---: |
| Categorias | 20 |
| Productos | 273 |
| Clientes | 3 |
| Usuarios | 3 |
| Ventas | 320 |
| Ventas completadas | 320 |
| Total ventas | 447,530.00 |
| Compras | 4 |
| Lotes | 4 |
| Unidades actuales en lotes | 387 |
| Valor costo en lotes | 86,555.00 |
| CxC | 0 |
| Ventas sin sucursal | 0 |
| Compras sin sucursal | 0 |
| Lotes sin sucursal | 0 |

Smokes prod:

- Membership control-plane: `storibio57+dryrun@gmail.com` -> `Santiago` / `ADMIN`.
- Sucursal token registrado para sucursal `01`.
- Login API prod con el email dry-run: HTTP 200.
- `/api/v1/auth/me/`: `tenant_key=royalplastdryrun`, `username=Santiago`.
- Token sync dry-run: `/api/v1/maestros/productos/` devuelve `count=273`.
- Aislamiento: `tnt_demo` tiene `productos=0`; no contiene los 273 productos RP.

Cierre:

- Cerrado 2026-06-18: se borraron `Tenant royalplastdryrun`,
  `tnt_royalplastdryrun` y la Identity dry-run si quedaba sin memberships.
- Se creo y luego se elimino la regla temporal de firewall
  `operator-rp-dryrun-186-7-5-23`.
