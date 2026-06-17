# Roadmap — Implementación DB-per-tenant (esquema C)

Estado: **fuente viva / plan de acción**. Fecha: 2026-06-16.

Diseño de referencia: [TENANCY_DB_PER_TENANT.md](TENANCY_DB_PER_TENANT.md) (decisión
cerrada: una BD PostgreSQL por tenant sobre un único servidor, con plano de
control global).

Este documento es para **seguirlo y trabajar en paralelo**. Cada fase tiene
entregables con checkbox, criterio de salida (🚪 gate) y marca de paralelismo.

---

## Principio rector

**Construir y validar la maquinaria DB-per-tenant localmente (o en el server dev
existente) con un tenant descartable ANTES de tocar Terraform prod y ANTES de
onboardear Royal Plast.** El error a evitar es el del piloto: debuggear
arquitectura *e* infraestructura a la vez. La arquitectura no necesita prod para
construirse ni probarse.

---

## Estado de partida (contrastado con el repo)

- **Royal Plast:** corriendo in-place local; sync cloud **diferido**
  (`SYNC_ENABLED=false`). Margen de trabajo confirmado con el cliente.
- **Bugs locales del piloto (#3 auditoría/impresión, #4 vars impresora):**
  trabajados en el repo (PC dev). Queda pendiente la rotación de
  `SECRET_KEY` (#9) en el cliente — requiere ventana de mantenimiento; cae
  naturalmente en el redeploy de Fase 4.
- **`reconciliar_cloud`:** sube **solo** `categorias, productos, clientes` vía API
  (one-time, idempotente, por clave natural). **No** sube ventas, inventario, CxC,
  usuarios, ni imágenes. El daemon `sincronizar` empuja eventos nuevos, no hace
  backfill histórico.
- **Cloud actual:** un schema compartido sin aislamiento de tenant en maestros.
  dev/staging **se reconstruyen de cero** (no hay datos que preservar).
- **Infra:** existe Terraform dev (RG, ACR, Container Apps, Container App Job, Key
  Vault, observabilidad, remote state) y un Postgres dev/staging. **No hay prod
  cloud formal.**

### Hallazgo clave → mecanismo de import inicial

El objetivo "subir **todo el historial** de Royal Plast" no lo cubre
`reconciliar_cloud` (maestros only). Bajo DB-per-tenant el camino limpio es
**cargar el dump de la BD local directo en `tnt_royalplast`** (pg_dump/pg_restore
data-only, o `dumpdata`/`loaddata` si el engine local difiere), preservando FKs e
IDs. Trae ventas + caja + inventario + CxC + usuarios + refs de imágenes en un
solo shot, sin escribir endpoints de import por entidad.

> **Refinamiento de diseño (confirmado 2026-06-16):** `Negocio` se mantiene como **fila única
> "self"** dentro de cada BD de tenant (para que `Sucursal.negocio`, los filtros
> `negocio_id` y `negocio_actual()` sigan funcionando sin tocar nada y sin FK
> cross-DB). El nuevo modelo `Tenant` del control plane es el **registro global**
> (routing/identidad/billing). `bootstrap_tenant` mantiene ambos en sync. Esto
> mantiene viva la asunción "un proceso = un tenant" del código. §2 del diseño
> ya refleja esto.

---

## Fases

### Fase 0 — Preparación  🟢 (en paralelo, días)

- [x] Backport de bugs locales del piloto (#3, #4) al repo.
- [x] Revisar `reconciliar_cloud` → confirmado: maestros only (no ventas).
- [x] Confirmar el **engine de la BD local de Royal Plast** → **PostgreSQL**
      (`settings_production` hereda `settings.py`, engine `postgresql`).
- [x] Decidir mecanismo de import inicial → **`pg_dump`/`pg_restore` a nivel BD**
      (no API, no dumpdata).
- [ ] Entorno multi-DB local: Postgres con `control_plane` + `tnt_demo` vacías y
      migrables (o usar el server dev existente; no requiere Terraform).
- 🚪 **Salida:** dos bases vacías migrables localmente + mecanismo de import
  decidido + engine local de RP confirmado.

### Fase 1 — Núcleo DB-per-tenant  ✅ implementado local/dev

- [x] **Control plane (mínimo):** `Tenant` (registro global), `Identity`,
      `Membership`, `Domain` (diferido), registro de tokens de sync, puntero
      delgado de plan. `Permiso`, `Modulo`/`Plan`/suscripción **NO** se mueven:
      quedan por tenant (decisión 7).
- [x] **Negocio self-row:** `Negocio` permanece en la BD del tenant como fila
      única; `bootstrap_tenant` la sincroniza con el `Tenant` global.
- [x] **Router + middleware:** registro dinámico de `DATABASES` desde el registro
      `Tenant`; tenant activo en thread-local; **fail-fast** si un modelo de tenant
      se consulta sin tenant activo (nunca caer a `default`).
- [x] **Auth:** login contra `Identity` → JWT con `tenant_key` → auth class que
      activa la BD del tenant y carga el `Usuario` operativo como `request.user`.
      El motor de permisos y los viewsets **no se tocan**.
- [x] **SYSADMIN + impersonation:** `Identity` global entra a un tenant emitiendo
      JWT con `tenant_key` (sin selector público).
- [x] **Comandos:** `bootstrap_tenant` idempotente (modo *clean* y modo
      *migración-desde-local*), `migrate_tenants`, flag `--tenant`, `backup_tenant`.
- [x] **Pruebas y smoke multi-DB** (`control_plane` + `demo` + `demo2`).
- [x] **Hardening runtime:** contexto tenant se limpia por request; JWT tenant
      esta disponible en settings base; router dual-home para
      `auth/contenttypes/usuarios/negocios`; admin control-plane soportado.
- 🚪 **Gate cumplido local/dev:** `bootstrap_tenant --tenant demo` levanta todo de cero; login por
  email entra a `demo`; un segundo `demo2` está **totalmente aislado** (cero datos
  cruzados); `migrate_tenants` corre en ambos; suite verde. **Sin tocar prod.**

Evidencia local (2026-06-16):

- `migrate` aplicó `tenancy.0001_initial` en `pos_fifo_dev`.
- `bootstrap_tenant --tenant demo` creó `tnt_demo`, migró y sembró admin/sucursal/token.
- `bootstrap_tenant --tenant demo2` creó `tnt_demo2`, migró y sembró admin/sucursal/token.
- Login `admin@demo.local` devuelve `tenant_id=demo` y token JWT válido.
- Producto `SMOKE-DEMO-001` creado en `demo` no aparece en `demo2`.
- Token de sync de `demo` lee `/api/v1/maestros/productos/` con HTTP 200; token desconocido falla con 401.
- `migrate_tenants --noinput` corre en `demo` y `demo2` sin migraciones pendientes.
- Cierre extra: `/api/v1/auth/me/` queda cubierto con JWT global en tests y el
  runbook `docs/runbooks/ROYAL_PLAST_IMPORT_DB_PER_TENANT.md` prepara el dry-run
  del dump real de Royal Plast.

> Bajo DB-per-tenant, los `.objects.all()` de maestros y el matching por clave
> natural del sync pasan a ser correctos por construcción. No hay auditoría masiva
> de querysets — ese es el punto de elegir C.

### Fase 2 — Storage  🟡 (núcleo local implementado; smoke Azure pendiente)

- [x] Backend de media con rutas canónicas **prefijadas por `tenant_key`** en BD
      (`demo/productos/...`, `demo/config/...`) cuando hay tenant activo.
- [x] Compatibilidad mono-tenant local: sin tenant activo conserva rutas legacy
      (`productos/...`, `config/...`).
- [x] Fix del upload multipart (#7) — ya correcto en el repo (verificado
      2026-06-16): `subirImagen` usa solo `X-CSRFToken`, sin `Content-Type`.
- [x] PDF header no depende de `logo.path`; puede leer logo desde storage remoto.
- [x] Comando de migración de media local → ruta/blob bajo prefijo del tenant:
      `migrar_media_tenant --tenant demo --source-media-root .\media --apply`.
- [ ] Activar `enable_media_storage=true` en Azure dev y ejecutar smoke real de
      blobs públicos.
- 🚪 **Salida:** imágenes de `demo` y `demo2` viven en `media-public/<tenant>/`
  y se sirven; upload multipart OK; Azure dev validado con URL pública.

### Fase 3 — Infra prod con Terraform  (en implementacion)

- [x] Nuevo root `platform` con remote state `azure/platform.tfstate`.
- [x] Nuevo root `prod` con remote state `azure/prod.tfstate`.
- [x] PostgreSQL Flexible Server (Burstable) vive en `platform`.
- [x] Prod consume `platform` via `terraform_remote_state`.
- [x] Prod no crea ACR propio: usa temporalmente `posfifodevacr` como ACR
      compartido por RBAC/Managed Identity.
- [x] Container Apps acepta registry externo y mantiene `AcrPull` para API/job.
- [x] **Migrate job tenant-aware:** comando `migrate_cloud` corre control plane +
      `migrate_tenants`.
- [ ] Aplicar `platform` en Azure.
- [ ] Cargar secretos prod en Key Vault.
- [ ] Aplicar `prod` y activar API/job con imagen SHA.
- 🚪 **Salida:** prod arriba; `demo` creado y validado *en prod*; migrate job corre
  migraciones tenant-aware; smoke `/health/` OK.

### Fase 4 — Onboarding real de Royal Plast  (convergencia)

- [ ] `bootstrap_tenant --tenant royalplast` en prod (crea BD, sucursal, admin,
      plan, token, prefijo de media; registra `Tenant` + `Identity` + `Membership`).
- [ ] **Import completo desde local** (modo migración): ventas/historial,
      inventario/lotes, CxC, usuarios operativos, roles/asignaciones, config —
      vía dump/load. Por lotes; subir `SYNC_HTTP_TIMEOUT` por el cold-start.
- [ ] Import de **imágenes** de RP a Blob.
- [ ] **Validación:** totales/inventario/CxC contra el local; el dueño ve totales
      desde el teléfono.
- [ ] Activar sync incremental cuando esté validado.
- [ ] Rotar `SECRET_KEY` (#9) en el redeploy del cliente.
- 🚪 **Salida:** RP operando en cloud, sync activo, totales correctos.

### Fase 5 — SK Performance

- [ ] Onboarding limpio con `bootstrap_tenant` (modo clean, sin data legacy).
- [ ] Solo después de RP estable.

---

## Dependencias y paralelismo

```text
Track RP (deuda local ✓ + confirmar engine + export prep) ──┐ (desde día 1)
                                                            ▼
Fase 1 (núcleo) ──────────────► Fase 3 (infra) ──► Fase 4 (RP) ──► Fase 5 (SK)
        │                                              ▲
        └──► Fase 2 (storage) ─────────────────────────┘ (paralela a F1)
```

- **Camino crítico:** Fase 1 → Fase 3 → Fase 4.
- **Paralelizable:** Fase 2 (storage) con Fase 1; Track RP (Fase 0) desde el día 1.
- **No hacer:** levantar prod en Terraform como primer paso, ni onboardear RP por
  el camino viejo (shared-schema) que se descarta.

---

## Decisiones cerradas (2026-06-16)

1. **Engine local de RP = PostgreSQL** → import vía `pg_dump`/`pg_restore` a nivel
   BD (no API, no dumpdata).
2. **`Negocio` self-row** por tenant + `Tenant` global en control plane.
3. **`control_plane` = `default`** + router fail-fast para modelos de tenant.
4. **Rol de Postgres** único en el MVP; por tenant = hardening futuro.
5. **Sync** mantiene claves naturales (`username`/`rol_slug`/`sucursal_codigo`),
   scopeadas por la BD del tenant.
6. **Token de sync** → registro global de tokens en control plane
   (`token_hash → tenant_key + sucursal_codigo`); resuelve el tenant antes de
   tocar la BD del tenant.
7. **Control plane MÍNIMO** — `Permiso`, suscripción y `Negocio` self-row viven
   por tenant (cualquier FK/M2M desde un modelo de tenant debe ser local). Reduce
   el scope de Fase 1.
8. **Auditoria operativa por tenant** — no se centraliza en `default`; SYSADMIN
   revisa por tenant via impersonation o usuario local.
9. **Django admin control-plane soportado** — `usuarios/negocios/auth/contenttypes`
   existen en `default` solo como compatibilidad de admin/control-plane.
10. **Multi-membership UI diferida** — reutilizar `admin-email` en otro tenant
    falla en bootstrap; el camino de soporte es Identity global + impersonation.

## Riesgos / abiertas

- **Receta exacta del dump/load** (flags, manejo de constraints en Postgres
  manejado) — se cierra con un dry-run en Fase 4 sobre una copia.
- **Paridad de migraciones local↔tenant** al momento del load (mismo commit).
- **Dominio futuro** (subdominios opt-in; aditivo, no rompe login por email).

---

## Próximo paso inmediato

1. Ejecutar el dry-run del dump real de Royal Plast siguiendo
   `docs/runbooks/ROYAL_PLAST_IMPORT_DB_PER_TENANT.md`.
2. Ejecutar smoke Azure dev de **Fase 2 — Storage** cuando se confirme prender
   el Storage Account: Terraform plan/apply, upload de demo/demo2 y verificación
   de URLs públicas.
3. Diseñar cómo el pipeline prod ejecutará `migrate` + `migrate_tenants` sin tocar
   Royal Plast todavía.
