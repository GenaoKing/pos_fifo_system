# Go-live (soft-launch) — Royal Plast local ↔ STAGING

Checklist y **handoff de contexto** para ejecutar la actualización de Royal Plast y
dejarla sincronizando contra el ambiente **staging** de Azure, mientras se sigue
desarrollando en dev. Este documento es autosuficiente: cualquier sesión de Claude
Code (incluida la de la PC de Royal Plast) puede continuar el trabajo leyéndolo.

## Estado / contexto (lo ya hecho y validado)

- Se agregó lo que faltaba para empaquetar y actualizar la instalación local:
  - `config/settings_production.py` (no existía pese a estar referenciado en todo deploy/).
  - `deploy/actualizar.bat` (update in-place: detiene servicios → backup BD → copia
    código → pip → migrate → collectstatic → seeds idempotentes → reinicia servicio).
  - `deploy/iniciar_sync.bat` + `deploy/registrar_sync_servicio.bat` (daemon NSSM
    `POSFifoSync`, separado del web `POSFifoSystem`).
  - Bloque `SYNC_*` agregado a `deploy/env_cliente.bat.template`.
  - `apps/sync/management/commands/reconciliar_cloud.py` (bootstrap único local→cloud
    del catálogo; idempotente; maneja throttling 429).
  - `dist/` regenerado con todo lo anterior.
- **Validado contra el dump real de Royal Plast** (BD `royal_plastic_pos`, 2026-06-13):
  - 37 migraciones aplicaron limpias, 0 pérdidas (273 productos, 320 ventas, 3 clientes,
    2 usuarios → 0 sin negocio).
  - `bootstrap_negocio` derivó el negocio **"Royal Plast EIRL"** desde su
    `ConfiguracionNegocio` (RNC 1-32-33458-2). `check` = 0 issues. Smoke HTTP OK.
  - `reconciliar_cloud` probado end-to-end (push + idempotencia + throttling).

## Decisiones de esta corrida

- **Sucursal:** `01` (debe coincidir local ↔ cloud).
- **e-CF:** APAGADO. `modulo_ecf` default `False`; no configurar Emisor/MSeller. Nada que hacer.
- **Negocio:** Royal Plast EIRL (ya en su BD; el código lo lee de `ConfiguracionNegocio`,
  no de `env_cliente.bat`). Mantener `env_cliente.bat` como estándar de config por
  instalación (DB, red, impresoras, secret, y el bloque `SYNC_*`).
- **Destino:** STAGING.
  - Backend: `https://posfifo-staging-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io`
  - Recursos: API `posfifo-staging-api`, job migrate `posfifo-staging-migrate`, RG `posfifo-staging-rg`.

---

## FASE 1 — Preparar backend staging (cloud) [requiere acceso az]

Comandos dentro del contenedor (su `DJANGO_SETTINGS_MODULE` ya es `config.settings_cloud`):
`az containerapp exec --name posfifo-staging-api --resource-group posfifo-staging-rg --command "<cmd>"`

1. Health: `curl.exe https://posfifo-staging-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io/api/v1/health/`
2. En el contenedor, uno por uno:
   ```
   python manage.py bootstrap_negocio --nombre "Royal Plast"
   python manage.py crear_sucursal --codigo 01 --nombre "Royal Plast"
   python manage.py bootstrap_negocio --nombre "Royal Plast"   # engancha la sucursal 01 al negocio
   python manage.py bootstrap_suscripciones
   python manage.py vincular_sucursal_token --sucursal 01      # -> TOKEN_SUCURSAL (para el daemon)
   python manage.py crear_tokens_api --usuario <sysadmin>      # -> TOKEN_ADMIN (para reconciliar_cloud)
   ```
   Guardar **TOKEN_SUCURSAL** y **TOKEN_ADMIN**.

## FASE 2 — Actualizar POS local [en la PC de Royal Plast, como admin]

1. En `deploy\env_cliente.bat`: `set SUCURSAL_CODIGO=01` y `set SYNC_ENABLED=false` (por ahora).
2. Copiar el paquete nuevo a una carpeta STAGING aparte (ej. `C:\pos_update\`) y ejecutar:
   `C:\pos_update\deploy\actualizar.bat`
3. Verificar POS local en `http://localhost:<SERVER_PORT>` (login, venta, CxC, reportes).

## FASE 3 — Subir catálogo local → staging [en la PC de Royal Plast]

```
set CLOUD_ADMIN_TOKEN=<TOKEN_ADMIN>
python manage.py reconciliar_cloud --cloud-url https://posfifo-staging-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io --dry-run
python manage.py reconciliar_cloud --cloud-url https://posfifo-staging-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io
```
Revisar el resumen (creados/omitidos/errores). Es idempotente; re-correr es seguro.

## FASE 4 — Encender sync local → staging [en la PC de Royal Plast, como admin]

1. En `deploy\env_cliente.bat`:
   ```
   set SYNC_ENABLED=true
   set SUCURSAL_CODIGO=01
   set CLOUD_API_URL=https://posfifo-staging-api.calmflower-b43e72c3.canadacentral.azurecontainerapps.io
   set CLOUD_API_TOKEN=<TOKEN_SUCURSAL>
   ```
2. `deploy\iniciar_sync.bat --once`  (probar una pasada; PUSH/PULL sin errores de auth)
3. `deploy\registrar_sync_servicio.bat`  (registra/levanta `POSFifoSync`)
   Desde aquí, editar maestros **desde el portal**, no localmente.

## FASE 5 — Portal (ASWA) → staging [GitHub + Azure]

Ver `docs/runbooks/FRONTEND_DEPLOY_AZURE_STATIC_WEB_APPS.md`. Resumen:
1. Crear ASWA staging: RG `posfifo-staging-frontend-rg`, nombre `posfifo-staging-portal-swa`,
   Free, branch `main`.
2. En el workflow del ASWA inyectar `VITE_API_URL: ${{ vars.VITE_API_URL_STAGING }}`
   (= URL backend staging).
3. Agregar el origen del ASWA staging a `api_cors_allowed_origins` en
   `infra/azure/environments/staging/terraform.tfvars` y `terraform apply`.
4. Smoke: login en el portal → `/productos` muestra el catálogo subido en la Fase 3.

---

## Rollback (si algo falla en la Fase 2)
- `nssm stop POSFifoSync` y `nssm stop POSFifoSystem`.
- `pg_restore -c -U <db_user> -d <db_name> "backups\..._PRE_UPDATE_*.dump"` (el backup lo
  hace `actualizar.bat` automáticamente antes de migrar).
- Restaurar el código anterior e iniciar `POSFifoSystem`.

## Notas
- El dump de evaluación debe guardarse FUERA de `dist/` (`preparar_paquete.bat` borra `dist/`).
- Detalle completo de la actualización genérica: `deploy/ACTUALIZACION_ROYAL_PLAST.md`.
