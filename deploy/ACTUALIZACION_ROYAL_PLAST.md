
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

## 4. Bootstrap inicial del catálogo (local → cloud) — UNA sola vez

> Hacerlo **antes** de encender el daemon de sync, y después de actualizar el POS.

Contexto: el **PULL** baja maestros cloud → local; el **PUSH** del daemon solo sube
ventas nuevas (no hace backfill). El catálogo autoritativo de Royal Plast hoy vive
**local**, así que primero lo subimos al cloud con el comando `reconciliar_cloud`.
Después de esto, el flujo correcto es el normal: **portal → local** (el cloud autora
los maestros y la sucursal los baja).

### 4.1 En el CLOUD: token de un usuario SYSADMIN/ADMIN
El push se autentica con un token DRF de un usuario **administrador** del cloud
(no el token de sucursal, que es de solo lectura):
```
python manage.py crear_tokens_api --usuario <sysadmin>
```
Copia el token.

### 4.2 En el LOCAL: correr el reconciliador
```
set CLOUD_ADMIN_TOKEN=<token-del-paso-4.1>
:: 1) Ensayo (no escribe nada, muestra qué haría)
python manage.py reconciliar_cloud --cloud-url https://<url-cloud-produccion> --dry-run
:: 2) Aplicar (sube categorías, productos y clientes)
python manage.py reconciliar_cloud --cloud-url https://<url-cloud-produccion>
```
Notas:
- Es **idempotente**: por defecto solo crea lo que falta (clave natural:
  categoría=nombre, producto=sku, cliente=cédula/nombre). Re-correrlo es seguro.
- Con `--actualizar` además hace PATCH de lo que ya existe en el cloud.
- Maneja el **throttling** del cloud automáticamente (espera y reintenta ante 429);
  para un catálogo grande puede tardar varios minutos — es normal.
- Omite lo que el portal no acepta y lo reporta: cliente CONTADO (genérico interno),
  productos con precio ≤ 0, y productos cuya categoría no se pudo crear.
- Si usas `--solo`, corre **categorías antes que productos** (la FK es obligatoria).

### 4.3 Verificar
Revisa el resumen por entidad (`creados/actualizados/ya_existian/omitidos/errores`)
y, si hubo `omitidos`/`errores`, corrige esos registros en el POS local y vuelve a
correr el comando (idempotente).

Solo cuando el catálogo está en el cloud, continúa con el paso 3 (activar el daemon).
A partir de ahí, edita los maestros **desde el portal**, no localmente.

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
