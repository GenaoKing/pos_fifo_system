# Handoff C01 — Windows/dotenv (entrega parcial 1)

Estado: **EN_CURSO** (primera entrega de C01; quedan items del encargo sin
cerrar, listados abajo). Fecha: **2026-09-10**.
Agente: B / Claude. Encargo: [CIERRE_PROD_CLAUDE.md](../../planes/CIERRE_PROD_CLAUDE.md#c01--actualización-windows-y-dotenv).

## SHA base / resultado

- Base consumida: `c4af604f48a9334cbf3cc35a9b866046e2ed052e` (bootstrap A00),
  worktree `C:/Proyectos/pos_fifo_system_cierre_claude`, rama
  `claude/cierre-prod-C01`.
- Resultado: commit local en esa rama con los archivos listados abajo. **No
  publicado a `origin`** (evita disparar CI/deploy de `develop`); pendiente de
  decidir con el usuario si se abre PR desde esta rama.

## Qué cubre esta entrega

Un hallazgo concreto reproducido y corregido, con foco en el punto 2 del
encargo C01 ("no implementar un parser `.env` con `for /f`...") y el punto 4
("probar `!`, `%`, `&`, `#`, `=`, comillas, Unicode y espacios").

### Hallazgo: `deploy/actualizar.bat` reintroducía la clase de bug #9

`actualizar.bat` leía `env_cliente.env` con:

```bat
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("...") do (
    if not "%%A"=="" set "%%A=%%B"
)
```

Ese patrón vuelve a pasar el valor ya sustituido por el parser de `cmd`, que
busca pares `%NOMBRE%` para expandir — exactamente el defecto de bug #9
(`DJANGO_SECRET_KEY` truncada por un `&`, documentado en `docs/BUGS.md` y en
`_EXPANSION_CMD` de `migrar_env_cliente.py`). La diferencia crítica: este
bloque alimentaba **`DB_PASSWORD` → `PGPASSWORD`** para el `pg_dump` de la
FASE 2 — el backup que existe como red de seguridad de toda la actualización.
Un valor con `&`, `%` pareado o comillas podía corromper la contraseña en
silencio justo en el paso pensado para poder revertir.

No estaba acreditado como cerrado en ningún documento — es un hallazgo nuevo
de esta revisión, no un ítem reabierto del inventario A00.

### Arreglo

- **`deploy/preflight_actualizar.py`** (nuevo, sin dependencias del proyecto
  Django): puente entre `actualizar.bat` y `env_cliente.env` con tres
  subcomandos:
  - `campo NOMBRE ARCHIVO` — imprime el valor crudo de una variable (lee con
    `python-dotenv`, ya presente en el venv VIEJO de cualquier cliente que ya
    tenga `.env`, porque la app no arranca sin él).
  - `backup ARCHIVO DESTINO` — corre `pg_dump` con los datos de conexión del
    `.env`; `DB_PASSWORD` viaja en el **entorno del subproceso** (`env=` de
    `subprocess.run`), nunca en una variable de BAT ni en argv.
  - `sync-configurado ARCHIVO` — exit 0/1 según `SYNC_ENABLED`/`CLOUD_API_TOKEN`
    sin imprimir el token nunca.
  - `riesgoso-como-argumento NOMBRE ARCHIVO` — exit 0 si el valor trae
    comillas, `!` o una expansión de variable pareada (ver limitación abajo).
- **`deploy/actualizar.bat`**: reemplaza el `for /f` por llamadas a
  `preflight_actualizar.py`. Los valores no sensibles (`DB_NAME`, `DB_USER`,
  `SUCURSAL_CODIGO`, `NEGOCIO_NOMBRE`) se vuelcan a variables de BAT con
  `set /p VAR=<archivo` — lectura literal de archivo, confirmada por prueba
  directa en `cmd.exe` que NO reinterpreta `%`, `&`, `!` ni comillas (ver
  «Pruebas» abajo). El formato `.bat` legado sigue sin tocarse: sus variables
  siguen viniendo de `call env_cliente.bat` nativo, como siempre.
- **`apps/configuracion/tests/test_preflight_actualizar.py`** (nuevo): 15
  tests cargando el script por ruta con `importlib` (vive fuera de `apps/`,
  sin dependencias Django, corre con el venv viejo antes del `pip install`).

## Limitación encontrada y NO resuelta en esta entrega

Reproducida en `cmd.exe` real (no solo inferida): un valor **correctamente
almacenado** en una variable de BAT (confirmado con `set VARNAME`, que no pasa
por expansión) puede llegar **alterado** si después se usa como argumento
entrecomillado de línea de comandos — `cmd` no soporta comillas anidadas, así
que una comilla o un `!` embebidos en el valor se pierden en el viaje. Esto
afecta a `NEGOCIO_NOMBRE`, que `actualizar.bat` pasa como
`--nombre "%NEGOCIO_NOMBRE%"` a `crear_sucursal` (`apps/sucursales`) y
`bootstrap_negocio` (`apps/permisos`) — **ambos archivos de Codex**, fuera de
mi propiedad.

Mitigación aplicada (dentro de mi propiedad): `riesgoso-como-argumento`
detecta el caso y `actualizar.bat` imprime un aviso explícito para que el
operador verifique el nombre creado, en vez de fallar en silencio.

**Solicitud a Codex:** que `crear_sucursal`/`bootstrap_negocio` puedan
resolver el nombre del negocio leyendo `NEGOCIO_NOMBRE` del entorno/`.env`
directamente (ya lo carga `settings.py` vía `load_dotenv` en el propio
proceso, sin pasar por `cmd`) en vez de depender exclusivamente de `--nombre`
por argv. Mientras no exista ese contrato, el aviso queda como mitigación.

## Pruebas

Automatizadas (venv `.venv` de este worktree, `pos_cierre_claude`):

```
POS_ENV_FILE=<worktree>/deploy/env_cliente.env \
  .venv/Scripts/python.exe manage.py test apps.configuracion --settings=config.settings_development
# 89 tests, OK (15 de preflight_actualizar + 11 de rotar_secret_key +
# los 63 ya existentes de migrar_env_cliente/env_loading/etc., sin regresiones)
```

Manuales, en `cmd.exe` real de este host (no simuladas — invocaciones directas
del intérprete, sin tocar servicios ni BDs reales):

- `set /p VAR=<archivo` con un valor conteniendo `Repuestos R&B 100% "Ferretería" Peña!`
  confirmado íntegro vía `set VARNAME` (consulta que no pasa por expansión).
- El mismo valor pasado como `"%VAR%"` a un subproceso pierde las comillas
  embebidas y el `!` final — reproducido, no solo señalado (ver limitación).
- `riesgoso-como-argumento` marca ese caso y no marca `Royal Plast` (caso
  limpio).
- Backup: test automatizado mockea `subprocess.run` y confirma que
  `PGPASSWORD` llega por `env`, nunca en el argv de `pg_dump`.
- `rotar_secret_key` corrido contra una copia real de `deploy/env_cliente.env`
  de este worktree (no un fixture): rotó únicamente esa línea, dejó el resto
  del archivo byte a byte idéntico (`diff` sin salida) y generó el respaldo
  `.bak_<timestamp>`.

## Punto 5 — rotación de `DJANGO_SECRET_KEY`: CERRADO

Nuevo comando `apps/configuracion/management/commands/rotar_secret_key.py`:

- Opera solo sobre el `.env` canónico; falla explícito (con sugerencia de
  `migrar_env_cliente`) si solo existe el `.bat` legado.
- Reemplaza únicamente la línea `DJANGO_SECRET_KEY=...` (última ocurrencia
  activa si hubiera duplicados, igual que resuelve `dotenv_values`), preserva
  comentarios/orden/otras variables, deja respaldo recuperable y nunca imprime
  ningún valor (ni el viejo ni el nuevo) en stdout.
- Pide confirmación interactiva salvo `--forzar` (rotar invalida sesiones
  abiertas).
- Precedencia documentada en el docstring del comando (acordada con A01 vía
  CT-03): `override=False` hace que una variable de PROCESO real le gane al
  `.env`, pero `DJANGO_SECRET_KEY` no se fija así en ningún lado de la
  operación normal — el servicio NSSM solo trae `DJANGO_SETTINGS_MODULE` y
  `POS_ENV_FILE` (confirmado en el punto 6, abajo).
- Runbooks de instalación/actualización actualizados para apuntar al comando
  en vez de "generar y pegar a mano".

11 tests nuevos (`test_rotar_secret_key.py`), incluida la corrida real contra
una copia del `.env` del worktree (arriba).

## Punto 6 — re-registro NSSM: CERRADO (verificado con NSSM real, no simulado)

Se descargó `nssm.exe` (release oficial 2.24) y se registró un servicio
Windows **descartable** (`POSFifoTestC01`, carpeta `C:\pos_c01_nssm_test`,
nunca `POSFifoSystem`/`POSFifoSync`) en una consola de Administrador del
usuario, reproduciendo exactamente la secuencia de `registrar_servicio.bat`
(`stop → remove confirm → install → set AppEnvironmentExtra`). Script de
prueba: `prueba_nssm_c01.ps1` (no versionado, vivió en el scratchpad de la
sesión). Resultado real, verificado por el usuario:

1. **Snapshot viejo se retira de verdad.** Se registró primero el servicio
   con 4 variables (`DJANGO_SETTINGS_MODULE`, `POS_ENV_FILE` +
   `VARIABLE_VIEJA_BOGUS` y `OTRA_VARIABLE_VIEJA`, simulando un registro
   pre-Fase-4). Tras `stop → remove confirm → install → set` con solo las 2
   variables reales, `nssm get POSFifoTestC01 AppEnvironmentExtra` mostró
   **únicamente** esas 2 — sin rastro de las viejas.
2. **El proceso del servicio (no la consola) lo confirma.** El script de
   prueba corrido POR el servicio escribió su propio log
   (`resultado.log`) mostrando `VARIABLE_VIEJA_BOGUS(env)=None`: la variable
   vieja no solo desapareció de `nssm get`, tampoco la heredó el proceso hijo
   real. `DJANGO_SETTINGS_MODULE` y `POS_ENV_FILE` sí llegaron con sus
   valores correctos.
3. **`python-dotenv` cargó el `.env` real desde el proceso del servicio, con
   caracteres peligrosos intactos.** El `.env` de prueba tenía
   `NOMBRE_CON_ESPECIALES=Repuestos R&B 100% "Ferreteria" Pena!`; el log
   escrito por el servicio muestra el valor **completo e íntegro** tras
   `load_dotenv`, confirmando el contrato `POS_ENV_FILE` + `override=False`
   funcionando end-to-end en un proceso NSSM real, no solo en un test unitario
   ni en la consola del operador.

Limitación de esta verificación: el servicio de prueba ejecuta un script que
termina enseguida (no un servidor persistente), así que NSSM lo reinició una
vez durante la ventana de espera — se ven dos ejecuciones en el log, ambas con
el mismo resultado correcto, y un mensaje cosmético
`Unexpected status SERVICE_PAUSED` de NSSM al intentar `start` sobre un
proceso que ya había terminado. No afecta la validez de lo verificado. No se
probó el servicio `POSFifoSync` en particular (misma mecánica, mismo script de
registro, se considera cubierto por la misma verificación) ni un escenario de
falla de arranque real (`server.py` rechazando un `DJANGO_SECRET_KEY` corto)
— eso pertenece a C06 con el paquete final.

## Pendientes explícitos, NO simulados como hechos

- No se ejecutó `actualizar.bat` completo de punta a punta (requiere admin,
  detiene servicios y corre contra una instalación real/copia — corresponde a
  C06, ensayo Windows en limpio).
- No se armó el paquete con wheelhouse/hashes offline (punto 9 del encargo).
- No se probó la instalación con `env_cliente.bat` legado real de un cliente.
- No se ejecutó `verificar_instalacion` de punta a punta contra el `.env`
  rotado (la BD de este worktree, `pos_cierre_claude`, no tiene migraciones
  aplicadas; validado en cambio con `config.env_check.validar_entorno` vía los
  tests existentes de `test_env_loading.py`, que sí cubren la longitud/formato
  de `DJANGO_SECRET_KEY`).

## Archivos

- `deploy/preflight_actualizar.py` (nuevo)
- `deploy/actualizar.bat` (modificado)
- `apps/configuracion/tests/test_preflight_actualizar.py` (nuevo)
- `apps/configuracion/management/commands/rotar_secret_key.py` (nuevo)
- `apps/configuracion/tests/test_rotar_secret_key.py` (nuevo)
- `docs/runbooks/INSTALACION_CLIENTE_NUEVO.md` (modificado)
- `docs/runbooks/ACTUALIZACION_CLIENTE_EXISTENTE.md` (modificado)
- Este handoff.

## Migraciones / BDs

Ninguna. Sin cambios de modelos ni settings.

## Riesgos y rollback

- Riesgo residual: la limitación de `NEGOCIO_NOMBRE` por argv (arriba) sigue
  abierta; mitigada con aviso, no eliminada.
- El `.bat` legado no se tocó: mismo comportamiento y mismos riesgos de
  siempre (aceptado, no es parte de este hallazgo).
- Rollback: `git revert` de los commits; no hay estado persistente ni
  migración que deshacer. La prueba NSSM fue enteramente descartable (servicio
  y carpeta de prueba eliminados al final del script).

## Dependencias del otro agente

- Solicitud abierta a Codex (arriba): lectura de `NEGOCIO_NOMBRE` desde
  entorno en `crear_sucursal`/`bootstrap_negocio`, para eliminar el paso por
  argv por completo.

## Siguiente tarea desbloqueada

Puntos 1-7 del encargo C01 cubiertos (con la limitación de `NEGOCIO_NOMBRE`
documentada como solicitud a Codex). Queda el punto 8 (backup de código/
entorno/servicios/media antes de parar servicios — ya parcialmente cubierto
por `actualizar.bat` existente, falta revisar la recuperación tras corte de
energía/disco lleno) y el punto 9 (empaquetado con wheelhouse/hashes offline
del baseline A01, que probablemente convenga esperar hasta que A01 publique
el baseline final para no empaquetar dos veces). Después: C02 (documentos,
imágenes e impresión) — en otro hilo, por decisión del usuario.
