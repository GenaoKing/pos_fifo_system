# Runbook — Instalación de un cliente nuevo (POS local)

Procedimiento completo para dejar operativo un cliente desde cero.

> **Cómo usar este documento.** Cada paso tiene **un comando exacto** y su
> **salida esperada**. Ejecutar en orden y verificar antes de seguir. Si un paso
> no da lo esperado, buscar el síntoma en la [tabla de fallos](#tabla-de-fallos)
> del final antes de improvisar.
>
> Está escrito para que lo ejecute igual una persona o un agente de IA: sin
> "configure según su caso", sin estado implícito, y con la verificación de cada
> paso incluida.

**Alcance:** instalación NUEVA. Para actualizar una instalación existente, usar
`deploy\actualizar.bat` (hace backup de la BD antes de migrar).

---

## 0. Requisitos previos

| Requisito | Cómo verificarlo | Valor esperado |
|---|---|---|
| Windows con permisos de Administrador | `net session` | sin error |
| Python 3.11 | `python --version` | `Python 3.11.x` |
| PostgreSQL 16 | `psql --version` | `psql (PostgreSQL) 16.x` |
| `nssm.exe` presente | `dir deploy\nssm.exe` | el archivo existe |

Datos a tener a mano **antes de empezar**:

- Nombre del negocio y su preset (`plasticos`, `accesorios_auto`, `retail_general`).
- Código de sucursal (ej. `01`). Debe coincidir **exactamente** con el código de
  la Sucursal en el cloud.
- Nombre exacto de la impresora térmica, como aparece en *Dispositivos e
  impresoras* (suele llevar espacios: `2connect pos`).
- Contraseña para el usuario de PostgreSQL.

---

## 1. Copiar el paquete

El paquete se genera en la PC de desarrollo con `deploy\preparar_paquete.bat`,
que produce la carpeta `dist\pos_fifo_system\`. Copiar **el contenido de esa
carpeta**, no `dist\` completo: de lo contrario `manage.py` queda anidado en
`C:\pos_fifo_system\pos_fifo_system\` y los pasos siguientes fallan.

```bat
xcopy /E /I dist\pos_fifo_system C:\pos_fifo_system
cd /d C:\pos_fifo_system
```

**Verificar:** existe `C:\pos_fifo_system\manage.py`.

> El paquete **no** incluye `env_cliente.env`: la configuración es de cada
> instalación y nunca viaja dentro del paquete.

---

## 2. Crear la configuración

```bat
copy deploy\env_cliente.env.template deploy\env_cliente.env
notepad deploy\env_cliente.env
```

Completar como mínimo:

| Variable | Ejemplo | Nota |
|---|---|---|
| `DB_NAME` | `pos_royal_plast` | sin espacios |
| `DB_USER` | `pos_user` | |
| `DB_PASSWORD` | `<contraseña>` | |
| `DJANGO_SECRET_KEY` | 50 caracteres aleatorios | ver aviso abajo |
| `SUCURSAL_CODIGO` | `01` | igual que en el cloud |
| `NEGOCIO_NOMBRE` | `Royal Plast` | |
| `NEGOCIO_PRESET` | `plasticos` | |
| `THERMAL_PRINTER_NAME` | `2connect pos` | **con sus espacios, sin comillas** |

> **Los valores se escriben tal cual.** Este archivo NO pasa por el intérprete de
> `cmd`, así que espacios, `&` y paréntesis se conservan íntegros. No hace falta
> escapar ni entrecomillar nada — y de hecho **no** hay que poner comillas: se
> guardarían como parte del valor.
>
> Esto es lo que arregla el bug #9, donde un `&` truncó el `DJANGO_SECRET_KEY` de
> un cliente a 5 caracteres sin que nadie lo notara.

Generar una `SECRET_KEY` válida:

```bat
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

> Esto es solo para la creación inicial (el `.env` todavía no existe). Para
> **rotar** la key de una instalación ya funcionando, no editar el archivo a
> mano: usar `python manage.py rotar_secret_key`, que reemplaza únicamente esa
> línea, deja un respaldo recuperable de la anterior y nunca imprime el valor.

---

## 3. Entorno virtual y dependencias

```bat
python -m venv venv
call venv\Scripts\activate.bat
python -m pip install --no-index --find-links wheelhouse\windows-py311 --require-hashes -r requirements.txt
```

El paquete Windows C06.1 trae `wheelhouse\windows-py311`; este paso no debe
consultar PyPI. Si se recibió deliberadamente un paquete antiguo **sin**
wheelhouse, registrar esa diferencia y usar `python -m pip install
--require-hashes -r requirements.txt` con red antes de continuar. No presentar
esa ruta como ensayo offline.

**Verificar:**

```bat
python -c "import dotenv, django, waitress; print('deps OK')"
```

Salida esperada: `deps OK`

---

## 4. Base de datos

```bat
psql -U postgres -c "CREATE USER pos_user WITH PASSWORD 'LA-CONTRASENA';"
psql -U postgres -c "CREATE DATABASE pos_royal_plast OWNER pos_user;"
```

**Verificar:** `psql -U pos_user -d pos_royal_plast -c "SELECT 1;"` devuelve `1`.

> Usar los valores reales de `DB_USER` / `DB_NAME` del `.env`.

---

## 5. Migraciones

```bat
python manage.py migrate --settings=config.settings_production
```

**Salida esperada:** una lista de `Applying ...  OK` y ningún traceback.

---

## 6. Datos iniciales

Ejecutar **en este orden**. El orden importa: `bootstrap_negocio` engancha la
sucursal al negocio, así que la sucursal debe existir antes.

```bat
python manage.py crear_sucursal --codigo 01 --nombre "Royal Plast" --settings=config.settings_production
python manage.py crear_config_inicial --sucursal 01 --nombre "Royal Plast" --preset plasticos --settings=config.settings_production
python manage.py bootstrap_negocio --nombre "Royal Plast" --settings=config.settings_production
python manage.py sync_permisos --settings=config.settings_production
python manage.py bootstrap_suscripciones --settings=config.settings_production
python manage.py sync_modulos --settings=config.settings_production
python manage.py collectstatic --noinput --settings=config.settings_production
```

> **El `--sucursal` de `crear_config_inicial` NO es opcional, y `bootstrap_suscripciones` tampoco.**
>
> `bootstrap_suscripciones` deriva qué módulos otorgarle al negocio leyendo los
> flags de las `ConfiguracionNegocio` **ligadas a las sucursales de ese negocio**.
> Si la config se creó sin `--sucursal`, esa consulta no encuentra nada y el
> negocio se queda **sin módulos vendibles**: se apaga la impresión de tickets,
> las cotizaciones, las etiquetas Zebra, la financiación y el e-CF. En silencio,
> sin ningún error.
>
> Es un fallo medido y reproducible (BUG-D en `docs/BUGS.md`). La firma para
> reconocerlo: la tabla `negocio_modulos` contiene **solo `cuentas_por_cobrar`**.
>
> El paso 7 lo detecta. No saltarlo.

---

## 7. Verificar antes de registrar servicios

```bat
python manage.py verificar_instalacion --settings=config.settings_production
```

**Salida esperada:** `RESULTADO: instalacion sana.`

Este paso es el **gate**: no seguir si algo aparece en rojo. Revisa configuración,
conexión a BD, migraciones pendientes, seeds y módulos activos.

---

## 8. Registrar el servicio del POS

```bat
deploy\registrar_servicio.bat
```

(Como Administrador.)

**Verificar que solo se registraron dos variables:**

```bat
deploy\nssm.exe get POSFifoSystem AppEnvironmentExtra
```

Salida esperada — exactamente estas dos líneas:

```
DJANGO_SETTINGS_MODULE=config.settings_production
POS_ENV_FILE=C:\pos_fifo_system\deploy\env_cliente.env
```

> Si aparecen más variables, el servicio quedó con el formato antiguo: las
> variables viejas del servicio **le ganan al `.env`** y los cambios de
> configuración no van a tomar efecto. Volver a ejecutar el script.

**Verificar que responde:**

```bat
curl http://127.0.0.1:8080/api/v1/health/
```

Salida esperada: un JSON con `"status":"ok"`.

---

## 9. Cambiar configuración (de aquí en adelante)

Este es el flujo normal de mantenimiento:

`/admin/` del POS local exige una cuenta activa con `is_staff` y rol
`SYSADMIN`. El rol `ADMIN` del negocio, incluso con `is_staff`, no abre
Django Admin. En cloud la ruta no se monta. Conservar separadas las credenciales
del POS y la identidad del portal.

```bat
notepad deploy\env_cliente.env
deploy\nssm.exe restart POSFifoSystem
```

**Nunca hace falta volver a registrar el servicio.** Si alguien dice que hay que
re-registrarlo para cambiar un valor, está siguiendo el procedimiento anterior a
la Fase 4.

---

## 10. Activar el sync con la nube (opcional)

Solo cuando el cloud ya tenga la Sucursal creada.

1. **En el cloud DB-per-tenant**, generar el token dentro del tenant:

   ```bash
   python manage.py with_tenant --tenant <tenant_key> -- vincular_sucursal_token --sucursal 01 --settings=config.settings_cloud
   ```

   El comando registra tanto el DRF Token de la base tenant como su hash en el
   control plane. En un cloud mono-tenant se puede usar el comando directo.
   Para rotar ambos registros, repetir con `--regenerar` y reemplazar el token
   local antes de reanudar el daemon.

2. **En el local**, editar `deploy\env_cliente.env`:

   ```
   SYNC_ENABLED=true
   CLOUD_API_URL=https://<url-del-cloud-de-produccion>
   CLOUD_API_TOKEN=<token del paso 1>
   ```

3. Registrar el daemon y verificar:

   ```bat
   deploy\registrar_sync_servicio.bat
   python manage.py verificar_sync --settings=config.settings_production
   ```

**Salida esperada:** `RESULTADO: sin perdida detectada.` y la configuración sin
alertas.

> Los eventos se encolan **aunque `SYNC_ENABLED=false`**. Encender el sync más
> tarde recupera el histórico en vez de perderlo; lo que no se hace sin sync es
> enviarlos.

---

## Tabla de fallos

| Síntoma observable | Causa | Arreglo |
|---|---|---|
| El POS **no imprime tickets** y no muestra ningún error | La `ConfiguracionNegocio` no está ligada a la sucursal, así que `bootstrap_suscripciones` no encontró flags de donde derivar módulos. Firma: `negocio_modulos` tiene solo `cuentas_por_cobrar` | Ligar la config a la sucursal y re-ejecutar `bootstrap_suscripciones`. Confirmar con `verificar_instalacion` |
| Cotizaciones / Zebra / CxC "desaparecieron" tras una actualización | Mismo caso que la fila anterior: se apagan todos los vendibles a la vez | Igual que arriba |
| Se cambia un valor del `.env` y **no toma efecto** | El servicio quedó con variables del formato antiguo, que le ganan al archivo | Re-ejecutar `deploy\registrar_servicio.bat` y verificar el paso 8 |
| Las ventas **no llegan al cloud**, sin errores ni pendientes | El servicio arrancó sin `SYNC_ENABLED`: las ventas no encolan evento (BUG-A) | `verificar_sync` lo reporta en rojo. Corregir el `.env` y reiniciar |
| Las cuentas por cobrar **no aparecen** en el portal | Cliente sin cédula: el cloud no podía identificarlo (BUG-C) | Requiere cloud y local actualizados; después `verificar_sync --reintentar-descartados --ejecutar` |
| `ValueError: invalid literal for int()` al arrancar | Variable numérica vacía | Poner un valor o borrar la línea del `.env` |
| Sesiones que se cierran solas / login que no persiste | `DJANGO_SECRET_KEY` truncada (bug #9) | `verificar_instalacion` la marca como crítica. Generarla de nuevo (paso 2) |
| El nombre de la impresora "no se encuentra" | El valor quedó con comillas pegadas | Quitar las comillas en el `.env`; en este formato no se usan |
| `manage.py migrate` falla por dependencia faltante | El paquete se instaló sin `requirements.txt` completo | `pip install -r requirements.txt` dentro del venv |
| El servicio no arranca y `service_stderr.log` menciona configuración | Falta una variable crítica | El log da el **nombre exacto**; completarla en el `.env` |
| `manage.py` no aparece en `C:\pos_fifo_system` después de copiar | Se copió `dist\` completo, pero el BAT entrega `dist\pos_fifo_system\` (A09-DEP-013) | Copiar la carpeta interna al destino, comprobar `manage.py` antes de crear el venv |
| El primer pull da **401** aunque el token DRF existe en el tenant cloud | En DB-per-tenant falta o difiere el hash del token en control plane (A09-DEP-001) | Confirmar tenant y sucursal; usar el comando corregido del paso 10 dentro de `with_tenant`, o `--regenerar` para rotar ambos registros y actualizar el `.env`. No publicar el token en logs ni tickets |
| `registrar_sync_servicio.bat` dice que `SYNC_ENABLED`/token faltan aunque estén en `.env` | BAT anterior al PR #32 todavía lee variables heredadas (A09-DEP-003) | Verificar SHA del paquete y ejecutar `venv\Scripts\python.exe deploy\check_sync_env.py deploy\env_cliente.env`; actualizar el BAT, sin volcar el `.env` |
| Portal inicia sesión, pero productos/reportes dan **403** | La cuenta solo tiene asignación RBAC de sucursal y la vista exige permiso global (A09-DEP-005) | Revisar el alcance de la asignación y la política del negocio; conceder global solo si está autorizado. No confundir con error de token del daemon |
| Crear/asignar un rol por API devuelve **500 `AUDIT_CONTEXT_INVALID`** | Backend anterior al PR #31 usa el slug comercial en auditoría tenant (A09-DEP-002) | Verificar SHA de backend y contexto `tenant_key`; promocionar la corrección. No desactivar auditoría ni repetir a ciegas |
| Pull RBAC deja asignaciones en `DiferidoSync` para usuarios inexistentes | El motor no provisiona usuarios locales desde sync (A09-DEP-006) | Revisar identidades y política de onboarding; no crear cuentas activas de relleno. El placeholder inactivo de la PC QA fue solo laboratorio |
| Pull de configuración exige `emisor_activo` | e-CF está habilitado en la configuración efectiva sin emisor local (A09-DEP-007) | Aprovisionar el emisor real; el override que deshabilitó e-CF fue solo de `QA-PC-01` en staging |
| La prueba HTTP de impresión devuelve éxito, pero no sale papel | El HTTP no verifica el trabajo físico (A09-DEP-011) | Comprobar nombre, puerto, estado/cola de Windows, cuenta NSSM y ticket físico; no cerrar el gate visual con un 200 |

---

## Referencias

- Actualizar una instalación existente: `deploy\actualizar.bat`
- Convertir una configuración antigua: `python manage.py migrar_env_cliente`
- Diagnóstico de instalación: `python manage.py verificar_instalacion`
- Diagnóstico de sincronización: `python manage.py verificar_sync`
- Probar cambios de sync sin desplegar: `docs/runbooks/PRUEBAS_SYNC_LOCAL.md`
- Detalle de los bugs citados: `docs/BUGS.md`
- Incidentes reales del alta staging + PC QA: `docs/handoffs/cierre_prod/A09-incidentes-deployment-2026-09-26.md`
