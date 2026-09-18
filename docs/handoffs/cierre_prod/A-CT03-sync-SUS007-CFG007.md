# A / CT-03 sync — SUS-007 y CFG-007

Estado: **implementado y validado localmente; pendiente de revisión e
integración.** Fecha: **2026-09-18**. No se hizo push, merge, despliegue ni
operación sobre datos reales.

## Base y resultado

- Base aislada: `integration/cierre-prod-A06-C04-C05@dfb1dfc7f5866d05e4bd8d1d1df2baa70c24dc51`.
- Rama/worktree: `codex/cierre-prod-CT03-sync` en
  `C:\Proyectos\pos_fifo_system_ct03_sync`.
- Resultado: `1019500` (`feat(sync): cerrar SUS-007 y CFG-007 CT-03`).
- No hay migraciones nuevas ni cambios en `develop`.

## Alcance entregado

1. **SUS-007.** `GET /api/v1/sync/configuracion/` conserva la respuesta lista
   y los campos `modulo_*` para POS instalados. Cuando la sucursal tiene
   negocio, sus valores se derivan de
   `apps.suscripciones.engine.modulo_activo(..., negocio=, sucursal=)`;
   sin negocio se conservan los flags legacy crudos.
2. **Cursor incremental.** La marca publicada es la revisión efectiva de la
   configuración: fila `ConfiguracionNegocio`, suscripción/plan, creación de
   overrides y CT-01 de los cambios oficiales de plan y override de negocio.
   Seleccionar un plan o crear/editar/eliminar un `NegocioModulo` por su API
   vuelve a exponer el singleton al cliente que ya había llegado al cursor.
   No se agregó schema, cursor ni evento de protocolo.
3. **CFG-007.** El receptor permite la misma allowlist parcial de antes,
   valida tipos con DRF y valida la proyección completa con `full_clean()` antes
   de mutar. Un error permanente no entra en `DiferidoSync`: el cursor queda
   bloqueado y la siguiente respuesta cloud válida puede resolverlo sin que un
   payload antiguo se reaplique después.

Archivos de código: `apps/sync/configuracion.py`, `apps/sync/engine.py`,
`apps/api/views/sync.py`. También se actualizaron los mapas de `apps/sync` y
`apps/api`, la entrada CT-03 de `CONTRATOS.md`, y pruebas focales propias.

## Evidencia local

Entorno aislado: `.venv` propio (Python 3.11.14, Django 5.2.17),
`DB_NAME=pos_fifo_ct03_sync` y
`TENANT_TEST_DB_NAMESPACE=ct03_sync_20260918`. El runner creó y destruyó la BD
temporal `test_pos_fifo_ct03_sync`; no se migró ni se escribió una instalación
operativa.

```powershell
.\.venv\Scripts\python.exe manage.py test `
  apps.sync.tests.test_configuracion_cfg012 `
  apps.api.tests.test_ct03_sync `
  apps.api.tests.test_sync_extended `
  apps.api.tests.test_suscripciones_admin `
  apps.suscripciones.tests.test_ct03_contrato `
  --settings=config.settings_development --noinput --verbosity 1
```

Resultado: **35 OK** en 13.194 s. Cubre payload inválido sin mutación, sin
diferido y con cursor bloqueado; payload parcial compatible; flags efectivos
por negocio/sucursal; fallback legacy; y el recorrido incremental real tras
cambiar plan y override mediante las rutas oficiales.

También pasaron:

```powershell
.\.venv\Scripts\python.exe manage.py check --settings=config.settings_development
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run `
  --settings=config.settings_development
git diff --check
```

`makemigrations` informó **No changes detected**. Emitió un `RuntimeWarning`
al comprobar el historial porque la BD base exclusiva no se creó (solo se usó
la BD temporal del runner); no fue un error de migración ni produjo escritura.

## Límite, rollback y siguiente paso

- La creación de `SucursalModuloOverride` ya participa en la revisión por
  `fecha_creacion`; su edición/borrado directo por Admin/ORM no emite CT-01 ni
  tiene marca durable. Antes de ofrecer esa mutación cloud hay que auditarla o
  acordar una revisión aditiva. No se asumió un protocolo nuevo aquí.
- Si un plan vuelve `modulo_ecf=True`, CFG-007 exige que la configuración local
  ya tenga `emisor_activo`; un POS sin ese prerequisito queda bloqueado en vez
  de almacenar una configuración fiscal inválida. El aprovisionamiento de ese
  emisor no pertenece a este bloque.
- Rollback local: revertir `1019500`; no hay migración ni estado remoto que
  revertir.
- Siguiente gate: revisión contra `dfb1dfc`, repetir esta matriz y el smoke
  HTTP con cloud/POS desechables. Integrar solo en un candidato controlado;
  no mover `develop`. C04 p6 sigue independiente.
