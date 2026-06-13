# Runbook — Actualización de Royal Plast (POS local) + activación de sync cloud

Guía operativa para llevar la instalación existente de Royal Plast a la versión
actual y dejarla sincronizando con la nube.

> **Idea clave:** esto es una **actualización in-place**, no un install fresco. Se
> preservan: base de datos, `deploy/env_cliente.bat`, `media/`, `logs/`, `backups/`
> y el `venv`. Solo se reemplaza el **código** y se aplican **migraciones + seeds**.

Features que entran con esta actualización: RBAC/permisos, módulos/suscripciones,
cuentas por cobrar, cotizaciones, sucursales, e-CF y el **motor de sincronización**.

---

## 0. Antes de empezar — preparar el paquete (en la PC de desarrollo)

1. Asegúrate de tener `config/settings_production.py` (ya en el repo) y los scripts
   nuevos en `deploy/` (`actualizar.bat`, `registrar_sync_servicio.bat`,
   `iniciar_sync.bat`).
2. Genera el paquete:
   ```
   deploy\preparar_paquete.bat
   ```
   Produce `dist\pos_fifo_system\` + `dist\MANIFIESTO.txt`.
3. Copia esa carpeta `dist\pos_fifo_system\` a una USB.

---

## 1. Ensayo de migración sobre una COPIA de la BD real (obligatorio)

No migrar producción a ciegas. Primero ensayar con el backup real de Royal Plast.

1. Pídele a Royal Plast (o toma tú) un `pg_dump` de su BD:
   ```
   pg_dump -U <db_user> -h localhost -F c -b -f royal_backup.dump <db_name>
   ```
2. En tu PC, restaura una copia:
   ```
   createdb -U postgres royal_plastic_pos_copia
   pg_restore -U postgres -d royal_plastic_pos_copia royal_backup.dump
   ```
3. Apunta a esa copia (variables de entorno o un settings temporal) y corre, con la
   versión NUEVA del código:
   ```
   set DB_NAME=royal_plastic_pos_copia
   python manage.py migrate --settings=config.settings_production
   python manage.py bootstrap_negocio --nombre "Royal Plast" --settings=config.settings_production
   python manage.py sync_permisos --settings=config.settings_production
   python manage.py bootstrap_suscripciones --settings=config.settings_production
   python manage.py sync_modulos --settings=config.settings_production
   python manage.py crear_sucursal --codigo SD-001 --nombre "Royal Plast" --settings=config.settings_production
   python manage.py check --settings=config.settings_production
   ```
4. Smoke funcional sobre la copia (login, abrir POS, una venta, CxC, reportes).
   Confirma que **no se pierde data** y que no hay errores de migración.

> Solo cuando este ensayo pasa limpio se actualiza producción.

---

## 2. Actualizar la instalación de Royal Plast (en su PC)

1. Copia el paquete nuevo a una carpeta **staging aparte** del install vivo, p. ej.
   `C:\pos_update\` (NO encima de `C:\pos_fifo_system\`).
2. Click derecho → **Ejecutar como administrador**:
   ```
   C:\pos_update\deploy\actualizar.bat
   ```
3. Cuando pregunte la ruta del POS instalado, confirma (default `C:\pos_fifo_system`).
4. El script hace, en orden: detener servicios → **backup BD** → copiar código →
   `pip install` → `migrate` → `collectstatic` → seeds idempotentes → reiniciar el
   servicio web `POSFifoSystem`.
5. Verifica el POS en `http://localhost:<SERVER_PORT>` (mira `env_cliente.bat`).

El backup pre-update queda en `C:\pos_fifo_system\backups\..._PRE_UPDATE_*.dump`.

---

## 3. Activar la sincronización con la nube

> Hacerlo **después** de validar que el POS actualizado funciona.

### 3.1 En el CLOUD (producción)
1. Verifica/crea la `Sucursal` de Royal Plast (mismo código que usarás local, p.ej. `SD-001`).
2. Genera el token de la sucursal:
   ```
   python manage.py vincular_sucursal_token --sucursal SD-001
   ```
   Copia el token (DRF no lo muestra de nuevo).

### 3.2 En el LOCAL (Royal Plast)
1. Edita `C:\pos_fifo_system\deploy\env_cliente.bat`:
   ```
   set SYNC_ENABLED=true
   set SUCURSAL_CODIGO=SD-001
   set CLOUD_API_URL=https://<url-cloud-produccion>
   set CLOUD_API_TOKEN=<token-del-paso-3.1>
   ```
2. Prueba una pasada antes de dejarlo como servicio:
   ```
   deploy\iniciar_sync.bat --once
   ```
   Debe mostrar métricas PUSH/PULL sin errores de auth/conexión.
3. Registra el daemon como servicio (admin):
   ```
   deploy\registrar_sync_servicio.bat
   ```
   Crea/levanta el servicio `POSFifoSync`. Logs en `logs\sync.log`.

---

## 4. ⚠️ Reconciliación de datos inicial (decidir antes de encender el PULL)

- El **PULL** baja maestros **cloud → local** (`update_or_create`, no borra).
- El **PUSH** sube solo **eventos nuevos** (ventas posteriores a `SYNC_ENABLED=true`);
  **no** hace backfill del histórico.
- El catálogo autoritativo de Royal Plast hoy vive **local**.

Opciones (confirmar con Santiago):
- **A (recomendada):** exportar el catálogo local e importarlo al cloud primero; luego
  encender el ciclo completo (push + pull).
- **B:** arrancar con `--only-push` (solo subir ventas) hasta consolidar el catálogo
  en el cloud, y recién ahí habilitar el pull.

Para forzar solo-push temporalmente: `deploy\iniciar_sync.bat --only-push` (o ajustar
el servicio). El daemon por defecto hace ciclo completo.

---

## 5. Rollback (si la migración o el arranque fallan)

1. Detener servicios: `nssm stop POSFifoSync` y `nssm stop POSFifoSystem`.
2. Restaurar la BD desde el backup pre-update:
   ```
   pg_restore -c -U <db_user> -d <db_name> "backups\..._PRE_UPDATE_*.dump"
   ```
3. Volver a poner el código anterior (mantén una copia del install previo antes de
   actualizar, o usa el último paquete bueno).
4. Iniciar `POSFifoSystem`.

---

## 6. Checklist de validación final

- [ ] `manage.py check` sin errores en producción.
- [ ] Login y POS funcionan; se puede facturar una venta.
- [ ] CxC, cotizaciones y reportes cargan.
- [ ] RBAC: roles/permiso aplican (un usuario no-admin no ve lo que no debe).
- [ ] `POSFifoSystem` (web) iniciado como servicio.
- [ ] `POSFifoSync` iniciado; `logs\sync.log` muestra ciclos PUSH/PULL OK.
- [ ] Una venta nueva aparece en el cloud tras un ciclo de sync.
