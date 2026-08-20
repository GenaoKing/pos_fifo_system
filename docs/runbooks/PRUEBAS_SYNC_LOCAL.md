# Runbook — Probar cambios de sync sin desplegar (cloud local)

Objetivo: validar un cambio del contrato de sincronización **con código nuevo en
ambos lados**, antes de tocar Azure y antes de visitar a un cliente.

> **Por qué existe.** Los entornos cloud van por CI (`develop`→dev, `main`→prod),
> así que hasta que un cambio no está desplegado, apuntar el POS local a ellos
> prueba el código **viejo** del otro lado. Eso da falsa confianza: se ve "verde"
> algo que en realidad no se ejercitó.
>
> La Fase 2 encontró **dos bugs** por hacer esta prueba, que los tests con mocks
> no podían ver: un pull inicial que aplicaba 416 items sobre un catálogo de 273,
> y una incompatibilidad que perdía 28 productos.

---

## Topología

```
  POS local (rig)                        Cloud local
  settings_demo_branch      --HTTP-->    settings_cloud_local
  BD pos_fifo_demo_branch                BD pos_fifo_cloud_local
  puerto: (no sirve HTTP)                puerto 8001
```

Las dos son BDs PostgreSQL locales. **El POS nunca se conecta a la BD del cloud**:
lo único que cruza es HTTP con un token de sucursal, igual que en producción.

---

## 1. Preparar el cloud local

Se siembra con `royal_eval` (copia local del catálogo real de Royal Plast). Sus
**273 productos superan el page size de 200**, así que ejercita paginación de
verdad — con un catálogo pequeño los bugs de cursor no aparecen.

```bash
# BD del cloud, a partir del catálogo real
psql -h localhost -U pos_user -d postgres -c "DROP DATABASE IF EXISTS pos_fifo_cloud_local;"
createdb -h localhost -U pos_user -T template0 pos_fifo_cloud_local
pg_dump -h localhost -U pos_user royal_eval | psql -h localhost -U pos_user -d pos_fifo_cloud_local -q

# Migrar al código actual
python manage.py migrate --settings=config.settings_cloud_local
```

Crear el token de sync del lado cloud:

```bash
python manage.py shell --settings=config.settings_cloud_local -c "
from rest_framework.authtoken.models import Token
from apps.sucursales.models import Sucursal
from apps.usuarios.models import Usuario
suc = Sucursal.objects.first()
if not suc.usuario_servicio_id:
    u,_ = Usuario.objects.get_or_create(username='svc_sucursal_01',
          defaults={'email':'svc01@local','rol':'CAJERA','activo':True})
    suc.usuario_servicio = u; suc.save(update_fields=['usuario_servicio'])
tok,_ = Token.objects.get_or_create(user=suc.usuario_servicio)
print('TOKEN:', tok.key)"
```

Levantarlo:

```bash
python manage.py runserver 8001 --noreload --settings=config.settings_cloud_local
```

## 2. Apuntar el rig al cloud local

```bash
export CLOUD_API_URL=http://127.0.0.1:8001
export CLOUD_API_TOKEN=<token del paso 1>
export SYNC_ENABLED=true
export SUCURSAL_CODIGO=01          # el código de la sucursal en la BD del RIG
export SYNC_HTTP_TIMEOUT=30

python manage.py sincronizar --once --only-pull --settings=config.settings_demo_branch
```

> **Trampa que ya nos mordió:** no exportar `PGSSLMODE=require` en la misma shell.
> Se hereda al proceso de Django y rompe la conexión al PostgreSQL local, que no
> habla SSL. Aislar las variables de `psql` en la línea del comando.

## 3. Qué verificar

**Pull (Fase 2).** Editar en el cloud el producto **alfabéticamente último** — el
que caía en la última página y se perdía — y volver a pullear:

```bash
python manage.py shell --settings=config.settings_cloud_local -c "
from decimal import Decimal
from apps.productos.models import Producto
p = Producto.objects.order_by('-nombre').first()
p.precio_venta = Decimal('9999.99'); p.save()
print('editado:', p.nombre)"
```

El pull siguiente debe traer **exactamente 1 producto**. Si trae más que el total
del catálogo, las páginas se están solapando.

**Push (Fase 1).** Crear un cliente **sin cédula** con una venta a crédito y
sincronizar. En el cloud deben aparecer los tres: el cliente (con
`origen_sucursal` / `origen_id_local`), la venta **con `cliente_id` no nulo**, y
la `CuentaPorCobrar` con sus cuotas.

**Idempotencia.** Re-encolar los mismos eventos y empujar de nuevo: no debe
duplicarse nada.

**Estado del cursor.**

```bash
python manage.py verificar_sync --settings=config.settings_demo_branch
```

La sección `CURSORES DE PULL` marca en rojo cualquier cursor **BLOQUEADO**, o sea
un registro del portal que falla al aplicarse y está frenando la marca de agua.

---

## 4. Probar compatibilidad contra un cloud VIEJO

Responde: *¿es seguro desplegar el POS local antes que el cloud?*

Se levanta una segunda instancia desde un commit anterior, contra la **misma BD**
(Django ignora las columnas que su código no conoce):

```bash
git worktree add --detach /ruta/temporal/cloud_viejo <commit-anterior>
cp config/settings_cloud_local.py /ruta/temporal/cloud_viejo/config/
cd /ruta/temporal/cloud_viejo
python manage.py runserver 8002 --noreload --settings=config.settings_cloud_local
```

Apuntar el rig a `:8002` **con una BD local fresca** (simula instalación nueva) y
comparar los productos distintos que llegan contra el total del cloud.

Limpieza al terminar:

```bash
git worktree remove /ruta/temporal/cloud_viejo --force
```

**Resultado registrado el 2026-08-19** (antes del fallback de compatibilidad):

| Escenario | Aplicados | Distintos que llegaron |
|---|---|---|
| Cliente nuevo → cloud viejo | 432 | **245 de 273 (28 perdidos)** |
| Cliente nuevo → cloud nuevo | 273 | 273 de 273 |

Por eso el cliente ahora **detecta** que el cloud no ordena por el cursor y
degrada a paginación legacy con un WARNING. Repetida la prueba con el fallback:
273 de 273.

---

## 5. Acceso a la BD de producción (operación administrativa)

Necesario para leer tokens de un tenant o diagnosticar. **Solo lectura salvo que
haya una razón explícita.**

El firewall del Flexible Server filtra por IP y las IPs residenciales cambian por
DHCP, así que esto se rompe cada tanto:

```bash
# 1. Ver la IP actual
curl -s https://api.ipify.org

# 2. Ver las reglas existentes
az postgres flexible-server firewall-rule list \
   -g posfifo-platform-rg -s posfifoplatformpg -o table

# 3. Agregar la IP (si no cae dentro de un rango ya permitido)
az postgres flexible-server firewall-rule create \
   -g posfifo-platform-rg -s posfifoplatformpg \
   --rule-name operador-$(date +%Y%m%d) \
   --start-ip-address <IP> --end-ip-address <IP>
```

> **Higiene:** las reglas `ClientIPAddress_*` que crea el portal de Azure se
> acumulan con IPs que ya no existen. Conviene borrar las viejas de vez en cuando;
> cada regla es una IP con acceso al servidor que aloja a **los dos clientes de
> producción**.

Credenciales (nunca hardcodearlas):

```bash
DBUSER=$(az keyvault secret show --vault-name posfifoprodkv --name db-user --query value -o tsv)
export PGPASSWORD=$(az keyvault secret show --vault-name posfifoprodkv --name db-password --query value -o tsv)
export PGSSLMODE=require
```

Token de sync en claro de un tenant:

```bash
psql -h posfifoplatformpg.postgres.database.azure.com -U "$DBUSER" -d tnt_<tenant> -tAc \
  "SELECT u.username, t.key FROM authtoken_token t JOIN usuarios u ON u.id=t.user_id;"
```

### Reglas de seguridad de estas pruebas

- **`royalplastdemo` es el único tenant de prueba autorizado.** `royalplast` y
  `skperformance` son clientes reales: nunca escribirles datos de prueba.
- Sus tokens están en `deploy/env_*_local.bat` (gitignored). Tener el token a mano
  **no** lo vuelve un destino válido de prueba.
- Los archivos temporales de settings con contraseñas van al scratchpad, fuera del
  repo, y se borran al terminar.

---

## 6. Limpieza

```bash
git worktree remove <ruta>/cloud_viejo --force
psql -h localhost -U pos_user -d postgres -c "DROP DATABASE IF EXISTS pos_fifo_compat;"
# pos_fifo_cloud_local se puede conservar: recrearla cuesta un pg_dump
```

`config/settings_cloud_local.py` sí vive en el repo: es una herramienta de prueba
reutilizable, no un artefacto de una sesión.
