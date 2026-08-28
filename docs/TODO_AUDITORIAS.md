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

- [ ] **Backend de caché compartido (Redis) en el cloud.** Sin él el motor de
      permisos funciona correctamente —deja de cachear entre requests— pero paga
      una consulta por request y usuario. Con Redis recupera el caché y la
      invalidación alcanza a los tres workers a la vez.
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

- [ ] `apps/productos` — 22 hallazgos
- [ ] `apps/configuracion` — 21 hallazgos
- [ ] `apps/cotizaciones` — 18 hallazgos
- [ ] `apps/clientes` — 21 hallazgos
- [ ] `apps/negocios` — 17 hallazgos
- [ ] `apps/api` — 8 hallazgos

**Pendientes de `apps/permisos`** (P1 cerrados; el resto sin entrar):
PER-012 a PER-018 (P2) y PER-019 a PER-021 (P3).

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
