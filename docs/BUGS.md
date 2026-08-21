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
- **Estado: CORREGIDO en codigo (2026-08-19), pendiente de desplegar.**
  Cursor keyset `(fecha_modificacion, id)` + marca de agua contigua. Ver Fase 2
  de `docs/ROADMAP_SYNC_CONFIABLE.md`.
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

**Correccion aplicada (2026-08-19).**

1. `SyncIncrementalMixin` ordena por `('fecha_modificacion', 'id')` **solo**
   cuando viene `?desde=`; sin cursor el portal conserva su orden alfabetico.
2. Nuevo `?desde_id=`: el corte pasa a ser sobre la tupla, no sobre la fecha
   sola. Con eso el empate de timestamps deja de perder registros.
3. `_pull_generic` lleva **dos** cursores: `req` (ultimo recibido, para pedir la
   pagina siguiente) y `commit` (ultimo aplicado con exito en secuencia
   contigua, lo unico que se persiste). Un item que falla congela la marca de
   agua en vez de saltarse; los items posteriores igual se aplican, porque son
   idempotentes.
4. El bloqueo es visible: `VersionMaestro.bloqueado_desde` / `bloqueado_detalle`,
   y `verificar_sync` lo reporta.
5. Los endpoints de sync no paginados (`roles`, `asignaciones`,
   `metodos-credito`) tambien ordenan por la tupla y exponen `cursor_id`
   — **token de paginacion, NO identidad**: la identidad cross-BD sigue siendo
   la clave natural de cada recurso.
6. Indice `(fecha_modificacion, id)` en `Producto`, `Categoria` y `Cliente`.

**Compatibilidad (CORREGIDO tras medirlo, 2026-08-19).** La primera version de
esta nota decia que el orden de despliegue no importaba. **Es falso**, y se
comprobo:

| Escenario | Aplicados | Productos distintos que llegaron |
|---|---|---|
| Cliente nuevo -> cloud VIEJO | 432 | **245 de 273 (28 perdidos)** |
| Cliente nuevo -> cloud NUEVO | 273 | 273 de 273 |

Un cloud anterior ordena por `nombre` e ignora `desde_id`, asi que el paseo
keyset del cliente es invalido: la clave del ultimo item de la pagina no es
frontera de nada y las paginas se solapan (y por tanto tambien dejan huecos).

**Mitigado en el cliente**, para que el orden de despliegue no pueda causar
perdida: `_pull_generic` verifica que la pagina venga ordenada por
`(fecha_modificacion, id)`; si no, loguea WARNING y degrada al recorrido legacy
(seguir `next`), que recorre el catalogo completo. Verificado: con el fallback,
contra el cloud viejo llegan 273 de 273.

Cliente viejo contra cloud nuevo siempre fue seguro: sigue `next` sobre un orden
total, que es correcto.

**Aun asi, desplegar el cloud primero es lo recomendable**: hasta que el cloud
tenga el keyset, la sucursal corre en modo degradado y no obtiene las garantias
de esta fase.

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

---

### BUG-D — La instalacion documentada deja el POS SIN IMPRIMIR (modulos vendibles apagados)

- Fecha de hallazgo: 2026-08-19, reproducido en una instalacion limpia.
- Severidad: **alta** (una funcion central del POS queda inutilizada, sin error).
- **Estado: causa raiz identificada; detectado por `verificar_instalacion`.
  La conducta del motor de suscripciones NO se cambio (decision explicita).**
- Sintoma: el POS deja de imprimir tickets. Tambien desaparecen cotizaciones,
  etiquetas Zebra, financiacion y e-CF. No hay error en pantalla ni en los logs
  de la aplicacion; el gate simplemente devuelve False.

**Cadena completa.**

```
utils/impresoras/manager.py:36  _is_printing_enabled()
  -> configuracion/utils.modulo_activo('impresion_termica')
      -> la sucursal NO tiene negocio  -> fail-OPEN -> lee el flag legacy -> imprime
      -> la sucursal SI tiene negocio  -> manda la suscripcion del negocio
            -> suscripciones/engine._resolver_negocio(negocio)
                  sin plan y sin filas NegocioModulo -> solo modulos core
                  -> `impresion_termica` NO es core -> False -> NO IMPRIME
```

**Causa raiz concreta.** `bootstrap_suscripciones` deriva los modulos con
`seed.derivar_modulos_de_flags`, que consulta:

```python
ConfiguracionModel.objects.filter(sucursal__negocio=negocio)
```

O sea, las `ConfiguracionNegocio` **ligadas a una sucursal del negocio**. Pero
`deploy/instalar.bat:327` llama `crear_config_inicial` **sin `--sucursal`**, asi
que la config queda con `sucursal=None`. La consulta no encuentra nada, no hay
flags de donde derivar, y el negocio se queda sin ningun modulo vendible.

**Firma para reconocerlo:** la tabla `negocio_modulos` contiene **solo
`cuentas_por_cobrar`** -- el unico que `derivar_modulos_de_flags` agrega
incondicionalmente. Verificado asi en el dump de Royal Plast (`royal_eval`).

**Reproducido** en una instalacion limpia siguiendo el procedimiento: 9 modulos
vendibles apagados, `impresion_termica` entre ellos. Correr
`bootstrap_suscripciones` **no lo arregla** (solo suma `cuentas_por_cobrar`).
Ligar la config a la sucursal y re-ejecutarlo si lo arregla: quedan todos activos
menos `ecf`, que esta apagado a proposito.

**Por que no exploto antes.** `instalar.bat` no llama `bootstrap_negocio`, asi
que una instalacion nueva queda sin negocio -> fail-open -> imprime. La trampa se
arma en `deploy/actualizar.bat`, que si engancha la sucursal al negocio. Encaja
con el sintoma de impresion que se vio en SK Performance tras una actualizacion.

**Arreglo del procedimiento (aplicado).** `crear_config_inicial` acepta
`--sucursal`; el runbook `docs/runbooks/INSTALACION_CLIENTE_NUEVO.md` ya lo usa,
y `actualizar.bat` ahora corre `verificar_instalacion` al terminar, que lo
reporta en rojo con la causa y el arreglo.

**Deuda pendiente (no se toco a proposito).** La asimetria de fondo sigue ahi:
`modulo_activo` con `negocio=None` falla ABIERTO, pero un negocio a medio
aprovisionar falla CERRADO, y los dos estados son indistinguibles desde afuera.
Lo canonico seria que un negocio **sin aprovisionar** (sin suscripcion y sin
`NegocioModulo`) tambien falle abierto, como el resto del sistema. Queda anotado
en `docs/ARQUITECTURA_MODULOS.md`.

---

### BUG-E — `/api/v1/auth/refresh/` devuelve 500 en vez de 401 cuando el usuario no resuelve

- Fecha de hallazgo: 2026-08-21 (revisando logs de prod tras un despliegue).
- Severidad: **baja** (raro: 2 veces en 30 dias), pero rompe la sesion del portal.
- **Preexistente**, NO es regresion del despliegue del 2026-08-20: el mismo error
  ocurrio el 2026-08-06 sobre la imagen anterior, y ese despliegue no toco
  `apps/api/views/auth.py`, `apps/tenancy/` ni `apps/usuarios/`.

**Sintoma:** el usuario esta trabajando en el portal, su token vence, el frontend
llama a `/auth/refresh/` y recibe un **500**. La sesion se cae sin explicacion.

**Causa raiz.** `rest_framework_simplejwt/serializers.py:116` resuelve el usuario
del refresh token con `get_user_model().objects.get(...)`. Si no lo encuentra,
`Usuario.DoesNotExist` sube sin capturar y DRF lo convierte en 500:

```
File "rest_framework_simplejwt/serializers.py", line 116, in validate
    user := get_user_model().objects.get(
apps.usuarios.models.Usuario.DoesNotExist: Usuario matching query does not exist.
```

**Por que no encuentra al usuario.** Bajo DB-per-tenant, `usuarios` es una
DUAL_HOME_APP: resuelve al tenant activo o a `default`. Si el refresh llega **sin
contexto de tenant**, la consulta va al control plane, donde el usuario del
tenant no existe. Un token emitido dentro de un tenant no se puede validar fuera
de el.

**Correccion sugerida (no aplicada).** Envolver la resolucion en el endpoint de
refresh y devolver **401** cuando el usuario no resuelva -- que es lo que el
frontend espera para mandar al login-- en vez de un 500. Y revisar por que el
refresh pierde el contexto de tenant: si el token lleva `tenant_id`, deberia
establecerse antes de resolver el usuario.

**Relacionado.** Misma familia que los 500 de `/login/` y `/reportes/`: el cloud
sirve rutas que bajo tenancy fallan ruidoso sin contexto. Endurecimiento
pendiente: esas rutas de template del POS local no deberian ser alcanzables en
el cloud.
