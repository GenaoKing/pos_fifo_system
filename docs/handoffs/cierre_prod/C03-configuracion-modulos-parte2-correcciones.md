# Handoff C03 parte 2 — correcciones (bootstrap legacy + fixtures CFG-012)

Estado: **REVISION.** Follow-up correctivo, acotado, de
[C03-configuracion-modulos-parte2.md](C03-configuracion-modulos-parte2.md).
No amplía C03 con hallazgos CFG/SUS nuevos, no publica CT-03 y no integra a
`develop`. Fecha: **2026-09-15**. Agente: B / Claude.

## Base / SHAs

- Repositorio/worktree: `C:/Proyectos/pos_fifo_system_cierre_claude`.
- Rama base verificada antes de tocar nada: `claude/cierre-prod-C03-parte2`
  en `29bd07f7fa5ef7d39272b19b9034c66a6b746782`, sobre
  `develop@fffd02bd384d8078928fa24761c550df34b711b5`. Worktree limpio
  (`git status` sin cambios) — verificado con `git status`, `git rev-parse
  HEAD` y `git rev-parse develop` antes de crear la rama nueva.
- Rama nueva: `claude/cierre-prod-C03-parte2-fixes`, creada desde ese mismo
  HEAD (sin reset ni descarte de nada).
- **SHA final de esta entrega: `ab8d4809f2a864c118b2d89e9aeb3be0240c4410`.**
- Commits (`git log 29bd07f..ab8d480`):
  1. `a865af3 fix(configuracion): CFG-012 - bootstrap() legacy ya no es get_or_create(pk=1)`
  2. `18dfc07 test(ventas): crear ConfiguracionNegocio explicita tras CFG-012`
  3. `3c94f4b test(inventario): crear ConfiguracionNegocio explicita tras CFG-012`
  4. `f5a994f test(cuentas_por_cobrar): crear ConfiguracionNegocio explicita tras CFG-012`
  5. `ab8d480 test(reportes): crear ConfiguracionNegocio explicita tras CFG-012`
- **No publicado a `origin` ni fusionado a `develop`.** Working tree limpio
  al cierre (`git status` en la sección de pruebas).
- **CT-03 NO se publicó** en esta entrega (instrucción explícita).

## 1. `ConfiguracionNegocio.bootstrap(sucursal=None)` — semántica legacy corregida

### Diagnóstico confirmado (no repetido de cero)

`bootstrap()` sin `sucursal` hacía `cls.objects.get_or_create(pk=1,
defaults=defaults)`. Con la FK a sucursal ya no forzada a `pk=1` desde
Fase 2, ese atajo:

- ignoraba cualquier fila legacy real con un PK distinto de 1 (una base
  importada, o una secuencia ya avanzada por borrados/recreaciones previas)
  y podía terminar creando una **segunda** fila "única";
- nunca detectaba ambigüedad: con dos filas legacy ya existentes,
  `get_or_create(pk=1)` seguía intentando crear/tocar la de `pk=1`
  específicamente, sin avisar que había otra.

### Corrección (`apps/configuracion/models.py`)

- Nueva excepción `ConfiguracionAmbigua(RuntimeError)`.
- `bootstrap(sucursal=...)` (con sucursal): **sin cambios** — ya era
  `get_or_create(sucursal=sucursal, ...)`, atómico por la unicidad real de
  la FK, idempotente.
- `bootstrap()` (sin sucursal, legacy): replica la misma regla que
  `apps.configuracion.utils._config_sin_sucursal` (CFG-002) ya usa para
  **leer**, aplicada a **escribir**:
  - cero filas → `cls.objects.create(**defaults)`, sin forzar el PK;
  - exactamente una fila (sea cual sea su PK) → se reutiliza esa misma fila,
    no se crea otra;
  - más de una candidata → `ConfiguracionAmbigua`, nunca `.first()`, nunca
    crea una tercera.
- `load()`, `get_config()`, `_config_sin_sucursal`, `config_o_none()`,
  `modulos_efectivos_o_vacio()`, el context processor, `crear_config_inicial`
  y el resto del contrato CT-03 propuesto en la parte 2: **sin cambios**.

### Pruebas nuevas

`apps/configuracion/tests/test_cfg012_leer_no_crea.py`, clase
`BootstrapLegacySinPk1Tests` (4 tests nuevos):

- `test_sin_filas_crea_una_sola_sin_forzar_el_pk`
- `test_fila_legacy_unica_con_pk_distinto_de_1_se_reutiliza`
- `test_replay_sobre_fila_con_pk_distinto_de_1_es_idempotente`
- `test_mas_de_una_candidata_es_ambiguedad_explicita_no_first`

Los tests preexistentes de `BootstrapExplicitoTests` (creación por
sucursal, replay legacy sobre cero filas, lectura tras bootstrap,
`crear_config_inicial`) se revalidaron sin tocar — siguen verdes, sin
cambio de conducta esperado ni observado.

### Mapa actualizado

`apps/configuracion/AGENTS.md` — entradas de `bootstrap()` describen ahora
la regla real (cero/una/ambigua) en vez de "get-or-create explícito" a
secas. `Última revisión: 2026-09-15`.

## 2. Fixtures/tests de dominios propios de Claude corregidos

### Los 9 del listado del handoff de parte 2 (§5), revalidados y corregidos

| Archivo | Causa exacta | Corrección |
|---|---|---|
| `apps/ventas/tests/test_ventas_service.py` | `setUp` sin config; `_set_config()` llamaba `ConfiguracionNegocio.load()` esperando que existiera | `ConfiguracionNegocio.objects.create()` en `setUp`; además `IdentidadDeSucursalTests.test_venta_asigna_la_sucursal_y_prefija_su_numero` crea una `Sucursal` que pasa a coincidir con `SUCURSAL_CODIGO` a mitad de test — necesita su propia `ConfiguracionNegocio.objects.create(sucursal=sucursal)`, no la legacy de la clase |
| `apps/ventas/tests/test_anulaciones.py` | Igual (`_set_config()`) | `ConfiguracionNegocio.objects.create()` en `setUp` |
| `apps/ventas/tests/test_concurrencia.py` | `get_config()` en `setUp` para "materializar" la config antes de lanzar hilos — ya no la crea | `ConfiguracionNegocio.objects.create()` en su lugar |
| `apps/ventas/tests/test_producto_precio_cache.py` | Mismo patrón, dentro del único test del archivo | `ConfiguracionNegocio.objects.create()` antes de `get_config()` (conserva el propósito del test: config cacheada) |
| `apps/ventas/tests/test_reimpresion_permisos.py` | `ConfiguracionNegocio.load(sucursal=self.suc_a)` en `setUp`, esperando que creara | `.objects.create(sucursal=self.suc_a)` |
| `apps/ventas/tests/test_anulacion_permisos.py` | Igual, con comentario que ya describía el mecanismo viejo (`pk=1` de `load()`) | `.objects.create(sucursal=self.suc_a)` + comentario actualizado |
| `apps/inventario/tests/test_concurrencia_inventario.py` | Mismo patrón que `test_concurrencia.py` | `ConfiguracionNegocio.objects.create()` |
| `apps/cuentas_por_cobrar/tests/test_modulo_gate.py` | `ConfiguracionNegocio.load(sucursal=self.suc_a)` en `setUp` | `.objects.create(sucursal=self.suc_a)` |
| `apps/reportes/tests/test_modulo_gate.py` | Igual | `.objects.create(sucursal=self.suc_a)` |

Ninguno de estos 9 prueba CFG-012: todos crean su `ConfiguracionNegocio`
explícita ahora, mismo patrón que `apps/caja/tests/test_cuadre.py` (ya
construido así). No se restauró auto-create ni se agregó ningún fallback de
producción.

### Hallazgo adicional de esta sesión: el listado de parte 2 estaba incompleto

La parte 2 documentó el mecanismo (`~234 ERROR` en el discovery completo)
pero **no itemizó la lista completa** — solo confirmó por muestreo
`test_concurrencia` y `test_concurrencia_inventario`, y nombró 9 archivos.
Corriendo la suite completa de los 4 apps propios de Claude que aparecen
citados en su propio §9 ("aplicar el arreglo mecánico... a los `setUp` de
`ventas`/`inventario`/`cuentas_por_cobrar`/`reportes`"), aparecieron **8
archivos más** con el mismo mecanismo exacto (`ConfiguracionNoInicializada`
en `setUp`, cero filas en la base). Se corrigieron con el mismo patrón
mecánico, dentro del mismo alcance de 4 apps que la parte 2 ya había
delegado a "quien retome C05":

| Archivo | Clase base corregida |
|---|---|
| `apps/ventas/tests/test_idempotencia_venta.py` | `VentaIdempotenciaTests` |
| `apps/cuentas_por_cobrar/tests/test_anulacion_pago.py` | `AnulacionPagoCxCTestsBase` |
| `apps/cuentas_por_cobrar/tests/test_auditoria_cxc.py` | `CxCTestCase` (también arregla `test_idempotencia_cobro.py`, que hereda de esta clase) |
| `apps/cuentas_por_cobrar/tests/test_credito_services.py` | `CreditoServicesTests` |
| `apps/cuentas_por_cobrar/tests/test_exports.py` | `ExportEstadoCuentaTests` |
| `apps/cuentas_por_cobrar/tests/test_interes.py` | `InteresFinanciamientoTests` |
| `apps/reportes/tests/test_auditoria_reportes.py` | `ReportesTestCase` |

`apps/cuentas_por_cobrar/tests/test_idempotencia_cobro.py` no se tocó
directamente: su única clase (`CobroIdempotenciaNReintentosTests`) hereda
de `CxCTestCase` (arreglado en `test_auditoria_cxc.py`) y quedó verde por
herencia.

### Explícitamente NO tocado — fuera del alcance de esta entrega

Al correr `apps.caja` y `apps.cotizaciones` completos se confirmó el
**mismo mecanismo exacto** (35 `ERROR` con la misma traza
`ConfiguracionNoInicializada`). Son apps de C05 (`caja`, `cotizaciones`,
`facturacion_electronica` por la tabla de propiedad del plan maestro) pero
**no** están nombradas en el §9 de la parte 2 ni en el encargo de esta
sesión — corregirlas aquí habría ampliado el alcance acotado más allá de lo
pedido. Quedan como **residual explícito para quien integre C05**, con el
mismo arreglo mecánico ya probado en este documento (agregar
`ConfiguracionNegocio.objects.create(...)` explícito al `setUp` que
corresponda). No se identificó ninguna otra causa distinta a CFG-012 en la
muestra revisada.

`apps/facturacion_electronica` **no** necesita este arreglo: su fixture
pytest (`apps/facturacion_electronica/tests/conftest.py::config_negocio`)
ya crea `ConfiguracionNegocio.objects.create(...)` explícita — nunca
dependió del auto-create. Confirmado corriendo su suite completa (72
passed).

## 3. Pruebas ejecutadas

Entorno: `C:/Proyectos/pos_fifo_system_cierre_claude`, venv aislado
`.venv` (Python 3.11.14, Django 5.2.17), BD `pos_cierre_claude` / test
`test_pos_cierre_claude`, `--settings=config.settings_development`, serial
(Windows).

```bash
# Focal configuracion/suscripciones (incluye los 4 tests nuevos de bootstrap)
python manage.py test apps.configuracion apps.suscripciones apps.api.tests.test_suscripciones_admin --settings=config.settings_development
# Ran 230 tests in 16.149s -- OK (skipped=2)

# Los 9 modulos del listado original de la parte 2
python manage.py test apps.ventas.tests.test_ventas_service apps.ventas.tests.test_anulaciones apps.ventas.tests.test_concurrencia apps.ventas.tests.test_producto_precio_cache apps.ventas.tests.test_reimpresion_permisos apps.ventas.tests.test_anulacion_permisos apps.inventario.tests.test_concurrencia_inventario apps.cuentas_por_cobrar.tests.test_modulo_gate apps.reportes.tests.test_modulo_gate --settings=config.settings_development
# Ran 76 tests -- OK

# Los 4 apps completos de Claude citados en el §9 de la parte 2 (antes y despues
# de corregir los 8 archivos adicionales)
python manage.py test apps.ventas apps.inventario apps.cuentas_por_cobrar apps.reportes --settings=config.settings_development
# Antes:  Ran 307 tests -- FAILED (errors=78), todos ConfiguracionNoInicializada
# Despues: Ran 307 tests in 192.285s -- OK

# e-CF (pytest, separado -- no encontro ningun uso del auto-create)
python -m pytest apps/facturacion_electronica -q
# 72 passed in 17.17s

# Verificacion final combinada sobre el SHA final
python manage.py test apps.configuracion apps.suscripciones apps.api.tests.test_suscripciones_admin apps.ventas apps.inventario apps.cuentas_por_cobrar apps.reportes --settings=config.settings_development --noinput
# Ran 537 tests in 192.539s -- OK (skipped=2)

pip check
# No broken requirements found.

python manage.py check --settings=config.settings_development
# System check identified no issues (0 silenced).

python manage.py makemigrations --check --dry-run --settings=config.settings_development
# No changes detected

python -m compileall -q apps config manage.py
# sin salida (exit 0)

git diff --check
# solo warnings de CRLF/LF (benignos), exit 0
```

**Residual, fuera de esta entrega (reportado, no ocultado ni arreglado
cruzando propiedad):**

```bash
python manage.py test apps.caja apps.cotizaciones --settings=config.settings_development
# Ran 96 tests -- FAILED (errors=35), mismo mecanismo ConfiguracionNoInicializada
```

No se ejecutó el discovery completo del proyecto (`manage.py test` sin
argumentos): la parte 2 ya documentó que tarda ~4h43min en este entorno y
que el mecanismo de los `ERROR` restantes es 100% identificado; repetirlo
no habría aportado información nueva sobre el cambio de esta entrega.

## 4. Riesgos y rollback

- **`bootstrap()` sigue sin consumidor de producción** (igual que en la
  parte 2): hasta que Codex aplique el pull de sync (§4.1 de
  `C03-configuracion-modulos-parte2.md`), no la llama nadie fuera de tests
  y del propio diseño. El cambio de esta entrega es interno a `bootstrap()`
  y no altera su firma ni su contrato observable para quien ya la use con
  `sucursal=<instancia>` (el caso que Codex va a cablear).
- **`ConfiguracionAmbigua` es una excepción nueva** que solo puede
  levantarse desde la rama legacy de `bootstrap()` (sin sucursal) con ≥2
  filas legacy preexistentes — un estado de datos que ya era anómalo antes
  de este cambio (la parte 1/2 nunca lo consideró "sano"). No hay ningún
  call site de producción que la reciba hoy.
- **Los fixtures corregidos son deterministas y sin riesgo de dato real**:
  todos crean una fila de test dentro de una transacción de test que se
  revierte; ninguno toca `crear_config_inicial` ni BDs fuera de las de test.
- **`apps/caja` y `apps/cotizaciones` siguen rojos** con el mismo mecanismo
  (§2, "Explícitamente NO tocado"). No bloquean esta entrega (no son su
  alcance) pero sí bloquean dar por cerrada la integración conjunta de C05
  a `develop` hasta que alguien los corrija con el mismo patrón.
- Rollback: `git revert` de los 5 commits de esta rama (o simplemente no
  mergearla); no hay migraciones ni estado persistente que deshacer.
  Revertir el commit de `bootstrap()` restaura el `get_or_create(pk=1)`
  original (y con él, el hallazgo que motivó esta corrección).

## 5. Lo que falta para Codex / integración

Sin cambios respecto de lo que ya pedía la parte 2 (no se repite el
análisis, sigue en pie tal cual):

- §4.1/4.2 de `C03-configuracion-modulos-parte2.md`: las dos líneas del
  pull de sync (`apps/sync/engine.py:1865`, `apps/api/views/sync.py:651`).
- §4.4: cablear `apps.suscripciones.seed.validar_plan_slug` en
  `bootstrap_tenant`.
- §4.5: quitar la redundancia de `get_config()` en
  `apps/usuarios/views.py:117,139`.
- Publicar CT-03 en `CONTRATOS.md` (sigue sin publicarse; instrucción
  explícita de esta sesión también fue no publicarlo).
- El bug de `apps/productos` (`ConfiguracionNoInicializada` filtrado en un
  400 con mensaje crudo) sigue siendo de Codex; no se tocó.

**Nuevo para quien integre C05:** aplicar el mismo arreglo mecánico
(`ConfiguracionNegocio.objects.create(...)` explícito en el `setUp` que
corresponda) a `apps/caja` y `apps/cotizaciones` antes de dar por cerrada
la integración conjunta — 35 `ERROR`, mismo mecanismo exacto que el resto
de este documento, sin necesidad de investigación adicional.

## 6. Estado final

- `git status`: limpio.
- Rama: `claude/cierre-prod-C03-parte2-fixes`.
- **SHA final: `ab8d4809f2a864c118b2d89e9aeb3be0240c4410`.**
- Sin push, sin merge a `develop`, sin despliegue.
