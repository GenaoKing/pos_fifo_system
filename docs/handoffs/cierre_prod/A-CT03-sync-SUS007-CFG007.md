# A / CT-03 sync — SUS-007 y CFG-007

> Actualización 2026-09-24: CT-03 y SUS-016 están integrados en el candidato
> consolidado. La espera de revisión/merge descrita abajo pertenece a la entrega
> original. Evidencia conjunta y siguiente gate: `INTEGRACION-TOTAL-2026-09-24.md`.

Estado: **implementado y validado localmente; pendiente de revisión e
integración.** Fecha: **2026-09-18**. No se hizo push, merge, despliegue ni
operación sobre datos reales.

## Base y resultado

- Base aislada: `integration/cierre-prod-A06-C04-C05@dfb1dfc7f5866d05e4bd8d1d1df2baa70c24dc51`.
- Rama/worktree: `codex/cierre-prod-CT03-sync` en
  `C:\Proyectos\pos_fifo_system_ct03_sync`.
- Resultados:
  - `1019500` (`feat(sync): cerrar SUS-007 y CFG-007 CT-03`).
  - `f83f67d` (`feat(suscripciones): reportar checkpoint parcial de sync`).
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
4. **SUS-016.** `python manage.py verificar_suscripciones_sync` entrega el
   checkpoint `suscripciones.sync-checkpoint.v1` de la instalación actual. La
   foto cubre catálogo/presets, cada negocio y su suscripción, configuraciones
   legacy sin sucursal y señales ya existentes del sync (diferidos, cursores
   bloqueados y último ciclo). La huella SHA-256 es estable si no cambia el
   estado material, para archivar o comparar ejecuciones. `--json` conserva
   esa forma y `--strict` da salida no cero ante `PARCIAL`; ambos son **solo
   lectura** y no llaman `bootstrap_suscripciones`, `sync_modulos`, reintentos
   ni reparaciones.

Archivos de código: `apps/sync/configuracion.py`, `apps/sync/engine.py`,
`apps/api/views/sync.py`, `apps/suscripciones/checkpoint.py` y el comando
`verificar_suscripciones_sync`. También se actualizaron los mapas de `apps/sync`,
`apps/api` y `apps/suscripciones`, la entrada CT-03 de `CONTRATOS.md`, y pruebas
focales propias.

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

Resultado inicial CT-03: **35 OK** en 13.194 s. Cubre payload inválido sin
mutación, sin diferido y con cursor bloqueado; payload parcial compatible;
flags efectivos por negocio/sucursal; fallback legacy; y el recorrido
incremental real tras cambiar plan y override mediante las rutas oficiales.

Resultado extendido SUS-016 (misma `.venv`, `--keepdb` sobre la base temporal):
**59 OK**. Incluye cuatro regresiones del checkpoint: foto estable sin escrituras,
detección simultánea de bootstrap/cursor/diferido/ciclo parcial, JSON +
`--strict` sin mutar, y plan personalizado que no se etiqueta falsamente como
drift. También pasaron `manage.py check` y `makemigrations --check --dry-run`.

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
- Rollback local: revertir `f83f67d` retira solo el checkpoint SUS-016;
  revertir además `1019500` retira CT-03. No hay migración ni estado remoto que
  revertir.
- Siguiente gate: revisión contra `dfb1dfc`, repetir esta matriz y el smoke
  HTTP con cloud/POS desechables. Integrar solo en un candidato controlado;
  no mover `develop`. C04 p6 sigue independiente.
