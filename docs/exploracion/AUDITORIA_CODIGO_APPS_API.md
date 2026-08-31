# Auditoria de codigo - apps/api

Fecha: 2026-06-10
Scope: `apps/api`
Modo: lectura y documentacion de hallazgos; no se aplicaron cambios funcionales.

## Resumen

`apps/api` ya tiene buena separacion por dominios (`views`, `serializers`,
`services`, permisos y tests), pero mezcla tres responsabilidades sensibles:
portal cloud multi-tenant, sync sucursal-cloud y endpoints de consulta para
frontend. En esta primera pasada aparecen riesgos de scoping cross-tenant,
un bug probable en reportes con ventas sin usuario, y duplicacion clara en los
viewsets de maestros.

## Hallazgos priorizados

### API-001 - Posible fuga cross-tenant en CxC

- Severidad: alta.
- Tipo: bug/seguridad.
- Evidencia:
  - `apps/api/views/cuentas_por_cobrar.py:58` permite acceso con
    `IsAuthenticated`, `requiere_modulo('cuentas_por_cobrar')` y
    `PuedeLeerMaestro`.
  - `apps/api/views/cuentas_por_cobrar.py:72` crea el queryset desde
    `CuentaPorCobrar.objects.select_related(...)` sin filtrar por sucursal,
    negocio del usuario ni negocio de la sucursal autenticada.
  - `apps/api/views/cuentas_por_cobrar.py:81` devuelve el queryset completo en
    `retrieve`, por lo que un token autorizado podria consultar un `pk` ajeno.
  - `apps/api/views/cuentas_por_cobrar.py:120` calcula `resumen` sobre
    `CuentaPorCobrar.objects.aggregate(...)` global.
  - `apps/api/tests/test_cuentas_por_cobrar_viewset.py:119` solo verifica que
    una sucursal pueda leer, pero no que vea solo sus cuentas.
- Impacto:
  - Un admin de un negocio o un token de sucursal podria ver cartera de otros
    negocios/sucursales si comparten la misma BD cloud.
  - `resumen/` podria mostrar totales globales en vez del tenant actual.
- Nota tecnica:
  - `PuedeLeerMaestro` permite tokens de sucursal por `request.auth.sucursal`,
    pero el queryset no usa esa sucursal para filtrar datos.
- Sugerencia de arreglo:
  - Centralizar un helper de scope para CxC: token de sucursal -> filtrar
    `sucursal=request.auth.sucursal`; usuario de negocio -> filtrar
    `sucursal__negocio=negocio_actual(request)`; `SYSADMIN` global -> exigir
    `?negocio=` o permitir global solo explicitamente.
  - Agregar tests de aislamiento: sucursal A no lista ni recupera cuenta de
    sucursal B; admin negocio A no ve CxC de negocio B; `resumen/` respeta el
    mismo scope.

### API-002 - Reportes cloud y estado de sucursales no aplican scope por negocio

- Severidad: alta si el portal cloud ya opera multi-tenant; media si aun se usa
  solo con un negocio.
- Tipo: bug/seguridad/contrato multi-tenant.
- Evidencia:
  - `apps/api/services/reporting.py:104` define `_active_sucursales(codigo=None)`
    con `Sucursal.objects.filter(activa=True)`, sin `negocio_actual`.
  - Ese helper alimenta `build_ventas_hoy`, `build_comparativo`,
    `build_ventas_por_cajero`, `build_top_productos` y
    `build_cierre_consolidado`.
  - `apps/api/views/sucursales.py:60` lista `Sucursal.objects.filter(activa=True)`
    para `/api/v1/sucursales/status/`.
  - En contraste, `apps/api/views/permisos.py` usa `negocio_actual(self.request)`
    para roles, usuarios y sucursales asignables.
- Impacto:
  - Un usuario con permiso de reportes podria recibir datos agregados de todas
    las sucursales activas, no solo de su negocio.
  - `/sucursales/status/` podria exponer salud operativa de otros tenants.
- Sugerencia de arreglo:
  - Pasar `request` o `negocio` al servicio de reporting y scopear
    `_active_sucursales`.
  - Reutilizar `apps.negocios.utils.negocio_actual` como punto unico.
  - Definir comportamiento explicito para `SYSADMIN`: global por defecto o
    requiere `?negocio=<id>`.

### API-003 - `ventas_por_cajero` puede romper con ventas replicadas sin usuario

- Severidad: media-alta.
- Tipo: bug escondido.
- Evidencia:
  - `apps/api/views/sync.py:364` resuelve el usuario por username y puede dejar
    `usuario = None`.
  - `apps/api/views/sync.py:369` crea la `Venta` con `usuario=usuario`, por lo
    que una venta replicada puede quedar sin usuario si el username no existe en
    cloud.
  - `apps/api/services/reporting.py:376` agrupa ventas por `venta.usuario_id`.
  - `apps/api/services/reporting.py:404` toma `usuario = row['usuario']` y luego
    usa `usuario.id`, `usuario.username` y `usuario.get_full_name()` en
    `apps/api/services/reporting.py:406-410`.
  - `apps/api/tests/test_reportes_cloud.py:132` siempre crea ventas con
    `usuario=self.cajera`, asi que no cubre el caso `usuario=None`.
- Impacto:
  - `GET /api/v1/reportes/ventas-por-cajero/` puede responder 500 si hay ventas
    cloud cuyo usuario no fue encontrado al replicar.
  - `cierre_consolidado` tambien puede fallar porque llama
    `build_ventas_por_cajero` en `apps/api/services/reporting.py:508`.
- Sugerencia de arreglo:
  - Representar usuario nulo como cajero desconocido/sistema en el payload.
  - Agregar test con venta `usuario=None` y pago/CxC con `registrado_por=None`
    si el modelo lo permite.

### API-004 - Duplicacion de create/update en viewsets de maestros

- Severidad: media.
- Tipo: duplicacion/refactor.
- Evidencia:
  - `ProductoViewSet.create/update` en `apps/api/views/maestros.py:149-175`.
  - `CategoriaViewSet.create/update` en `apps/api/views/maestros.py:222-245`.
  - `ClienteViewSet.create/update` en `apps/api/views/maestros.py:302-325`.
  - Los tres repiten el mismo patron: validar serializer de escritura, guardar,
    serializar de nuevo con serializer de lectura y devolver la respuesta.
- Impacto:
  - Cada nuevo maestro repetira el mismo bloque.
  - Cambios de contrato, headers o manejo de errores se tendran que aplicar en
    varios sitios.
- Sugerencia de arreglo:
  - Extraer un mixin `ReadAfterWriteMixin` con `read_serializer_class` por
    viewset, manteniendo el contrato actual de respuesta.

### API-005 - `SyncIncrementalMixin` usa mutacion de `self.queryset`

- Severidad: media.
- Tipo: logica fragil/deuda tecnica.
- Evidencia:
  - `SyncIncrementalMixin.get_queryset` llama `super().get_queryset()` en
    `apps/api/views/maestros.py:55-56`.
  - Cada viewset de maestros construye filtros, asigna `self.queryset = queryset`
    y llama `super().get_queryset()` (`apps/api/views/maestros.py:144-147`,
    `219-220`, `299-300`).
- Impacto:
  - El orden de herencia y la mutacion de atributo hacen dificil razonar si un
    cambio futuro en DRF, otro mixin o un action custom respetara los filtros.
  - `X-Total-Count` en `list()` cuenta `self.get_queryset()` ya filtrado, aunque
    el comentario dice "total de registros (sin paginar)"; queda ambiguo si es
    total filtrado o total del recurso.
- Sugerencia de arreglo:
  - Separar `get_base_queryset`, `apply_filters` y `apply_sync_since`.
  - Documentar si `X-Total-Count` debe ser total post-filtros o total global.

### API-006 - `inventario_consolidado` conserva forma multi-sucursal pero usa stock local

- Severidad: media.
- Tipo: contrato ambiguo/deuda funcional.
- Evidencia:
  - `apps/api/services/reporting.py:567` usa `Producto.objects.select_related`.
  - `apps/api/services/reporting.py:584` lee `producto.stock_actual`.
  - `apps/api/services/reporting.py:596` devuelve `stock_por_sucursal` con
    `{'LOCAL': stock}` y `apps/api/services/reporting.py:609-610` deja metadata
    de sucursal vacia.
- Impacto:
  - El endpoint se llama consolidado, pero no esta consolidando inventario por
    sucursal. Puede inducir decisiones incorrectas en portal cloud si se muestra
    como dato multi-sucursal real.
- Sugerencia de arreglo:
  - Marcarlo como snapshot local/placeholder en el contrato o conectar con una
    fuente real de inventario por sucursal antes de mostrarlo como consolidado.

### API-007 - Ruido de `__pycache__` bajo `apps/api`

- Severidad: baja.
- Tipo: higiene.
- Evidencia:
  - Existen archivos `__pycache__` dentro de `apps/api`, incluso bytecode
    `cpython-314` mezclado con `cpython-311` en la inspeccion local.
  - `git ls-files "*__pycache__*"` no devuelve archivos, asi que no parecen
    estar versionados.
- Impacto:
  - Ensucia busquedas, auditorias y conteos de tamano. Si estuvieran versionados,
    tambien contaminan diffs.
- Sugerencia de arreglo:
  - Limpiarlos localmente cuando convenga; no hace falta cambio de repo salvo
    que vuelvan a aparecer como archivos trackeados.

### API-008 - Contrato CxC no refleja campos de interes financiero en cambios locales

- Severidad: media si los cambios de interes financiero entran al flujo actual.
- Tipo: contrato API / cambio cross-app pendiente.
- Contexto:
  - Durante esta auditoria habia cambios locales no atribuibles a este documento
    en `apps/cuentas_por_cobrar/`, incluyendo modelo, servicios y migraciones
    de interes financiero.
- Evidencia:
  - `apps/cuentas_por_cobrar/models.py:42` agrega
    `MetodoPlazoCredito.interes_porcentaje`.
  - `apps/cuentas_por_cobrar/models.py:121-128` agrega
    `CuentaPorCobrar.saldo_original`, `interes_porcentaje` y `monto_interes`.
  - `apps/cuentas_por_cobrar/models.py:173-176` introduce
    `monto_financiado`.
  - `apps/api/serializers/cuentas_por_cobrar.py:85-102` expone en la API solo
    `total`, `monto_inicial` y `saldo`, pero no los campos de interes.
- Impacto:
  - El portal podria mostrar una cuenta financiada sin explicar cuanto es
    capital, cuanto es interes y cual fue el saldo original financiado.
  - Si el frontend calcula diferencias desde `total - monto_inicial`, puede
    quedar inconsistente con `monto_financiado`.
- Sugerencia de arreglo:
  - Cuando se cierre la feature de interes financiero, actualizar el serializer,
    tests de CxC y contrato frontend en el mismo cambio.
  - Decidir nombres de API estables: por ejemplo `saldo_original`,
    `interes_porcentaje`, `monto_interes` y `monto_financiado`.

## Tests recomendados antes de tocar codigo

- `apps.api.tests.test_cuentas_por_cobrar_viewset`: agregar aislamiento por
  negocio/sucursal para list, retrieve y resumen.
- `apps.api.tests.test_reportes_cloud`: agregar usuarios de dos negocios y
  validar que reportes/sucursales se scopeen al negocio del request.
- `apps.api.tests.test_reportes_cloud`: agregar venta con `usuario=None` y
  confirmar que `ventas-por-cajero` y `cierre-consolidado` no hacen 500.
- `apps.api.tests.test_producto_viewset`, `test_categoria_viewset` y
  `test_cliente_viewset`: mantener contrato de read-after-write si se extrae
  mixin para maestros.

## Orden sugerido de correccion

1. Cerrar scoping multi-tenant en CxC, reportes y sucursales status.
2. Blindar reportes contra usuario nulo en ventas/cobros replicados.
3. Refactorizar duplicacion de maestros con tests de contrato.
4. Aclarar contrato de inventario consolidado.
5. Alinear serializer/API de CxC con la feature de interes financiero si se
   confirma para el proximo corte.
6. Limpiar `__pycache__` si esta versionado o si molesta en auditorias locales.

## Resolucion (2026-06-18)

Verificados los 8 hallazgos contra el codigo actual y corregidos 001-006 + 008.
Decision de scope multi-tenant: el solicitante sin negocio resoluble (SYSADMIN/
global, sin `?negocio=`) ve TODO por defecto (compat con `negocio_actual` y con
"Null = usuario global" del modelo Usuario); puede acotar con `?negocio=<id>`.
Patron unico de tenant: `apps.negocios.utils.negocio_actual`.

> **La decision de scope de este parrafo quedo SUPERADA el 2026-08-30 por
> NEG-001.** Ver [Re-verificacion](#re-verificacion-2026-08-30) al final: ver
> todo pasa a depender de ser un principal global verificado, no de que la
> resolucion del negocio haya fallado. El resto de la resolucion sigue vigente.

- **API-001 — RESUELTO.** Helper `_scope_por_tenant` en
  `apps/api/views/cuentas_por_cobrar.py` (token de sucursal -> esa sucursal;
  usuario con negocio -> `sucursal__negocio`; global -> sin filtro). Aplicado en
  `get_queryset` (cierra list y el IDOR de retrieve -> 404) y en `resumen`,
  `aging`, `cartera_clientes`, `cobros`, `proximos_vencimientos`.
  Tests: `apps/api/tests/test_cxc_scope_negocio.py`.

- **API-002 — RESUELTO.** `_active_sucursales(codigo, negocio)` en
  `apps/api/services/reporting.py`; `negocio` propagado a `build_ventas_hoy`,
  `build_comparativo`, `build_ventas_por_cajero`, `build_top_productos`,
  `build_cierre_consolidado`. Las vistas en `apps/api/views/reportes.py` resuelven
  `negocio_actual(request)`. `sucursales_status` (`apps/api/views/sucursales.py`)
  filtra sucursales por negocio. `inventario_consolidado` NO se scopea (Producto
  sin FK negocio; aislamiento por DB-per-tenant) — ver API-006.
  Tests: `apps/api/tests/test_reportes_scope_negocio.py`.

- **API-003 — RESUELTO, con matiz.** El 500 descrito NO podia ocurrir: `Venta.usuario`
  es NOT NULL, asi que una venta replicada sin usuario nunca se persiste; el bug
  real era que `_handler_venta_creada` (`apps/api/views/sync.py`) reventaba con
  IntegrityError y dejaba el evento en ERROR (la venta NO se replicaba). Fix:
  fallback `usuario = _resolver_usuario(...) or sucursal.usuario_servicio` (mismo
  patron que el handler de pagos CxC, que cae a `cuenta.creado_por`). Se mantiene
  ademas el null-check defensivo en `build_ventas_por_cajero`.
  Tests: `apps/api/tests/test_sync_venta_sin_usuario.py`.

- **API-004 — RESUELTO.** `ReadAfterWriteMixin` en `apps/api/views/maestros.py`;
  Producto/Categoria/Cliente declaran `read_serializer_class` y ya no repiten
  create/update. Contrato de respuesta intacto.

- **API-005 — RESUELTO.** `SyncIncrementalMixin` ya no muta `self.queryset`: cada
  viewset implementa `get_base_queryset()` y el mixin aplica `?desde=` sobre el.
  Comentario de `X-Total-Count` aclarado (total FILTRADO, antes de paginar).

- **API-006 — RESUELTO (contrato).** `build_inventario_consolidado` agrega
  `es_snapshot_local: true` y `fuente_stock: 'LOCAL'`, con docstring explicito de
  que es snapshot local (no consolidado por sucursal) y no scopeado por negocio.
  Forma de respuesta estable; clave `LOCAL` intacta (el frontend la renderiza).

- **API-007 — NO APLICA.** `git ls-files` no devuelve `__pycache__`/`.pyc`
  trackeados; `.gitignore` ya los ignora.

- **API-008 — RESUELTO.** El serializer ya exponia `saldo_original`,
  `interes_porcentaje`, `monto_interes`; se agrega `monto_financiado` (property del
  modelo). Aditivo: el frontend lo computaba a mano.

Regresion: las 92 pruebas previas de maestros/CxC/reportes siguen verdes; 18
pruebas nuevas cubren el aislamiento y el fallback de sync.

---

# Re-verificacion (2026-08-30)

Cierre de la auditoria de `apps/api`, la ultima de la serie. **No habia nada que
corregir**: los 8 hallazgos se resolvieron en junio y los 8 siguen resueltos
contra el codigo de hoy. Lo que si cambio es una **decision**, y esa es la razon
de esta seccion.

## Los 8, contra el codigo actual

| ID | Estado | Evidencia |
|---|---|---|
| API-001 | Resuelto | `apps/api/views/cuentas_por_cobrar.py:72` — `resolver_negocio(request).filtrar(...)`; `:111` aplica `_scope_por_tenant` en las agregaciones. |
| API-002 | Resuelto | `apps/api/services/reporting.py:105` — `_active_sucursales(codigo=None, resolucion=None)`. |
| API-003 | Resuelto | `apps/api/views/sync.py:744` y `:1291` — `_resolver_usuario(...) or sucursal.usuario_servicio`. |
| API-004 | Resuelto | `apps/api/views/maestros.py:163` — `ReadAfterWriteMixin`. |
| API-005 | Resuelto | Cuatro `get_base_queryset()`; ninguna asignacion a `self.queryset`. |
| API-006 | Resuelto | `reporting.py:701` — `es_snapshot_local: True`, `fuente_stock: 'LOCAL'`. |
| API-007 | No aplica | `git ls-files apps/api` no devuelve `__pycache__` ni `.pyc`. |
| API-008 | Resuelto | `apps/api/serializers/cuentas_por_cobrar.py:92,112` — `monto_financiado`. |

## Lo que cambio: la decision de scope

La resolucion de junio dejo escrita esta regla:

> el solicitante **sin negocio resoluble** (SYSADMIN/global, sin `?negocio=`)
> ve TODO por defecto

La frase junta dos cosas que se ven igual y no lo son. **SYSADMIN** es un
principal global: ver todo es su trabajo. **"Sin negocio resoluble"** es otra
cosa — es que la resolucion *fallo*—, y el parentesis las trata como sinonimos.

Eso es exactamente NEG-001, corregido en la auditoria de `apps/negocios`: un
`ADMIN` activo, no staff, no superusuario y con `negocio_id=NULL` recibia la
cartera y los reportes de **todos** los negocios. `es_acceso_total` le concedia
el permiso y `negocio_actual` devolvia `None` —"no pude resolver"— que los
consumidores leian como "sin filtro". Un dato de aprovisionamiento faltante se
leia como la autorizacion mas amplia del sistema.

**La regla vigente es la de NEG-001:** ver todo depende de ser un principal
global *verificado*. `resolver_negocio()` devuelve un resultado tipado
(`TENANT` / `GLOBAL` / `SIN_ACCESO`) en vez de un `None` sobrecargado, y el
huerfano cae en `SIN_ACCESO` cuando hay mas de un negocio activo — "denegar
donde hay algo que aislar", no "huerfano = denegar", para no romper la
instalacion local de un solo negocio que nunca corrio el bootstrap.

En la practica **API-001 y API-002 quedaron mas restrictivos que su propia
resolucion de junio**: fallan cerrado donde antes fallaban abierto. Los
endpoints afectados son los mismos que junio ya cubria: cartera CxC (list,
retrieve, `resumen`, `aging`, `cartera_clientes`, `cobros`,
`proximos_vencimientos`), reportes cloud y `sucursales/status`.

Se agrego ademas NEG-002, que junio no contemplaba: `?negocio=<id>` con un id
inexistente o inactivo caia a GLOBAL. Un typo o un bookmark viejo **ensanchaban**
la consulta que el operador intentaba acotar; ahora devuelve vacio.

## Que se toco

Nada de `apps/api` — el codigo ya cumplia. Solo descripciones y cobertura:

1. **`apps/api/tests/test_auditoria_api.py`** (nuevo, 12 pruebas). Los tests de
   junio cubren al SYSADMIN, que sigue viendo todo y por eso nunca fallaron. El
   caso que la regla vieja abria —el huerfano— no estaba cubierto **en la
   frontera de la API**, que es donde la fuga se materializaba. Ahora si.
2. **`apps/api/tests/test_cxc_scope_negocio.py`**: el docstring del modulo y el
   comentario de `self.sysadmin` describian el contrato viejo (`negocio_actual
   None`). Los tests no cambian —eran correctos—; cambia lo que dicen que
   prueban.

## Despliegue

**Sin migraciones. Sin permisos nuevos. Sin cambios de contrato** respecto de lo
ya desplegado: `apps/api` no se modifico en esta pasada.

> **Revisar antes de desplegar** — no es de `apps/api` sino de NEG-001, y ya
> figura en `docs/ESTADO_AUDITORIAS.md`, pero se repite aca porque es donde se
> observa:
>
> ```sql
> SELECT id, username, rol FROM usuarios_usuario
> WHERE negocio_id IS NULL AND activo AND NOT is_superuser;
> ```
>
> En una instalacion con **mas de un negocio activo**, esas cuentas pasan de ver
> todo a no ver nada. Es la correccion, no una regresion — pero si alguna es una
> cuenta operativa en uso, hay que asignarle su negocio **antes** de desplegar o
> se queda sin cartera ni reportes.

## Pruebas

Suite completa, serial: **1110 tests, OK.**

Modulo nuevo: `apps/api/tests/test_auditoria_api.py` (12 pruebas), sobre las 92
previas de maestros/CxC/reportes y las 18 de junio.

**Verificacion por mutacion.** Revertido `_resolver_huerfano()` a la conducta
previa a NEG-001 (huerfano -> GLOBAL): fallan las 4 pruebas del huerfano — vuelve
a ver las dos carteras, recupera la cuenta ajena por pk, y consolida reportes y
sucursales. Revertida por separado la caida a GLOBAL del `?negocio=` invalido:
falla la prueba de NEG-002. Las pruebas miden lo que dicen medir.
