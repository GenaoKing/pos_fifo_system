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
- **Estado: CORREGIDO y DESPLEGADO.** En prod desde el 2026-08-22 (cloud +
  Royal Plast en sitio). El gate se movio de la emision al envio y el evento
  ahora se escribe dentro de la transaccion de negocio (Fase 1 de
  `docs/ROADMAP_SYNC_CONFIABLE.md`). Reparado con `verificar_sync --backfill
  --reintentar-descartados --ejecutar` en Royal Plast; SK Performance queda
  pendiente de su visita.
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
- **Estado: CORREGIDO y DESPLEGADO.** En prod desde el 2026-08-22 (cloud + Royal Plast en sitio). Reparado con `verificar_sync --backfill --reintentar-descartados --ejecutar` en Royal Plast; SK Performance queda pendiente de su visita.
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
- **Estado: CORREGIDO y DESPLEGADO.** En prod desde el 2026-08-22 (cloud + Royal Plast en sitio). Reparado con `verificar_sync --backfill --reintentar-descartados --ejecutar` en Royal Plast; SK Performance queda pendiente de su visita.
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

**Deuda resuelta el 2026-08-24.** La asimetria de fondo -- `modulo_activo` con
`negocio=None` fallaba ABIERTO, pero un negocio a medio aprovisionar fallaba
CERRADO -- ya no existe. `apps/suscripciones/engine.py::_resolver_negocio`
trata un negocio sin suscripcion activa con plan NI una sola fila de
`NegocioModulo` igual que un negocio sin resolver: fail-open. En cuanto existe
una suscripcion o UNA fila de override (aunque sea una exclusion), se respeta
tal cual -- ya no es fail-open indiscriminado. `verificar_instalacion` sigue
avisando que el negocio no tiene entitlements de verdad configurados, pero ya
no lo marca como roto: el fail-open es una red de seguridad, no el estado
deseado de una instalacion terminada. Detalle de diseno en
`docs/ARQUITECTURA_MODULOS.md`.

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

**Corregido el 2026-08-24.** `TenantTokenRefreshSerializer.validate()`
resuelve el tenant del token (`tenant_key`) y activa `tenant_context(tenant)`
ALREDEDOR de la llamada a `super().validate()` -- antes corria despues, o no
corria. Con el contexto activo, `Usuario.objects.get(id=...)` resuelve a la
base del tenant, no al control plane. Si de verdad el usuario ya no existe
(borrado, no solo sin contexto), `ObjectDoesNotExist` se atrapa explicito y se
traduce a `InvalidToken` (401), nunca un 500 sin capturar. Tests en
`apps/api/tests/test_refresh_tenant_context.py`.

**Tambien resuelto: las rutas de template del POS local dejaron de ser
alcanzables en el cloud.** `config/urls.py` ahora excluye
`inventario/`, `pos/`, `productos/`, `clientes/`, `cuentas-por-cobrar/`,
`cotizaciones/`, `reportes/`, `caja/`, `auditoria/`, las de `usuarios`
(incluye `/login/`) y `facturacion-electronica/` cuando
`TENANCY_DB_PER_TENANT_ENABLED` esta activo -- el mismo flag que activa el
router. `/admin/`, `/api/` y el health check siguen disponibles siempre. Sin
esas rutas, un scanner tocando `/login/` o `/reportes/` recibe 404 en vez de
gatillar `TenantContextError` -> 500. Tests en
`apps/tenancy/tests/test_urls_ocultas_en_cloud.py`.

---

### BUG-F — `migrate` en verde sobre bases tenant sin tablas: caida del login del portal

- Fecha: 2026-08-22 21:06 UTC (se sembro) → 2026-08-22 22:20 UTC (se manifesto).
- Severidad: **critica**. El portal cloud quedo **sin poder autenticar a nadie**
  durante ~5 horas, en los cuatro tenants de produccion.
- Resuelto el 2026-08-23 03:5x UTC. **No requirio despliegue**: fue reparacion de
  datos + re-corrida del job de migraciones.

**Sintoma.** Credenciales correctas en el portal React → error. Credenciales
incorrectas → `400 Credenciales invalidas` normal. O sea: **el login solo
fallaba cuando el password era el correcto**, porque el fallo estaba despues del
`check_password`:

```
django.db.utils.ProgrammingError: relation "token_blacklist_outstandingtoken" does not exist
LINE 1: INSERT INTO "token_blacklist_outstandingtoken" ("user_id", "...
```

Marcador util para fechar el corte: `tenancy_identities.ultimo_acceso` solo se
escribe si el login **completa**. Quedo congelado en el ultimo login bueno.

**Causa raiz — el registro de migraciones miente por diseno.**
`allow_migrate` filtra las **operaciones** de una migracion, pero Django
**registra la migracion como aplicada de todos modos**. Mientras la app se
mantiene excluida de esas bases, es inofensivo. El dia que la app cambia de
bucket en el router, Django ya cree que esta aplicada y **nunca crea las tablas**.

La secuencia exacta:

1. Se instalo `rest_framework_simplejwt.token_blacklist` con el router
   poniendola en `DEFAULT_ONLY_APPS` (control plane).
2. El deploy migro: en `pos_fifo_prod` creo las 2 tablas; en las 4 bases `tnt_*`
   `allow_migrate` bloqueo el `CreateModel` **pero registro las 12 migraciones**.
3. Primer fallo: `RefreshToken.for_user()` intentaba crear el `OutstandingToken`
   en `default` con FK a un `Usuario` cargado desde `tnt_*` →
   `ValueError: the current database router prevents this relation`.
4. Se corrigio el router moviendo `token_blacklist` a `DUAL_HOME_APPS` (commit
   `c99d203`) — **correcto y necesario**: su FK apunta a `usuarios`, que es
   dual-home; todo FK tiene que vivir donde vive su destino.
5. El deploy volvio a migrar y reporto `migrados: 4/4`. **No aplico nada**: las
   12 migraciones ya figuraban aplicadas. Tablas: cero.
6. El error solo cambio de forma: de `prevents this relation` a
   `relation ... does not exist`.

**Lo peligroso no fue el bug, fue la senal.** `migrate` termino en verde dos
veces sobre una base rota. Nada en el pipeline miraba las tablas reales.

**Reparacion aplicada.**

```sql
-- por cada base tenant, tras respaldar las filas
DELETE FROM django_migrations WHERE app='token_blacklist';
```

```bash
az containerapp job start -n posfifo-prod-migrate -g posfifo-prod-rg
```

Verificacion (las 4 bases pasaron de `tablas=0 migraciones=12` a
`tablas=2 migraciones=12`), y despues el camino completo del login ejercitado
contra produccion para las 4 identidades.

**Prevencion (aplicada).** `migrate_tenants` ahora corre `tablas_faltantes(alias)`
**despues** de migrar cada tenant y falla si algun modelo del reparto tenant no
tiene tabla real. Compara contra `pg_tables`, no contra `django_migrations`,
porque el registro es justamente lo que mintio. Regresion cubierta en
`apps/tenancy/tests/test_verificacion_tablas.py`.

**Regla que queda.** Mover una app entre `CONTROL_PLANE_APPS`,
`DEFAULT_ONLY_APPS` y `DUAL_HOME_APPS` **no es un cambio de configuracion**: es
una migracion de datos. Toda app que cambie de bucket necesita, en cada base
afectada, desregistrar sus migraciones y volver a aplicarlas.

---

### BUG-G — Cadena de fallos en la visita de actualizacion a Royal Plast (2026-08-24)

- Fecha: 2026-08-24, actualizacion en sitio con el negocio cerrado (agente
  remoto vía Remote Control, reporte completo entregado a esta sesión).
- Severidad: **critica** la pieza 3 (perdida silenciosa de secreto); el resto
  **bloqueante mientras se diagnostica**, pero sin dano de datos.
- **Resultado final de la visita:** `verificar_sync` cerro con
  `RESULTADO: sin perdida detectada.` Cola 641 CONFIRMADO / 2 ERROR (ver
  Hallazgo 6, abajo). 36 hechos + 31 descartados repuestos; 15 CxC recuperadas
  (RD$232,635 facturado / RD$97,918.55 saldo). El detalle completo de la
  reparacion de datos queda en la bitacora del runbook, no aqui.

Cuatro fallos estaban en codigo que este repo controla y habrian reaparecido
en el proximo cliente. Los cuatro **corregidos el mismo dia**:

**1. `actualizar.bat`: el timestamp del backup dependia del locale de Windows.**
`%date:~6,4%%date:~3,2%%date:~0,2%` asume un formato fijo; en `es-DO`,
`%DATE%` trae el dia de la semana adelante (`sáb. 22/08/2026`), el recorte se
desalinea y produce un nombre de archivo con `/` adentro -- `pg_dump` no
puede crearlo, y como el script aborta a proposito si el backup falla, la
actualizacion nunca pasaba de la FASE 2. El parche manual que se venia
usando (`BACKUP_FILE=%DB_NAME%.dump`, sin fecha) sobrescribia el backup
anterior en cada corrida: se perdio el punto de rollback previo. Corregido
con `powershell Get-Date -Format` (no depende del locale).

**2. Byte de control `0x0B` colado en una ruta `\venv`.** El paquete usado en
la visita traia `call "...\x0Benv\Scripts\python.exe"` -- alguien tecleo la
secuencia de escape `\v` en algun editor/generador y quedo interpretada
literal. El `call` fallaba sin chequeo de errorlevel y el script seguia de
largo. La fuente en este repo ya estaba limpia (corregida en una sesion
anterior), pero el paquete de la visita se genero antes de ese fix. Como red
de seguridad permanente, `scripts/lint_bat.py` -- que ya corre como gate de
`preparar_paquete.bat` -- ahora tambien escanea BEL/BS/VT/FF a nivel de
bytes en cualquier `.bat`, asi el origen del proximo descuido (cual sea) no
importa.

**3. `migrar_env_cliente.py` perdia el `DJANGO_SECRET_KEY` en silencio -- el
mas grave.** La heuristica vieja descartaba CUALQUIER valor con un `%`
literal (`if '%' in valor: ignoradas.add(nombre)`), sin distinguir eso de una
expansion de cmd real (`%NOMBRE%`) sin resolver. El `DJANGO_SECRET_KEY` de
Royal Plast traia dos `%` en su alfabeto aleatorio y se omitio del `.env`
generado. Combinado con `settings.py` cayendo al default inseguro del repo
cuando falta la variable, la instalacion habria arrancado firmando cookies
con una clave publica en el código fuente -- **sin ningun error**. El
`.env` que `env_check.PLACEHOLDERS` marca como CRITICO existe como red de
seguridad, pero depende de que alguien corra `verificar_instalacion`.

Corregido: la heuristica ahora exige el PAR `%[A-Za-z_][A-Za-z0-9_]*%` (una
expansion de verdad), no un `%` suelto. Y ademas -- la correccion de fondo --
si una variable de la lista CRITICAS (`DJANGO_SECRET_KEY`, mas `DB_NAME`,
`DB_USER`, `DB_PASSWORD`, `DB_HOST` para este comando) queda omitida por
seguir pareciendo una expansion sin resolver, el comando **falla con
`CommandError`** despues de escribir el resto del archivo (no se pierde lo
demas, pero no se puede leer "N variables escritas" y seguir de largo). 7
tests nuevos en `apps/configuracion/tests/test_migrar_env_cliente.py`,
incluida la reproduccion exacta del secret real.

**4. El orden de `actualizar.bat` garantizaba que la conversion a `.env`
nunca ocurriera en la primera pasada.** La conversion estaba ANTES de la
FASE 3 (copiar codigo nuevo), pero el comando `migrar_env_cliente` solo
existe en el paquete nuevo -- `Unknown command: 'migrar_env_cliente'` en
cada primera actualizacion de un cliente que siguiera en formato `.bat`. El
runbook prometia una conversion que nunca pasaba. Movido a despues de la
FASE 4 (dependencias instaladas, ademas necesario porque el comando importa
`python-dotenv`).

**Hardening relacionado, mismo dia:**
- `registrar_sync_servicio.bat` no validaba que `env_cliente.env` existiera
  antes de registrar el servicio (a diferencia de `registrar_servicio.bat`,
  que si lo hace). El sintoma real de la visita: `POSFifoSync` quedaba en
  bucle de reinicio (`CommandError: SYNC_ENABLED=False en settings`),
  aparentando RUNNING porque nssm lo relanzaba. Ahora valida igual que su
  contraparte.
- FASE 8 de `actualizar.bat`, ante un fallo de arranque de `POSFifoSystem`,
  imprimia solo un mensaje generico que sugeria "el servicio no existe" --
  cuando la causa real (el gate de `env_check.py` rechazando un
  `DJANGO_SECRET_KEY` invalido) estaba en `logs\service_stdout.log`. Ahora
  imprime las ultimas 15 lineas de `service_stdout.log`/`service_stderr.log`
  en ese caso.
- Cosmetico: el prompt de ruta destino mostraba `[]` vacio en vez del
  default (`%DEFAULT_DST%` se expandia en tiempo de parseo del bloque, antes
  del `set`); corregido a `!DEFAULT_DST!`.
- Cosmetico: `registrar_servicio.bat` y `registrar_sync_servicio.bat`
  calculaban `PROJECT_DIR` como `%~dp0..` sin resolver el `..`, asi que
  `POS_ENV_FILE` y las rutas de log quedaban como
  `C:\pos_fifo_system\deploy\..\deploy\env_cliente.env`. Windows lo resuelve
  igual, pero ensucia cualquier diagnostico (Task Scheduler, `nssm dump`).
  Corregido con el mismo patron de canonicalizacion que ya usaba
  `actualizar.bat` (`for %%I in ("%PROJECT_DIR%") do set "PROJECT_DIR=%%~fI"`).

**Hallazgo 6 -- sin resolver a proposito, requiere decision.** Dos eventos
quedaron en ERROR permanente porque no son backfilleables por diseno
(`apps/sync/registry.py`: `cxc_creadas`/`cxc_pagos` tienen `backfill=False`
adrede -- ver el docstring del modulo):

- `VENTA_CREADA V-20260623-0001`: el producto `PROD-0361` no existe en cloud
  todavia. Se repara con `reconciliar_cloud`, que exige un
  `CLOUD_ADMIN_TOKEN` de sysadmin que no estaba disponible en sitio.
- `CXC_PAGO_REGISTRADO V-20260622-0001-P5` y su analoga en `V-20260623-0002`:
  la CxC de origen nunca emitio `CXC_CREADA`. `serializar_cxc` manda el
  estado ACTUAL (saldo 0.00, PAGADA), no el estado al momento de crearse;
  emitir el evento ahora crearia la cuenta ya saldada y el pago pendiente se
  aplicaria encima -- si el handler del cloud resta en vez de recalcular, el
  saldo terminaria negativo. Impacto acotado: ambas cuentas estan PAGADA con
  saldo 0 (no hay plata por cobrar en juego), son RD$25,850 de historial que
  no se ve en el portal.

Ninguna reparacion de datos de produccion se ejecuto a ciegas para esto --
correcto: adivinar sobre datos reales de un cliente pagando es peor que
dejarlo pendiente. Pendiente: decidir si vale la pena escribir un evento
`CXC_CREADA` "reconstruido" (con el estado historico, no el actual) para
este tipo de hueco, o si se acepta como perdida de historial cosmetica.
