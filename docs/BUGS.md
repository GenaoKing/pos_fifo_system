# Bugs y hallazgos

## Resueltos

### Dashboard de reportes mostraba KPIs de hoy en cero durante la noche

- Fecha de hallazgo: 2026-05-16 23:10 America/Santo_Domingo.
- Sintoma: `Ultimas Ventas` mostraba ventas recientes, pero `Ventas Hoy`, `Efectivo`, `Transferencia` y `Cajeros Hoy` salian en cero.
- Causa raiz: `apps/reportes/views.py` usaba `timezone.now().date()`, que toma la fecha UTC. En Santo Domingo, a las 11:00 p. m. locales ya era el dia siguiente en UTC, asi que el dashboard consultaba 2026-05-17 aunque las ventas pertenecian al 2026-05-16 local.
- Correccion: cambiar calculos de "hoy" a `timezone.localdate()` y el reloj del servidor a `timezone.localtime()`.
- Evidencia local: a `2026-05-17 03:13 UTC`, la fecha local correcta era `2026-05-16`; habia 3 ventas completadas por `$49,200.00`, con `$15,800.00` en efectivo y `$33,400.00` en transferencia.

### Portal cloud: header del dashboard mostraba el dia anterior

- Fecha de hallazgo: 2026-06-12.
- Sintoma: el encabezado de `/dashboard` en el portal (`pos-cloud-dashboard`) mostraba la fecha del dia anterior ("jueves, 11 de junio" siendo viernes 12).
- Causa raiz: contraparte frontend del bug de timezone de arriba. `/api/v1/reportes/ventas-hoy/` devuelve `fecha` como date-only (`YYYY-MM-DD`) y `formatDateLong` en `src/lib/format.ts` la parseaba con `new Date(...)`, que interpreta date-only como medianoche UTC; en Santo Domingo (UTC-4) retrocede un dia. `formatDate` ya manejaba el caso pero `formatDateLong` no.
- Correccion: helper compartido `parseLocalDate` en `src/lib/format.ts` que construye fechas date-only en hora local; usado por `formatDate` y `formatDateLong`. Tests en `src/lib/format.test.ts`.

### Referencias antiguas a `Venta.cajero` tras refactor a `Venta.usuario`

- Sintoma: algunos reportes/API seguian usando `cajero`/`cajero_id`, aunque el modelo `Venta` ya no tiene ese campo.
- Impacto: la API de metricas para cajeras podia fallar con `FieldError`; el filtro por cajero en reportes on-demand no aplicaba contra el campo real; el template admin intentaba leer `venta.cajero`.
- Correccion: usar `usuario`, `usuario_id` y `venta.usuario` en reportes.

## Pendientes

### BUG-A — Perdida SILENCIOSA de eventos de sync cuando el servicio del POS no tiene `SYNC_ENABLED`

- Fecha de hallazgo: 2026-08-19.
- **Estado: CORREGIDO en codigo (2026-08-19), pendiente de desplegar.**
  El gate se movio de la emision al envio y el evento ahora se escribe dentro de
  la transaccion de negocio. Ver Fase 1 de `docs/ROADMAP_SYNC_CONFIABLE.md`.
  Falta desplegar a los clientes y correr `verificar_sync --backfill` para
  recuperar lo ya perdido.
- Severidad: **alta** (perdida de datos irrecuperable por el mecanismo actual).
- Sintoma: ventas registradas en la sucursal que NUNCA llegan al cloud, sin que
  nada falle a la vista: `sync_status` reporta 0 pendientes, el daemon
  `POSFifoSync` corre sano y no hay errores en pantalla.

**Causa raiz.** La emision de eventos esta gateada por `SYNC_ENABLED`:

```python
# apps/sync/events.py:40
def _crear_evento(tipo, payload, ...):
    if not getattr(settings, 'SYNC_ENABLED', False):
        return None
```

Quien CREA los eventos es el servicio web del POS (`POSFifoSystem`), no el daemon
de sync (`POSFifoSync`). Si el servicio web arranca sin `SYNC_ENABLED=true` en su
entorno, cada venta se guarda local pero **no se encola ningun `EventoSync`**. Como
no existe la fila, no hay pendiente que reintentar ni backlog que inspeccionar: la
venta simplemente no existe para el cloud, para siempre.

Esto es exactamente el modo de falla del registro de variables de entorno con
`nssm` (las variables se pasan por `AppEnvironmentExtra` al registrar el servicio;
si el registro se rehace o falla, el servicio queda sin ellas).

**Descartado tras revisar el modelo (2026-08-19):** se sospecho una segunda via
por `SUCURSAL_CODIGO` ausente, pero `EventoSync.sucursal` es `null=True` y
`push_eventos()` no filtra por sucursal (el cloud la resuelve desde el token), asi
que un evento con `sucursal=None` se encola y se envia igual. No hay perdida por
ese camino; solo se pierde trazabilidad local. **La unica via de perdida
silenciosa confirmada es el gate de `SYNC_ENABLED`.**

**Evidencia en produccion (2026-08-19).** Huecos en la numeracion diaria de
ventas del cloud (`V-YYYYMMDD-NNNN` es correlativo por dia):

| Tenant | Dia | Presentes | Max | Faltan |
|---|---|---|---|---|
| `royalplast` | 2026-06-20 | 13 | 14 | `V-20260620-0011` (dia del cutover, explicable por el dump) |
| `royalplast` | 2026-06-23 | 3 | 10 | `V-20260623-0001` .. `0007` |
| `skperformance` | — | — | — | sin huecos |

El patron del 2026-06-23 (faltan las 7 primeras del dia y aparecen desde la #8)
encaja con "el servicio arranco sin la variable y a media manana se corrigio".
Fuera de esos dias el sync de ambos clientes esta al dia (eventos CONFIRMADO
hasta hoy; 0 pendientes, 0 fallidos).

**Por que no se puede reparar hoy.** `reconciliar_cloud` solo sube **maestros**
(categorias, productos, clientes). No existe ninguna ruta para reenviar ventas
historicas: el push solo mira `EventoSync` pendientes, y para estas ventas la
fila nunca se creo.

**Correccion propuesta.**

1. **Comando `verificar_sync`** (corre en la sucursal): reporta ventas /
   cierres / aperturas sin `EventoSync` asociado (`objeto_id_local`), y avisa si
   `SYNC_ENABLED` esta apagado mientras hay `CLOUD_API_TOKEN` configurado
   (= sintoma de entorno mal registrado).
2. **`verificar_sync --backfill`**: encola los `EventoSync` faltantes
   re-serializando los objetos. Es **seguro**: el cloud deduplica por
   `hash_payload` y `_handler_venta_creada` ademas hace short-circuit si
   `numero_venta` ya existe.
3. **Fail loud en vez de silencioso**: si `CLOUD_API_TOKEN`/`CLOUD_API_URL` estan
   configurados pero `SYNC_ENABLED` es false, loguear WARNING al arrancar y
   mostrarlo en el panel de sync del POS.
4. **Endurecer el registro del servicio** (`deploy/registrar_servicio.bat`):
   validar que las variables criticas quedaron efectivamente en
   `AppEnvironmentExtra` despues del registro y abortar si no.

---

### BUG-B — Cursor de pull incremental puede saltarse registros (high-water-mark)

- Fecha de hallazgo: documentado en `docs/runbooks/SYNC_EMULACION_SUCURSAL_PROD.md` §5;
  confirmado en codigo 2026-08-19.
- Severidad: media (perdida de ACTUALIZACIONES de maestros, no de transacciones).
- Sintoma: un producto/cliente editado en el portal no se refleja en la sucursal
  aunque el pull corra "sin errores".

**Causa raiz — tres piezas que no encajan:**

1. El cursor filtra `fecha_modificacion__gt` (`apps/api/views/maestros.py:83`).
2. Los endpoints de maestros **ordenan por `nombre`** (`Meta.ordering = ['nombre']`
   en `Producto`, `Categoria`, `Cliente`) y paginan de 200 en 200 (`LargePagination`).
3. `_pull_generic` (`apps/sync/engine.py:304`) avanza el cursor al **maximo
   `fecha_modificacion` visto**, y lo guarda si `count > 0`.

Consecuencias:

- **Item fallido = perdida permanente.** Si `apply_func(item)` lanza excepcion, el
  `except` la loguea y sigue; el cursor igual avanza con la fecha de los otros
  items del lote. Ese registro nunca vuelve a entrar en un pull incremental.
- **Paginacion inestable.** Ordenar por `nombre` mientras el criterio de corte es
  temporal significa que los registros no llegan en orden de cursor; ademas, sin
  `ORDER BY` unico, PostgreSQL no garantiza consistencia entre paginas (un
  registro puede repetirse o saltarse si algo se edita a mitad de paginacion).

**Correccion propuesta.**

1. Ordenar los endpoints de sync por `('fecha_modificacion', 'id')` cuando viene
   `?desde=` (orden total y estable, alineado con el cursor).
2. Avanzar el cursor **solo hasta el ultimo registro aplicado con exito**: si un
   item falla, cortar el avance en la fecha del anterior (o registrar el fallo en
   una cola de reintento) en vez de saltarlo.
3. No guardar el cursor en pulls incompletos (hoy un corte de red retorna antes
   del `save()`, lo cual esta bien; mantener esa invariante al refactorizar).
4. Test de regresion con >200 maestros (fuerza paginacion) + un item que falla.

---

### BUG-C — Las cuentas por cobrar NUNCA replican al cloud (clave natural de cliente inexistente)

- Fecha de hallazgo: 2026-08-19.
- Severidad: **alta** (dinero real invisible en el portal, y creciendo).
- **Estado: CORREGIDO en codigo (2026-08-19), pendiente de desplegar.**
- Sintoma: el dueno mira el portal y no ve ninguna cuenta por cobrar, aunque en
  la sucursal si existen ventas a credito con su cartera.

**Evidencia en produccion.**

| Tenant | Ventas a credito replicadas | `CuentaPorCobrar` en cloud | Monto sin reflejar |
|---|---|---|---|
| `royalplast` | 16 (2026-06-22 a 2026-08-11) | **0** | **RD$240,435.00** |
| `skperformance` | 3 | **0** | RD$3,200.00 |

Ademas, **404 de 405** ventas de contado post-cutover quedaron con
`cliente_id = NULL` en el cloud. La unica venta que si enlazo su cliente es la
del unico cliente que tiene la cedula cargada.

**Causa raiz.** Los handlers resolvian al cliente **solo** por `cedula_rnc`:

```python
if payload.get('cliente_cedula_rnc'):
    cliente = Cliente.objects.filter(cedula_rnc=payload['cliente_cedula_rnc']).first()
```

Pero `Cliente.cedula_rnc` es `blank=True, null=True` — opcional por diseno,
porque el cliente de mostrador no siempre la da. Sin cedula no habia forma de
identificarlo, asi que:

1. La venta replicaba **sin cliente**.
2. `_handler_cxc_creada` lanzaba `ValueError('Cliente de CxC ... no existe en
   cloud')`, fallaba **identico en cada reintento**, agotaba `SYNC_MAX_RETRIES`
   y el evento terminaba en `DESCARTADO`. Perdida permanente.

En una frase: **la clave natural elegida para clientes no existe en los datos
reales del negocio.**

**Por que no lo cubria BUG-A.** Aqui el evento SI se creo y SI se envio. Se
rechazo al aplicarse. Un evento rechazado para siempre esta tan perdido como uno
que nunca se escribio — por eso ambos se arreglaron en la misma fase.

**Correccion aplicada.**

1. `Cliente` gana `origen_sucursal` + `origen_id_local`: identidad estable que no
   depende de datos que el negocio puede omitir.
2. Los payloads de venta, CxC y cotizacion llevan un bloque `cliente` con esa
   identidad y los datos necesarios para crearlo.
3. Resolutor unico `_resolver_o_crear_cliente` en `apps/api/views/sync.py`:
   cedula -> origen -> crear. `_handler_cxc_creada` ya no lanza.
4. Se elimino el fallback "buscar por nombre exacto" que tenia el resolutor de
   cotizaciones: fusionaba homonimos en silencio, y sobre una cotizacion que
   puede volverse venta a credito eso corrompe cartera.

**Reparacion pendiente** (requiere acceso a las PCs de los clientes): desplegar
el cloud primero, luego el paquete local, y correr
`verificar_sync --reintentar-descartados --ejecutar`. Los eventos atascados se
re-envian ya con el bloque `cliente`.

**Deuda relacionada.** Esto revisa la decision **B11b** de
`docs/ROADMAP_PORTAL.md`: un cliente puede nacer en la sucursal y promoverse al
cloud. El cloud sigue siendo la autoridad para EDITAR maestros; aqui solo se crea
lo que no existe.
