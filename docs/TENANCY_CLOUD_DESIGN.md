# Tenancy cloud design

Estado: historico / reemplazado.

Fecha: 2026-06-14

> Decision actual 2026-06-16: la arquitectura objetivo ya no es
> `django-tenants`/schema-per-tenant. La fuente viva es
> `docs/TENANCY_DB_PER_TENANT.md`, con una base PostgreSQL por tenant sobre un
> servidor compartido y un control plane global. Este documento queda como
> registro historico de la exploracion inicial.

Este documento define el contrato objetivo para operar POS FIFO como SaaS
multi-cliente usando PostgreSQL schema-per-tenant. Nace despues del piloto de
Royal Plast en staging, donde se valido gran parte del deploy cloud pero tambien
quedaron expuestos problemas de onboarding manual, sync diferido, media sin
storage cloud final y riesgos de datos compartidos.

## Objetivo

- Cada empresa cliente es un tenant.
- Cada tenant vive en su propio schema PostgreSQL.
- Cada tenant puede tener una o varias sucursales.
- El portal cloud, la API y el sync local siempre resuelven un tenant antes de
  leer o escribir datos operativos.
- El modelo debe soportar onboarding desde:
  - una instalacion local existente;
  - un clean install cloud/local desde cero.

## Decisiones cerradas

### Identidad del tenant

- Un tenant representa una empresa legal/comercial completa.
- Todas las sucursales de una empresa viven dentro del mismo tenant.
- No se soporta, por ahora, una misma empresa con multiples RNC dentro del mismo
  tenant.
- El identificador visible sera comercial, por ejemplo `royal-plast` o
  `sk-performance`.
- El nombre comercial puede cambiar despues. El `schema_name` debe tratarse como
  identificador tecnico estable y no cambiarse sin migracion controlada.

### Dominios y acceso

- El portal usara subdominio por tenant.

```text
royalplast.posfifo.com
skperformance.posfifo.com
```

- No se usara selector publico de negocios en el login para evitar exponer la
  lista de clientes.
- Debe existir un usuario global/SYSADMIN para soporte y administracion central.
- En el MVP, un usuario operativo pertenece a un unico tenant.
- Si en el futuro un mismo dueno tiene varias empresas, se resolvera con usuario
  global/multi-tenant o cuentas separadas por tenant. No bloquear el diseno por
  ese caso ahora.
- El sync POS local debe resolver tenant por token y por destino. El token debe
  estar amarrado a tenant + sucursal.

### Datos por tenant

Los datos operativos viven dentro del schema del tenant:

- productos;
- categorias;
- clientes;
- ventas;
- inventario, lotes y movimientos;
- cuentas por cobrar;
- sucursales;
- usuarios operativos del negocio;
- roles, asignaciones y permisos efectivos del negocio;
- configuracion del negocio/sucursal;
- emisores e-CF y configuracion fiscal;
- auditoria operativa;
- eventos/logs de sync;
- reportes derivados cuando se guarden.

Los datos globales viven en `public`:

- tabla de tenants;
- dominios/subdominios;
- usuario(s) globales de soporte;
- catalogo base de permisos;
- catalogo de modulos;
- planes comerciales;
- metadata SaaS/global;
- configuracion de infraestructura o billing futuro.

La suscripcion activa del tenant puede vivir globalmente si se quiere gestionar
desde el panel global, pero el runtime debe poder leerla desde el tenant actual.
Decision pendiente: tabla global `TenantSubscription` vs tabla dentro del schema.

### Onboarding

- Al inicio, el onboarding lo ejecuta el equipo POS FIFO manualmente.
- A futuro debe evolucionar a un flujo mas self-service o semi-automatizado.
- El onboarding debe crear:
  - tenant/schema;
  - dominio/subdominio;
  - sucursal inicial;
  - usuario administrador del tenant;
  - plan/modulos;
  - tokens de sync;
  - prefijo de media;
  - carga inicial de catalogo si existe una instalacion previa.
- Royal Plast debe ser el primer cliente migrado al contrato schema-per-tenant,
  porque ya existe compromiso de entrega.
- SK Performance no debe subirse al flujo cloud hasta que tenancy este alineado.

### Sync local a cloud

- Cada sucursal tiene token unico.
- El token esta amarrado a tenant + sucursal.
- Cambiar una instalacion local de tenant queda bloqueado o requiere
  intervencion del equipo POS FIFO.
- La fuente normal de maestros es cloud -> local.
- La creacion/edicion de maestros desde POS local solo se permite si hay conexion
  cloud y escribe contra la API cloud. No se crean maestros locales offline en v1.
- Reconciliaciones local -> cloud se permiten como operacion excepcional y unica:
  onboarding, migracion o reparacion controlada.
- La importacion inicial desde local debe contemplar:
  - categorias;
  - productos;
  - clientes;
  - usuarios;
  - imagenes;
  - inventario inicial;
  - cuentas por cobrar;
  - historial de ventas si el cliente lo requiere.

### Media y storage

- Imagenes de productos y logos se consideran publicas.
- Para mantener costo bajo se usara un solo Storage Account por ambiente activo,
  con un solo container publico y prefijo por tenant.

```text
media-public/
  royal-plast/
    productos/
    config/
  sk-performance/
    productos/
    config/
```

- Esta opcion es mas barata y simple que un container o Storage Account por
  tenant.
- Reportes, cierres, XML/e-CF, PDFs fiscales y documentos privados no van en
  este container publico.
- En esta fase los reportes se regeneran cuando sea posible.
- Si mas adelante se guardan documentos privados, deben ir a un container
  privado y descargarse mediante backend o URLs SAS temporales.

### Ambientes

- Hoy dev y staging son ambientes de prueba; no hay produccion cloud formal.
- Produccion debe tener PostgreSQL separado y administrado por Terraform desde
  el inicio, si el presupuesto lo permite.
- Dev/staging pueden contener datos mezclados temporalmente durante la migracion
  porque no hay produccion cloud real todavia.
- Para tenant testing se prefiere:
  - staging con tenants reales de prueba cuando se valide un cliente;
  - datos anonimizados o clones parciales cuando se hagan pruebas peligrosas;
  - no mezclar datos reales de clientes en dev salvo necesidad puntual.

### Operacion

- Deben existir comandos por tenant.

```bash
python manage.py <comando> --tenant royal-plast
```

- Debe existir backup/export por tenant como requisito de produccion.
- Al migrar un cliente existente, se intenta traer todo lo que haya en ese
  momento si el volumen es razonable.
- La administracion inicial de usuarios/roles la hace POS FIFO desde panel
  global.
- Cada tenant puede tener modulos/planes distintos.
- El aislamiento por schema es requisito antes de produccion multi-cliente.
- La auditoria operativa vive dentro del tenant.

## Aprendizajes del piloto Royal Plast

El intento de actualizacion/go-live de Royal Plast dejo claro que el onboarding
no debe depender de pasos manuales dispersos.

Problemas observados:

- El sync cloud quedo diferido porque el catalogo no termino de pasarse.
- `reconciliar_cloud --dry-run` valido 20 categorias, 273 productos y 2 clientes,
  pero el bootstrap real quedo pendiente.
- El orden de bootstrap local creo una sucursal sin negocio hasta correr
  `bootstrap_negocio` otra vez.
- La subida de imagen de producto fallo por usar header JSON en un upload
  multipart.
- El cold start de staging produjo timeouts con el timeout HTTP default.
- Variables de entorno de impresoras estaban duplicadas o desalineadas.
- `DJANGO_SECRET_KEY` local quedo expuesto a riesgo por sintaxis de `.bat`.
- La auditoria de impresiones sigue pendiente de compatibilizar con el modelo
  actual.

Implicacion para tenancy:

- El onboarding necesita un comando/runbook idempotente.
- La creacion de tenant, sucursal, admin, plan y tokens debe tener validaciones.
- Las reconciliaciones deben exigir tenant explicito.
- Los uploads/media deben resolverse antes de activar un cliente con catalogo
  real.
- Los comandos deben fallar rapido si no hay tenant, sucursal o token correcto.

## Diseno tecnico objetivo

### Django apps

Se propone separar las apps en `SHARED_APPS` y `TENANT_APPS`.

`SHARED_APPS` candidatas:

- `django_tenants`;
- `apps.tenancy` o `apps.negocios` adaptada como tenant registry;
- auth/global support si se decide tener usuarios globales fuera de tenants;
- catalogo base de permisos;
- catalogo de modulos/planes;
- admin global;
- health/version.

`TENANT_APPS` candidatas:

- `apps.productos`;
- `apps.clientes`;
- `apps.ventas`;
- `apps.inventario`;
- `apps.cuentas_por_cobrar`;
- `apps.configuracion`;
- `apps.sucursales`;
- `apps.usuarios` para usuarios operativos;
- `apps.permisos` para roles/asignaciones del tenant;
- `apps.suscripciones` si se decide que la suscripcion vive por tenant;
- `apps.sync`;
- `apps.reportes`;
- `apps.auditoria`;
- `apps.facturacion_electronica`;
- otras apps operativas locales/cloud.

Decision pendiente: si `apps.usuarios` se parte en usuarios globales y usuarios
tenant, o si se mantiene un unico modelo en tenant apps y el soporte global usa
otra app/modelo.

### Modelo tenant

El modelo actual `Negocio` ya tiene `nombre`, `slug`, `rnc` y `activo`.
Para `django-tenants` debe evolucionar a algo equivalente a:

```python
class Tenant(...):
    nombre = ...
    slug = ...
    schema_name = ...
    rnc = ...
    activo = ...
```

Ademas se requiere dominio:

```python
class Domain(...):
    domain = "royalplast.posfifo.com"
    tenant = royal_plast
    is_primary = True
```

Regla recomendada:

- `slug` comercial puede cambiar con cuidado.
- `schema_name` no cambia salvo migracion especial.
- dominio puede cambiar y redirigirse.

### API y frontend

- El frontend resuelve tenant por subdominio.
- El backend resuelve tenant antes de auth operativa.
- El JWT puede seguir incluyendo `tenant_id`, pero bajo schemas pasa a ser
  informativo; el aislamiento principal lo hace el schema activo.
- Usuarios globales de soporte necesitan flujo especial para entrar a un tenant
  especifico sin exponer selector publico de negocios.

### Sync y reconciliacion

El comando actual:

```bash
python manage.py reconciliar_cloud --cloud-url ...
```

debe evolucionar a:

```bash
python manage.py reconciliar_cloud --tenant royal-plast --cloud-url ...
```

o apuntar al subdominio del tenant:

```bash
python manage.py reconciliar_cloud --cloud-url https://royalplast.posfifo.com
```

La API de sync debe rechazar cualquier token que no resuelva tenant + sucursal.

### Migrate job

El job actual de migraciones no debe seguir siendo solo:

```bash
python manage.py migrate --settings=config.settings_cloud --noinput
```

Con schemas debe ser un job consciente de tenants, por ejemplo:

```bash
python manage.py migrate_schemas --shared --settings=config.settings_cloud --noinput
python manage.py migrate_schemas --tenant --settings=config.settings_cloud --noinput
```

Tambien conviene tener comandos separados para:

```bash
python manage.py create_tenant ...
python manage.py bootstrap_tenant ...
python manage.py seed_tenant ...
python manage.py backup_tenant ...
```

## Plan recomendado

### Fase T0 - Diseno y auditoria

- Cerrar este documento.
- Auditar modelos para clasificar shared vs tenant.
- Auditar endpoints API que hoy usan querysets globales.
- Decidir dominio local/staging para tenants.
- Decidir donde viven usuarios globales vs usuarios tenant.

### Fase T1 - Base django-tenants local

- Agregar dependencia `django-tenants`.
- Crear/ajustar app de tenancy.
- Configurar `SHARED_APPS`, `TENANT_APPS`, middleware y router.
- Probar con DB local temporal.
- Crear tenant `royal-plast` local.
- Ejecutar migraciones por schema.

### Fase T2 - Storage por tenant

- Mantener un Storage Account + container publico por ambiente.
- Agregar prefijo tenant obligatorio para media cloud.
- Ajustar storage/upload para que productos y logos escriban bajo:

```text
<tenant-slug>/productos/...
<tenant-slug>/config/...
```

- Crear comando de migracion de media local -> blob con prefijo tenant.

### Fase T3 - Sync tenant-aware

- Hacer que tokens de sucursal resuelvan tenant + sucursal.
- Hacer `reconciliar_cloud` tenant-aware.
- Bloquear maestros locales offline.
- Agregar smoke tests de sync por tenant.

### Fase T4 - Royal Plast como primer tenant real

- Crear schema `royal_plast` o nombre tecnico equivalente.
- Cargar tenant, sucursal, admin, plan, roles y tokens.
- Importar catalogo real.
- Importar imagenes a Blob.
- Importar inventario/CxC/historial segun decision operativa.
- Activar sync cuando el catalogo y el inventario esten validados.

### Fase T5 - SK Performance

- Solo iniciar despues de T1-T4 validados.
- SK debe entrar directamente con el contrato schema-per-tenant.

## Dudas pendientes

1. Nombre de dominio real:
   - `posfifo.com`, otro dominio propio, o subdominio temporal de Azure.
2. Usuarios globales:
   - modelo separado en public;
   - o usuario tenant + mecanismo de impersonation/control support.
3. Suscripcion:
   - global en `public`;
   - o dentro de cada tenant.
4. Staging:
   - usar tenants reales de prueba;
   - o clonar datos anonimizados/temporales por tenant.
5. Produccion PostgreSQL:
   - crear servidor nuevo via Terraform;
   - o importar/modelar el servidor existente con Terraform mas adelante.
6. Historial inicial:
   - importar todo para Royal Plast;
   - o importar catalogo/inventario/CxC y dejar ventas historicas solo local.

## No objetivos de la primera version

- Self-service completo de onboarding.
- Billing automatico.
- Media privada para documentos fiscales.
- CDN.
- Multi-empresa por usuario operativo.
- Cambio automatico de una instalacion POS local de un tenant a otro.
- Importar SK antes de cerrar tenancy.
