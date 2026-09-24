# A06 — cierre de candidato backend/POS

Estado: **CANDIDATO LOCAL COMPLETO EN BACKEND/POS; SIN INTEGRAR, PUSH NI
PUBLICAR**. Fecha: **2026-09-17**. Dueño: Codex.

Este handoff sustituye el alcance «inicial» de
`A06-conflictos-maestros.md`. No declara aprobado el gate A06 integral:
portal C04, selectores comerciales C05 y la prueba HTTP de las dos puntas siguen
siendo gates separados.

## Base y aislamiento

- Base: `integration/cierre-prod-A05-C03@609c98f44efd595e39bb9df128d09e57781c3dfc`.
- Worktree: `C:/Proyectos/pos_fifo_system_a06`.
- Rama: `codex/cierre-prod-A06`.
- Implementación completa: `b25a3b7` (`feat(a06): completar conflictos y estado operativo de maestros`).
- Venv: `.venv` aislado; BD control temporal `pos_fifo_a06` y tenant temporal
  `tnt_a06_verify`. No se leyó ni escribió una BD operativa.

## Entregado

1. CT-04 portal conserva GET/POST con RBAC por fila, cursor opaco, CAS, motivo,
   auditoría y ledger inmutable separado.
2. El cloud devuelve decisiones al POS por
   `GET /api/v1/sync/mutaciones-maestro/resoluciones/` con
   `master.conflict-resolution-sync.v1`, alcance estricto de token/sucursal y
   cursor keyset. El POS valida el envelope y las filas en runtime; schema
   desconocido o replay distinto falla cerrado. La decisión local libera solo
   su bloqueo, sin borrar el veredicto original.
3. `Producto` y `Categoria` ganan motivo/timestamp de inactivación, baja lógica
   y reactivación segura. La categoría no muta ni resucita los flags individuales
   de producto. API y pull transportan inactivos; `?operativo=true|false` sirve
   al listado sin filtrar el sync.
4. El POS local permite mutaciones de maestros offline, exige motivo al dar de
   baja, conserva visibles catálogos/históricos administrativos y muestra la
   superficie `/productos/conflictos/`. El checkout usa la misma regla efectiva
   de vendibilidad y el receptor de ventas conserva hechos/snapshots aunque haya
   conflicto de maestro.
5. `DELETE` API de producto/categoría ahora es baja lógica con motivo, no
   eliminación física; fotos, stock y documentos históricos no se eliminan.

## Migraciones

- `productos.0014_estado_operativo_maestros`.
- `sync.0016_alter_versionmaestro_tabla`.
- Repetición de `migrate_tenants --tenant a06_verify --incluir-inactivos`:
  **1/1 OK**. Verificación física: `tablas_faltantes == []`, existe
  `sync_resolucionconflictomaestro` y existen ambos pares
  `motivo_inactivacion`/`inactivado_at` en las tablas reales `productos` y
  `categorias`.

## Evidencia

Todos los comandos usaron `POS_ENV_FILE` local y `DB_NAME=pos_fifo_a06`.

```powershell
python manage.py makemigrations --check --dry-run --settings=config.settings_development
python manage.py check --settings=config.settings_development
python -m compileall -q apps/api apps/productos apps/sync

python manage.py test apps.productos.tests.test_a06_estado_operativo `
  apps.sync.tests.test_resoluciones_conflicto_a06 `
  --settings=config.settings_development --noinput --verbosity 2
# 8 OK

python manage.py test apps.api.tests.test_sync_resoluciones_a06 `
  apps.sync.tests.test_resoluciones_conflicto_a06 `
  apps.api.tests.test_categoria_viewset `
  apps.api.tests.test_producto_stub_anti_clobber `
  --settings=config.settings_development --noinput --verbosity 2
# 31 OK

python manage.py test apps.api.tests.test_producto_viewset `
  apps.api.tests.test_sync_auditoria `
  --settings=config.settings_development --noinput --verbosity 1
# 29 OK
```

Además se ejecutó serialmente la regresión combinada de mutaciones A05,
conflictos A06, maestros, sync y checkout; solo emitió los warnings esperados de
casos negativos HTTP/RBAC/CAS y terminó sin fallo.

## Límite y siguiente gate

- No se movió `develop`, no hubo push, despliegue ni datos operativos.
- C04 no se fusiona aquí. Debe ejecutar sus gates y una prueba HTTP contra este
  candidato para `GET /api/v1/maestros/conflictos/` y su POST versionado antes
  de integración frontend.
- Los selectores C05 y la aceptación integral portal/POS/cloud siguen fuera de
  este commit; no se marcan como aprobados por las pruebas locales.
- Durante el bootstrap exclusivamente desechable, el seed se detuvo al generar
  un email de servicio inválido para el `tenant_key` permitido con `_`. El
  esquema ya había migrado y la verificación física posterior fue verde. No se
  modificó ese comportamiento ajeno a A06 ni se aplicó reparación sobre datos.
