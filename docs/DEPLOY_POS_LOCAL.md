# Deploy del POS local (sucursal) — referencia maestra

Fuente viva del sistema de **instalación / actualización del POS local** (la app
Django que corre en la PC de cada sucursal, p. ej. Royal Plast) y su **sincronización
con la nube**. Para los pasos operativos concretos ver los runbooks en `deploy/`
(que sí viajan en el paquete); este documento es la referencia interna de desarrollo.

- Runbook de actualización in-place: [deploy/ACTUALIZACION_ROYAL_PLAST.md](../deploy/ACTUALIZACION_ROYAL_PLAST.md)
- Checklist de go-live a staging: [deploy/GO_LIVE_STAGING_ROYAL_PLAST.md](../deploy/GO_LIVE_STAGING_ROYAL_PLAST.md)
- Estado/pendientes de Royal Plast: memoria `royal-plast-golive-2026-06`

---

## 1. Arquitectura

- **App**: Django 5 + DRF + PostgreSQL, servida con **Waitress** (`server.py`) en la LAN
  sobre HTTP. Settings de producción local: **`config/settings_production.py`**
  (hereda de `config/settings.py`, todo parametrizado por entorno; apaga DEBUG, agrega
  WhiteNoise, arma `ALLOWED_HOSTS` desde `SERVER_IP`/`EXTRA_HOSTS`, logging a `logs/`).
- **Configuración por instalación**: `deploy/env_cliente.bat` (creado desde
  `env_cliente.bat.template`). Contiene DB, red, impresoras, `SECRET_KEY` y el bloque
  `SYNC_*`. **Los datos del negocio (nombre/RNC/dirección) NO van aquí**: viven en
  `ConfiguracionNegocio` (BD).
- **Servicios Windows (NSSM)**:
  - `POSFifoSystem` — servidor web (`server.py`).
  - `POSFifoSync` — daemon de sincronización (`manage.py sincronizar`), independiente.
- **Sincronización cloud** (app `apps/sync`): el daemon **empuja** ventas (cola
  `EventoSync`) y **baja** maestros (categorías/productos/clientes/roles). Se autentica
  con un **token DRF de sucursal** generado en el cloud. Modelo de datos: el cloud (portal)
  autora los maestros y la sucursal los baja (pull); las ventas suben (push).

## 2. Toolchain

### Scripts de cliente (`deploy/`, se empaquetan)
| Script | Rol |
|---|---|
| `preparar_paquete.bat` | (DEV) valida + construye `dist/pos_fifo_system/`. Corre el **gate** antes de empaquetar. |
| `instalar.bat` | Instalación **fresca** v3 (crea BD, usuario, migra, seeds, sysadmin, caja, SECRET_KEY). |
| `actualizar.bat` | **Actualización in-place** (preserva BD/env/media): detener → backup → copiar código → pip → migrate → collectstatic → seeds idempotentes → reiniciar. |
| `iniciar_servidor.bat` / `detener_servidor.bat` | Levantar/parar el web manualmente. |
| `registrar_servicio.bat` | Registrar `POSFifoSystem` como servicio NSSM. |
| `iniciar_sync.bat` | Correr el daemon de sync manual (`--once` para probar). |
| `registrar_sync_servicio.bat` | Registrar `POSFifoSync` como servicio NSSM. |
| `backup_db.bat` / `programar_backup.bat` | Backup `pg_dump` y su tarea programada. |
| `verificar_sistema.py` | Diagnóstico (DEBUG, SECRET_KEY, ALLOWED_HOSTS, impresoras, etc.). |
| `env_cliente.bat.template` | Plantilla de configuración por instalación. |

### Tooling de dev (`scripts/`, NO se empaqueta)
| Script | Rol |
|---|---|
| `validar_paquete.bat` | **Gate** pre-empaquetado: lint de `.bat` + check en **venv limpio**. |
| `lint_bat.py` | Detecta `echo` con paréntesis dentro de bloques `if(...)` (rompe cmd). |

### Comandos de management relevantes
- Seeds (idempotentes, instalación existente): `bootstrap_negocio`, `sync_permisos`,
  `bootstrap_suscripciones`, `sync_modulos`, `crear_sucursal`.
- Sync: `sincronizar` (daemon), `reconciliar_cloud` (bootstrap único catálogo local→cloud,
  con `--http-timeout` y warm-up para cold-start), `vincular_sucursal_token` (token de
  sucursal, en el cloud), `crear_tokens_api` (token admin, en el cloud).

## 3. Flujos

### A. Construir y validar el paquete (DEV)
1. `deploy\preparar_paquete.bat` → corre el **gate** (`scripts\validar_paquete.bat`):
   - Lint de `.bat` (sin `echo` con paréntesis en bloques).
   - `pip install -r requirements.txt` + `manage.py check` en **venv limpio** (atrapa
     dependencias faltantes).
   - Si el gate falla, **aborta** (no construye paquete inválido).
2. Produce `dist/pos_fifo_system/` (+ `MANIFIESTO.txt`). Copiar a USB.

### B. Actualización in-place (CLIENTE)
Ensayar SIEMPRE primero sobre una **copia** del dump real (restaurar + `migrate` + seeds +
`check` + smoke). Luego en la PC: copiar el paquete a una carpeta staging aparte y correr
`deploy\actualizar.bat` (hace backup automático antes de migrar). Ver el runbook.

### C. Activar sync con la nube
1. En el cloud: asegurar `Sucursal` + `vincular_sucursal_token --sucursal <cod>` (token de
   sucursal) y `crear_tokens_api --usuario <sysadmin>` (token admin).
2. Bootstrap único del catálogo: `reconciliar_cloud --cloud-url <url>` (con el token admin).
3. En `env_cliente.bat`: `SYNC_ENABLED=true`, `CLOUD_API_URL`, `CLOUD_API_TOKEN`
   (token de sucursal), `SUCURSAL_CODIGO`.
4. `iniciar_sync.bat --once` para probar; luego `registrar_sync_servicio.bat`.
5. De ahí en adelante los maestros se editan **desde el portal** (cloud→local).

## 4. Convenciones / lecciones (deployment)

Estas reglas vienen de los hallazgos del go-live de Royal Plast (2026-06-13). El **gate**
de `preparar_paquete.bat` hace cumplir varias automáticamente. Ver memoria
`deployment-lessons-pos-local`.

1. **Backportear SIEMPRE los hotfixes al repo.** Un arreglo hecho solo en la PC del
   cliente reaparece en el próximo paquete. El repo es la fuente de verdad.
2. **Validar antes de empaquetar.** `manage.py check` en venv limpio atrapa deps
   faltantes en `requirements.txt`; el lint atrapa `.bat` rotos.
3. **`.bat`: nunca `echo` con `(` o `)` dentro de un bloque `if(...)`** — cmd cierra el
   bloque antes de tiempo ("no se esperaba . en este momento"). Sácalos del bloque o
   reescribe sin paréntesis.
4. **`SECRET_KEY` segura para `cmd`**: generar alfanumérica (`secrets.token_urlsafe`) y
   escribir entrecomillada (`set "DJANGO_SECRET_KEY=..."`); un `&`/`(`/`)` sin comillas la
   trunca.
5. **Impresora**: una sola fuente (`PRINTER_TERMICA`/`PRINTER_ZEBRA`) mapeada a lo que lee
   la app (`THERMAL_PRINTER_NAME`/`ZEBRA_PRINTER_NAME`). El cajón usa pin `2` o `5`.
6. **Errores de impresión** van a `logs/` (try/except), no a la pantalla — revisar logs.
7. **Orden de seeds**: `crear_sucursal` antes de `bootstrap_negocio` (este engancha la
   sucursal al negocio).
8. **El dump de evaluación** se guarda FUERA de `dist/` (`preparar_paquete.bat` borra `dist/`).

## 5. Historial de cambios

### 2026-06-13 — Go-live Royal Plast (actualización in-place + staging)
- Actualización in-place sobre BD de producción: 37 migraciones OK, data intacta (273
  productos, 320 ventas), sucursal `01` enganchada al negocio, acceso LAN configurado.
- Sync cloud **diferido** (catálogo aún no subido). Detalle e incidencias en el reporte
  `bugs-actualizacion-2026-06-13.md` y en la memoria `royal-plast-golive-2026-06`.

### 2026-06-14 — Hardening (P0/P1/P2)
- **P0 — Backport de hotfixes al repo**: térmica con `getattr` (#1); `CASH_DRAWER_PIN`
  default `2` validado (#2); `djangorestframework-simplejwt` en `requirements.txt` (#6);
  `subirImagen` sin `jsonHeaders()` en multipart (#7); `actualizar.bat` con `crear_sucursal`
  antes de `bootstrap_negocio` (#8); paréntesis en `echo` de `actualizar.bat` (#5).
- **P1 — Endurecer el empaquetado**: nuevo **gate** (`scripts/validar_paquete.bat` +
  `lint_bat.py`) enganchado en `preparar_paquete.bat`; `SECRET_KEY` alfanumérica
  entrecomillada (#9, preventivo); unificación de variables de impresora (#4). El gate
  cazó de inmediato `pytz` y `qrcode` faltantes en `requirements.txt` y 6 `echo`-con-
  paréntesis latentes en `instalar.bat`/`registrar_servicio.bat`, todos corregidos.
- **P2 — Deuda de impresión + sync**: auditoría de impresión remapeada al esquema actual
  de `Auditoria` (`metadata`/`fecha_hora`/`exito`, marca `metadata.origen='impresion'`,
  nuevos `TipoAccion`) + migración `auditoria.0003` (#3); auto-print post-venta deja rastro
  en log (helper `_hook_imprimir_ticket`); `reconciliar_cloud` con `--http-timeout` +
  warm-up para cold-start de staging.

## 6. Pendientes

- **Operativo (no código):** rotar la `SECRET_KEY` truncada de Royal Plast en ventana de
  mantenimiento (al cambiarla se cierran sesiones; re-registrar el servicio después).
- Sync de Royal Plast: ejecutar el bootstrap real del catálogo (`reconciliar_cloud` sin
  `--dry-run`) y encender el daemon cuando el inventario local esté final.
