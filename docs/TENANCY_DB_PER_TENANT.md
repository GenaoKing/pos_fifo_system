# Tenancy cloud — diseño DB-per-tenant (esquema C)

Estado: decision global aprobada para multi-tenant cloud. Fecha: 2026-06-16.

Este documento es la **fuente viva** para tenancy cloud. Reemplaza la
exploración previa de `django-tenants`
(`TENANCY_CLOUD_DESIGN.md`), que queda como referencia histórica de la decisión.
Tras comparar opciones de segmentación (row-level, schema-per-tenant,
DB-per-tenant, server-per-tenant), se eligió **una base de datos PostgreSQL por
tenant sobre un único servidor compartido**, con un **plano de control** global.

Razones de la elección (resumen):

- El código ya asume "un proceso/conexión = un tenant" (`SUCURSAL_CODIGO`,
  `get_sucursal_actual`), así que el routing por conexión encaja sin reescribir
  cada queryset.
- Modo de falla **ruidoso** (BD equivocada = error/vacío), no fuga silenciosa
  como en row-level. Crítico para datos de plata/fiscales.
- Evita el infierno de `AUTH_USER_MODEL` shared/tenant de `django-tenants`.
- Costo Azure igual al de schema-per-tenant: se paga por **servidor**, no por
  base. N bases en un Flexible Server Burstable ≈ costo incremental cero.
- Backup/export por tenant trivial (`pg_dump` de la base).

**Alcance de este documento:** fundación cloud — plano de control, una BD por
tenant, routing de conexión, identidad/login y onboarding idempotente. El
rediseño detallado del **sync local↔cloud** vive en un documento aparte; acá solo
se fija su **contrato** (el token amarra negocio + sucursal, los maestros se
escriben contra la BD del tenant).

---

## 1. Punto de partida real (honesto)

- Hoy el cloud es **un único schema compartido sin aislamiento de tenant** en los
  maestros: `Producto`, `Categoria` y `Cliente` **no tienen FK a negocio** y los
  viewsets hacen `.objects.all()`. Los handlers de sync hacen matching por clave
  natural **global** (`sku`, `cedula_rnc`, `numero_venta`).
- Existe `Negocio` (apps/negocios) como tenant lógico, `Sucursal` con FK a
  negocio, `negocio_actual(request)` como resolutor, y el JWT ya emite
  `tenant_id = negocio.slug`. Pero nada de eso scopea los datos operativos.
- En producción hay 2 clientes (Royal Plast, SK), 1 sucursal c/u, corriendo en
  **instalaciones locales independientes** (cada install es su propia BD
  mono-tenant y es la **fuente autoritativa** de su data).
- **dev/staging cloud no contienen datos que preservar** y se reconstruyen de
  cero al adoptar este diseño.

Implicación: no se "corta" la BD compartida actual (no hay discriminador de
tenant para hacerlo limpio). Cada tenant cloud se **construye fresco** importando
desde su instalación local.

---

## 2. Modelo de dos planos

Principio rector del reparto: **cualquier FK o M2M desde un modelo de tenant debe
resolverse dentro de la misma BD.** Por eso el control plane queda **mínimo** y
casi todo vive en la BD del tenant. Las lecturas cross-DB (p.ej. el panel global
leyendo la suscripción real de un tenant) son explícitas y de solo lectura.

### Plano de control (BD `control_plane`, global) — MÍNIMO

Solo lo que enruta y autentica, sin FK hacia modelos de tenant:

- **`Tenant`** — registro global de clientes (routing, identidad, billing futuro).
- **`Identity`** — credenciales globales de login (email + password). Una por
  persona real.
- **`Membership`** — `Identity → Tenant (+ username/rol operativo)`. MVP 1:1; el
  esquema admite N para el futuro multi-negocio.
- **`Domain`** — subdominios por tenant (diferido; tabla presente, sin uso en MVP).
- **Registro de tokens de sync** — `token_hash → tenant_key + sucursal_codigo`,
  para resolver el tenant del token antes de tocar una BD de tenant (ver §8).
- **Puntero delgado de plan/estado por tenant** — denormalizado, solo para el
  panel global/billing. NO es la fuente de verdad del entitlement (esa vive por
  tenant).
- Metadata SaaS / infra.

### Plano de datos (BD `tnt_<tenant_key>`, una por tenant)

Todo lo operativo **y los catálogos que los modelos de tenant referencian**:

- **`Negocio`** como **fila única "self"** del tenant (mantiene `Sucursal.negocio`,
  los filtros `negocio_id` y `negocio_actual()` sin FK cross-DB; `bootstrap_tenant`
  la sincroniza con el `Tenant` global);
- productos, categorías, clientes;
- ventas y FIFO; inventario, lotes y movimientos;
- cuentas por cobrar; caja/turnos;
- sucursales del tenant;
- **usuarios operativos** (`Usuario`) del negocio;
- **catálogo de permisos** (`Permiso`) — sembrado por migración, idéntico en cada
  tenant (lo exige el M2M `Rol.permisos`);
- roles y asignaciones (`Rol`, `AsignacionRol`);
- **módulos/planes/suscripción y overrides** (app `suscripciones` completa) — lo
  exige `SucursalModuloOverride → Sucursal/Modulo`; el entitlement real se resuelve
  acá;
- configuración del negocio/sucursal; emisores e-CF y config fiscal;
- auditoría operativa; eventos/logs de sync;
- reportes derivados cuando se guarden.

> **Por qué casi todo es por-tenant:** `Venta.usuario`, `AsignacionRol.usuario`,
> `Rol.permisos` (M2M a `Permiso`) y `SucursalModuloOverride` (→ `Sucursal`/`Modulo`)
> le hacen FK/M2M a modelos de tenant, y **un FK/M2M no cruza bases de datos**. Por
> eso el catálogo de permisos y la suscripción viven por tenant; la credencial de
> login es lo único global (ver §4).

---

## 3. Identificadores

Cada tenant tiene tres identificadores con ciclos de vida distintos:

| Campo | Mutable | Uso |
|---|---|---|
| `tenant_key` | **No** | Nombre de BD (`tnt_<key>`), prefijo de media, nombre de secreto en Key Vault, flag `--tenant` de comandos. Identificador técnico estable. |
| `slug` | Sí (con cuidado) | Routing/branding comercial. |
| `nombre` | Sí | Display. |

Regla: el `tenant_key` **nunca cambia** sin migración controlada (renombrar una
BD y un prefijo de media es costoso). El `slug` y el `nombre` pueden cambiar.

Ejemplo: `tenant_key=royalplast` → BD `tnt_royalplast`, media
`media-public/royalplast/...`, secreto `pg-royalplast`.

---

## 4. Identidad y login (el punto crítico)

### Decisiones

- **`AUTH_USER_MODEL` sigue siendo `usuarios.Usuario`** (sin swap, que es
  doloroso). La tabla `usuarios` existe en todas las bases por el grafo de
  dependencias de Django. En `default` solo soporta Django admin/control-plane;
  los usuarios operativos reales viven en la BD del tenant.
- El control plane agrega **`Identity`** (credenciales globales) + **`Membership`**.
  Son la fuente de verdad de "quién puede entrar y a qué tenant".
- **La credencial vive en `Identity` (control plane) desde el día 1**, no en el
  `Usuario` del tenant. Esto hace que el futuro multi-negocio sea aditivo (sumar
  membresías) en vez de un refactor de auth.
- **Login por email + un solo dominio** (MVP). No hay subdominios ni wildcard TLS.
  El backend resuelve el tenant a partir de la `Identity`/`Membership`, no del
  hostname.

### Flujo de login

```
POST /api/v1/auth/login  { email, password }
  1. Autenticar contra Identity (control_plane).
  2. Resolver Membership → tenant T (MVP: exactamente 1).
  3. Emitir JWT con claims: { identity_id, tenant_key, username, rol }.
```

### Flujo de cada request autenticado

```
1. Auth class lee tenant_key del JWT.
2. Middleware activa la conexión de la BD de T (alias tnt_<key>).
3. Carga el Usuario operativo (por username) desde la BD de T.
4. request.user = ese Usuario  → el motor de permisos y los viewsets siguen
   funcionando SIN CAMBIOS (operan sobre Usuario/AsignacionRol del tenant).
```

Ventaja: el código de permisos (`apps/permisos/engine`) y los viewsets de
maestros no se tocan; solo cambia de dónde sale `request.user`.

### Email como clave global

Como el login es por email, el email debe ser único entre tenants. Si en el
futuro una misma persona tiene dos negocios, el email deja de ser 1:1 y ahí entra
el `Membership` con N filas + selector de negocio (fuera del MVP, ver §10).
En el MVP, `bootstrap_tenant` falla si se reutiliza un `admin-email` con una
membresía activa en otro tenant; para soporte multi-tenant se usa Identity global
e impersonation.

> **Invariante — el cloud siempre corre tenancy-ON.** Todos los ambientes cloud
> usan `TENANCY_DB_PER_TENANT_ENABLED=true`. El portal React manda `email` en
> `POST /api/v1/auth/login/`, lo que asume el serializer tenant
> (`TenantPortalLoginSerializer`). **No correr el backend cloud con tenancy OFF:**
> en modo legacy el login usa `username` (SimpleJWT) y el portal recibiría 400. Si
> alguna vez hiciera falta un ambiente cloud legacy, primero el backend debe
> aceptar `email` o `username` en el login.

### Usuario global / SYSADMIN

- Es una `Identity` con `is_global=True`, sin membership fija.
- Para entrar a un tenant usa **impersonation**: un endpoint que emite un JWT con
  el `tenant_key` elegido. No hay selector público de negocios.
- Sirve para soporte y administración central por POS FIFO.

### Cajeras y usuarios operativos

- El portal cloud es **admin-only** (hoy `PortalTokenObtainPairView` bloquea
  roles distintos de ADMIN/SYSADMIN). Las cajeras **no tienen `Identity`** (no
  loguean al cloud).
- Pero su `Usuario` operativo **sí debe existir en la BD del tenant** para que
  `Venta.usuario` enganche. El onboarding (§6) los siembra.
- Alta de cajera desde el portal = un write normal a la BD del tenant ya activa
  (el router lo resuelve solo). Esto cierra el pendiente "provisión de usuarios
  cross-DB".

---

## 5. Routing de conexión

### Topología

- **Un** Azure Database for PostgreSQL Flexible Server (Burstable) por ambiente.
- BD `control_plane` = alias Django `default`.
- BD `tnt_<tenant_key>` por tenant, registradas dinámicamente.

### Mecanismo

- **Registro dinámico de `DATABASES`** lazy: al autenticar, correr comandos o
  entrar a `tenant_context`, se lee el registro `Tenant` del control plane y se
  agrega el alias de conexión por tenant activo. Se evita consultar la BD dentro
  de `AppConfig.ready()`.
- **Middleware tenant:** limpia el contexto al inicio y al final de cada request.
  La autenticación DRF fija el `tenant_key` activo y guarda los tokens de reset
  en el request Django subyacente para evitar fugas entre requests.
- **DB router**:
  - Modelos del control plane (`Tenant`, `Identity`, `Membership`, `Domain`,
    registro de tokens, puntero de plan) → `default`. **No incluye** `Permiso`,
    `Modulo`, `Plan` ni suscripción: esos viven por tenant (ver §2).
  - Compatibilidad admin/control-plane: `admin`, `sessions` → `default`;
    `auth`, `contenttypes`, `usuarios` y `negocios` son dual-home. Sin tenant
    activo usan `default`; con tenant activo usan la BD del tenant.
  - Modelos de tenant → alias del tenant activo (thread-local).
  - **Si un modelo de tenant se consulta sin tenant activo → el router lanza
    error** (fail-fast, defensa en profundidad: nunca caer silenciosamente a
    `default`).
- **Credenciales (MVP):** un único rol de Postgres con acceso a todas las BD de
  tenant + control plane → **un solo secreto** en Key Vault. El aislamiento real
  lo da que cada conexión apunta a una BD distinta (la ORM no cruza). Hardening de
  producción: rol por tenant (ver §10).

### Comandos conscientes de tenant

```bash
python manage.py <comando> --tenant royalplast      # fija el alias activo
python manage.py migrate                            # control plane (default)
python manage.py migrate_tenants                    # itera registro → migrate por BD
python manage.py migrate_tenants --tenant royalplast
python manage.py with_tenant --tenant royalplast <comando>
python manage.py backup_tenant --tenant royalplast  # pg_dump de tnt_royalplast
```

Las migraciones del control plane corren una vez; las de tenant corren **N veces**
(una por BD) — inherente al modelo, igual que en schema-per-tenant.

---

## 6. Onboarding — comando idempotente

La lección central de Royal Plast: el onboarding no puede ser pasos manuales
dispersos (de ahí salió el bug "sucursal sin negocio"). Se consolida en **un solo
comando idempotente con validaciones y `--dry-run`**:

```bash
python manage.py bootstrap_tenant \
    --tenant royalplast \
    --nombre "Royal Plast EIRL" \
    --rnc ... \
    --admin-email dueno@royalplast.com \
    --sucursal-codigo 01 \
    --plan empresarial \
    [--dry-run]
```

Pasos (cada uno chequea existencia → re-ejecutable sin romper):

1. Crear/actualizar fila `Tenant` (tenant_key, nombre, slug, rnc, db_name,
   media_prefix) en el control plane.
2. `CREATE DATABASE tnt_<key>` si no existe; registrar alias.
3. Correr migraciones sobre la BD del tenant.
4. Seed operativo en la BD del tenant: `Sucursal` inicial, `Usuario` admin
   operativo, roles default + permisos (`sync_permisos`), plan/módulos
   (`bootstrap_suscripciones`).
5. Crear `Identity` (email + password) + `Membership` → tenant + username admin,
   en el control plane.
6. Generar token de sync de la sucursal (api_key / usuario_servicio), **amarrado
   a tenant + sucursal**.
7. Crear el prefijo de media `media-public/<tenant_key>/`.

Reglas: **fail-fast** si falta tenant, sucursal o credencial; orden fijo (la
sucursal se crea antes de engancharse al negocio); todo el seed que ya existe
(`crear_sucursal`, `bootstrap_negocio`, `bootstrap_suscripciones`, `sync_permisos`)
queda **encadenado en orden** dentro de este comando, no suelto.
También falla si `--slug` explícito ya pertenece a otro tenant o si
`--admin-email` ya tiene una membership activa en otro tenant. Si el slug se
omite, se genera uno único de forma idempotente.

---

## 7. Media y storage

- **Un** Storage Account por ambiente, **un** container público, **prefijo por
  `tenant_key` estable** (no por slug, que puede cambiar):

```text
media-public/
  royalplast/
    productos/
    config/
  skperformance/
    productos/
    config/
```

- Imágenes de productos y logos = públicas. Reportes, XML/e-CF, PDFs fiscales y
  documentos privados **no** van acá (futuro: container privado + URLs SAS).
- La ruta canónica guardada en BD incluye el prefijo estable del tenant:
  `royalplast/productos/...` y `royalplast/config/...`. El modo mono-tenant
  local conserva rutas legacy (`productos/...`, `config/...`) mientras no haya
  tenant activo.
- Comando de migración de media local → blob/ruta prefijada:
  `migrar_media_tenant --tenant <key> --source-media-root <media> --apply`.
- Recordatorio del piloto: el upload de imagen es **multipart**, no JSON — no
  reusar `jsonHeaders()` en ese path.

---

## 8. Contrato de sync local↔cloud (solo contrato; detalle en doc aparte)

- Cada sucursal tiene un token único **amarrado a tenant + sucursal**. La API de
  sync rechaza cualquier token que no resuelva ambos.
- **Resolución de tenant desde el token (clave):** el sync llega con un DRF Token,
  sin email ni subdominio, así que el tenant se resuelve **primero** contra el
  **registro de tokens del control plane** (`token_hash → tenant_key +
  sucursal_codigo`, poblado por `bootstrap_tenant`). Con eso se activa la BD del
  tenant y recién ahí se valida el `Usuario`/`Sucursal` operativos. El token sigue
  siendo la única credencial.
- Fuente normal de maestros: **cloud → local** (pull incremental por `?desde=`).
- La creación/edición de maestros desde el POS local escribe contra la API cloud,
  que enruta a la **BD del tenant** resuelta por el token.
- La reconciliación **local → cloud** es la operación de **onboarding/migración**
  (de hecho, es la primera carga de todo cliente existente; no es una excepción).
- El detalle (reconciliar_cloud tenant-aware, import inicial, escritura de
  maestros por tenant) va en el documento de sync.

---

## 9. Ambientes

- **dev/staging:** un Flexible Server compartido con `control_plane` + BDs de
  tenant de prueba. Sin datos que preservar hoy → se reconstruyen de cero.
- **prod:** servidor PostgreSQL separado, vía Terraform, cuando el presupuesto lo
  permita. El aislamiento por BD es requisito antes de producción multi-cliente.
- Tenants de prueba reales en staging cuando se valide un cliente; clones
  anonimizados solo para pruebas peligrosas.

---

## 10. Migración de Royal Plast (alcance alto; runbook detallado aparte)

Decidido: **cargar TODO desde local → cloud**, incluido el histórico de ventas,
para que el dueño vea totales desde el teléfono.

1. Crear una BD tenant descartable/final y restaurar el dump completo con
   `pg_restore`.
2. Registrar `Tenant` en el control plane apuntando a esa BD.
3. Correr `migrate_tenants --tenant <tenant_key>` y luego
   `normalizar_import_tenant` para crear self-row `Negocio`, sucursal, admin,
   plan, token sync y backfills.
4. Validar catálogo + inventario + totales contra el local.
5. Importar imágenes a Blob cuando el smoke de media esté cerrado.
6. Activar sync cuando esté validado.
7. SK Performance entra **después**, limpio, directo con el contrato C (no se
   sube hasta validar el camino con Royal Plast).

> Nota de volumen: importar todo el histórico de ventas hace el bootstrap más
> pesado; conviene subir `SYNC_HTTP_TIMEOUT` y/o cargar por lotes (el cold-start
> de Container Apps que escala a cero ya mordió en el piloto).

---

## 11. No objetivos del MVP

- Subdominios por tenant / wildcard TLS (diferido: sin dominio comprado todavía).
- Selector de negocio / multi-membership en UI (el esquema lo permite; la UI no).
- Rol de Postgres por tenant (MVP: un rol compartido, un secreto).
- Billing automático; media privada para documentos fiscales; CDN.
- Cambio automático de una instalación POS local de un tenant a otro.
- Importar SK antes de validar el contrato C con Royal Plast.

---

## 12. Decisiones cerradas y dudas abiertas

Cerradas (2026-06-16):

1. **Rol de Postgres:** un solo rol/secreto en el MVP; rol por BD = hardening
   futuro.
2. **`control_plane` = `default`** de Django; el router enruta modelos de tenant
   al alias activo y **falla si no hay tenant activo** (no cae a `default`).
3. **e-CF / config fiscal:** viven en la BD del tenant (§2).
4. **Identidad cross-DB en sync:** se mantienen claves naturales
   (`username`/`rol_slug`/`sucursal_codigo`), scopeadas por la BD del tenant.
5. **Reparto control_plane vs tenant:** control plane mínimo; `Permiso`,
   suscripción y `Negocio` self-row viven por tenant (§2).
6. **Resolución de tenant en el token de sync:** registro global de tokens en el
   control plane (§8).
7. **Implementación Fase 1:** `apps.tenancy` queda opt-in con
   `TENANCY_DB_PER_TENANT_ENABLED`; el modo mono-tenant local sigue funcionando
   cuando la bandera está apagada.

8. **Auditoria operativa:** vive en la BD del tenant. No se centraliza en
   `default`; soporte/SYSADMIN revisa por tenant via impersonation o usuario
   local.
9. **Django admin control-plane:** queda soportado con tablas compat en
   `default` para `auth/contenttypes/usuarios/negocios`; no representan datos
   operativos compartidos.
10. **Multi-membership:** selector publico diferido. Reusar `admin-email` en otro
    tenant falla en bootstrap; soporte multi-tenant usa Identity global +
    impersonation.

Abiertas:

- **Dominio futuro:** si/cuándo se compra dominio, plan de adopción de subdominios
  sobre el mismo backend (aditivo; no rompe el login por email).
