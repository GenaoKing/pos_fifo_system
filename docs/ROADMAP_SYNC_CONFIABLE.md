# Roadmap — Sync confiable (local ↔ cloud)

Estado: **plan aprobado, sin ejecutar**. Fecha: 2026-08-19.
Fuentes relacionadas: `docs/BUGS.md` (BUG-A, BUG-B),
`docs/runbooks/SYNC_EMULACION_SUCURSAL_PROD.md`, `docs/ROADMAP_TENANCY_DBPERTENANT.md`.

---

## Objetivo

Convertir el canal de sincronización de "creo que está sincronizado" a
"**sé** que está sincronizado", sin cambiar el stack ni el patrón de diseño.

El diagnóstico del 2026-08-19 confirmó que la arquitectura (outbox transaccional +
pull incremental + claves naturales + idempotencia por hash) es la correcta. Lo que
falla es (1) el outbox no es realmente transaccional, (2) el cursor de pull está
mal emparejado con el orden de los endpoints, y (3) **no existe ninguna capa de
verificación de integridad extremo a extremo**.

## Principio rector

> Ningún error de configuración, de red o de serialización puede hacer que un
> hecho de negocio ocurrido en la sucursal deje de existir para el cloud.
> Y si aun así diverge, el sistema lo detecta solo — no un humano mirando.

## Cómo ejecutar este documento

Cada fase es independiente y mergeable por separado. Al terminar una fase se
llena su bloque **Resultado** con lo que realmente pasó (números, no adjetivos) y
se marca el estado en la tabla de abajo. No pasar a la siguiente fase con la
anterior en rojo.

| Fase | Nombre | Estado | Rama sugerida |
|---|---|---|---|
| 0 | Rig de simulación + medición del daño | 🟡 parcial — comando listo, falta ciclo real y correr en clientes | `features/sync-verificacion` |
| 1 | Outbox transaccional + upsert de cliente (BUG-A + BUG-C) | 🟡 código listo y probado; falta desplegar | `features/sync-verificacion` |
| 2 | Cursor keyset estable (BUG-B) | 🟡 código listo y probado; falta desplegar | `features/sync-verificacion` |
| 3 | Anti-entropía (reconciliación periódica) | ⬜ pendiente | `features/sync-antientropia` |
| 4 | Configuración dinámica del servicio (dotenv) | 🟡 código listo y probado; falta desplegar | `features/sync-verificacion` |
| 5 | Actualización remota del POS local | ⬜ futura (requiere Fase 4) | — |

---

## Fase 0 — Rig de simulación + medición del daño

**Objetivo.** Tener un POS local real contra un tenant cloud real donde probar
todo lo demás sin tocar clientes, y saber el tamaño exacto del daño actual.

**Por qué primero.** Las fases 1-3 cambian el camino por donde viajan las ventas
de dos negocios en producción. Necesitamos un banco de pruebas antes, no después.

### 0.1 Topología del rig

El POS local es una **sucursal de verdad**: su propia app Django contra su propia
BD PostgreSQL local. Nunca se conecta a la BD del cloud. Lo único que cruza es
**HTTP contra la API**, autenticado con un token de sucursal; es el cloud quien,
a partir de ese token, resuelve a qué tenant y a qué BD escribir.

```
  PC local (simula la sucursal)              Azure
  ┌─────────────────────────────┐            ┌──────────────────────────────┐
  │ Django POS                  │            │ posfifo-prod-api             │
  │ settings_demo_branch        │  HTTPS     │  (Container App)             │
  │                             │ ─────────► │   token ⇒ tenant             │
  │ PostgreSQL local            │  token de  │      ↓                       │
  │  pos_fifo_demo_branch       │  sucursal  │ posfifoplatformpg            │
  │  (única BD que toca el POS) │            │  tnt_royalplastdemo          │
  └─────────────────────────────┘            └──────────────────────────────┘
```

Tenant destino en el cloud: **`royalplastdemo`** (`tnt_royalplastdemo`, 273
productos, 321 ventas, sucursal `01`). Autorizado por el dueño para pruebas.
**No usar `royalplast` ni `skperformance`.**

> Las consultas `psql` directas contra el servidor de Azure que aparecen en los
> runbooks son para **diagnóstico del operador**, no parte del camino del POS.

> `demo` (`tnt_demo`) está vacío: sirve para probar el bootstrap desde cero, pero
> no para probar paginación ni cursores. Para eso se usa `royalplastdemo`, cuyos
> 273 productos **superan el page size de 200** y por tanto ejercitan justo el
> camino donde vive BUG-B.

### 0.2 Estado actual del rig (verificado 2026-08-19)

**El rig ya está montado** desde el 2026-06-19; no hay que crearlo, hay que
retomarlo. `config/settings_demo_branch.py` existe y la BD local también:

| Pieza | Estado |
|---|---|
| BD local `pos_fifo_demo_branch` | existe, 13 MB |
| Sucursal local | `01` — "Royal Plast - Sucursal Local (DEMO)" |
| Maestros bajados del cloud | 273 productos, 20 categorías, 3 clientes |
| Movimiento local | 1 venta, 2 eventos |
| Cursores `sync_versionmaestro` | poblados; último pull exitoso 2026-06-19 |

Para retomarlo hace falta solo: el token de sync en claro de `royalplastdemo`
(sacarlo siguiendo `docs/runbooks/SYNC_EMULACION_SUCURSAL_PROD.md` §3) y las
variables de entorno de sync apuntando a la API de prod.

Ciclo de humo:
`python manage.py sincronizar --once --settings=config.settings_demo_branch`

### 0.3 Comando `verificar_sync` (solo lectura)

Nuevo: `apps/sync/management/commands/verificar_sync.py`. No escribe nada.

Reporta:

1. **Objetos sin evento** — ventas, aperturas, cierres, movimientos de caja,
   compras y ajustes cuya PK no aparece en ningún `EventoSync.objeto_id_local`
   del tipo correspondiente. Agrupado por día.
2. **Huecos de numeración** — días donde `max(secuencia) <> count(*)` sobre
   `numero_venta` (`V-YYYYMMDD-NNNN`). Es el detector que encontró el hueco de
   Royal Plast.
3. **Diagnóstico de configuración** — bandera roja si `CLOUD_API_URL` /
   `CLOUD_API_TOKEN` están definidos pero `SYNC_ENABLED` es false (= síntoma
   exacto del entorno mal registrado en el servicio).
4. **Salud de la cola** — eventos por estado, los que superaron `max_retries`,
   antigüedad del más viejo pendiente.

Flags: `--dias=N` (ventana, default 90), `--json` (para automatizar después).

**Criterios de aceptación.**

- Corre en el rig y en una copia de la BD de Royal Plast sin escribir nada.
- Sobre el rig, detecta correctamente un hueco inducido a mano.
- Sobre la copia de RP, reporta ≥8 ventas sin evento (las conocidas del 20 y 23
  de junio) y ningún falso positivo en los días sanos.

**Riesgos.** Ninguno: es de solo lectura.

### Resultado Fase 0

**Ejecutado parcialmente el 2026-08-19** (rama `features/sync-verificacion`).

**Hecho:**

- `apps/sync/management/commands/verificar_sync.py` implementado (solo lectura)
  con los 4 chequeos: configuración, hechos sin evento, huecos de numeración y
  salud de la cola. Flags `--dias`, `--json`, `--detalle`.
- `apps/sync/tests/test_verificar_sync.py`: **19 tests, verdes**.
- Suite completa: 323 tests, 1 falla **preexistente y ajena** (el time-bomb de
  `test_anulacion_pago` con fecha fija `2026-07-15`).

**Validación contra datos reales.** Se corrió el comando en modo solo lectura
contra `tnt_royalplast` y reprodujo **exactamente** los huecos que se habían
encontrado a mano con SQL:

| Día | Presentes | Máximo | Faltan |
|---|---|---|---|
| 2026-06-20 | 13 | 14 | `0011` (día del cutover) |
| 2026-06-23 | 3 | 10 | `0001`–`0007` |

`tnt_skperformance`: **sin huecos**.

**Falso positivo encontrado y corregido.** La primera corrida reportó además un
hueco el 2026-05-21. Era un **bug del propio comando**: la ventana `--dias=N`
cortaba en "hace N días exactos" (12:19), dejando fuera una venta de las 12:13 de
ese día e inventando el hueco. La ventana ahora arranca a **medianoche local**.
Con test de regresión.

**El import al cloud NO fue con pérdida (verificado).** Se comparó el conjunto de
`numero_venta` de la copia local de Royal Plast (`royal_eval`, dump del
2026-06-13, 320 ventas) contra `tnt_royalplast` en la misma ventana:

- En local pero no en cloud: **ninguna**.
- En cloud pero no en local: 4 (`VENTA-20260613-0001..0004`), explicables porque
  el dump de evaluación se tomó a media tarde y el cutover usó uno posterior.

Conclusión: la pérdida confirmada se reduce a la ventana del **2026-06-23**
(7 ventas) y posiblemente `V-20260620-0011`.

**Hallazgo del rig.** `SUCURSAL_CODIGO` resuelve a `SD-001` (el default de
`config/settings.py:400`) pero la sucursal de la BD del rig es `01`. Con esa
configuración los eventos se encolarían sin sucursal. Hay que fijar
`SUCURSAL_CODIGO=01` en el entorno del rig antes de correr un ciclo.

**Guardarraíl añadido.** Al correrlo contra una BD del lado cloud, el análisis
"sin evento" reportaba 779 falsos positivos (en el cloud los eventos recibidos
no llevan `objeto_id_local`). El comando ahora lo detecta y lo avisa, y solo da
por válidos los huecos de numeración. Con test.

**Pendiente de la fase:**

- [ ] Correr un ciclo real del rig contra `royalplastdemo` (requiere el token de
      sync en claro y `SUCURSAL_CODIGO=01` en el entorno).
- [ ] Correr `verificar_sync` en las instalaciones de Royal Plast y SK
      Performance — ahí es donde el análisis "sin evento" sí aplica y donde se
      sabrá el número real de objetos perdidos. Requiere acceso a esas PCs.
      **No bloquea las fases 1-3**: lo perdido ya está perdido y seguirá siendo
      medible y reparable después. Lo urgente es dejar de perder.

**Nota de fidelidad del rig.** El rig corre `settings_development` con
`runserver`; los clientes corren `settings_production` bajo waitress registrado
como servicio Windows con `nssm`. BUG-A es *específicamente* un fallo de que las
variables no lleguen al servicio. Por eso: para las fases 1-3 (código) el rig
actual alcanza; para la **Fase 4** (dotenv/nssm) hay que levantar el rig
**como servicio registrado**, o no se estará probando el modo de falla real.

---

## Fase 1 — Outbox transaccional + backfill (BUG-A)

**Objetivo.** Que sea imposible que una venta exista sin su evento.

**Por qué.** Hoy `_crear_evento` corre en `transaction.on_commit`, o sea en una
transacción **posterior y separada** a la del negocio, y arranca con un
`if not SYNC_ENABLED: return None`. Resultado: la venta se guarda y el evento
nunca se escribe, sin error, sin pendiente, sin rastro.

### 1.1 Mover el gate de emisión a envío

- Quitar el `if not settings.SYNC_ENABLED` de `apps/sync/events.py:_crear_evento`.
- Aplicar el gate en el **push** (`SyncEngine.push_eventos` y el comando
  `sincronizar`, que ya lo tiene).
- Efecto: una instalación sin cloud acumula eventos inertes en su tabla local.
  Es barato (una fila por transacción) y hace que **encender el sync más tarde
  recupere el histórico automáticamente**, en vez de perderlo.
- Añadir purga opcional (`--purgar-confirmados --dias=N`) para que la tabla no
  crezca sin control en instalaciones standalone.

### 1.2 Escribir el evento dentro de la transacción de negocio

Sustituir el patrón actual en los 18 puntos de llamada:

```python
# ANTES — el evento vive fuera de la transacción
transaction.on_commit(lambda v=venta: sync_events.evento_venta_creada(venta))

# DESPUÉS — el evento es parte de la misma transacción que la venta
sync_events.evento_venta_creada(venta)   # dentro del with transaction.atomic()
```

Si la transacción hace rollback, el evento desaparece con ella — que es
exactamente la garantía que da el patrón outbox y la razón de que exista.

**Manejo del riesgo de serialización.** Serializar dentro de la transacción
implica que un fallo del serializador podría tumbar una venta. Inaceptable en un
POS. Solución de dos niveles dentro de `_crear_evento`:

1. Intentar serializar normalmente → fila completa con `payload` y `hash_payload`.
2. Si la serialización falla → escribir igual la fila con
   `payload=None` y `estado='PENDIENTE_SERIALIZAR'`, y loguear ERROR.
   El push reintenta la serialización desde la BD antes de enviar.

Así nunca se pierde el hecho de que algo ocurrió, y nunca se rompe una venta.

- Normalizar de paso `apps/inventario/views.py:36,42,43`, que hoy emiten **sin**
  `on_commit` mientras el resto del código sí lo usa.

### 1.3 `verificar_sync --backfill`

Encola los `EventoSync` faltantes que la Fase 0 detectó, re-serializando desde la
BD local.

**Seguro por construcción** (verificado en el código del cloud):
`recibir_eventos` deduplica por `hash_payload`, y `_handler_venta_creada` además
hace short-circuit si `numero_venta` ya existe. Un backfill de más no duplica nada.

Flags: `--backfill` (ejecuta), `--dry-run` (default), `--tipos=VENTA_CREADA,...`.

### 1.4 Fail loud

- Al arrancar (`server.py` y `apps/sync/apps.py`): si hay `CLOUD_API_TOKEN` pero
  `SYNC_ENABLED` es false → WARNING visible en log.
- Panel de sync del POS: mostrar el estado real de la configuración, no solo la
  cola. Una cola vacía con el sync apagado no es "todo bien".

**Criterios de aceptación.**

- Test: venta creada con `SYNC_ENABLED=false` → el `EventoSync` **existe** en
  estado PENDIENTE.
- Test: rollback de la transacción de venta → **no** queda evento huérfano.
- Test: serializador que lanza excepción → la venta se completa y queda fila en
  `PENDIENTE_SERIALIZAR`.
- Test: backfill dos veces seguidas → el cloud responde DUPLICADO, no crea nada.
- En el rig: borrar eventos a mano, correr `--backfill`, confirmar que el cloud
  queda con los mismos totales que el local.

**Riesgos.**

- Toca los 18 puntos de emisión → superficie amplia. Mitigación: la suite
  completa debe quedar verde y el rig debe reproducir un ciclo real.
- Cambia el comportamiento de instalaciones standalone (ahora acumulan filas).
  Mitigación: purga opcional y medición del crecimiento en el rig.

### Resultado Fase 1

**Implementada el 2026-08-19.** Alcance ampliado a BUG-C sobre la marcha (ver
`docs/BUGS.md`): durante el diseño se descubrió que las cuentas por cobrar nunca
replicaban por un problema de identidad de cliente — RD$240,435 invisibles en el
portal de Royal Plast. Un evento rechazado para siempre está tan perdido como uno
que nunca se escribió, así que ambas mitades se arreglaron juntas.

**Tests:** 343 en total (eran 323), **+20 nuevos**, todos verdes. Siguen las 2
fallas preexistentes y ajenas (time-bomb de `test_anulacion_pago` y el flake de
resolución de reloj en Windows). **Cero regresiones.**

- `apps/sync/tests/test_outbox_transaccional.py` — 9 tests.
- `apps/api/tests/test_sync_cliente_upsert.py` — 7 tests.
- `apps/sync/tests/test_verificar_sync.py` — 19 (migrado al registro compartido).

**Qué cambió (1A — BUG-A):**

- El gate de `SYNC_ENABLED` se movió de la emisión al envío. Una instalación sin
  cloud ahora acumula eventos inertes en vez de perderlos.
- Los 16 puntos de hechos de negocio emiten **dentro** de la transacción; los 4
  de snapshot se quedan en `on_commit`.
- Regla escrita: **hechos de negocio siempre; fotos de estado solo con cloud.**
  El snapshot es lo único que conserva el gate — recorre todo el catálogo
  calculando FIFO, y acumularlo sin cloud llenaría la BD local de JSON inútil.
- Estado `SIN_PAYLOAD` + `payload` nullable: un serializador roto ya no puede
  tumbar una venta, y el push re-serializa desde la BD.
- `apps/sync/registry.py`: mapa único tipo→modelo→serializador, consumido por el
  push, por `verificar_sync` y (más adelante) por la Fase 3.
- `verificar_sync` gana `--backfill`, `--reintentar-descartados` y
  `--purgar-confirmados`, todos dry-run salvo `--ejecutar`.

**Qué cambió (1B — BUG-C):**

- `Cliente` gana `origen_sucursal` + `origen_id_local` (migración `clientes/0003`).
- Los payloads de venta, CxC y cotización llevan bloque `cliente`.
- Resolutor único `_resolver_o_crear_cliente`: cédula → origen → crear.
- Se eliminó el fallback "buscar por nombre exacto" del resolutor de
  cotizaciones: fusionaba homónimos en silencio.

**Corregido de paso:** el orden de los eventos. `crear_cuenta_para_venta`
registraba su `on_commit` de `CXC_CREADA` antes que el de `VENTA_CREADA`, así que
la CxC se empujaba primero y el cloud la rechazaba por venta inexistente. Al
emitir en línea, `evento_venta_creada` va antes por construcción.

**Verificado en el rig** (`pos_fifo_demo_branch`, migrado): con
`SYNC_ENABLED=false`, una venta ahora **sí** encola su `VENTA_CREADA` en
`PENDIENTE` y el payload incluye el bloque `cliente`. Ese es exactamente el
escenario que costó 7 ventas en Royal Plast.

**Pendiente de esta fase — nada de esto es código:**

- [ ] Desplegar el cloud primero (`develop` → `main` → `workflow_dispatch` a prod
      + job `posfifo-prod-migrate` a mano, porque `PROD_RUN_MIGRATIONS_ON_DEPLOY=false`).
      **La migración de `clientes` corre sobre las 5 BDs de tenant.**
- [ ] Round-trip completo del rig contra `royalplastdemo`: sin el cloud
      desplegado sólo se puede probar la mitad local, porque prod corre `main`
      y todavía no tiene el resolutor nuevo.
- [ ] Desplegar el paquete local a Royal Plast y SK.
- [ ] Reparar: `verificar_sync --backfill --reintentar-descartados --ejecutar`
      en cada PC.

---

## Fase 2 — Cursor keyset estable (BUG-B)

**Objetivo.** Que un registro editado en el portal llegue siempre a la sucursal,
sin importar su nombre, su posición en la paginación ni si otro item falló.

**Por qué.** El cursor corta por `fecha_modificacion__gt` pero los endpoints
ordenan por `nombre` y paginan de 200 en 200; y el cursor avanza al máximo visto
aunque un item haya fallado. Ver BUG-B en `docs/BUGS.md`.

### 2.1 Orden total y estable en los endpoints de sync

En `SyncIncrementalMixin`, cuando viene `?desde=`:

```python
queryset = queryset.order_by('fecha_modificacion', 'id')
```

Ordenar por el mismo criterio que corta el cursor. El `id` como desempate da
**orden total** (dos registros con idéntico `fecha_modificacion` no pueden
intercambiarse entre páginas).

### 2.2 Paginación keyset en vez de offset

Cursor compuesto `(fecha_modificacion, id)`; cada página pide
`(fecha_modificacion, id) > (cursor_fecha, cursor_id)`.

Elimina el problema clásico de la paginación por offset: si algo se edita a mitad
del recorrido, con offset se salta o se repite un registro; con keyset no.

- `VersionMaestro` gana `ultimo_id` junto a `ultima_version`.
- Migración de cursores existentes: `ultimo_id = 0` (arranca inclusivo en la
  fecha actual del cursor; a lo sumo reprocesa unos registros, que es idempotente
  porque todo el pull es `update_or_create`).

### 2.3 Avance honesto del cursor

En `_pull_generic`: llevar el cursor **solo hasta el último item aplicado con
éxito en orden**. Si el item N falla, el cursor se queda en N-1 y el próximo pull
vuelve a intentarlo — en vez de saltárselo para siempre.

```
items:  [ok, ok, FALLA, ok, ok]
hoy:    cursor = fecha del último (salta el fallido para siempre)
target: cursor = fecha del 2º (reintenta desde el fallido)
```

Un item que falla repetidamente bloquea el avance: es lo correcto (falla ruidosa),
pero hay que hacerlo visible — contador de reintentos y WARNING con la referencia
del item atascado.

### 2.4 Nota sobre versión monótona

Lo canónico sería un `version bigint` de secuencia en vez de reloj de pared
(inmune a NTP y a saltos de reloj). Se difiere a propósito: exige columna,
migración y coordinación en 3+ modelos de ambos lados, y el keyset
`(fecha_modificacion, id)` cubre todos los modos de falla observados. Anotado
como endurecimiento futuro, no como deuda urgente.

**Criterios de aceptación.**

- Test de regresión con **>200 maestros** (fuerza paginación) donde se edita un
  registro alfabéticamente tardío → llega en el siguiente pull.
- Test con un item que falla al aplicarse → el cursor no lo sobrepasa y el
  siguiente pull lo reintenta.
- Test de dos registros con `fecha_modificacion` idéntica → ninguno se pierde.
- En el rig contra `royalplastdemo` (273 productos, supera el page size real):
  editar un producto en el portal y confirmarlo abajo.

**Riesgos.**

- Cambiar el orden de los endpoints afecta también al portal si algún consumidor
  asume orden alfabético. Mitigación: el reorden aplica **solo** cuando viene
  `?desde=`; sin cursor, el orden por `nombre` se mantiene.

### Resultado Fase 2

**Implementada el 2026-08-19.**

**Tests: 363 en total (eran 343), +20 nuevos, y la suite quedó VERDE COMPLETA
por primera vez** — cero fallas.

- `apps/sync/tests/test_pull_keyset.py` — 13 tests.
- `apps/api/tests/test_maestros_keyset.py` — 7 tests.

**Baseline estabilizado primero.** Las 3 fallas que arrastrábamos hacían
inservible el criterio de aceptación, y dos de ellas probaban justamente el
mecanismo `?desde=` que esta fase reescribe:

- `test_categoria_viewset` y `test_cliente_viewset` tomaban `antes = now()` y
  esperaban que un filtro `__gt` incluyera un registro modificado en el mismo
  tick de 15.6 ms del reloj de Windows. Ahora restan 100 ms.
- `test_anulacion_pago` tenía la fecha fija `2026-07-15`, ya vencida. Ahora es
  relativa.

**Qué cambió:**

- `SyncIncrementalMixin` ordena por `('fecha_modificacion', 'id')` **solo** con
  `?desde=`; hay un test que protege el orden alfabético del portal.
- Nuevo `?desde_id=` → el corte es sobre la tupla, no sobre la fecha sola.
- `_pull_generic` con doble cursor (`req` / `commit`) y marca de agua contigua.
- `VersionMaestro` gana `ultimo_id`, `bloqueado_desde`, `bloqueado_detalle`.
- Los 3 endpoints de sync no paginados ordenan por la tupla y exponen
  `cursor_id`, documentado como token de paginación y no identidad.
- Índice `(fecha_modificacion, id)` en los tres maestros.
- `verificar_sync` reporta el estado de los cursores y marca los bloqueados.

**Beneficio lateral no planificado:** pedir cada página por su clave en vez de
seguir el `next` de DRF hace que un corte de red a media paginación **conserve
lo aplicado** y retome donde iba. Antes se descartaba el avance y el ciclo
siguiente empezaba de cero. Hay test.

**Error propio que vale registrar:** al insertar el helper `_filtrar_keyset_sync`
quedó *debajo* de los decoradores `@api_view`, que pasaron a decorar el helper en
vez del endpoint. La suite lo atrapó con 6 fallas en los tests de sync. Corregido
moviendo el helper por encima del bloque de decoradores.

**Verificación end-to-end HECHA (2026-08-19).** Runbook para reproducirla:
`docs/runbooks/PRUEBAS_SYNC_LOCAL.md`.

- **Fase 2:** se editó el producto en posición alfabética **273 de 273** en el
  cloud y llegó a la sucursal en un pull incremental de 1 registro.
- **Fase 1:** cliente **sin cédula** + venta a crédito de RD$15,000 → en el cloud
  aparecieron el cliente (sellado con `origen_sucursal`/`origen_id_local`), la
  venta **con cliente enlazado** y la CxC con sus cuotas. Es exactamente lo que
  hoy falla en producción.
- **Idempotencia:** reenviar los mismos eventos no duplicó nada.
- De paso se confirmó que el rollback de una venta se lleva su evento, y que el
  WARNING de fail-loud salta con credenciales cloud y `SYNC_ENABLED=False`.

**Dos bugs que encontró la prueba real y no los tests:**

1. **Pull inicial aplicaba 416 items sobre 273.** El cliente solo mandaba `desde`
   cuando ya tenía cursor, así que en el primer pull el servidor ordenaba por
   `nombre` y la clave del último item no era frontera de nada. Los 13 tests de
   la fase no lo vieron porque **mockeaban la respuesta del servidor**. Corregido
   enviando el epoch; cubierto por `RecorridoRealDelClienteTests`, que recorre el
   endpoint de verdad.
2. **La suposición de compatibilidad era falsa.** Contra un cloud viejo real
   llegaron **245 de 273 productos: 28 perdidos**. Ver BUG-B en `docs/BUGS.md`.
   Mitigado con detección + degradado a paginación legacy.

**Pendiente de esta fase:**

- [ ] Despliegue conjunto con la Fase 1 (una sola visita a cada cliente).
      **El cloud va primero**: hasta que tenga el keyset, la sucursal corre en
      modo degradado (correcto, pero sin las garantías nuevas).

**La ventana de despliegue es segura (verificado 2026-08-19).** Entre desplegar
el cloud y actualizar cada sucursal pasan días, y en ese periodo el cloud recibe
payloads del formato anterior. Se comprobó que:

- Ventas y cuentas **siguen replicando**: desplegar el cloud no corta el sync.
- Una CxC nacida en la ventana queda a nombre del genérico CLIENTE CONTADO
  (mejor que perderla), y **el reenvío posterior corrige el titular**.

Lo segundo hubo que implementarlo: el handler saltaba la cuenta por existir y el
titular equivocado quedaba para siempre. Ahora el reenvío es **correctivo** —
rellena lo que falta, nunca pisa datos buenos — tanto para el cliente de una
venta como para el titular de una CxC. Tests en
`apps/api/tests/test_ventana_despliegue.py`.

---

## Fase 3 — Anti-entropía (la capa que falta)

**Objetivo.** Que el sistema compare periódicamente los dos lados y avise cuando
divergen, sin depender de que el canal de eventos se haya portado bien.

**Por qué.** Las 7 ventas perdidas de Royal Plast estuvieron ausentes **dos
meses** y se encontraron por casualidad. Cada evento individual se confirma, pero
nadie pregunta nunca "¿tenemos los mismos datos?". Esta es la brecha más grande
del sistema, más que el transporte.

### 3.1 Endpoint de resumen en el cloud

`GET /api/v1/sync/resumen/?desde=YYYY-MM-DD&hasta=YYYY-MM-DD`
(token de sucursal, scopeado al tenant y su sucursal).

Devuelve, por día y por entidad, un **checksum barato**:

```json
{
  "ventas": [
    {"dia": "2026-08-18", "count": 10, "suma": "15400.00", "max_ref": "V-20260818-0010"}
  ],
  "cierres_caja": [...],
  "aperturas_caja": [...]
}
```

`count` + `suma` + `max_ref` detecta faltantes, sobrantes y montos divergentes sin
transferir un solo registro completo. Barato de calcular en ambos lados.

### 3.2 Comando local `conciliar`

`python manage.py conciliar --dias=30 [--json] [--backfill]`

Calcula el mismo resumen sobre la BD local, lo compara con el del cloud y reporta
las diferencias por día y entidad. Con `--backfill`, reutiliza la maquinaria de
la Fase 1 para encolar lo que falte arriba.

### 3.3 Integración operativa

- El daemon `sincronizar` corre la conciliación **una vez al día** (ventana
  configurable) además de sus ciclos normales, y la deja en `sync_logsync`.
- Divergencia detectada → WARNING en log + bandera visible en el panel de sync
  del POS.
- El portal cloud muestra por sucursal: último heartbeat, último evento y
  resultado de la última conciliación. Es lo que convierte al portal en el
  "ente vivo" que se quiere: primero que reporte la verdad, después que escriba.

**Criterios de aceptación.**

- Borrar 3 ventas del cloud en el rig → la conciliación las reporta al día
  siguiente (o al correrla a mano) con el día exacto.
- `--backfill` las restituye y una segunda corrida da diferencia cero.
- Costo: la conciliación de 90 días no debe tardar más de unos segundos ni
  transferir más de unos pocos KB.

**Riesgos.**

- Falsos positivos por zona horaria: el cloud agrupa en UTC y el POS en
  America/Santo_Domingo. **Definir explícitamente** que ambos lados agrupan por
  fecha local de la sucursal. Este es el error más probable de toda la fase
  (ya nos mordió antes: ver los dos bugs de timezone resueltos en `BUGS.md`).

### Resultado Fase 3

> _(llenar: tiempo de la conciliación, diferencias reales encontradas en RP y SK
> al correrla por primera vez contra producción)_

---

## Fase 4 — Configuración dinámica del servicio (dotenv)

**Objetivo.** Que editar la configuración de una sucursal no requiera volver a
registrar el servicio, y que los valores con espacios, `&` o paréntesis dejen de
romperse.

### El diagnóstico del intento anterior

El intento previo fue pedirle a `nssm` que leyera las variables del entorno de
forma dinámica. **No era ambición mal puesta: es algo que nssm estructuralmente no
hace.** `nssm` guarda un snapshot estático en `AppEnvironmentExtra` en el momento
del registro; no tiene noción de "leer un archivo al arrancar". Cualquier `.bat`
que intente lograrlo termina siendo un generador de listas de variables — que es
justo lo que se quería evitar.

**El enfoque correcto es mover la responsabilidad del gestor de servicios a la
aplicación.** No es nssm quien debe leer la configuración: es Django.

### 4.1 `.env` leído por la aplicación

- Añadir `python-dotenv` a `requirements.txt` (hoy **no está instalado**).
- Cargar el `.env` al inicio de `config/settings.py` (cubre `server.py`,
  `manage.py`, el daemon de sync y cualquier comando, todos con la misma config).
- `nssm` registra **solo** `DJANGO_SETTINGS_MODULE` y la ruta del `.env`.
  Nunca más una lista de variables.
- Editar el `.env` + reiniciar el servicio = configuración nueva aplicada.
  Sin re-registrar, sin tocar ningún `.bat`.

### 4.2 Lo que esto resuelve de arrastre

- **Espacios en valores** (`THERMAL_PRINTER_NAME=2connect pos`): dotenv los
  parsea nativamente; hoy dependen de comillas que `cmd` maltrata.
- **Bug #9** (`DJANGO_SECRET_KEY` truncado por un `&` sin comillas): desaparece,
  porque el valor deja de pasar por el intérprete de `cmd`.
- **Bug #4** (`PRINTER_TERMICA` vs `THERMAL_PRINTER_NAME`): con una sola fuente
  de verdad, el mapeo de traducción en el `.bat` sobra.

### 4.3 Migración

- `deploy/env_cliente.bat.template` → `deploy/env_cliente.env.template`.
- Script de conversión de un `env_cliente.bat` existente a `.env` (los clientes
  actuales no deben re-escribir su configuración a mano).
- `registrar_servicio.bat` se simplifica mucho: deja de armar el bloque de
  variables.
- Validación al arrancar: si falta una variable crítica, el arranque falla
  **ruidoso** con el nombre de la que falta, en vez de arrancar a medias.

**Criterios de aceptación.**

- En el rig: cambiar `SYNC_INTERVAL` en el `.env`, reiniciar el servicio y ver el
  cambio aplicado sin tocar nssm.
- Un valor con espacios, uno con `&` y uno con paréntesis llegan íntegros a
  `settings`.
- El servicio arranca y sirve tráfico registrado solo con las dos variables.

**Riesgos.**

- Toca el camino de instalación de **clientes en producción**. Aplicar primero en
  el rig, después en SK (que ya tiene el problema y menos volumen), y por último
  en Royal Plast, con la ventana de rotación de `SECRET_KEY` (#9) de una vez —
  ambas cosas invalidan sesiones, conviene un solo corte.

### Resultado Fase 4

> _(llenar)_

---

## Deuda operativa diferida (documentada para hacer luego)

No entra en las fases de arriba. Registrada aquí para que no se pierda.

### D1 — SK Performance corre un paquete viejo

La instalación de SK (`pos_autorepuestos`) tiene pendientes heredados del
onboarding del 2026-06-23:

- **Bug #3 vivo en el cliente:** sus logs muestran
  `Auditoria() got unexpected keyword arguments: 'detalles'` en **cada
  impresión**. El repo ya está corregido; falta re-empaquetar e instalar.
  Consecuencia hoy: el historial de impresión de SK no registra nada.
- **Tickets duplicados:** los logs muestran cada venta imprimiéndose dos veces.
  El commit `528507e` (cantidad de copias configurable) ataca justo eso y
  tampoco está desplegado allí.
- **Registro del servicio con nssm inestable** — se resuelve con la Fase 4.
- Confirmar si su instalación quedó con `SYNC_ENABLED` persistente tras los
  reintentos de registro (aunque su numeración de ventas en cloud no muestra
  huecos, lo que sugiere que sí).

Requiere: re-empaquetar desde el repo actual y correr `deploy/actualizar.bat`
en sitio. Coordinar con la Fase 4 para no hacer dos visitas.

### D2 — ~~BD huérfana~~ — DESCARTADO (2026-08-20)

Se registró aquí que `tnt_staging_royalplast` no tenía fila en `tenancy_tenants`
y por tanto era un residuo a borrar. **Era un error de diagnóstico:** solo se
consultó el control plane de *prod* (`pos_fifo_prod`).

La BD pertenece al control plane de **staging** (`pos_fifo_staging`), donde está
registrada como el tenant `staging_royalplast` y activa. Es el banco de pruebas
de staging y **no hay que borrarla**.

Lección: con DB-per-tenant, "huérfana" solo se puede afirmar tras revisar
**todos** los control planes, no el de producción.

### D3 — Aislamiento del servidor de base de datos

Un único Flexible Server `posfifoplatformpg` (B1ms Burstable, HA deshabilitada,
backup 7 días sin geo-redundancia) aloja **todo**: control plane de producción,
las 5 BDs de tenant, `pos_fifo_dev` y `pos_fifo_staging`. Con 135 MB de 32 GB no
hay presión de recursos, pero el radio de daño de un error en dev es total.

Decisión pendiente (es de costo, no técnica): separar dev/staging a su propio
servidor, subir la retención de backup, o aceptar el riesgo explícitamente
mientras la escala sea esta.

---

## Lo que este roadmap deliberadamente NO hace

- **No cambia el stack.** Nada de CDC/Debezium, CRDTs ni frameworks local-first.
  Los bugs encontrados son de corrección, no de escala (778 ventas en dos meses,
  135 MB en total); ninguna de esas tecnologías los habría prevenido, porque
  todas asumen que el evento se escribió.
- **No implementa sync bidireccional todavía.** Escribir maestros desde la
  sucursal hacia el cloud se apoya sobre las fases 1-3: sin outbox confiable,
  cursor estable y conciliación, la bidireccionalidad multiplica los modos de
  divergencia sin manera de detectarlos. El modelo objetivo cuando toque es
  **autoridad única por agregado** (transacciones: la sucursal manda; maestros:
  el cloud manda; inventario: cada sucursal dueña de su ledger, el cloud agrega)
  con **promoción de registros provisionales** para el caso "el cajero creó un
  cliente", no multi-master con resolución de conflictos.

---

## Fase 5 — Actualización remota del POS local (futura)

**Objetivo.** Dejar de viajar a cada cliente para actualizar.

**Lo que ya existe:**

- El canal: el daemon de sync llama al cloud en cada ciclo.
- El mecanismo: `deploy/actualizar.bat` hace el update completo y **respalda la
  BD antes de migrar**.
- El diagnóstico: `verificar_instalacion` y `verificar_sync` dicen si quedó bien.

**Lo que faltaría.** El cloud publica versión + paquete; el local compara con la
suya, descarga, verifica integridad, aplica y reporta el resultado.

**Lo delicado no es el mecanismo, es el fallo.** Sin nadie en sitio, una
actualización mala deja al cliente sin poder vender. Mínimo exigible:

- Health-check después de aplicar, con **rollback automático** al backup.
- Despliegue escalonado: SK primero, Royal Plast solo tras confirmar.
- Interruptor de cancelación desde el portal.
- Firma del paquete: el local no debe ejecutar lo que le mande cualquiera.

**Precondición: la Fase 4.** Automatizar actualizaciones sobre el registro frágil
de `nssm` multiplica el riesgo — y era justo la parte que fallaba en SK. Con el
`.env`, actualizar deja de tocar la configuración del servicio.

**Primera versión sugerida: semi-automática.** El cloud avisa que hay
actualización y la deja lista; una persona confirma. Ahorra el viaje sin apostar
la operación del cliente a que todo salga bien solo.

---

## Bitácora de despliegue

### 2026-08-20 — dev y staging desplegados y validados

**dev** (`posfifo-dev-api`, revisión `0000031`, imagen `44cf6fa`): CI automático
desde `develop`. Verde.

**staging** (`posfifo-staging-api`, revisión `0000010`, imagen `e1cd524`):

- Migraciones: job `posfifo-staging-migrate` a mano (no corre solo). Control
  plane `pos_fifo_staging` y su tenant `tnt_staging_royalplast` pasaron de **81 a
  89 migraciones** — las 8 nuevas.
- **Radio de daño verificado ANTES de lanzarlo:** el control plane de staging
  registra un solo tenant (`staging_royalplast`), así que el job no podía tocar
  los de producción. Confirmado después: `tnt_royalplast` y `tnt_skperformance`
  siguen sin `sync.0007`.

**Validación end-to-end contra staging desplegado**, con código nuevo en ambos
lados:

| Prueba | Resultado |
|---|---|
| Heartbeat (Fase 1) | ✅ responde |
| Pull con cursor keyset (Fase 2) | ✅ **sin degradar** — el servidor honra el contrato |
| Cliente sin cédula + venta a crédito (Fase 1 / BUG-C) | ✅ cliente creado con `origen_sucursal=1`, `origen_id_local=5`; venta con `cliente_id` **no nulo**; CxC de RD$8,500 con titular correcto y su cuota |

Que el fallback de compatibilidad **no** se disparara es la prueba de que la
Fase 2 está viva del lado servidor: contra el prod viejo sí se dispara.

Datos de prueba limpiados de staging y del rig.

**Error que atrapó CI y no la suite local:** `requirements_ci.txt` hereda de
`requirements_cloud.txt`, donde faltaba `python-dotenv` — solo se había agregado
a `requirements.txt`. Cinco tests fallaron con `ModuleNotFoundError`. Mismo
patrón que el bug #6. Los requirements están repartidos en 4 archivos.

**Pendiente:** promover a `main` + `workflow_dispatch` a prod + job
`posfifo-prod-migrate`. Ese job sí toca las 5 BDs de tenant, incluidas las de los
dos clientes reales.
