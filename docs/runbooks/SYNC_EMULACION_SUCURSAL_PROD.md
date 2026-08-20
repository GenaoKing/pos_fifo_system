# Runbook — Emular una sucursal de prueba contra el cloud de PROD

Objetivo: levantar una sucursal local "de mentira" (`pos_fifo_demo_branch`) que
sincronice contra el backend de producción usando el tenant **descartable `demo`**,
sin tocar datos de clientes reales (`royalplast`, `skperformance`).

Sirve para reproducir/observar el comportamiento del sync (push de eventos + pull
de maestros) en condiciones reales sin riesgo.

> **Seguridad:** este runbook NO contiene secretos. Las credenciales de la BD de
> prod se leen en vivo desde Key Vault con `az`. No pegar passwords ni tokens en
> el repo.

---

## 0. Prerrequisitos

- `az` CLI logueado en la suscripción correcta (`az account show`).
- Acceso de red a `posfifoplatformpg` (la IP del operador debe estar en el
  firewall del Flexible Server).
- `psql` y el entorno conda `pos_fifo` disponibles.
- BD local `pos_fifo_demo_branch` ya creada y migrada con
  `--settings=config.settings_demo_branch` (hereda `settings_development`, apunta a
  esa BD dedicada).

---

## 1. Datos fijos de la plataforma prod

| Recurso | Valor |
|---|---|
| Postgres server | `posfifoplatformpg.postgres.database.azure.com` |
| Control plane DB | `pos_fifo_prod` |
| Key Vault | `posfifoprodkv` (RG `posfifo-prod-rg`) |
| API prod | `https://posfifo-prod-api.greenglacier-6158bae1.canadacentral.azurecontainerapps.io` |
| Tenant de prueba | `demo` → BD `tnt_demo`, sucursal `SD-001` |

---

## 2. Recuperar credenciales de la BD de prod (Key Vault)

```bash
# usuario y password del admin de Postgres (posadmin)
DBUSER=$(az keyvault secret show --vault-name posfifoprodkv --name db-user --query value -o tsv)
DBPASS=$(az keyvault secret show --vault-name posfifoprodkv --name db-password --query value -o tsv)

export PGPASSWORD="$DBPASS"
export PGSSLMODE="require"
HOST="posfifoplatformpg.postgres.database.azure.com"

# smoke de conexión (debe imprimir el usuario admin)
psql -h "$HOST" -p 5432 -U "$DBUSER" -d pos_fifo_prod -tAc "SELECT current_user;"
```

Otros secretos disponibles en el vault: `django-secret-key`.

---

## 3. Localizar el tenant y su token de sync

El control plane (`pos_fifo_prod`) registra los tokens **solo por hash**
(`tenancy_sync_tokens.token_hash`). El token DRF en claro vive en la BD del
tenant (`tnt_demo.authtoken_token`).

```bash
# tenants registrados + su sucursal de sync
psql -h "$HOST" -p 5432 -U "$DBUSER" -d pos_fifo_prod -tAc \
  "SELECT t.tenant_key, t.db_name, st.sucursal_codigo, st.activo
     FROM tenancy_tenants t
     LEFT JOIN tenancy_sync_tokens st ON st.tenant_id=t.id
    ORDER BY t.tenant_key;"

# token de sync EN CLARO del tenant demo (usuario de servicio sucursal_service_SD-001)
psql -h "$HOST" -p 5432 -U "$DBUSER" -d tnt_demo -tAc \
  "SELECT u.username, t.key
     FROM authtoken_token t JOIN usuarios u ON u.id=t.user_id;"
```

> Alternativa idempotente vía Django (re-imprime el token existente, NO lo
> regenera porque usa `get_or_create`):
> ```
> python manage.py bootstrap_tenant --tenant demo --nombre "Demo" \
>   --settings=config.settings_azure_pg
> ```
> Esto sí ESCRIBE en prod (re-corre `migrate_tenants` + seed idempotente). Para
> solo leer el token, preferir la query psql de arriba.

---

## 4. Configurar el entorno de la sucursal de prueba y sincronizar

```bat
REM PowerShell / cmd, con el conda env pos_fifo activo
set CLOUD_API_URL=https://posfifo-prod-api.greenglacier-6158bae1.canadacentral.azurecontainerapps.io
set CLOUD_API_TOKEN=<token-en-claro-del-paso-3>
set SYNC_ENABLED=true
set SUCURSAL_CODIGO=01
set SYNC_INTERVAL=60

REM una sola pasada (diagnóstico)
python manage.py sincronizar --once --settings=config.settings_demo_branch

REM solo pull / solo push si se quiere aislar
python manage.py sincronizar --once --only-pull --settings=config.settings_demo_branch
python manage.py sincronizar --once --only-push --settings=config.settings_demo_branch
```

Observar:
- Salida `PUSH procesados/confirmados/fallidos` y `PULL categorias/productos/clientes`.
- Tabla local `sync_logsync` (resultado del ciclo) y `sync_versionmaestro` (cursores).
- Lado cloud: `GET /api/v1/sync/status/` con el token devuelve eventos
  pendientes/confirmados y `version_maestros`.

---

## 5. Verificar la corrección del cursor (BUG-B, Fase 2)

> Esta sección describía cómo **reproducir** el bug del cursor. Desde el
> 2026-08-19 el bug está corregido (cursor keyset + marca de agua contigua), así
> que ahora describe cómo **verificar la corrección** contra un tenant real.

El escenario que antes perdía datos: los endpoints ordenaban por `nombre`
mientras el cursor cortaba por `fecha_modificacion`, así que un registro editado
podía quedar fuera del rango para siempre.

`royalplastdemo` sirve bien porque sus **273 productos superan el page size de
200**, o sea que ejercita paginación de verdad.

1. Pull completo para dejar el cursor al día:

   ```
   python manage.py sincronizar --once --only-pull --settings=config.settings_demo_branch
   ```

2. En el portal, editar un producto **alfabéticamente tardío** (que caiga en la
   última página del orden por nombre). Antes era justo el caso que se perdía.

3. Volver a pullear. El producto debe llegar. Confirmar el cursor:

   ```
   python manage.py verificar_sync --settings=config.settings_demo_branch
   ```

   La sección `CURSORES DE PULL` muestra `ultima_version` y avisa en rojo si
   algún cursor quedó **BLOQUEADO** — eso significa que un registro del portal
   falla al aplicarse localmente y está frenando la marca de agua. Antes ese
   registro se saltaba en silencio; ahora el cursor se detiene y lo dice.

4. Prueba del empate de timestamps: editar dos productos en el mismo segundo.
   Ambos deben bajar (antes el segundo se perdía en el borde del cursor).

5. Prueba de resiliencia: cortar la red a media paginación. El siguiente ciclo
   debe **retomar** donde iba, no empezar de cero.

Código relevante: `apps/sync/engine.py` (`_pull_generic`, doble cursor) y
`apps/api/views/maestros.py` (`SyncIncrementalMixin._filtrar_keyset`).

## Notas

- No reusar el token de un cliente real para pruebas: usar siempre `demo`.
- `tnt_demo` es descartable; ensuciarlo con eventos de prueba es aceptable.
- Si el firewall rechaza la conexión: agregar la IP del operador al Flexible
  Server `posfifoplatformpg` (RG `posfifo-platform-rg`).
