# Handoff C03 — Configuración y módulos vendibles (entrega parcial 1)

Estado: **PARCIAL.** Esta entrega cierra 13 hallazgos self-contained de
`apps/suscripciones` y `apps/configuracion` (más SUS-007 y SUS-016 parciales; sin
migraciones, sin tocar archivos de Codex salvo la API específica de suscripciones
que el encargo asigna a C03, sin depender de CT-01/CT-02 implementados) y
**propone CT-03** para desbloquear el resto. Los hallazgos que exigen el resolutor único (CFG-009 / SUS-007),
auditoría CT-01 (CFG-017 / SUS-015), enforcement CT-02 (SUS-006) o tocan
`apps/tenancy` (SUS-014) quedan explícitamente para entregas siguientes — no se
simulan como hechos. Fecha: **2026-09-10**.
Agente: B / Claude. Encargo:
[CIERRE_PROD_CLAUDE.md](../../planes/CIERRE_PROD_CLAUDE.md#c03--configuración-y-módulos-vendibles).

## SHA base / resultado

- Base consumida: tip de `claude/cierre-prod-C02` (`45e0f73`), worktree
  `C:/Proyectos/pos_fifo_system_cierre_claude`, rama nueva
  `claude/cierre-prod-C03`.
- Commits de esta entrega, en orden:
  - `d968e3f` — suscripciones: fail-closed baja, deny key desconocida, check catálogo.
  - `1ca1688` — suscripciones: bootstrap preserva por sucursal, adopta legacy, atómico.
  - `fe5c8de` — configuración: diagnóstico fiel y comando sin objetivo ambiguo.
  - `38e5647` — configuración: validación cruzada (CFG-006) + borrado protegido (CFG-011).
  - `6e3d551` — suscripciones: semántica de `Plan.activo` y overrides (SUS-013).
  - `b19c4a5` — configuración: la UI lee el entitlement efectivo (CFG-009 / SUS-007 mitad UI).
  - `8fd83a0` — cierra los tres bloqueadores de `REVISION_MERGE_A02-C03.md`
    (ver sección siguiente).
- **No publicado a `origin`** ni fusionado a `develop` (mismo criterio que C01/C02:
  cada bloque cierra en su rama secuencial; la integración a `develop` se decide
  en un checkpoint mayor con el usuario/Codex). Working tree limpio al cierre.

## Cierre de los bloqueadores de merge (2026-09-10, `8fd83a0`)

Codex revisó este checkpoint junto con A02 en
[REVISION_MERGE_A02-C03.md](REVISION_MERGE_A02-C03.md) y encontró tres
hallazgos que bloqueaban el merge a `develop`. Los tres se cerraron en
`8fd83a0`, sobre este mismo worktree/rama:

- **MERGE-C01-ENVONLY** — `deploy/actualizar.bat` exigía siempre
  `env_cliente.bat`, aunque el resto del script (líneas 111+) ya soportaba
  `env_cliente.env` solo. El chequeo temprano ahora acepta las tres variantes
  (BAT legado, solo `.env`, o ambos) y solo falla si ninguna existe.
- **MERGE-C01-SECRET-CAMPO** — `deploy/preflight_actualizar.py campo`
  aceptaba cualquier `nombre` y lo imprimía. Nueva allowlist explícita
  (`_CAMPOS_NO_SENSIBLES`) con todos los campos no sensibles del template;
  cualquier nombre fuera de ella —`DB_PASSWORD`, `DJANGO_SECRET_KEY`,
  `INITIAL_SYSADMIN_PASSWORD`, `CLOUD_API_TOKEN` incluidos— se rechaza (exit 1)
  sin tocar stdout/stderr. Cubierto por
  `test_preflight_actualizar.CampoTests.test_claves_sensibles_se_rechazan_sin_imprimir`
  y `test_nombre_arbitrario_fuera_de_allowlist_se_rechaza`.
- **MERGE-C03-ALIAS-ATOMIC** — `seed.bootstrap` abría `transaction.atomic()`
  sin alias: bajo `with_tenant`, el router manda los managers a la BD del
  tenant mientras la transacción se abría sobre `default`, así que un fallo a
  mitad de camino no revertía nada en el tenant. `bootstrap` ahora resuelve un
  alias (parámetro explícito `using`, o el tenant activo en contexto vía
  `apps.tenancy.context.get_current_tenant_alias()`, o `default` fuera de
  tenancy — mismo patrón que `apps/notificaciones/services.py` y
  `apps/api/views/sync.py`), aplica `.using(alias)` a cada consulta de
  `sembrar_modulos`/`crear_planes_default`/`derivar_modulos_de_flags`/
  `_preservar_overrides_por_sucursal`, y abre `transaction.atomic(using=alias)`.
  `bootstrap_suscripciones` resuelve el mismo alias y envuelve su propio
  `--dry-run` con `transaction.atomic(using=alias)` +
  `transaction.set_rollback(True, using=alias)` (antes el rollback del dry-run
  quedaba en una transacción `default` separada de la del tenant). La
  migración `0002_seed_suscripciones` pasa `using=schema_editor.connection.alias`
  explícito, sin depender del contexto ambiente.
  - **La firma de `seed.bootstrap` sigue siendo compatible**: `using` es
    keyword-only con default `None`; los llamadores existentes de
    `apps/tenancy` (`bootstrap_tenant.py`, `normalizar_import_tenant.py`, no
    tocados — son de Codex) corren dentro de `tenant_context(...)`, así que
    heredan el alias correcto sin cambios.
  - **Falta cerrar** (ver "Secuencia acordada" en
    [REVISION_MERGE_A02-C03.md](REVISION_MERGE_A02-C03.md)): un test que
    inyecte un fallo dentro de `bootstrap` corriendo sobre una BD tenant real
    (vía `with_tenant`) y confirme que revierte módulos/planes/suscripción/
    overrides en esa BD sin tocar `default`. La infraestructura de dos BDs
    físicas (namespace `TENANT_TEST_DB_NAMESPACE`, TEN-016) es de A02 y todavía
    no está en este worktree — se agrega al fusionar el tip de `develop` en el
    paso 2 de la secuencia acordada.

Suite focal de `apps.configuracion` + `apps.suscripciones` + `apps.api`
(112 tests), `manage.py check` y `makemigrations --check --dry-run` en verde.
Sin migraciones nuevas.

## Qué cubre esta entrega

Punto 1 del encargo ("cerrar todos los hallazgos CFG/SUS vigentes... no solo los
de dotenv") para los hallazgos que se pueden cerrar **sin** el resolutor único,
sin auditoría CT-01 y sin enforcement CT-02, dentro de las dos apps propias.

### apps/suscripciones — commit `d968e3f`

- **SUS-010 (P1, "el más peligroso")** — `engine._datos_bloqueantes` tragaba
  cualquier excepción del hook de datos en vuelo y devolvía `None`
  ("no hay datos pendientes"), así que un fallo de infra (tabla indisponible,
  error de esquema) **autorizaba** la baja de un módulo. Ahora falla cerrado:
  loguea y devuelve un motivo bloqueante. Un hook que no puede *demostrar* que
  no hay trabajo en vuelo no habilita apagar nada.
- **SUS-018 (P3)** — `modulo_activo(key, negocio=None)` devolvía `True` para
  cualquier string en el camino fail-open; una `key` fuera del registro (typo en
  un gate nuevo) se colaba invisible y sin log. Ahora una key desconocida
  **deniega y avisa**; el fail-open queda solo para keys reales del catálogo.
- **SUS-012 (P2)** — nuevo `apps/suscripciones/checks.py` (system checks,
  registrados en `apps.py`): falla si el registro en código es inconsistente
  (keys duplicadas → E001, dependencia colgada → E002, ciclo → E003) o si el
  espejo DB `Modulo` diverge del registro (fantasma en DB → E004; falta sembrar →
  W001; `core` divergente → W002). El drift silencioso (un plan que promete un
  módulo que runtime no reconoce) pasa a ser un fallo con diff accionable.

### apps/suscripciones — commit `1ca1688` (onboarding)

- **SUS-008 (P1)** — el bootstrap derivaba el set como UNIÓN de las sucursales,
  así que una sucursal con e-CF=False quedaba con e-CF encendido si otra lo tenía
  en True. Ahora, en la misma transacción, crea un override negativo por sucursal
  (`SucursalModuloOverride activo=False`) para conservar cada estado previo bit
  por bit.
- **SUS-009 (P1)** — la derivación filtraba `sucursal__negocio` e ignoraba en
  silencio las configuraciones legacy con `sucursal=NULL`. Ahora se adoptan
  cuando hay un único negocio (atribución inequívoca) y se aborta con
  `BootstrapAmbiguo` —sin escribir nada— cuando hay varias/ninguna.
- **SUS-016 (P2, parcial)** — `seed.bootstrap` corre dentro de
  `transaction.atomic()` y devuelve un dict-resumen; el comando
  `bootstrap_suscripciones` agrega `--dry-run` (simula y revierte) e informa el
  resumen. Falta la parte de "checkpoint reanudable por flota"; para una sola
  instalación (RP/SK) la atomicidad + dry-run alcanza.

> **La firma de `seed.bootstrap` NO cambió.** `SucursalModuloOverride` se
> resuelve desde `NegocioModuloModel._meta.apps.get_model(...)` — el registro de
> apps del llamador — así que funciona tanto con los modelos históricos de una
> migración como en runtime, y **los llamadores de `apps/tenancy` no se tocan**.
> Ver "Dependencias del otro agente".

### apps/configuracion — commit `fe5c8de` (diagnóstico y comandos)

- **CFG-013 (P2)** — `verificar_instalacion` en modo legacy devolvía SIEMPRE
  `apagados=[]` sin mirar los flags; podía certificar "sano" con
  `impresion_termica` apagada. Ahora enumera el mismo conjunto que
  `modulo_activo()` y marca `roto` si `impresion_termica` quedó apagada.
- **CFG-014 (P2)** — el diagnóstico mostraba `ConfiguracionNegocio.objects.first()`
  (podía ser la de OTRA sucursal) y terminaba en 0 aunque hubiera problemas.
  Ahora reporta la config de la sucursal resuelta (con `pk` y binding) y agrega
  `--strict` para terminar distinto de cero (gate de despliegue).
- **CFG-015 (P2)** — `crear_config_inicial` sin `--sucursal` tomaba `.first()` y
  la pisaba aunque estuviera ligada a una sucursal. Ahora aborta si existen
  configs ligadas (pide `--sucursal` y lista los códigos), y el modo legacy opera
  solo sobre la fila `sucursal=NULL`, fallando si hay más de una.

### apps/configuracion — commit `38e5647` (modelo)

- **CFG-006 (P2)** — el modelo no tenía `clean()` y `full_clean()` aceptaba
  combinaciones inseguras. Ahora rechaza: cero medios de pago (el POS no cobra),
  e-CF activo sin `emisor_activo`, e ITBIS fuera de `[0,100]`. Validación de
  aplicación (Admin/forms); los `CheckConstraint` DB quedan para un preflight
  coordinado (una instalación existente podría violarlos hoy y romper el migrate).
- **CFG-011 (P2)** — `delete()` del modelo era un `pass` silencioso (el caller
  creía haber borrado) mientras `QuerySet.delete()` no pasaba por ahí y SÍ
  borraba. Ahora ambas rutas levantan `ConfiguracionProtegidaError`, via un
  manager con QuerySet propio. Sin migración (manager sin `use_in_migrations`).

### apps/suscripciones — commit `6e3d551` (semántica de estados)

- **SUS-013 (P2)** — `Plan.activo=False` pasa a significar "no vendible a nuevas
  altas": el serializer rechaza asignarlo a una nueva alta / cambio de plan, pero
  **no** bloquea re-guardar a un suscriptor que ya lo tiene (no suspende clientes
  existentes — la decisión del encargo). Excluir un módulo core, o apagarlo por
  sucursal, se rechaza (era un no-op). `SucursalModuloOverride.clean()` rechaza
  `activo=True` y targets core. Toca `apps/api/serializers/suscripciones.py`
  (la "API específica" de suscripciones que el encargo asigna a C03).

### apps/configuracion + templates — commit `b19c4a5` (resolutor único, mitad UI)

- **CFG-009 (cerrado, UI) / SUS-007 (parcial)** — templates y menús leían
  `config.modulo_*` (flag legacy crudo) mientras el backend gateaba por el engine.
  Nuevo `utils.modulos_efectivos()` = set de keys activas para la sucursal actual
  por el **mismo** motor que el backend; el context processor lo inyecta como
  `modulos_efectivos`. Migrados los 7 gates de template (`base.html`,
  `pos/punto_venta`, `pos/venta_exitosa`, `caja/index`, `caja/historial`) a
  `{% if 'key' in modulos_efectivos %}`. **Falta la otra mitad de SUS-007**: el
  pull de sync (`apps/api/views/sync.py`, `apps/sync/engine.py`) sigue serializando
  los flags legacy — es de Codex; se solicita abajo.

## Pruebas

```
python manage.py test --settings=config.settings_development   # suite completa, serial
# Ran 1252 tests ... OK.   (tras CFG-006/011, SUS-013 y CFG-009/SUS-007 mitad UI)
```

Dirigidos por app: `apps.suscripciones` 61 OK (21 nuevos:
SUS-008/009/010/012/013/016/018), `apps.configuracion` 106 OK
(test_crear_config_inicial nuevo + regresiones CFG-006/009/011/013/014),
`apps.caja/ventas/reportes/cotizaciones` (render de templates migrados) OK,
`apps.tenancy` + api gating/admin (llamadores/consumidores) OK, cero regresiones.

`manage.py check` → sin issues (los nuevos system checks pasan contra la BD real).
`makemigrations configuracion suscripciones --check` → sin cambios (ninguna
migración en toda la entrega).

## Archivos

- `apps/suscripciones/engine.py` (SUS-010, SUS-018)
- `apps/suscripciones/checks.py` (nuevo — SUS-012)
- `apps/suscripciones/apps.py` (registra los checks)
- `apps/suscripciones/seed.py` (SUS-008, SUS-009, SUS-016)
- `apps/suscripciones/management/commands/bootstrap_suscripciones.py` (--dry-run/resumen)
- `apps/suscripciones/tests/test_auditoria_suscripciones.py` (regresiones)
- `apps/suscripciones/AGENTS.md` (invariantes + fecha)
- `apps/configuracion/management/commands/verificar_instalacion.py` (CFG-013/014)
- `apps/configuracion/management/commands/crear_config_inicial.py` (CFG-015)
- `apps/configuracion/models.py` (CFG-006 `clean()`, CFG-011 delete/manager)
- `apps/suscripciones/models.py` (SUS-013 `SucursalModuloOverride.clean()`)
- `apps/api/serializers/suscripciones.py` (SUS-013 `validate_plan` + core exclusion)
- `apps/configuracion/tests/test_verificar_instalacion.py` (regresiones + un test
  actualizado al contrato correcto de CFG-013)
- `apps/configuracion/tests/test_crear_config_inicial.py` (nuevo — CFG-015)
- `apps/configuracion/tests/test_auditoria_configuracion.py` (regresiones CFG-006/009/011)
- `apps/configuracion/utils.py` (CFG-009 `modulos_efectivos()`)
- `apps/configuracion/context_processors.py` (inyecta `modulos_efectivos`)
- `templates/base.html`, `templates/pos/punto_venta.html`,
  `templates/pos/venta_exitosa.html`, `templates/caja/index.html`,
  `templates/caja/historial.html` (gates migrados a `modulos_efectivos`)
- `apps/configuracion/AGENTS.md` (invariantes + fecha)
- Este handoff.

## Migraciones / BDs

Ninguna. Todo es lógica, comandos, checks y tests.

---

## Propuesta de contrato CT-03 — configuración/capacidades efectivas

Cumple el punto 2 del encargo ("publicar un único resolutor efectivo consumible
por UI, API, servicios y sync") y desbloquea CFG-009 y SUS-007. Se propone; A
integra `config/**`, routers/settings y `apps/sync`.

### Alcance: dos resolutores, una regla cada uno

CT-03 cubre **dos** verdades efectivas distintas, hoy ya implementadas en las
apps de C03, que este contrato fija para que UI/API/servicio/sync consuman lo
mismo:

**(A) Configuración efectiva de la sucursal** — identidad fiscal, medios de pago,
parámetros operativos. Resolutor: `apps.configuracion.utils`:

- `get_config()` → la `ConfiguracionNegocio` de la sucursal del proceso
  (`SUCURSAL_CODIGO`), cacheada con clave `config_negocio:<tenant>:<sufijo>`
  (namespace de tenant, CFG-001), TTL 30 s local / 600 s compartido (CFG-005),
  fail-loud sin tenant activo bajo tenancy.
- `config_de_sucursal(sucursal)` / `config_para_documento(sucursal)` → la de
  una sucursal concreta (COM-001), sin mirar `SUCURSAL_CODIGO`.
- Precedencia de settings (ya fijada por A00/A01, se hereda tal cual): variables
  de proceso > `POS_ENV_FILE` > defaults; `load_dotenv(override=False)`.
- **Leer no crea configuración** — es la regla que falta cerrar (CFG-012): hoy
  `load()` hace `get_or_create` y el context processor lo llama en cada template.
  CT-03 fija que `get_config()`/render/GET no cambian conteo ni timestamps.

**(B) Capacidades efectivas (módulos) del negocio/sucursal** — resolutor:
`apps.suscripciones.engine`:

```python
modulos_activos(negocio, sucursal=None) -> set[str]
modulo_activo(key, negocio=None, sucursal=None) -> bool
estado_suscripcion(negocio) -> {SIN_APROVISIONAR|SUSPENDIDA|CON_PLAN|CUSTOM}
```

- Regla: `cierre( plan.modulos ∪ incluidos − excluidos ) ∪ core − {overrides
  sucursal apagados}`. Estados explícitos (SUS-001). Key desconocida deniega
  (SUS-018). Fail-open comercial solo sin negocio ni SIN_APROVISIONAR.
- BD como verdad + memo por request; cache con versión + namespace tenant (SUS-002);
  convergencia entre workers dentro del TTL (SUS-003).

### Lo que falta para "una sola verdad" (CFG-009 / SUS-007)

Hoy **templates y sync leen `ConfiguracionNegocio.modulo_*` (flags legacy)**,
mientras servicios y decoradores leen el engine. Propuesta:

1. **Context processor de capacidades** (C, `apps/configuracion`): junto a
   `config`, inyectar `modulos_efectivos` = `set` de keys activas para la
   sucursal actual, calculado por el engine. `templates/base.html` y
   `templates/pos/*` pasan a preguntar por `modulos_efectivos` en vez de
   `config.modulo_*`. (C-owned; no hay RBAC/entitlement paralelo en JS.)
2. **Payload `/auth/perfil/`** (CT-02, A): el envelope `rbac.modules` ya es el
   canal; C entrega el set del engine, A lo cablea al endpoint.
3. **Pull de sync** (`apps/api/views/sync.py`, `apps/sync/engine.py` — **A/Codex**):
   hoy serializa los flags legacy `modulo_*`. Se solicita a Codex migrar el pull
   a un snapshot efectivo por sucursal (o marcar los flags legacy como derivados
   del engine). C entrega el helper y los casos; A edita esos archivos.

### Fixture / errores / revisión

- Fixture canónico propuesto: `fixtures/ct03_capacidades_efectivas_v1.json`
  (negocio con plan + un override negativo de sucursal → set esperado por
  sucursal). C lo propone como `C03-*`; A lo integra a un solo SHA canónico.
- Errores: `TenantContextError` (config/módulos sin tenant activo bajo tenancy),
  `ConfiguracionNoResuelta` (código no resuelve con >1 config).
- Revisión: `capabilities.v1`; cambiar la regla exige repetir las pruebas
  consumidoras (matriz permisos × módulos × suspensión × sucursal del encargo).

---

## Deltas propuestos a documentos que edita Codex

**`docs/TODO_AUDITORIAS.md`** — mover a corregido: **SUS-008, SUS-009, SUS-010,
SUS-012, SUS-013, SUS-016 (parcial), SUS-018, CFG-006, CFG-009, CFG-011, CFG-013,
CFG-014, CFG-015**. Siguen abiertos: SUS-006, SUS-007 (parcial: UI hecha, pull de
sync pendiente), SUS-011, SUS-014, SUS-015, SUS-017, SUS-019; CFG-010, CFG-012,
CFG-017, CFG-018, CFG-019, CFG-020, CFG-021. (CFG-016 es de C01; CFG-018/COM-* de C02.)

**`docs/ESTADO_AUDITORIAS.md`** — registrar: C03 cerró el batch de engine/onboarding
de suscripciones y de diagnóstico/comandos de configuración; **sin migraciones
nuevas**; **cambio de conducta a revisar antes de desplegar** (ver Riesgos);
CT-03 propuesto en este handoff, pendiente de integrar por A.

**`docs/PROJECT_STATUS.md`** — donde lista el avance del cierre, agregar:
"C03 iniciado (suscripciones engine/onboarding y configuración diagnóstico
cerrados; resolutor único CT-03 propuesto, enforcement/auditoría pendientes)".

**`docs/BUGS.md`** — sin bug vivo nuevo; SUS-010 (fail-open de integridad) queda
documentado como cerrado.

**Snapshots de auditoría** (`AUDITORIA_CODIGO_APPS_SUSCRIPCIONES.md`,
`_CONFIGURACION.md`) — históricos, no se editan; la fuente de verdad pasa a ser
este handoff + los `AGENTS.md` (ya actualizados) + el código.

## Dependencias del otro agente

- **`seed.bootstrap` cambió de conducta (no de firma).** Ahora (a) crea overrides
  negativos por sucursal (SUS-008) y (b) aborta con `BootstrapAmbiguo` si hay
  filas legacy `sucursal=NULL` y ≠1 negocio (SUS-009). Sus llamadores
  `apps/tenancy/management/commands/bootstrap_tenant.py` y
  `normalizar_import_tenant.py` (Codex) **no se editaron**. En una BD por tenant
  hay exactamente 1 negocio, así que el aborto no dispara y la adopción es la
  correcta; aun así **Codex debería re-correr sus tests de provisión de tenant**
  con este seed (sus 57 tests de tenancy/api pasan localmente, ver Pruebas).
- **CT-01 (auditoría)**: `registrar_mutacion` sigue `PENDIENTE A02`. CFG-017 y
  SUS-015 (auditoría de cambios de config/comerciales) quedan a la espera de ese
  commit; los puntos de enganche son las mutaciones del Admin/serializers/comandos.
- **CT-02 (permisos)**: confirmar si A03 ya implementó el envelope de capacidades;
  condiciona SUS-006 (enforcement HTML) y el payload `/auth/perfil/` de CT-03(B).
- **Sync (Codex)**: la parte de SUS-007 que toca `apps/api/views/sync.py` y
  `apps/sync/engine.py` (pull de flags legacy) se solicita a Codex; C entrega
  helper + casos.

## Pendientes explícitos del encargo C03, NO cubiertos en esta entrega

1. **SUS-007 (resto)** — la mitad UI del resolutor único ya está (CFG-009 cerrado,
   context processor + templates). Falta el **pull de sync**
   (`apps/api/views/sync.py`, `apps/sync/engine.py`), que aún serializa los flags
   legacy `modulo_*`: es de **Codex**. C entrega `utils.modulos_efectivos()` como
   helper y los casos; A migra el pull a un snapshot efectivo por sucursal (o
   marca los flags como derivados del engine).
2. **CFG-010** — `AccesoRapidoPOS` sin ámbito de sucursal/negocio ni constraints
   (requiere migración; el consumidor `apps/ventas/views.py` es de C05, coordinar).
3. **CFG-012** — leer no crea configuración (separar `get` de `bootstrap`; toca el
   context processor —C— y el GET de sync —Codex—; cuidado: render de login/error).
4. **CFG-017 / SUS-015** — auditoría de dominio (espera CT-01).
5. **SUS-006** — enforcement de módulo en vistas HTML de CxC y reportes on-demand
   (apps de dominio C05; coordinar; capa adicional al permiso).
6. **SUS-011** — señales `on_commit`. Necesita el patrón memo-local/`on_commit` de
   permisos (con `TransactionTestCase` o memo de request) para no romper la
   invalidación síncrona que esperan los tests actuales; se dejó fuera del batch 1
   a propósito.
7. **SUS-014** — divergencia `Tenant.plan_slug` vs plan operativo
   (`bootstrap_tenant` es de Codex; C entrega validación, A la cablea).
8. **SUS-017** — presets versionados (el sync de planes default no restaura).
9. **CFG-018/019/020/021 · SUS-019** — lifecycle de logo, superficies muertas,
   gramática de barcode, y ampliar la matriz de tests (multi-worker/scope).
10. **OPS-SUS-001, OPS-CFG-002, OPS-CFG-003, OPS-COM-001** — preflight de solo
    lectura por instalación y asignación de `configuracion.administrar`; se cierran
    en C06/visita, no por un mock.
11. Pendiente heredado de C02 (#6 de su handoff): `ConfiguracionNegocio.logo` sin
    validator de tamaño/formato **al subir** — candidato natural con CFG-018,
    reusando `utils.imagenes.validar_imagen_subida`/`TAMANO_MAX_BYTES`.

## Riesgos y rollback

- **Cambio de conducta a comunicar antes de desplegar (SUS-008/009):** al correr
  `bootstrap_suscripciones` sobre una instalación multi-sucursal ya existente,
  ahora se crean overrides negativos por sucursal — una sucursal que "heredaba"
  un módulo por la unión dejará de tenerlo si su flag estaba en False. Es lo
  correcto (preserva la conducta real previa), pero conviene un `--dry-run`
  antes. Y si hay una config legacy `sucursal=NULL` con varios negocios, el
  comando ahora **aborta** en vez de ignorarla: hay que asignarle sucursal.
- **SUS-010 fail-closed:** si un hook de datos bloqueantes falla por infra, una
  baja de módulo ahora se rechaza (antes se permitía). Correcto, pero un fallo de
  BD intermitente puede bloquear una operación administrativa legítima; el log
  dice la causa real (infra, no permiso).
- **CFG-014 `--strict`:** sólo cambia el exit code cuando se pasa el flag; sin él,
  la conducta es la de antes. Los scripts de deploy deberían adoptarlo como gate.
- **CFG-006 `clean()` en Admin:** una `ConfiguracionNegocio` existente que hoy
  viole una regla nueva (cero pagos, e-CF sin emisor, ITBIS fuera de rango) no
  se podrá **guardar desde Admin** hasta corregirla. No hay constraint DB, así
  que el `migrate` no falla y el runtime sigue leyéndola; sólo el guardado por
  formulario la exige.
- **CFG-009 (UI) cambio de conducta:** los menús/pantallas ahora reflejan el
  entitlement efectivo. En un negocio **sin aprovisionar** (fail-open) aparecen
  todos los módulos —incluidos los que antes ocultaba un flag legacy en False
  (p. ej. financiación)—, porque así coincide con lo que el backend ya permitía.
  En modo legacy (sin negocio) la UI sigue leyendo los flags. Ninguna regresión
  en la suite, pero conviene saberlo antes del go-live.
- Rollback: `git revert` de los commits de la rama (6 de código + handoffs); sin
  estado persistente ni migración.

## Siguiente tarea desbloqueada

Sobre este mismo worktree y rama `claude/cierre-prod-C03`. Orden sugerido para la
próxima sesión (independientes primero, coordinados después):

1. **CFG-010** (ámbito/constraints de `AccesoRapidoPOS`) + **SUS-017** (presets
   versionados) — self-contained; CFG-010 lleva migración y coordina el consumidor
   de C05 (`apps/ventas/views.py`).
2. **CFG-012** (leer no crea configuración) — cuidado con el render de login/error
   y el GET de sync (Codex).
3. **Solicitud a Codex:** migrar el pull de sync a `modulos_efectivos()` (resto de
   SUS-007). Y coordinar el merge de **A02/CT-01** a la base para cerrar CFG-017 /
   SUS-015; con **CT-02** (A03), SUS-006 y el payload `/auth/perfil/` de CT-03(B).
