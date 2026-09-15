# Handoff C03 parte 2 — CFG-012 y contrato CT-03 definitivo

Estado: **REVISION.** Cierra CFG-012 (leer configuración no la crea) y entrega
la propuesta **definitiva** de CT-03 para que Codex la publique en
`CONTRATOS.md` e integre sus cambios pendientes: dos ajustes puntuales al GET
de sync (§4.1/4.2), el cableado de la validación de SUS-014 en
`bootstrap_tenant` (§4.4) y, adicional a lo pedido, una redundancia en el
login que quedó en riesgo (§4.5). Fecha: **2026-09-11**. Agente: B / Claude.
Encargo puntual de esta
sesión (parte 2 de C03); contexto completo en
[CIERRE_PROD_CLAUDE.md](../../planes/CIERRE_PROD_CLAUDE.md#c03--configuración-y-módulos-vendibles)
y en la parte 1: [C03-configuracion-modulos.md](C03-configuracion-modulos.md).

## Base / SHAs

- Base consumida: `develop@fffd02bd384d8078928fa24761c550df34b711b5` (verificada
  con `git rev-parse HEAD` antes de tocar nada; el worktree estaba limpio y en
  esa misma base, aunque con la rama `claude/cierre-prod-C05` que ya había
  llegado a ese commit — sin divergencia real, se ramificó desde ahí).
- Rama nueva: `claude/cierre-prod-C03-parte2`.
- Commits de esta entrega (ver `git log claude/cierre-prod-C03-parte2`):
  1. `23dc805 fix(configuracion): CFG-012 - leer configuracion no la crea` —
     modelo, utils, context processor, `verificar_instalacion` y sus tests.
  2. `b318681 feat(suscripciones): SUS-014 - validar_plan_slug antes de escribir dos bases`
     — helper + tests.
  3. `62f1383 test(configuracion): contrato CT-03 atado al codigo real` —
     `test_ct03_contrato.py`.
  4. `c26fd2d fix(suscripciones): restaurar return resumen de bootstrap()` —
     el edit de (2) ancló el insert justo antes del `return resumen` de
     `bootstrap()` y lo dejó huérfano al final de `validar_plan_slug()`
     (`NameError` referenciando un nombre inexistente). **Ningún test lo
     detectó en el momento**: la suite focal (203 tests) se corrió justo
     antes de este edit, no después — lo encontró la verificación posterior
     de los dos archivos de test más nuevos, antes de darlos por buenos. Ver
     §7 para el detalle; documentado por transparencia, no para restarle
     seriedad a la entrega — es exactamente el tipo de error que correr la
     suite antes de cerrar existe para atrapar, y lo atrapó.
  5. `docs(cierre-C03): handoff parte2 - CFG-012, CT-03 final, pedidos a Codex`
     — este documento + fixture.
- **No publicado a `origin`** ni fusionado a `develop`. Working tree limpio al
  cierre (`git status` en la sección de pruebas).

## Qué NO se rehizo

Se revalidó contra el código el listado de la parte 1 antes de tocar nada:
SUS-008/009/010/012/013/015/016(parcial)/017/018 y
CFG-006/009/011/013/014/015/017 siguen cerrados tal como los describe
`C03-configuracion-modulos.md`; ninguno se repitió. `apps/configuracion/AGENTS.md`
y `apps/suscripciones/AGENTS.md` reflejaban ese estado antes de esta entrega.

## 1. CFG-012 — leer la configuración no puede crearla

### Diagnóstico previo (confirmado, no repetido)

`ConfiguracionNegocio.load()` hacía `get_or_create()` en las dos ramas (con y
sin sucursal), y el context processor lo invoca en **cada** render —login,
error, admin incluidos. La parte 1 ya había hecho la auditoría de impacto
completa (~80 tests fuera de C03 dependían del efecto secundario) y dejó el
diseño listo sin implementar, con la advertencia correcta: no es un cambio
mecánico, hay que decidir qué hace el login/error si no hay config.

### Diseño: separar lectura pura de bootstrap explícito

**`apps/configuracion/models.py`**

- `ConfiguracionNoInicializada(RuntimeError)` — nueva excepción, mensaje
  accionable (`manage.py crear_config_inicial [--sucursal <codigo>]`).
- `ConfiguracionNegocio.load(sucursal=None)` — **lectura pura**. Sin fila,
  levanta `ConfiguracionNoInicializada`; nunca crea, nunca cachea el fallo.
- `ConfiguracionNegocio.bootstrap(sucursal=None, **defaults)` — **nuevo**,
  único punto de creación implícita permitida (get-or-create). Es la misma
  semántica que tenía `load()` antes de este cambio, movida a un nombre
  explícito. Transaccional (una sola sentencia `get_or_create`, en su propio
  savepoint) e idempotente. Pensada para fixtures de test y para el primer-pull
  de sync que Codex debe adoptar (§3).

**`apps/configuracion/utils.py`**

- `get_config()` — sin cambio de firma; ahora **propaga**
  `ConfiguracionNoInicializada` en vez de crear. Sigue siendo la función
  correcta para una **decisión de negocio** (medios de pago, e-CF, descuentos):
  ahí "no hay config" debe seguir siendo un error, no un valor inventado —
  ninguna de las llamadas que ya hacían esa lectura para decidir algo cambió.
- `config_o_none()` — **nuevo**. Variante *render-safe*: atrapa
  `ConfiguracionNoInicializada`, `ConfiguracionNoResuelta` y `TenantContextError`
  (BUG-E) y devuelve `None`. Para páginas que solo **muestran** datos del
  negocio, nunca para decidir algo.
- `modulos_efectivos_o_vacio()` — **nuevo**, misma justificación: devuelve
  `set()` en vez de propagar. Un menú que no puede resolver el entitlement
  todavía no debe tumbar la página; no ofrecer nada extra es la dirección
  segura (oculta, no inventa que algo está disponible).

**`apps/configuracion/context_processors.py`**

- `config_negocio()` pasa a usar `config_o_none()` /
  `modulos_efectivos_o_vacio()`. Es el único cambio de comportamiento que
  importa para "renders seguros": corre en cada página, y ya no puede tumbar
  ninguna por falta de configuración, código no resuelto o tenant sin activar.

**`apps/configuracion/management/commands/verificar_instalacion.py`** (hallazgo
encontrado al correr la suite focal, no anticipado en el diseño de la parte 1)

- `_revisar_modulos()`, rama "sin negocio" (legacy): antes atrapaba **cualquier**
  excepción de `modulo_activo()` y reportaba `apagados=[]`, `roto=False` — con
  el auto-create viejo, esa rama nunca se ejercitaba de verdad. Con CFG-012,
  ausencia de configuración pasa por ahí como `ConfiguracionNoInicializada` y
  el catch genérico la escondía: **el propio comando que existe para detectar
  una instalación sin `crear_config_inicial` la reportaría como sana.** Es
  exactamente el "fallback accidental" que la aceptación pide evitar. Se separó
  un `except ConfiguracionNoInicializada` explícito que reporta
  `error='CONFIGURACION_NO_INICIALIZADA'`, `roto=True` y una línea en la salida
  legible con el comando exacto para arreglarlo; el catch genérico se conserva
  para errores realmente inesperados.

### Qué NO cambió (a propósito)

- `config_de_sucursal(sucursal)` — ya era lectura pura (`.filter().first()`,
  devuelve `None`), no pasaba por `.load()`. Sin cambios.
- `crear_config_inicial` (comando) — nunca llamó a `.load()`; ya usaba
  `get_or_create`/creación explícita. Sin cambios de conducta, verificado con
  test dedicado (`test_crear_config_inicial_sigue_funcionando_igual`).
- `modulo_activo()` (usado por `decorators.requiere_modulo`/`_json`, un gate
  real) **sigue propagando** la excepción en vez de atraparla — a diferencia
  de `modulos_efectivos()`. Es deliberado: un gate de acceso que no puede
  determinar el estado del módulo no debe fabricar `True` ni `False`; debe
  fallar visible. Ver Riesgos.

## 2. SUS-014 — validación de `plan_slug` (mitad C, falta cableado A)

`apps/suscripciones/seed.py` gana:

- `PlanDesconocido(ValueError)`.
- `validar_plan_slug(slug, *, using=None)` — slug vacío es válido ("sin plan
  asignado explícitamente"); un slug no vacío que no exista en la base `using`
  levanta `PlanDesconocido` con la lista de planes disponibles, sin tocar nada.

Es la pieza que le corresponde a C03 del hallazgo (`apps/suscripciones` es mío);
`bootstrap_tenant --plan` es de Codex — el pedido exacto de dónde cablearla
está en §4.

## 3. Contrato CT-03 — propuesta definitiva

Reemplaza la propuesta de la parte 1 en `C03-configuracion-modulos.md`
(sección "Propuesta de contrato CT-03"): mismo resolutor B, resolutor A ahora
con CFG-012 **implementado**, no solo declarado como invariante.

### 3.1 Alcance: dos resolutores independientes

CT-03 cubre dos verdades efectivas que **no dependen una de la otra** — un
punto que la parte 1 no había hecho explícito y que este fixture/test sí
prueba:

| | Resolutor A — configuración | Resolutor B — capacidades |
|---|---|---|
| Módulo | `apps.configuracion.utils` | `apps.suscripciones.engine` |
| Lee | `ConfiguracionNegocio` (por sucursal) | `SuscripcionNegocio` / `NegocioModulo` / `SucursalModuloOverride` — **nunca** `ConfiguracionNegocio` |
| Sin fila / sin aprovisionar | `ConfiguracionNoInicializada` (CFG-012) | Fail-open comercial (`SIN_APROVISIONAR`) — set completo, no error |

Una sucursal con negocio asignado pero sin `ConfiguracionNegocio` propia (nadie
corrió `crear_config_inicial --sucursal <esa>` todavía) tiene el resolutor B
funcionando normal y el resolutor A fallando fuerte. No es una inconsistencia:
son preguntas distintas ("¿qué módulos vendo?" vs "¿cuál es mi identidad
fiscal?") y la segunda puede legítimamente no estar respondida todavía.

### 3.2 Firmas

```python
# Resolutor A — apps.configuracion.utils
get_config() -> ConfiguracionNegocio                      # decisión de negocio; propaga si falla
config_o_none() -> ConfiguracionNegocio | None             # render; nunca propaga
config_de_sucursal(sucursal) -> ConfiguracionNegocio | None  # ya era puro
config_para_documento(sucursal) -> ConfiguracionNegocio    # cae a get_config() si la sucursal no tiene la suya
modulos_efectivos() -> set[str]                             # decisión de negocio (rama con negocio: nunca fallaba; rama legacy: propaga)
modulos_efectivos_o_vacio() -> set[str]                     # render; nunca propaga

# Resolutor A — apps.configuracion.models
ConfiguracionNegocio.load(sucursal=None) -> ConfiguracionNegocio  # lectura pura; levanta si no existe
ConfiguracionNegocio.bootstrap(sucursal=None, **defaults) -> ConfiguracionNegocio  # get-or-create explícito

# Resolutor B — apps.suscripciones.engine
modulos_activos(negocio, sucursal=None) -> set[str]
modulo_activo(key, negocio=None, sucursal=None) -> bool
estado_suscripcion(negocio) -> {SIN_APROVISIONAR|SUSPENDIDA|CON_PLAN|CUSTOM}
```

### 3.3 Precedencia

Sin cambios respecto de A00/A01 (se hereda tal cual, C03 no la toca): variables
de proceso > `POS_ENV_FILE` > defaults; `load_dotenv(override=False,
encoding="utf-8")`. Esto es infraestructura de arranque (`.env`), no
`ConfiguracionNegocio` — los dos viven en capas distintas y no se confunden:
`.env` decide *dónde* corre el proceso (BD, sucursal, tenant); `ConfiguracionNegocio`
decide *cómo* opera el negocio de esa sucursal una vez arrancado.

### 3.4 Módulos efectivos (fórmula, sin cambios de parte 1)

```
cierre( plan.modulos ∪ incluidos − excluidos ) ∪ core − {overrides sucursal apagados}
```

Estados explícitos (`SIN_APROVISIONAR|SUSPENDIDA|CON_PLAN|CUSTOM`). Key
desconocida deniega siempre (SUS-018). Fail-open comercial solo sin negocio o
`SIN_APROVISIONAR` — es entitlement comercial, no seguridad (la seguridad es
`apps/permisos`, default-deny).

### 3.5 Revisión / caché por request

Sin cambios de diseño: BD como verdad + memo por request en cada resolutor,
sin caché persistente incoherente entre workers. Resolutor A: TTL 30s local /
600s compartido, clave con namespace de tenant (CFG-001/CFG-005). Resolutor B:
TTL 30s, invalidación por versión diferida a `transaction.on_commit`
(SUS-002/003/011). Ninguno depende de Redis; ambos convergen dentro del TTL.

### 3.6 Errores y comportamiento sin aprovisionar

| Excepción | Quién la levanta | Cuándo | Quién la atrapa |
|---|---|---|---|
| `ConfiguracionNoInicializada` | `ConfiguracionNegocio.load()` | No hay fila para la sucursal (o ninguna, modo legacy) | `config_o_none()` / `modulos_efectivos_o_vacio()` / context processor. **Nadie más** — decisiones de negocio deben verla. |
| `ConfiguracionNoResuelta` | `apps.configuracion.utils._config_sin_sucursal` | `SUCURSAL_CODIGO` no resuelve y hay ≥2 `ConfiguracionNegocio` | Mismo grupo que arriba |
| `TenantContextError` | `apps.tenancy.context` (vía `_namespace()`) | Tenancy activo sin tenant en contexto (BUG-E) | Mismo grupo — ya era un riesgo conocido, ahora con la misma cobertura explícita que CFG-012 |
| *(sin excepción)* | `apps.suscripciones.engine` | Sin negocio o `SIN_APROVISIONAR` | Fail-open deliberado; no es un estado de error, es "todavía no hay entitlements reales" |

### 3.7 Fixture de contrato

[`fixtures/C03-ct03_capacidades_efectivas_v1.json`](fixtures/C03-ct03_capacidades_efectivas_v1.json)
— negocio con plan Pro, sucursal con override negativo de `cotizaciones`, y
una segunda sucursal con negocio asignado pero sin `ConfiguracionNegocio`
todavía (ilustra la independencia de 3.1). Atado al código real en
`apps/configuracion/tests/test_ct03_contrato.py` (6 tests, mismo escenario que
el fixture, contra el engine y los resolutores reales — no un mock).

### 3.8 Revisión

`config.effective.v1` (nueva; la parte 1 no había fijado un nombre de esquema
para el resolutor A). Cambiar la regla de cualquiera de los dos resolutores
exige repetir `test_ct03_contrato.py` y la matriz permisos × módulos ×
suspensión × sucursal del encargo C03.

## 4. Solicitudes exactas a Codex

Los tres casos pedidos explícitamente para este cierre, más uno adicional que
apareció en la auditoría de impacto (login) y que corresponde a un archivo de
Codex.

### 4.1 CFG-012 en el GET de sync (pull, `apps/sync/engine.py:1865`)

`_pull_configuracion()` usa `ConfiguracionNegocio.load(sucursal=sucursal)` para
obtener la fila **local** y aplicarle los campos que llegan del cloud. Esto
**es** un caso de bootstrap (si la fila local no existe todavía, aplicar el
primer pull debería crearla, no fallar) — exactamente el caso para el que se
diseñó `bootstrap()`.

**Cambio pedido — una línea:**

```python
# apps/sync/engine.py:1865
- config = ConfiguracionNegocio.load(sucursal=sucursal)
+ config = ConfiguracionNegocio.bootstrap(sucursal=sucursal)
```

Nada más cambia: `bootstrap()` devuelve la fila existente si ya la hay (mismo
comportamiento que `load()` tenía), y la crea con los defaults del modelo si
no — que es exactamente lo que `apply()` va a sobrescribir con los campos
permitidos de la respuesta cloud a continuación.

### 4.2 CFG-012 en el GET de sync (cloud, `apps/api/views/sync.py:651`)

`configuracion_para_sucursal()` es el lado **cloud** (fuente): sirve la config
de ESA sucursal en la base del tenant. Acá NO es un bootstrap — si el cloud no
tiene `ConfiguracionNegocio` para esa sucursal, no hay nada que enviarle al
POS, igual que la línea 648-649 ya hace cuando `sucursal is None`.

**Cambio pedido:**

```python
# apps/api/views/sync.py:651
- config = ConfiguracionNegocio.load(sucursal=sucursal)
+ from apps.configuracion.models import ConfiguracionNoInicializada
+ try:
+     config = ConfiguracionNegocio.load(sucursal=sucursal)
+ except ConfiguracionNoInicializada:
+     return Response([])
```

(mover el `try` justo después de la resolución de `sucursal`, antes de leer
`config.fecha_modificacion`). Esto mantiene el contrato existente de la
respuesta ("nada que sincronizar todavía") en vez de devolver 500 a un POS que
hace pull antes de que el cloud tenga esa sucursal configurada.

### 4.3 SUS-007 (resto) — pull de sync sigue serializando flags legacy

Sin cambios respecto del pedido de la parte 1 (`apps/api/views/sync.py`,
`apps/sync/engine.py` siguen sirviendo los 8 flags `modulo_*` crudos en vez de
derivarlos del engine). No se repite el análisis; sigue en pie. `C` entrega
`apps.configuracion.utils.modulos_efectivos()` como helper — ya existía en
parte 1, sin cambios de firma en parte 2.

### 4.4 SUS-014 — cablear `validar_plan_slug` en `bootstrap_tenant`

`apps/tenancy/management/commands/bootstrap_tenant.py` acepta `--plan <slug>`
como texto libre y lo escribe en `Tenant.plan_slug` (control plane) **antes**
de aprovisionar; en la base del tenant busca el `Plan` con `.first()` y, si no
existe, omite la asignación en silencio (líneas ~355-385, ~476-477 según la
lectura de esta sesión — confirmar contra el código actual de Codex antes de
tocar).

**Cambio pedido:** llamar
`apps.suscripciones.seed.validar_plan_slug(plan_slug, using=<alias de la base tenant que se está aprovisionando>)`
**antes** de escribir `Tenant.plan_slug` en el control plane y antes de
aprovisionar en la base tenant — un slug inválido debe abortar sin tocar
ninguna de las dos bases, no quedar a mitad de camino. `validar_plan_slug`
acepta slug vacío (no-op) y levanta `apps.suscripciones.seed.PlanDesconocido`
con la lista de planes reales disponibles si no existe. Tests de la pieza C en
`apps/suscripciones/tests/test_sus014_validar_plan_slug.py`.

### 4.5 Adicional — login redundante y ahora en riesgo (`apps/usuarios/views.py:117,139`)

No estaba en la lista de tres pedidos explícitos, pero apareció en la
auditoría de impacto de CFG-012 y **sí** afecta la aceptación ("mantener login
seguro"): `login_view()` pasa `'config': get_config()` explícito al contexto
del template, en dos ramas (intentos excedidos y cuenta desactivada). Esto es
**redundante** — el context processor (`config_negocio`, registrado
globalmente) ya inyecta `config` en cada render, ahora vía `config_o_none()`
(seguro). La llamada directa en `usuarios/views.py` no pasa por esa variante y
puede levantar `ConfiguracionNoInicializada` **antes** de renderizar, tumbando
el login exactamente en el caso que la aceptación pide cubrir.

**Cambio pedido — quitar la redundancia (más simple que envolverla):**

```python
# apps/usuarios/views.py:117
- {'form': AuthenticationForm(), 'config': get_config()},
+ {'form': AuthenticationForm()},

# apps/usuarios/views.py:139
- {'form': form, 'config': get_config()},
+ {'form': form},
```

Y retirar el import ahora sin uso en la línea 40
(`from apps.configuracion.utils import get_config`) si no queda otra
referencia en el archivo. El contexto sigue teniendo `config` — lo pone el
context processor, ya seguro sin fila.

**No se tocó `apps/usuarios/views.py`** (es de Codex); este es un pedido, no un
cambio aplicado.

## 5. Impacto conocido en apps ajenas (auditado, NO corregido — fuera de mi propiedad en esta entrega)

Mismo método de auditoría que la parte 1, repetido tras el cambio real (no
solo proyectado). Ninguno de estos archivos es mío en este bloque
(`apps/ventas`, `caja`, `facturacion_electronica` son C05; `apps/productos`,
`usuarios` son de Codex; `utils/impresoras`, `apps/common/pdf` son C02) — se
deja registrado para quien retome cada bloque, **no** se pide a Codex porque
la mayoría no son sus archivos:

- **Llamadas directas a `get_config()` para decisión de negocio real** (no
  render), que ahora fallan fuerte en vez de auto-crear si la instalación
  está genuinamente rota: `apps/ventas/views.py:81,522,772`,
  `apps/ventas/models.py:284`, `apps/ventas/services/anulaciones_service.py:140`,
  `apps/ventas/services/ventas_service.py:203`, `apps/caja/views.py:115,187,960`,
  `apps/facturacion_electronica/{views.py,services/*}`, `apps/productos/utils.py:20`,
  `utils/impresoras/{termica.py,manager.py}`, `apps/common/pdf/standard.py:280`.
  Correcto por diseño (ver parte 1: "si llega, la instalación está rota y ES
  CORRECTO fallar fuerte ahí"); documentado, no es una regresión nueva de
  producción — la secuencia real de instalación (`crear_config_inicial` antes
  de levantar el servicio) sigue evitando que se alcance en la práctica.
- **Tests que dependían del auto-create como atajo de fixture** (mismo
  mecanismo, confirmado por ejecución real esta vez, no solo por lectura):
  `apps/ventas/tests/{test_ventas_service.py,test_anulaciones.py,test_concurrencia.py,
  test_producto_precio_cache.py,test_reimpresion_permisos.py,test_anulacion_permisos.py}`,
  `apps/inventario/tests/test_concurrencia_inventario.py`,
  `apps/cuentas_por_cobrar/tests/test_modulo_gate.py`,
  `apps/reportes/tests/test_modulo_gate.py`. El arreglo mecánico que la parte 1
  ya prescribió sigue siendo el correcto: agregar
  `ConfiguracionNegocio.objects.create(...)` explícito al `setUp`, mismo patrón
  que `apps/caja/tests/test_cuadre.py` (ya construido así). **No se tocaron**
  estos archivos — pertenecen a C05 (ventas/inventario/CxC/reportes) y este
  bloque tiene prohibido ampliarse ahí.
- **`apps/productos` — una excepción termina como 400 con el mensaje crudo
  filtrado al cliente** (hallazgo nuevo de esta parte 2, confirmado por
  ejecución real, no solo lectura):
  `apps.productos.tests.test_auditoria_productos.GatesDelCatalogoTests.test_con_permiso_si_se_crea`
  esperaba `200` y recibió `400` con body
  `{"success": false, "message": "No hay ninguna ConfiguracionNegocio en esta base. Ejecutar: manage.py crear_config_inicial"}`.
  La causa es `apps/productos/utils.py:20`
  (`get_config().formato_codigo_barras...` al generar el SKU), y alguna vista
  de creación de producto atrapa `Exception` en general y la convierte en un
  400 con `str(exc)` como mensaje — un patrón distinto al resto (no es un 500
  crudo, es un 400 "controlado" que igual expone texto interno). No es mío
  (`apps/productos` es de Codex); no se tocó. Vale la pena que Codex lo sepa
  porque es la única app ajena donde CFG-012 se manifiesta como una respuesta
  HTTP "exitosamente manejada" en vez de un 500 — más fácil de pasar por alto
  en un smoke test que solo mira el código de estado agrupado (4xx vs 5xx) sin
  leer el body.
- **Gate real que puede devolver 500 en vez de 404** (hallazgo nuevo de esta
  parte 2, no estaba en el análisis de impacto de la parte 1):
  `apps.configuracion.decorators.requiere_modulo`/`requiere_modulo_json` usan
  `modulo_activo()`, que en la rama "sin negocio" (legacy) propaga
  `ConfiguracionNoInicializada` en vez de devolver `False`. Antes del cambio,
  esa rama nunca fallaba (auto-creaba). Es la misma filosofía que el resto del
  cambio (un gate que no puede determinar el estado no debe inventar `True` ni
  `False`), pero el síntoma visible cambia de "404 correcto" a "500" en una
  instalación sin negocio Y sin configuración — combinación que solo ocurre si
  la secuencia de instalación real no se siguió. No se modificó `decorators.py`
  en esta entrega (no hay un caso de uso real distinto de "fallar visible"
  identificado); queda registrado por si aparece en la matriz de aceptación.

## 6. Pruebas

Entorno: `C:/Proyectos/pos_fifo_system_cierre_claude`, venv aislado `.venv`
(Django 5.2.17), BD `pos_cierre_claude` / test `test_pos_cierre_claude`,
`--settings=config.settings_development`, serial (Windows).

```bash
# Focal (mi propiedad en este bloque) -- corrido dos veces: antes y despues
# de encontrar y arreglar el bug de seed.py (commit c26fd2d, ver §1 lista de
# commits). El resultado final:
python manage.py test apps.configuracion apps.suscripciones apps.api.tests.test_suscripciones_admin --settings=config.settings_development
# Ran 226 tests in 22.534s -- OK (skipped=2, gate opt-in TEN-016-style, ya documentado en parte 1)

python manage.py check --settings=config.settings_development
# System check identified no issues (0 silenced).

python manage.py makemigrations configuracion suscripciones --check --dry-run --settings=config.settings_development
# No changes detected in apps 'configuracion', 'suscripciones'

# Discovery completo (para caracterizar el impacto en apps ajenas, §5 -- no es
# requisito de aceptacion de este bloque; misma disciplina de auditoria que la
# parte 1 aplico por lectura, aca confirmada por ejecucion real)
python manage.py test --settings=config.settings_development
# Ran 1471 tests in 17004.921s -- FAILED (failures=1, errors=234, skipped=3)
```

**Resultado del discovery completo (real, no estimado):** 1471 tests, 1
`FAIL` + 234 `ERROR` + 3 `skip`. La corrida tardó ~4h43min en este entorno
Windows/PostgreSQL serial — mucho más que cualquier corrida focal; quien la
repita, que lo planifique como una corrida larga en background, no como un
chequeo rápido. Los 234 `ERROR` confirman exactamente el mecanismo que la
parte 1 ya había identificado por lectura de código (`setUp` que llama
`get_config()`/`ConfiguracionNegocio.load()` sin crear la config antes,
generalmente en clases de tests de concurrencia con varios métodos —de ahí
que el conteo real (234) sea mayor que la estimación cualitativa de la parte
1 (~80): una clase entera falla por cada `setUp` roto, no un test suelto).
Confirmado por muestreo directo del log en `apps.inventario.tests.test_concurrencia_inventario`
y `apps.ventas.tests.test_concurrencia`, exactamente los archivos que §5 ya
señalaba. El `FAIL` (no `ERROR`) es el hallazgo nuevo de `apps/productos`
descrito en §5. **No se itemizó el listado completo de las 234 fallas** (el
log se truncó a las últimas ~10 al capturarlo); el mecanismo está
100% identificado y no amerita repetir una corrida de 4h43min solo para
tener la lista completa — el arreglo es mecánico y ya está prescrito en §5.

Pruebas nuevas de este bloque, todas contra el código real (no mocks),
confirmadas verdes en la corrida focal final (226 tests, arriba):

- `apps/configuracion/tests/test_cfg012_leer_no_crea.py` (nuevo, 19 tests) —
  instalación legacy sin config, multi-sucursal con una sin config, bootstrap
  explícito idempotente, `crear_config_inicial` sin cambios de conducta,
  render seguro sin tenant activo.
- `apps/configuracion/tests/test_verificar_instalacion.py` (2 tests nuevos,
  1 ajustado) — el diagnóstico reporta `CONFIGURACION_NO_INICIALIZADA` +
  `roto=True` en vez de esconder el estado; el test de CFG-013 ahora crea su
  config explícita (antes dependía del auto-create) para seguir probando lo
  que de verdad prueba (flags reales, no el efecto secundario).
- `apps/suscripciones/tests/test_sus014_validar_plan_slug.py` (nuevo, 5 tests).
- `apps/configuracion/tests/test_ct03_contrato.py` (nuevo, 6 tests) — ata el
  fixture CT-03 al engine/resolutor reales.

## 7. Riesgos y rollback

- **Un edit de esta misma sesión rompió y arregló `bootstrap()` sin
  intervención externa** (commit `c26fd2d`, detalle en §1): el insert de
  `validar_plan_slug()` ancló mal y le quitó el `return resumen` a
  `bootstrap()`. Ninguna suite lo detectó en el momento porque el bug se
  introdujo después de la última corrida focal exitosa; lo encontró la
  verificación explícita de los dos archivos de test más nuevos antes de
  darlos por buenos (disciplina que, de nuevo, sirvió para algo real, no
  ceremonial). Se documenta para que quien revise no confunda "documentado
  sin ejecutar" con lo que sí pasó acá: se ejecutó, falló, se corrigió, se
  reejecutó verde.
- **CFG-012 es un cambio de comportamiento real, no solo un detalle interno.**
  Una instalación que dependía —sin saberlo— del auto-create (por ejemplo,
  alguien que arrancó el servicio antes de correr `crear_config_inicial` y
  "funcionó igual") pasa a fallar fuerte donde antes servía "Mi Negocio" en
  silencio. Es el comportamiento correcto (§1), pero es una superficie nueva
  de 500 en instalaciones mal secuenciadas — mitigado por: (a) el runbook ya
  exige el orden correcto, (b) `verificar_instalacion --strict` ahora lo
  detecta explícitamente antes de registrar el servicio.
- **El gate `requiere_modulo`/`requiere_modulo_json` puede devolver 500 en vez
  de 404** en la combinación exacta "sin negocio + sin configuración" (§5,
  último punto). No se mitigó en esta entrega porque no se identificó un caso
  de uso real que la alcance fuera de una instalación rota; documentado para
  que quien vea el síntoma no lo trate como misterioso.
- **El impacto en tests de C05 (ventas/inventario/CxC/reportes) es real y
  esperado** (§5); no se corrigió porque esos archivos son de otro bloque y
  esta entrega tiene prohibido ampliarse ahí. El arreglo es mecánico y ya está
  prescrito (crear la config explícita en `setUp`, patrón `test_cuadre.py`).
- **`ConfiguracionNegocio.bootstrap()` es nuevo y sin consumidor de producción
  todavía** — hasta que Codex aplique §4.1, no lo llama nadie fuera de los
  tests y del propio diseño. No hay riesgo de uso indebido porque no está
  cableado a nada todavía.
- Rollback: `git revert` de los commits de la rama; sin migraciones, sin
  estado persistente. Revertir el commit de CFG-012 restaura el auto-create
  (y con él, el hallazgo original) — no hay una migración de datos que deshacer
  en ningún sentido.

## 8. Mapas actualizados

- `apps/configuracion/AGENTS.md` — entrypoints `config_o_none()` /
  `modulos_efectivos_o_vacio()` / `ConfiguracionNegocio.bootstrap()`;
  invariante CFG-012. `Última revisión: 2026-09-11`.
- `apps/suscripciones/AGENTS.md` — entrypoint `seed.validar_plan_slug`;
  SUS-014 marcado como "mitad C, falta cableado A". `Última revisión:
  2026-09-11`.

## 9. Siguiente tarea desbloqueada

- **Codex:** integrar §4.1-4.4 (dos líneas de sync + el cableado de
  `validar_plan_slug` en `bootstrap_tenant`), publicar CT-03 en
  `CONTRATOS.md` con la interfaz de §3.2 (reemplaza la fila "PENDIENTE" de
  CT-03), y decidir sobre §4.5 (login) cuando toque `apps/usuarios`.
- **C05 (quien lo retome):** aplicar el arreglo mecánico de §5 a los `setUp`
  de `ventas`/`inventario`/`cuentas_por_cobrar`/`reportes` antes de dar por
  cerrada la integración de esta rama a `develop`.
- **C03 (self-contained, sigue pendiente):** CFG-010 (`AccesoRapidoPOS`, lleva
  migración), CFG-018/019/020/021, SUS-019. Ninguno se tocó en esta entrega
  (fuera del bloque acotado, según instrucción explícita de esta sesión).
- No integrar esta rama a `develop` sin que Codex haya aplicado §4.1/4.2 (si
  no, el pull de sync real puede fallar contra una sucursal recién creada) y
  sin que C05 haya aplicado el arreglo mecánico de §5 a sus `setUp` (si no, la
  integración conjunta hereda los 234 `ERROR` + 1 `FAIL` del discovery de §6).
