# TODO — pendientes de las auditorías

Lista accionable. El contexto de cada punto está en
[ESTADO_AUDITORIAS.md](ESTADO_AUDITORIAS.md) y en el documento de auditoría del
módulo. Marcar `[x]` al cerrar.

Última actualización: **2026-09-16** (reconciliación de cierres C02/C03 ya en `develop`; ver INVENTARIO.md)

## Cierre A02 (sin despliegue)

Los commits `583863f` + `f0a255c` + `bb7f774`, sobre CT-01 `cd8a3b4`, cierran en código los pendientes de
auditoría, identidad, negocios y tenancy asignados a A02: AUD-008/009/010/013/
016/018/019/020/021, USR-007/010/011/013/015/016/017/019,
NEG-006/007/008/009/011/012/013/014/016/017 y TEN-016. USR-012 queda parcial:
la identidad ya está acotada, pero las invariantes de privilegio/revocación son
A03/CT-02. USR-014 sigue A08. Los preflights sobre filas operativas continúan
abiertos para A08; A02 no leyó ni modificó datos reales.

## Cierre A03 / CT-02 (sin despliegue)

La implementación `3e6cec1`, integrada localmente en `develop` por `b7147fb` y
validada con la matriz combinada, cierra en código PER-006/007, PER-012 y
PER-014–021: identidad/revisión/tombstones RBAC, servicios atómicos y auditados,
seed/comandos tenant-aware y migraciones históricas congeladas. PER-013 se
entrega a C02/C05 porque sus consumidores son superficies Claude. El bypass de
ADMIN no se retira en esta fase: queda condicionado al preflight por tenant de
A08/A09.

## Cierre A04 + C05 parte 1 (sin despliegue)

A04 (`be15ea0`) y C05 parte 1 (`60c6dbc`) se integraron localmente en el árbol
`9ff61c2`. A04 cierra claim/lease, diferidos durables, identidad scopeada y la
herramienta dry-run de BUG-K; C05 cierra PER-013 en anulación/reimpresión y
SUS-006 con gates HTML/API de CxC y reportes on-demand. La matriz conjunta pasó
237 focales, 1.386 Django y 72 e-CF. No hubo push, despliegue ni operación sobre
datos de clientes.

## Cierre C05 parte 2 (sin despliegue)

> **Actualización A06/C04/C05 (2026-09-18, sin publicar).** El candidato
> `integration/cierre-prod-A06-C04-C05@dfb1dfc` integra el backend/POS A06,
> selectores comerciales C05/CT-04 y C05 p6. La matriz backend focal terminó
> con 363 pruebas OK. C04 frontend permanece separado en `e319058`, con 109
> pruebas verdes; el smoke HTTP autenticado contra este backend reportó 23/23
> verdes. Falta la aceptación UI C04 a más de 200 filas contra backend vivo.

La entrega Claude (`b6e898a..fc0aafd`) se revisó y endureció en `18e0898`.
Quedan cerrados DB-CONSTRAINTS, COT-008/010/011/012/014/015,
CXC-MIG-ALIAS, CXC-IDEMP-CONC, INV-RBAC-SCOPE, PAG-CXC-CAJA y RPT-005;
CAJA-002, CXC-006, RPT-004 y VEN-ANULAR-LEGACY se revalidaron. **COT-009 ya
cierra del todo**: el selector `productos_vendibles()`/`es_vendible` (PRO-007)
ya filtra activo+categoría activa+`MutacionMaestro` en `CONFLICTO` en
búsqueda, escaneo, accesos rápidos y — el gate final — la carga transaccional
de la venta; `guardar_cotizacion` usa el mismo selector. Solo faltaba
cobertura de test del lado producto/categoría (ya existía para cliente) y un
accesorio menor (categoría en accesos rápidos no miraba conflicto) — ambos
cerrados en `claude/cierre-prod-C05-ct04-selectores`. **C04 arrancó** con la
pantalla de conflictos de maestros (rama `claude/cierre-prod-C04`, repo
frontend, sin publicar) — ver
`docs/handoffs/cierre_prod/C04-conflictos-maestros-ct04.md`. El backend A06 ya
existe en candidato aislado y el smoke HTTP real está acreditado. Los selectores
comerciales ya están dentro del candidato; el gate pendiente es C04 p6 (UI,
cursor/selectores a >200), no reimplementar el selector.

---

## ✅ Bloqueantes de seguridad cerrados por A03

Los dos casos de privilegio persistente quedaron resueltos en código por el
contrato `rbac.sync.v2`; el despliegue y su verificación operativa siguen fuera
de este cierre.

- [x] **PER-006 — Mover una asignación revoca la anterior en el POS local.**
      A03 agrega `cloud_id` inmutable y movimiento revoke-old/create-new
      transaccional; V2 conserva claves legacy solo por compatibilidad.
- [x] **PER-007 — Borrar un rol custom se propaga.** A03 usa baja lógica
      versionada y snapshots completos que reconcilian únicamente filas
      `origen_cloud`, luego de validar tenant y sucursal.

> Ambos son P1 críticos de `apps/permisos` y comparten solución: es un cambio de
> contrato de sincronización con su propia migración, del tamaño de las
> auditorías de `apps/sync`.

---

## 🟠 Decisiones que dependen del negocio

- [ ] **PRO-002 + PRO-003 + PRO-004 — quién es el escritor autoritativo de
      los maestros de producto.** Los tres son la misma pregunta:
      * las escrituras HTML son locales y no se propagan (PRO-002);
      * el SKU, que el pull usa como clave, es editable localmente, y
        cambiarlo y bajar el anterior crea DOS productos (PRO-003);
      * el `DELETE` de la API no deja tombstone, así que la sucursal
        conserva y vende lo que el cloud ya borró (PRO-004).
      La base de la solución es darle a `Producto` una identidad cloud
      inmutable, como ya tienen las categorías. **No se aplicó la
      contención que sí se puso en clientes** porque ahí `origen_cloud_id`
      ya existía; en productos no, y no hay forma fiable de distinguir un
      producto bajado del cloud de uno creado en la sucursal.
- [ ] **CLI-004 — proxy de escritura de maestros hacia el cloud.** Hoy, con
      sync activo, editar un cliente adoptado por el cloud devuelve **409** y
      remite al portal: es contencion, no la solucion. La decision ya tomada
      en el roadmap es que toda mutacion local de maestros pase por la API
      cloud y refresque la replica. Falta construirla.

Ninguna está tomada. El sistema funciona con la opción elegida; cambiarla es
acotado. Ver §3 de ESTADO_AUDITORIAS.

- [x] **CAJA-002 — Efectivo sin caja abierta.** La política mantiene la venta
      habilitada; el pago queda sin turno y la matriz C05 revalidó que sea
      distinguible en el arqueo.
- [x] **CXC-006 — Anulación con abonos aplicados.** Se bloquea con 409 hasta que
      el operador revierta manualmente los abonos LIFO auditados.
- [x] **RPT-004 — Cierre contable deliberado.** El resumen nace BORRADOR y se
      recalcula; solo `--finalizar` lo congela.
- [x] **RPT-005 — Sin cierre automático.** Se retiró el launcher NSSM roto y la
      operación soportada quedó en `docs/runbooks/CIERRE_DIARIO_MANUAL.md`.
      Automatizar con Task Scheduler es una decisión explícita por instalación.
- [ ] **¿Backfill de datos históricos?** Tres posibles, ninguno hecho:
      `turno_caja` en pagos viejos, `sucursal` en cierres diarios,
      conciliación de movimientos de inventario duplicados.

---

## 🟡 Infraestructura y despliegue

- [ ] **Antes de desplegar: ejecutar el preflight financiero read-only.**
      `python manage.py verificar_integridad_financiera` y, bajo tenancy,
      `--tenant <tenant_key>`/`--todos-los-tenants`. Las migraciones nuevas de
      ventas, inventario y cotizaciones abortan ante PKs incompatibles; no
      corrigen historia automáticamente.
- [ ] **Antes de desplegar: avisar del cambio de `$` a `RD$`** en todos los
      PDFs. Es visible para el cliente final; si hay plantillas, capturas o
      material impreso que lo referencien, conviene anticiparlo.
- [ ] **Antes de desplegar: sucursales sin configuracion propia.**
      `Sucursal.objects.filter(configuracionnegocio__isnull=True)` — sus
      documentos caen al contexto actual y dejan un warning (COM-001).
- [ ] **Antes de desplegar: revisar cotizaciones pendientes vencidas.**
      `Cotizacion.objects.filter(estado='PENDIENTE', fecha_creacion__lt=timezone.now()-timedelta(days=15)).count()`
      Dejan de ser convertibles (COT-007). Si el negocio venia convirtiendo
      ofertas viejas, avisarle o revisar la ventana en `Cotizacion.DIAS_VALIDEZ`.
- [ ] **Antes de desplegar: asignar `cotizaciones.precio_negociado`** a
      quien deba negociar precios. No esta en ningun rol por defecto a
      proposito: es la decision financiera que COT-002 pide separar.
- [ ] **Antes de desplegar: revisar suscripciones suspendidas o sin plan.**
      `SuscripcionNegocio.objects.filter(Q(activa=False) | Q(plan__isnull=True))`
      Cada fila ahí opera HOY con todos los módulos (SUS-001) y pasará a
      operar con los que le correspondan. Si alguna estaba suspendida "de
      mentira" —usada para dejar el plan abierto— hay que darle un plan
      explícito antes.
- [ ] **Antes de desplegar: verificar `SUCURSAL_CODIGO`.** Tiene que
      corresponder a una `Sucursal` existente. Si no, y hay mas de una
      configuracion, la aplicacion ahora se detiene en vez de operar con la
      identidad fiscal y los medios de pago de otra tienda (CFG-002).
- [ ] **Antes de desplegar: asignar `configuracion.administrar`.** El
      permiso existia en el catalogo pero no habilitaba nada, asi que es
      probable que nadie lo tenga. Sin el, el Admin de configuracion queda
      cerrado incluso para quien tenga el permiso Django (CFG-003).
- [ ] **Backend de caché compartido (Redis) en el cloud.** Ya son TRES los
      controles que pagan el mismo precio —TTL corto o sin caché entre
      requests, para no discrepar entre los tres workers de Gunicorn—:
      permisos, configuración y entitlements. Los tres lo recuperan con un
      backend compartido, y la invalidación pasa a alcanzar a todos.
- [ ] **Corregir `docs/RBAC_PERMISOS.md:73-74`**, que describe Azure como
      single-worker mientras el `Dockerfile` arranca Gunicorn con `--workers 3`.
- [ ] **USR-014 — definir proxies confiables.** La IP de auditoría confía en
      cualquier `X-Forwarded-For`. Por eso el nuevo freno de fuerza bruta del
      login **no** lo lee: confiar en una cabecera que cualquiera envía
      convertiría el contador en algo que el atacante reinicia a voluntad.
      Detrás del proxy de Azure eso cuesta resolución; cerrarlo mejora las dos
      cosas a la vez.
- [ ] **Decidir el resto de USR-002**: restringir `/admin/` por red, exigir
      MFA y auditarlo como frontera aparte. El gate de identidad global ya
      está; esto es despliegue.
- [x] **Matriz PostgreSQL multi-DB en CI** (TEN-016). `583863f` levanta dos
      bases namespaced por corrida, prueba mismo PK con filas/referencias
      aisladas y destruye únicamente esos artefactos de test.
- [ ] **Drill de restauración.** `backup_tenant` verifica el artefacto, pero
      nadie lo restauró end-to-end.
- [ ] **Antes de desplegar: revisar categorias inactivas con productos
      activos.** Esos productos dejan de aparecer en el POS (PRO-007).
      `Producto.objects.filter(activo=True, categoria__activa=False).count()`
- [ ] **Antes de desplegar: revisar los clientes marcados CONTADO.**
      `Cliente.objects.filter(tipo='CONTADO').values('id','nombre','cedula_rnc')`.
      La migracion `clientes.0006` consolida los duplicados limpios del
      generico, pero **aborta** si encuentra un cliente real convertido a
      CONTADO: reasignar sus ventas al generico falsificaria la historia
      comercial. Hay que corregirle el `tipo` primero.
- [ ] **Antes de desplegar: verificar el self-row de cada tenant.**
      `Negocio.self_row()` ahora falla si una base tenant tiene mas de una
      fila `Negocio`, en vez de retitular la de menor PK y dejar la otra
      colgando. Conviene revisarlo antes de que lo descubra el provisioning.
- [ ] **Revisar usuarios sin negocio.** El resolver ahora deniega a un
      huerfano cuando hay algo que aislar (bajo tenancy siempre; sin tenancy,
      con mas de un negocio activo). El bootstrap los enlaza, pero una
      instalacion migrada a mano puede tenerlos.
- [ ] **Revisar roles custom después de desplegar.** Las data migrations tocan
      los roles **de sistema**; un rol creado a mano no recibe los permisos
      nuevos (`caja.operar`, `reportes.ver`, `productos.fotografiar`).

---

## 🟢 Robustez y deuda de contrato

- [x] **Claim durable del push de sync** (`EN_VUELO` + lease): A04 persiste un
      lease de cinco minutos antes del HTTP, recupera vencidos e ignora ACK de
      un worker que perdió la propiedad (`be15ea0`).
- [x] **Cola durable de diferidos** en sync: A04 avanza el cursor solo tras
      aplicar o persistir; reintenta en transacción y reporta `PARCIAL` mientras
      haya pendientes (`be15ea0`).
- [ ] **`_pull_legacy`** se conserva deliberadamente como compatibilidad con
      clouds pre-Fase 2; retirarlo requiere terminar la flota y la matriz real.
- [x] **`CheckConstraint` de respaldo** en ventas, inventario y cotizaciones:
      constraints + preflight acreditados en `b6e898a`/`18e0898`.
- [x] **Idempotencia del cobro CxC**: seis reintentos con la misma clave dejan
      exactamente un efecto financiero (`b066636`).
- [x] **PER-013 / consumidores C02-C05 — anulación y reimpresión usan RBAC y
      scope de la venta.** C05 consume el helper de A03 contra la sucursal de la
      propia venta y la integración repitió la matriz (`60c6dbc`, `9ff61c2`).
- [x] **Scope por sucursal en inventario**: el servicio reautoriza el ajuste
      contra la sucursal del lote bloqueado y cubre asignaciones A/B (`95dddb3`).
- [x] **Identidad compuesta en el receptor cloud**: A04 deduplica por identidad
      estable/hash + sucursal autenticada y los handlers de venta/CxC consultan
      la venta dentro de esa sucursal; una colisión real queda `ERROR`, no ACK
      exitoso (`be15ea0`).
- [ ] **Auditoría de mutaciones API bajo tenancy**: `SesionImpersonacion`
      registra el acceso, no cada mutación de la sesión.
- [ ] **Retirar el bypass de `ADMIN`** solo después de ejecutar
      `preflight_rbac_admin_cutover --tenant <tenant_key>` y corregir los
      ADMIN activos reportados. El código ya soporta
      `RBAC_LEGACY_ADMIN_BYPASS=False`; A03 no leyó datos operativos ni cambió
      esa bandera.
- [x] **Guards RBAC de notificaciones unificados.** Notificaciones consume
      `permisos.engine.asignaciones_efectivas` como consulta canónica.

---

## 🔵 Presentación y rendimiento

- [x] **Paginación/visibilidad CxC-caja**: historial de turnos paginado
      (`eb72f69`); cartera conserva el tope de 300 pero informa
      `cuentas_ocultas`/`tope_lista`. El inventario ya declara
      `productos_ocultos`.
- [ ] **Cerrar el último tramo de AUD-002.** El historial ya es append-only
      contra la aplicación, y una edición externa es **detectable** por el
      hash de cada fila. Lo que falta: borrar la ÚLTIMA fila no deja hueco de
      secuencia. Lo cerraría una cadena de hashes (obliga a serializar cada
      INSERT de auditoría — caro en el camino de una venta) o, mejor, una
      **exportación periódica a almacenamiento WORM**, que además protege
      contra el borrado total de la tabla.
- [x] **Chart.js desde CDN** sin integridad ni fallback local
      (`templates/reportes/on_demand.html`). Resuelto: sirve el asset local
      `static/js/chart.min.js` (Chart.js 4.4.0). Revisado PASS (`f085d77`) e
      **integrado por Codex en `integration/cierre-prod-A05-C03`** (`1009ba3`);
      aún no en `develop`. Ver `docs/handoffs/cierre_prod/C02-chart-offline.md`.

---

## ⚪ Auditorías escritas pero sin procesar

**Ninguna.** La serie quedó cerrada el 2026-08-30 con `apps/api` (8 hallazgos,
ya resueltos en junio de 2026 y re-verificados; su decisión de scope quedó
superada por NEG-001). Lo que sigue abajo son hallazgos P2/P3 dentro de módulos
ya procesados.

**Pendientes de `apps/permisos`:** no quedan hallazgos de código de la ronda;
permanece el gate operacional de ADMIN documentado arriba.

**Pendientes de `apps/common`** — solo queda **COM-013** (los builds no fijan
ReportLab/Pillow aunque existan snapshots exactos que el Dockerfile no usa; es
A+C, la parte de deps es de Codex). **COM-012** (tablas materializaban todos los
registros) quedó **integrado y revalidado localmente** en
`integration/cierre-prod-A05-C03` el 2026-09-17 (origen `78bec9a`):
`standard_table` recorre perezoso y corta en `TABLA_MAX_FILAS`.
El resto de la ronda de renderizado quedó **cerrado en `develop` por
C02** (`d937db5`, con tests en `apps/common/tests`, revalidado 2026-09-16):
COM-005 (valida la forma de la tabla y rechaza geometría inválida), COM-006
(vacíos/dimensiones degradan con aviso), COM-007 (degrada logo corrupto con
warning), COM-008 (distingue "fallo de storage" de "no hay logo"), COM-009 (lee
el logo remoto por chunks con tope `LOGO_MAX_BYTES` + pre-check de `size` — ya
no agota memoria) y COM-014 (escala el logo manteniendo proporción). Ya estaban
cerrados COM-001/002/003/004/010/011/015.

**Pendientes de `apps/cotizaciones`** después de C05 parte 2: COT-013 (borrar
no converge con cloud); COT-017 conserva el stock al convertir y el
presupuesto de queries (la lista ya pagina); COT-018 conserva rutas/floats
residuales. COT-008/009/010/011/012/014/015 quedaron acreditados.

**Pendientes de `apps/suscripciones`** — el grueso quedó **cerrado en
`develop` por C03** (revalidado 2026-09-16, con tests en
`test_auditoria_suscripciones`/`test_sync_modulos`): SUS-008/009 (`1ca1688`,
bootstrap preserva flags por sucursal y adopta legacy sin sucursal),
SUS-010/012/018 (`d968e3f`, fail-closed en la baja, reconciliación ruidosa
código↔DB y default-deny de key desconocida), SUS-011 (`506edf2`, invalidación
diferida a `on_commit`), SUS-013 (`6e3d551`, semántica real de `Plan.activo`),
SUS-015 (`7201043`, auditoría CT-01), SUS-017 (`3018ce8`, presets versionados).
**SUS-019** (fronteras del guard de degradación por plan/`activa`) quedó
integrado y revalidado localmente en `integration/cierre-prod-A05-C03` el
2026-09-17 (origen `0548384`). Quedan
abiertos: **SUS-007** (mitad UI hecha, falta migrar el pull de sync a derivar del
engine — es de Codex) y **SUS-016** (C+A: `--dry-run`/atomicidad hechos, falta el
reporte de sync parcial de Codex). **SUS-014** quedó acreditado localmente:
prevención (`validar_plan_slug` + `bootstrap_tenant`) y detección read-only
`PLAN_DRIFT` cableada a `verificar_identidad_tenant`; no corrige datos.

**Pendientes de `apps/configuracion`** — cerrados en `develop` por C03
(revalidado 2026-09-16, con tests de configuración): CFG-006 (`38e5647`,
`full_clean` rechaza combinaciones inseguras), CFG-009 (`b19c4a5`, la UI lee
`modulos_efectivos()` en vez de `config.modulo_*`), CFG-011 (`38e5647`, el
borrado por instancia y por `QuerySet` levanta `ConfiguracionProtegidaError`),
CFG-013/014/015 (`fe5c8de`, diagnóstico fiel + comando sin objetivo ambiguo),
CFG-017 (`7201043`, auditoría CT-01 transaccional). Integrados y revalidados
localmente el 2026-09-17: **CFG-010** (ámbito por sucursal + integridad de fila;
origen `0db8f57`; preflight: backfill legacy + CheckConstraint), **CFG-018**
(borrar logo anterior al reemplazar), **CFG-019** (retirar decoradores sin uso)
y **CFG-020** (validar formato del código de barras del lado config) — los tres
con origen `1a7b767`. **CFG-021** verificado como ya cubierto por los tests de
C03. Quedan
abiertos: **CFG-012** (leer configuración aún puede crearla — el fix vive solo en
`integration/cierre-prod-A05-C03`, no en `develop`), CFG-007 (el pull omite
validadores; C+A) y CFG-008 (controles e-CF sin unidad, diferida). CFG-016
(round-trip BAT→env) es de C01.

**Pendientes de `apps/productos`** (P1 6/8; PRO-018 cerrado):
PRO-009 (HTML y modelo omiten validaciones que la API sí aplica),
**PRO-010 (cambios de precio sin auditoría de dominio — conviene pronto:
un precio es una decisión financiera y hoy no queda registro de que
ocurrió)**, PRO-011, PRO-012 (ciclo de vida de imágenes no atómico),
PRO-013, PRO-014 (carreras en los generadores de SKU y código de barras),
PRO-015, PRO-016 (el chequeo cloud ocurre antes de autenticar), PRO-017
(impresión sin permiso propio ni cuota), PRO-019 a PRO-022.

**Pendientes de `apps/clientes`** (P1 cerrados, más CLI-014/020):
CLI-006 (aislamiento por negocio en base compartida — contenido por
DB-per-tenant), CLI-008 (escrituras locales sin `full_clean`), CLI-009
(cédula/RNC sin formato canónico), CLI-010 (identidad de origen a medias),
CLI-011 (mutaciones sin auditoría), CLI-012 (sucursal en la auditoría de
límite — hecho en el toggle, falta en la edición), CLI-013 (`DELETE` físico
da 500 con referencias), **CLI-015 (la ruta de detalle apunta a una
plantilla inexistente: 500 garantizado)**, CLI-016 (N+1 financieros),
CLI-017, CLI-018, CLI-019, CLI-021.

**Pendientes posteriores a A02 de `apps/negocios`:** ninguno de código en el
inventario A02. Persisten los preflights operativos de identidad/self-row para
A08; no se resuelven adjudicando o fusionando filas automáticamente.

**Pendientes posteriores a A02 de `apps/auditoria`:** AUD-002-ULTIMA continúa
como riesgo aceptado: sin WORM no se promete detectar el borrado externo de la
última fila. La adopción de CT-01 por cada productor se acredita en su bloque.

**Pendientes posteriores a A02 de `apps/usuarios`:** USR-012 pasa a A03/CT-02
para unificar privilegios y revocaciones; USR-014 permanece en A08 porque exige
validar la cadena real de proxies. La pantalla portal de gestión/clave sigue en
C04; A02 publicó el servicio/comando backend y separó las credenciales.

---

## 🧹 Higiene del repo

- [ ] `config/settings_auditoria_sucursales_temp.py` sin trackear y con nombre
      de temporal: decidir si se versiona o se borra.
