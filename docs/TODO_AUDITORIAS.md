# TODO — pendientes de las auditorías

Lista accionable. El contexto de cada punto está en
[ESTADO_AUDITORIAS.md](ESTADO_AUDITORIAS.md) y en el documento de auditoría del
módulo. Marcar `[x]` al cerrar.

Última actualización: **2026-08-27**

---

## 🔴 Bloqueantes de seguridad — privilegio que persiste

Lo único de esta lista donde **hoy hay un permiso vivo que alguien cree
retirado**. Recomendado tomar a continuación.

- [ ] **PER-006 — Mover una asignación no revoca la anterior en el POS local.**
      El payload cloud→local identifica la asignación por
      `usuario_username + rol_slug + sucursal_codigo`, y la API permite cambiar
      esos tres campos. La nueva relación baja a la sucursal; la anterior queda
      activa **indefinidamente**.
      *Arreglo:* identidad cloud inmutable en la asignación + tratar el cambio
      de terna como revoke-old + create-new en una transacción.
      *Contención barata:* hacer inmutables esos tres campos y exigir
      soft-delete + alta nueva.
- [ ] **PER-007 — Borrar un rol custom no se propaga.** La API hace
      `instance.delete()` físico; el endpoint de sync solo emite filas
      existentes y `_pull_roles()` solo hace upsert, nunca reconcilia ausencias.
      El rol y sus asignaciones siguen activos en cada sucursal.
      *Arreglo:* soft-delete versionado o ledger de tombstones, más una
      reconciliación completa periódica.

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

- [ ] **CAJA-002 — ¿Una tienda sin caja abierta puede cobrar en efectivo?**
      Hoy: sí, y el pago queda sin turno (distinguible). Rechazarlo es un `if`
      en `_resolver_turno_caja` (`apps/ventas/services/ventas_service.py`).
- [ ] **CXC-006 — ¿Qué pasa al anular una venta con abonos aplicados?**
      Hoy: se bloquea con 409 y el operador revierte los abonos a mano.
      Alternativas: reversa automática LIFO con egreso de caja, o saldo a favor.
- [ ] **RPT-004 — ¿Cuándo queda cerrado contablemente un día?**
      Hoy: el resumen nace BORRADOR y se recalcula; `--finalizar` lo congela.
      Falta decidir si debe finalizar solo cuando no queden turnos abiertos.
- [ ] **RPT-005 — Hora y zona del cierre automático.**
      `instalar_cierre.ps1` está roto a propósito: apunta a un `.bat` ausente,
      usa NSSM autoarranque para una tarea one-shot, y dice 7 PM mientras el
      modelo documenta 10 PM. El comando ya es correcto; falta el launcher real
      y la hora acordada.
- [ ] **¿Backfill de datos históricos?** Tres posibles, ninguno hecho:
      `turno_caja` en pagos viejos, `sucursal` en cierres diarios,
      conciliación de movimientos de inventario duplicados.

---

## 🟡 Infraestructura y despliegue

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
- [ ] **Matriz PostgreSQL multi-DB en CI** (TEN-016). Único hallazgo de tenancy
      sin corregir; requiere levantar dos bases en el pipeline.
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

- [ ] **Claim durable del push de sync** (`IN_FLIGHT` + lease): el claim local
      no sobrevive a un crash a mitad de envío.
- [ ] **Cola durable de diferidos** en sync: un ítem diferido congela la marca
      de agua.
- [ ] **`_pull_legacy`** sigue existiendo como fallback para clouds pre-Fase 2.
- [ ] **`CheckConstraint` de respaldo** en ventas e inventario: las invariantes
      de importes y cantidades se validan solo en la aplicación.
- [ ] **Idempotencia concurrente del cobro CxC**: falta el test de N reintentos
      con la misma clave.
- [ ] **`_puede_anular` usa el rol legacy** (`ADMIN`/`SYSADMIN`) en vez de RBAC.
- [ ] **Scope por sucursal en los gates de inventario**: `tiene_permiso` se
      llama sin sucursal en varios puntos de esa app. Con el contrato nuevo del
      motor (PER-003) esos gates ahora consultan solo asignaciones globales —
      correcto pero más restrictivo de lo que probablemente se quiso.
- [ ] **Identidad compuesta en el cloud** para `_handler_venta_creada`.
- [ ] **Auditoría de mutaciones API bajo tenancy**: `SesionImpersonacion`
      registra el acceso, no cada mutación de la sesión.
- [ ] **Retirar el bypass de `ADMIN`** en `es_acceso_total`. Exige migrar antes
      a cada admin a asignaciones explícitas, con comprobación previa de
      lockout. Ya está acotado: no aprueba códigos inexistentes ni capacidades
      del operador SaaS.

---

## 🔵 Presentación y rendimiento

- [ ] **Paginación real** en cartera CxC (corta a 300) e historial de turnos
      (corta a 50, **sin avisar**). El inventario ya declara `productos_ocultos`.
- [ ] **Cerrar el último tramo de AUD-002.** El historial ya es append-only
      contra la aplicación, y una edición externa es **detectable** por el
      hash de cada fila. Lo que falta: borrar la ÚLTIMA fila no deja hueco de
      secuencia. Lo cerraría una cadena de hashes (obliga a serializar cada
      INSERT de auditoría — caro en el camino de una venta) o, mejor, una
      **exportación periódica a almacenamiento WORM**, que además protege
      contra el borrado total de la tabla.
- [ ] **Chart.js desde CDN** sin integridad ni fallback local
      (`templates/reportes/on_demand.html`). En un POS sin Internet estable los
      gráficos fallan aunque los datos estén.

---

## ⚪ Auditorías escritas pero sin procesar

Existen y describen hallazgos reales; nadie las verificó ni corrigió.

- [ ] `apps/cotizaciones` — 18 hallazgos
- [ ] `apps/common` — 15 hallazgos
- [ ] `apps/api` — 8 hallazgos

**Pendientes de `apps/permisos`** (P1 cerrados; el resto sin entrar):
PER-012 a PER-018 (P2) y PER-019 a PER-021 (P3).

**Pendientes de `apps/suscripciones`** (P1 5/10):
SUS-006 (CxC y reportes on-demand sin enforcement HTML de módulo), SUS-007
(plantillas y sync leen flags legacy, servicios leen el entitlement),
SUS-008 (el bootstrap une flags entre sucursales: si A tenía e-CF y B no,
ambas terminan con e-CF), SUS-009 (las configuraciones legacy sin sucursal
se ignoran al migrar), **SUS-010 (los hooks de datos bloqueantes tragan
cualquier excepción: un fallo de base se interpreta como "no hay datos
pendientes" y AUTORIZA la baja — el más barato de los cinco y el más
peligroso)**, SUS-011 a SUS-019.

**Pendientes de `apps/configuracion`** (P1 cerrados):
CFG-006 (combinaciones operativas y fiscales inseguras), CFG-007 (el pull
omite validadores), CFG-008 (controles e-CF sin unidad), CFG-009 (dos
fuentes de verdad entre plantillas y gates), CFG-010, **CFG-011 (la
proteccion contra borrar configuracion es ilusoria: `QuerySet.delete()` no
pasa por el modelo)**, **CFG-012 + CFG-017 (leer configuracion puede
crearla, y ningun cambio deja auditoria de dominio: hoy no se puede
reconstruir quien activo el inventario negativo ni cuando)**, CFG-013,
CFG-014, CFG-015, CFG-016, CFG-018 a CFG-021.

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

**Pendientes de `apps/negocios`** (P1 cerrados, más NEG-010/015):
NEG-006 (tres fuentes de identidad comercial), NEG-007 (ciclo de vida del
tenant sin auditoría), NEG-008 (cascada al borrar un negocio — la mitad de
usuarios ya la cubre USR-003), NEG-009 (`slug` mutable en identidad legacy),
NEG-011 (RNC sin política de unicidad), NEG-012 (escrituras directas evitan
validadores), NEG-013 (autogeneración de slug omitible), NEG-014 (carrera
TOCTOU en el slug), NEG-016, NEG-017.

**Pendientes de `apps/auditoria`** (P1 cerrados, más AUD-007/011/012/014/015/022):
AUD-008 (política de fallo contradictoria), AUD-009 (la anulación registra el
estado nuevo como si fuera el anterior), AUD-010 (acción/nivel/resultado
incoherentes), AUD-013 (excepciones sin redacción), AUD-016
(`registrar_compra()` no serializa su payload), AUD-018 (taxonomía sin
productores), AUD-019 (el visor oculta datos), AUD-020 (sin lifecycle de
retención), AUD-021 (identidad histórica del objeto).

**Pendientes de `apps/usuarios`** (P1 cerrados, más USR-008/009/018):
USR-007 (flujo de provisión de usuarios tenant), USR-010 (`Identity` y
`Usuario` son credenciales independientes), USR-011 (el manager omite
validación), USR-012 (tres fuentes de privilegio), USR-013 (mutaciones sin
auditoría de dominio), USR-015 (`last_login` vs `ultimo_acceso`), USR-016
(unicidad sensible a mayúsculas), USR-017 (sesión sin máximo absoluto),
USR-019 (rutas de desarrollo).

---

## 🧹 Higiene del repo

- [ ] `config/settings_auditoria_sucursales_temp.py` sin trackear y con nombre
      de temporal: decidir si se versiona o se borra.
