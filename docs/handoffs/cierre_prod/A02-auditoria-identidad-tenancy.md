# Handoff A02 — auditoría, identidad, negocios y tenancy

Estado: **VALIDADO para integración local; no publicado en remoto**. Fecha:
**2026-09-10**. No hubo despliegue, migración operativa ni lectura/escritura de
datos de staging o producción.

## SHAs y propiedad

- Entrada: `develop@2618731` (cierre A01 local).
- CT-01 funcional temprano: `cd8a3b4`.
- Handoff temprano CT-01: `abd39d0`.
- Cierre técnico A02: `583863f`.
- Compatibilidad descubierta por suite completa: `f0a255c`.
- Matriz CT-01 exhaustiva y sin comodines inválidos: `bb7f774`.
- Cierre documental: el commit que contiene este archivo.
- Rama/worktree: `codex/cierre-prod-A02` en
  `C:\Proyectos\pos_fifo_system_cierre_codex`.

Codex modificó únicamente superficies A: `apps/auditoria`, `apps/usuarios`,
`apps/negocios`, `apps/tenancy`, autenticación puntual en `apps/api`,
`config/settings.py`, `config/urls.py`, CI y documentación compartida. No tocó
los worktrees/ramas de Claude, frontend, `deploy/**`, Terraform ni staging.

## Resultado consumible

### CT-01 y auditoría

- `registrar_mutacion(..., using=)` implementa `audit.event.v1`: referencias
  estables, tenant/sucursal/canal, before/after, resultado, correlación,
  idempotencia y redacción recursiva.
- Éxito de dominio y evento comparten BD/transacción; un fallo del writer
  revierte ambos. Intentos fallidos se registran después del rollback. Logout
  invalida primero y su auditoría es best-effort.
- Impersonación conserva por separado el actor operativo y
  `impersonator_ref` de la Identity global.
- `productores.py` es la matriz viva: cada acción legacy tiene un productor
  verificable o `SIN_PRODUCTOR`; V1 enumera cada acción concreta y válida, sin
  comodines; el visor no presenta ausencia como cobertura.
- La consulta local tiene índices y rango temporal para al menos 90 días. No se
  agregó purga automática ni WORM y se conserva explícitamente el límite
  AUD-002-ULTIMA.

Claude debe consumir el cierre completo `cd8a3b4` + `583863f` + `f0a255c` +
`bb7f774`; un productor no queda acreditado solo por importar el helper: sus
tests deben demostrar misma transacción, actor real, scope y redacción. Codex no
integra ni limpia el worktree de Claude: C debe hacerlo después de su handoff
local limpio.

### Usuarios, credenciales y sesiones

- `apps.usuarios.services.provisionar_usuario` crea Usuario + AsignacionRol +
  CT-01 en una transacción tenant. `actualizar_usuario` exige actor activo de la
  misma BD, permiso, motivo y campos acotados.
- `provisionar_usuario_tenant` exige actor explícito y obtiene la clave inicial
  desde una variable de entorno; no la imprime.
- `create_human_user` aplica validadores de password y `full_clean`;
  `create_service_user` usa password no utilizable. `create_user` permanece
  como compatibilidad ORM, no como alta humana soportada.
- Username/email quedan canonizados y con unicidad `Lower()`; la migración
  aborta colisiones y nunca renombra identidades.
- Usuario POS e Identity portal tienen secretos y rotaciones independientes.
  `bootstrap_tenant` y `normalizar_import_tenant` rechazan reutilizar la misma
  clave; el flag legado conjunto exige ambos secretos distintos.
- Login local y portal sincronizan `last_login`/`ultimo_acceso`. Cookies y JWT
  (access y refresh) tienen máximo absoluto de 12 horas.
- `/admin/` y las rutas POS/dev no se montan en cloud. Admin local permanece,
  sin delete masivo; el alta de usuarios se hace por servicio/comando.

La UI equivalente y el cambio de password del portal continúan en C04. La
unificación de privilegios/revocaciones de USR-012 continúa en A03/CT-02.

### Negocios y provisioning

- `Negocio.slug` es inmutable; RNC canónico tiene nueve dígitos y unicidad. Los
  servicios de alta/actualización validan autorización, reservan slug con retry
  acotado y persisten CT-01.
- `Tenant.tenant_key/slug/db_name/media_prefix` son identidad inmutable y
  exacta. Las migraciones abortan drift/colisiones; no inventan RNC, reapuntan
  bases, adjudican huérfanos ni fusionan identidades aproximadas.
- `preparar_tenant_provisioning` y `marcar_estado_provisioning` auditan cada
  transición control-plane. Los estados PENDING/DB_READY/SCHEMA_READY/
  TENANT_READY/CONTROL_READY/ACTIVE/FAILED permiten reanudar; cada BD conserva
  su transacción propia y no se promete atomicidad distribuida.
- `verificar_identidad_tenant` compara control plane, self-row y configuración
  sin corregir ni escribir.

### TEN-016

CI define `TENANT_TEST_DB_NAMESPACE` por run/attempt. El gate registra dos
aliases y dos PostgreSQL físicos exclusivos, usa el mismo PK en ambos y prueba
que filas y referencias opacas de auditoría no cruzan bases. El runner destruye
solo los nombres derivados de su namespace.

El gate usa `TEST.MIGRATE=False` deliberadamente para probar routing con el
schema actual; el grafo histórico ya tiene su batería separada. Al intentar el
grafo completo en ambas bases apareció una deuda de propiedad C:
`cuentas_por_cobrar.0002` usa el manager sin
`.using(schema_editor.connection.alias)` y trata de sembrar `default`. Se
registró como `CXC-MIG-ALIAS` para C05/A08; Codex no editó esa migración.

## Esquema candidato (no aplicado)

- `auditoria.0008_auditoria_accion_codigo_and_more` (CT-01, ya en `cd8a3b4`).
- `negocios.0002_*`: RNC canónico y constraints nombre/slug.
- `usuarios.0005_*`: unicidad case-insensitive y rol válido.
- `tenancy.0004_*`: estados/checkpoints y RNC canónico.

Todos los backfills son preflights conservadores: abortan lo ambiguo y no
reescriben identidades. Deben ensayarse en copias durante A08 antes de cualquier
promoción.

## Evidencia ejecutada

Entorno Windows aislado: CPython 3.11.14, Django 5.2.17, PostgreSQL 16 y BD
`pos_cierre_codex`/test derivada.

- Suite completa de apps A02 + refresh: **210 OK, 1 skip esperado** (TEN-016
  opt-in).
- Regresión focal de aceptación: **116 OK**.
- TEN-016 habilitado con namespace `codex_a02_20260910_8`: **1 OK**, creó y
  destruyó `default` de test y dos BDs físicas; consulta posterior: **0** bases
  bajo ese namespace.
- `manage.py check`: 0 issues.
- `manage.py check --settings=config.settings_cloud` dentro de la imagen Linux
  A01: 0 issues.
- `makemigrations --check --dry-run`: `No changes detected`.
- `compileall`: OK; `git diff --check`: sin errores (solo avisos CRLF del
  checkout Windows).
- Suite Django completa, excluyendo e-CF: **1.218 OK, 1 skip esperado** en
  **382.930 s**. La corrida autoritativa creó desde cero y destruyó únicamente
  `test_pos_cierre_codex`.
- e-CF separado: **72 passed en 16.05 s**.
- Una corrida diagnóstica con `--keepdb` encontró el plan semilla ausente en la
  BD conservada y produjo 26 errores de fixture. La reproducción desde cero de
  los 45 casos afectados pasó; esos errores no se contabilizan como evidencia
  verde ni como regresiones de producto.

## Pendientes y límites

- A08 conserva los preflights sobre self-row, RNC, colisiones case-insensitive,
  huérfanos y migraciones en copias. No se consultaron datos reales en A02.
- USR-014 (proxy/IP) sigue A08; AUD-002-ULTIMA sigue diferido por decisión de no
  introducir WORM.
- C04 provee las alternativas visuales cloud; A02 solo cerró `/admin/` y dejó
  servicios/comandos backend autorizados.
- C05/A08 corrigen y prueban `CXC-MIG-ALIAS` antes de exigir migración histórica
  completa dentro del gate físico.

## Rollback

- Revertir `bb7f774`, `f0a255c` y `583863f`, luego `abd39d0`/`cd8a3b4` si
  también se retira CT-01.
- Las migraciones candidatas no se aplicaron; no existe rollback de datos ni de
  infraestructura.
- Las BDs TEN-016 fueron desechables y ya no existen. No se borró ninguna BD
  compartida u operativa.

## Siguiente bloque

A03 parte de este cierre y publica CT-02 (`rbac.capabilities.v1` y
`rbac.sync.v2`) para C02-C05. Conviene abrir un chat nuevo para A03, conservando
este archivo como handoff completo; primero se integra A02 por fast-forward en
el `develop` **local**, sin push ni despliegue.
