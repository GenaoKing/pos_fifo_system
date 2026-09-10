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
# 78 tests, OK (incluye los 15 nuevos de preflight_actualizar + los 34 de
# migrar_env_cliente/env_loading ya existentes, sin regresiones)
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

**Pendientes explícitos, NO simulados como hechos:**

- No se ejecutó `actualizar.bat` completo de punta a punta (requiere admin,
  detiene servicios y corre contra una instalación real/copia — corresponde a
  C06, ensayo Windows en limpio).
- No se re-registraron servicios NSSM reales (puntos 6 y 8 del encargo C01).
- No se armó el paquete con wheelhouse/hashes offline (punto 9).
- No se probó la instalación con `env_cliente.bat` legado real de un cliente.

## Archivos

- `deploy/preflight_actualizar.py` (nuevo)
- `deploy/actualizar.bat` (modificado)
- `apps/configuracion/tests/test_preflight_actualizar.py` (nuevo)
- Este handoff.

## Migraciones / BDs

Ninguna. Sin cambios de modelos ni settings.

## Riesgos y rollback

- Riesgo residual: la limitación de `NEGOCIO_NOMBRE` por argv (arriba) sigue
  abierta; mitigada con aviso, no eliminada.
- El `.bat` legado no se tocó: mismo comportamiento y mismos riesgos de
  siempre (aceptado, no es parte de este hallazgo).
- Rollback: `git revert` del commit; no hay estado persistente ni migración
  que deshacer.

## Dependencias del otro agente

- Solicitud abierta a Codex (arriba): lectura de `NEGOCIO_NOMBRE` desde
  entorno en `crear_sucursal`/`bootstrap_negocio`, para eliminar el paso por
  argv por completo.

## Siguiente tarea desbloqueada

Seguir con el resto del encargo C01 (puntos 5/6/8/9: rotación de
`DJANGO_SECRET_KEY` sobre el archivo canónico, re-registro real de ambos
servicios NSSM en un host de prueba, empaquetado con wheelhouse/hashes) y
después C02. Esta entrega es deliberadamente acotada — un hallazgo cerrado con
evidencia — en vez de simular cobertura del encargo completo.
