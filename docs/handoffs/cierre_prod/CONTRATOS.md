# Registro de contratos del cierre de producción

Propietario: **A / Codex**. Última actualización: **2026-09-11**.

Este archivo es la única fuente para nombres y semántica compartidos. Un
contrato publicado no afirma que el backend ya lo implemente: la columna
`Implementación` lo distingue. Cambiar semántica exige nueva revisión y repetir
las pruebas consumidoras.

## Registro

| Contrato | Revisión | Productor | Consumidores | Interfaz | Implementación | Commit |
| --- | --- | --- | --- | --- | --- | --- |
| CT-01 auditoría/identidad | `audit.event.v1` | A02 | C01-C05 y dominios A | **PUBLICADA** | **IMPLEMENTADA / A02 CERRADO** | `cd8a3b4`, `583863f`, `f0a255c`, `bb7f774` |
| CT-02 permisos/capacidades | `rbac.capabilities.v1` + `rbac.sync.v2` | A03 | C02-C05/POS/frontend | **PUBLICADA** | **PRODUCTOR A03 + CONSUMIDORES C05 INTEGRADOS/VALIDADOS** | `3e6cec1`, `60c6dbc`, árbol `9ff61c2` |
| CT-03 configuración efectiva | por proponer | C03; A integra settings/sync | A01/A04 y C | PENDIENTE | PENDIENTE | — |
| CT-04 maestros offline | por publicar | A05/A06 | C04/C05 | PENDIENTE | PENDIENTE | — |
| CT-05 artefacto/actualización | por cerrar | A01/A08 + C01/C06 | ambos | EN_CURSO | PENDIENTE | — |

Las interfaces CT-01/02 se publicaron temprano para permitir trabajo paralelo.
**Ninguna integración consumidora se considera terminada hasta consumir el
commit de implementación de A02/A03 y repetir sus tests.**

## CT-01 — auditoría e identidad (`audit.event.v1`)

### Invariantes

- Un evento exitoso y la mutación que describe se escriben en la **misma BD y
  transacción**. `using` nunca cae implícitamente a `default` bajo tenancy.
- Actor, tenant, sucursal, canal y objeto se congelan como identidad histórica.
  Los textos de presentación son snapshots; no son claves.
- Bajo impersonación, `actor.ref` identifica al usuario operativo del tenant y
  `actor.impersonator_ref` identifica por separado a la `Identity` global.
- `before`/`after` representan estados del dominio, no el objeto ya mutado en
  ambos lados. `result` es `SUCCEEDED`, `DENIED` o `FAILED`.
- Contraseñas, hashes de contraseña, tokens, cookies, claves, secretos,
  `Authorization`, datos VAPID y credenciales de BD se redactan recursivamente
  antes de persistir. Un productor no puede desactivar la redacción.
- La correlación es propagable entre request, mutación, outbox y receptor sync.
  Una clave idempotente no reemplaza al `event_id` del registro de auditoría.
- Un fallo al auditar una mutación exitosa revierte esa mutación. Excepciones:
  intentos rechazados/fallidos se registran después del rollback y el logout
  invalida primero la sesión; si su log falla, la sesión continúa cerrada.
- La retención consultable local mínima es 90 días. No hay purga ni WORM nuevo
  en este release y no se promete detectar el borrado externo de la última fila.

### Interfaz Python reservada

El productor usará `apps.auditoria.services.registrar_mutacion`:

```python
registrar_mutacion(
    *, accion, actor, entidad, antes, despues, resultado,
    canal, tenant=None, sucursal=None, correlacion_id=None,
    idempotencia_key=None, metadata=None, error=None, using,
) -> Auditoria
```

Los códigos nuevos de `accion` usan tres o más segmentos en minúscula
(`dominio.recurso.operacion`) y caracteres `[a-z0-9_]`; por ejemplo,
`configuracion.sucursal.actualizada`. El productor conserva el código después
de publicarlo. Los valores mayúsculos de `Auditoria.TipoAccion` son solo
compatibilidad legacy y no se usan en productores CT-01 nuevos.

`using` es obligatorio. Para una entidad persistida debe coincidir con
`entidad._state.db`; para eventos de control-plane debe ser `default`. A02 puede
añadir parámetros opcionales, pero no cambiar estos nombres en v1.

El adaptador histórico `Auditoria.registrar(...)` se conserva durante la
migración de productores. No es el contrato para código nuevo de C; no garantiza
por sí solo canal, identidad estable, correlación o transacción correcta.

### Esquema transportable

```json
{
  "schema_version": "audit.event.v1",
  "event_id": "uuid",
  "occurred_at": "RFC3339 con offset",
  "recorded_at": "RFC3339 con offset",
  "actor": {
    "ref": "identidad opaca e inmutable",
    "kind": "USER|SERVICE|SYSTEM",
    "username_snapshot": "texto",
    "display_snapshot": "texto",
    "impersonator_ref": null
  },
  "tenant": {"key": "tenant estable"},
  "branch": {"ref": "identidad opaca", "code_snapshot": "01"},
  "channel": "POS_LOCAL|PORTAL_API|SYNC|COMMAND|SYSTEM",
  "action": "codigo estable",
  "entity": {
    "type": "app.Model",
    "ref": "identidad opaca e inmutable",
    "display_snapshot": "texto"
  },
  "before": {},
  "after": {},
  "result": "SUCCEEDED|DENIED|FAILED",
  "correlation": {
    "correlation_id": "uuid o null",
    "idempotency_key": "texto o null"
  },
  "metadata": {},
  "error": {"code": "estable", "message": "redactado"}
}
```

`tenant`/`branch` pueden ser `null` solo cuando el evento genuinamente pertenece
al control plane o al sistema. “No pude resolverlos” es error de contexto, no
permiso para guardar un evento sin scope.

Fixture canónico:
[`fixtures/ct01_audit_event_v1.json`](fixtures/ct01_audit_event_v1.json).

### Errores y compatibilidad

| Código | Condición | Efecto |
| --- | --- | --- |
| `AUDIT_CONTEXT_INVALID` | Falta/mismatch de BD, tenant, sucursal o entidad. | La mutación exitosa se revierte. |
| `AUDIT_IDENTITY_UNRESOLVED` | Actor/objeto no tiene identidad estable cuando es obligatoria. | La mutación se revierte; el caller no inventa una PK global. |
| `AUDIT_REDACTION_FAILED` | No se puede producir payload seguro. | No se persiste material sensible; la mutación se revierte salvo logout. |
| `AUDIT_WRITE_FAILED` | Falló la escritura de auditoría. | Misma política transaccional; señal sanitizada. |

Los lectores antiguos continúan viendo columnas históricas (`accion`,
`descripcion`, `datos_anteriores`, `datos_nuevos`, `exito`, actor snapshot). La
migración A02 rellena solo datos derivables sin inventar tenant/sucursal/actor.

### Aceptación mínima

- Rollback del dominio también revierte auditoría y viceversa, en `default` y
  una BD tenant real.
- Dos tenants con PK iguales producen `actor.ref`/`entity.ref` distintos.
- Renombrar o borrar el objeto no cambia su identidad/snapshot histórico.
- Redacción adversarial en diccionarios/listas anidados y excepciones.
- Logout cierra sesión aunque el writer falle.
- Consulta scoped de 90 días usa índices y no cruza sucursales/tenants.

Implementación y pruebas focales publicadas en
[`A02-CT01-temprano.md`](A02-CT01-temprano.md). La prueba PostgreSQL con dos BDs
tenant físicas se entrega con TEN-016 al cierre de A02.

## CT-02 — permisos y capacidades

CT-02 separa tres conceptos: catálogo de códigos, presets de roles y decisión
efectiva. El frontend refleja la decisión; el servidor siempre vuelve a
autorizar la acción.

### Interfaz server-side estable

```python
usuario.tiene_permiso(codigo, sucursal=<Sucursal>) -> bool
permisos_de_usuario(usuario, sucursal=<Sucursal>|None|TODAS) -> set[str]
```

- `sucursal=None` significa **solo asignaciones globales**.
- `TODAS` sirve únicamente para construir menús/resúmenes (“puede en algún
  lugar”); no se usa para autorizar una mutación concreta.
- Código fuera de `apps.permisos.catalogo` siempre deniega.
- Una ruta protegida declara un código de catálogo; ocultar el botón no es
  enforcement. DRF usa `requiere_permiso`, HTML/JSON los decoradores comunes.
- Módulo/entitlement y permiso son gates ortogonales: ambos deben aprobar.
- Presets de sistema son versionados; seed repetido no pisa rol custom ni
  revive asignaciones revocadas.

### Payload de capacidades para portal/POS

`/api/v1/auth/perfil/` y el login conservan los campos actuales `permisos` y
`modulos`, y añaden el envelope:

```json
{
  "rbac": {
    "schema_version": "rbac.capabilities.v1",
    "subject_ref": "identidad opaca",
    "tenant_key": "demo",
    "scope": {"branch_code": null, "kind": "ANY_FOR_DISPLAY"},
    "catalog_revision": 1,
    "assignments_revision": 17,
    "permissions": ["productos.ver"],
    "modules": ["inventario"]
  }
}
```

La lista del perfil es pista de navegación. Antes de confirmar una operación,
el endpoint reevalúa permiso, módulo, tenant, sucursal, usuario/rol activos y
revisión relevante.

Fixture canónico:
[`fixtures/ct02_capabilities_v1.json`](fixtures/ct02_capabilities_v1.json).

### Contrato de revocación cloud → POS (`rbac.sync.v2`)

Se mantienen `/api/v1/sync/roles/` y `/api/v1/sync/asignaciones/`. El POS nuevo
solicita `X-RBAC-Schema: rbac.sync.v2` con `snapshot=full`. Sin ese header, el
servidor conserva la lista legacy; un POS nuevo también acepta una lista de un
cloud anterior. V2 es aditivo sobre `slug`, `usuario_username`, `rol_slug`,
`sucursal_codigo`, `activo`, `fecha_modificacion` y `cursor_id`.

Respuesta de roles:

```json
{
  "schema_version": "rbac.sync.v2",
  "snapshot_complete": true,
  "tenant_key": "demo",
  "revision": 17,
  "roles": [{
    "cloud_id": "uuid", "revision": 4, "slug": "cajero",
    "active": false, "permission_codes": [], "deleted_at": "RFC3339"
  }]
}
```

Respuesta de asignaciones para el token de sucursal:

```json
{
  "schema_version": "rbac.sync.v2",
  "snapshot_complete": true,
  "tenant_key": "demo",
  "scope": {"branch_ref": "uuid", "branch_code": "01"},
  "revision": 21,
  "assignments": [{
    "cloud_id": "uuid", "revision": 9,
    "user_ref": "uuid", "usuario_username": "ana",
    "role_cloud_id": "uuid", "rol_slug": "cajero",
    "branch_ref": "uuid", "sucursal_codigo": "01",
    "active": false, "deleted_at": "RFC3339"
  }]
}
```

Reglas obligatorias:

1. `cloud_id` es inmutable. Cambiar usuario/rol/sucursal equivale a revocar la
   asignación anterior y crear otra dentro de una transacción.
2. Borrar es baja lógica versionada. Reactivar un rol no reactiva asignaciones.
3. El POS aplica `active=false` **antes** de validar permisos nuevos desconocidos;
   una revocación nunca queda diferida por una capacidad que el POS viejo ignora.
4. Un tombstone compatible no necesita códigos nuevos. Las claves legacy se
   conservan hasta retirar la flota vieja.
5. Ausencia solo revoca cuando `snapshot_complete=true`, la respuesta terminó
   correctamente y pertenece al mismo tenant/sucursal. Una página parcial,
   error o filtro incremental no es snapshot.
6. Revisiones son monotónicas por entidad. Replay de revisión igual es
   idempotente; una revisión menor se ignora y queda diagnosticada.
7. El catálogo puede contener códigos desconocidos para un POS antiguo, pero
   nunca debe bloquear bajas de rol/asignación ni el avance de otros recursos.
8. Antes de aplicar o reconciliar, el POS compara `tenant_key` con el contexto
   técnico activo (fallback `Negocio.slug` en row-level) y, para asignaciones,
   exige el mismo `scope.branch_code` de la instalación.

La negociación quedó implementada de forma aditiva en A03. No se retira ninguna
clave legacy ni `_pull_legacy` durante esta transición.

### Errores HTTP

| HTTP / código | Uso |
| --- | --- |
| `401 authentication_required` | Falta/expiró identidad. |
| `403 permission_denied` | Identidad válida sin permiso/módulo/scope; incluir `required` y `scope`, no datos sensibles. |
| `409 rbac_revision_conflict` | Cliente intenta escribir/resolver contra revisión obsoleta. |
| `400 unknown_permission_code` | Mutación administrativa trae un código fuera del catálogo. |

### Aceptación mínima

- Matriz acción × tenant × sucursal × global/custom/system role.
- Mover, borrar y reactivar rol/asignación; revocación gana ante duplicados.
- Cloud nuevo contra POS viejo y POS nuevo contra cloud viejo.
- Permiso nuevo desconocido no bloquea `active=false` ni el cursor completo.
- Snapshot parcial/fallido no revoca ausentes.
- Seed repetido preserva customizaciones y revocaciones.
- Remover bypass `ADMIN` solo después de preflight que evita lockout.

## Extensión A04 — transporte financiero durable

Implementación productora/consumidora: `be15ea0`. Es una extensión aditiva del
batch existente de eventos; no crea CT-04 ni adelanta la cola de maestros A05.

### Push y ACK compatibles

- Cada elemento puede agregar `event_id` UUID. Es obligatorio en un POS A04 y
  opcional para un POS legacy. El cloud nuevo sigue aceptando elementos sin él.
- Las reservas únicas son `(sucursal, event_id)` y
  `(sucursal, hash_payload)`; el tenant ya está determinado por el alias de BD.
  Misma identidad/hash solo es `DUPLICADO` si tipo, hash y payload coinciden.
- `detalle[]` refleja `event_id` cuando vino en la solicitud y conserva `hash`.
  `CONFIRMADO`/`DUPLICADO` son terminales; `ERROR` siempre se reintenta. Los
  códigos nuevos son `EVENT_SCOPE_MISMATCH` y `EVENT_IDENTITY_CONFLICT`.
- Un POS A04 tolera un ACK legacy sin `event_id` mediante el hash scopeado. Un
  cloud A04 tolera un POS legacy sin `event_id`; le asigna identidad interna.

El POS persiste `EN_VUELO`, `lease_id` y `lease_expires_at` antes del HTTP. Solo
el dueño del lease puede aplicar el ACK. El valor default es 300 segundos y se
configura con `SYNC_LEASE_SECONDS`; leases vencidos se reclaman sin descartar el
evento ni cambiar su identidad.

### Pull diferido

`DiferidoSync` guarda tenant técnico, sucursal, tabla, cursor, identidad legible,
hash y payload. Un cursor solo atraviesa el elemento tras aplicar o confirmar
esa escritura local. Los pendientes se reintentan dentro de la misma
transacción que cambia `PENDIENTE` a `RESUELTO`; cualquier crash revierte el
efecto y conserva la fila. Un ciclo con pendientes es `PARCIAL`.

### Sonda `sync.reconciliation.v1`

`POST /api/v1/sync/reconciliacion-eventos/` requiere token de sucursal y no
escribe. Solicitud:

```json
{
  "schema_version": "sync.reconciliation.v1",
  "eventos": [{
    "event_id": "uuid",
    "tipo_evento": "VENTA_CREADA",
    "payload": {},
    "hash_payload": "sha256",
    "objeto_referencia": "SD-001-V20260911-0001"
  }]
}
```

La respuesta fija `scope.tenant_key`, `scope.branch_code`, `scope.branch_ref` y
clasifica cada fila como `DUPLICADO_REAL`, `DIVERGENCIA_CONTENIDO`,
`HECHO_SIN_EVENTO_CLOUD`, `FALSO_ACK`, `NO_VERIFICABLE` o `AMBITO_INVALIDO`.
Solo `FALSO_ACK` y `HECHO_SIN_EVENTO_CLOUD` proponen reenvío dirigido.

`reparar_bug_k` es dry-run por defecto. Una escritura exige simultáneamente
`--ejecutar`, un archivo `sync.bug_k.repair_plan.v1` revisado y su digest con
`--confirmar-plan`; revalida cloud y precondiciones locales, guarda antes/después
y rechaza repetir el mismo plan. A04 no autoriza ejecutar esos flags en clientes.

## CT-03 — reserva para configuración efectiva

C03 propone en su handoff: firma del resolutor, precedencia, validaciones,
errores, revisión y fixtures. Restricciones ya fijadas por A00/A01:

- variables de proceso > archivo indicado por `POS_ENV_FILE` > defaults;
- `load_dotenv(..., override=False, encoding="utf-8")`;
- C01 registra `POS_ENV_FILE` como ruta absoluta; comillas exteriores se
  normalizan antes de resolverla;
- si `POS_ENV_FILE` está declarada vacía, relativa, ausente, no apunta a un
  archivo o no puede leerse como UTF-8, el arranque falla con
  `ImproperlyConfigured` y la ruta/variable exacta, nunca con fallback oculto;
- si `POS_ENV_FILE` no está declarada, `deploy/env_cliente.env` conserva
  compatibilidad como default opcional; su ausencia devuelve `None`;
- el cargador devuelve el `Path` absoluto efectivamente leído o `None`, no
  registra valores ni secretos;
- BD como verdad dinámica y memo solo por request, sin Redis;
- leer no crea configuración;
- Codex integra `config/**`, routers/settings y `apps/sync`.

## CT-04 — reserva para maestros offline

A05/A06 publicarán UUID estable, revisión/CAS, outbox local separado de hechos
financieros, ACK/retry, conflicto y resolución. No se acepta last-write-wins,
proxy online ni filtro de pull que omita inactivos.

## CT-05 — reserva para artefacto y actualización

A01 fija runtimes/locks/imagen; C01 propone paquete/wheelhouse/preflight Windows;
A08/C06 cierran el manifiesto con SHAs y hashes. El artefacto aprobado no se
recompila para producción.

Baseline consumible desde A01:

- POS/paquete: CPython 3.11.14 x64 + `requirements.txt` con hashes;
- desarrollo Windows: mismo runtime + `requirements-dev.txt`;
- cloud: CPython 3.12.14 x64 + `requirements_cloud.txt`;
- CI: CPython 3.12.14 x64 + `requirements_ci.txt`;
- instalacion siempre con pip 26.0.1 y `--require-hashes`;
- inputs humanos, regeneracion, digest de la imagen generadora y contrato de
  wheelhouse en `requirements/README.md`;
- C01 registra SHA-256 de cada wheel y del lock, y no incluye `.env`, dumps ni
  credenciales en el paquete.

## Propiedad de archivos y fixtures compartidos

| Superficie compartida | Escritor | Consumidor / regla |
| --- | --- | --- |
| Este archivo, `INVENTARIO.md`, fixtures `ct01_*`/`ct02_*` | A | C propone cambios en su handoff; no edita en paralelo. |
| Futuro fixture CT-03 | C lo propone como `C03-*`; A lo integra aquí o en `config/**` | Un solo SHA canónico antes de consumir. |
| Futuro fixture CT-04 | A | C04/C05 consumen el commit integrado. |
| `config/**`, `apps/api/urls.py`, routers/settings globales | A | C solicita firma/ruta. |
| `apps/api/**` auth, RBAC, maestros, sync, sucursales, notificaciones | A | Excepciones de C son solo las listadas en el plan maestro. |
| `apps/auditoria`, `permisos`, `usuarios`, `negocios`, `tenancy`, `sync` | A | C integra productores/gates a través de CT-01/02. |
| `apps/common/pdf/**`, impresión e imágenes utilitarias | C | A integra hooks de Producto/Cliente. |
| `templates/base.html`, navegación y estáticos globales | C | A solicita enlaces; no hay RBAC paralelo en JS. |
| `requirements*`, locks, Docker, workflows, `infra/**` | A | C publica solicitudes de dependencias; no edita esos archivos. |
| Workflows frontend | A | C posee resto del frontend. |
| Tests/migraciones | Dueño del dominio | Fixture compartido se asigna aquí antes de editar. |

Archivo no listado o frontera dudosa: se asigna en este registro antes de que
alguien lo modifique.
