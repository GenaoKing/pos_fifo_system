# Handoff A03 — RBAC y compatibilidad de revocaciones

- Estado: **REVISION**
- Fecha: **2026-09-11**
- Propietario: **A / Codex**
- Base exacta: `develop@e3635de`
- Rama aislada: `codex/cierre-prod-A03`
- Commit de implementación: `3e6cec1`
- Contratos: `rbac.capabilities.v1` + `rbac.sync.v2`

No hubo push, despliegue, ejecución de launchers ni lectura/escritura de datos
operativos. A03 no integró ni modificó la rama o el worktree de Claude. Durante
esta validación, otro flujo avanzó el `develop` compartido hasta `6ee21b8`; esa
deriva concurrente no forma parte de los commits A03.

## Alcance cerrado

A03 cierra PER-006, PER-007, PER-012 y PER-014–021, además de la parte RBAC de
USR-012. PER-013 queda **PARCIAL_A03**: el contrato y helper están listos, pero
sus consumidores pertenecen a Claude:

- `Rol` y `AsignacionRol` tienen `cloud_id` UUID inmutable, revisión monotónica,
  tombstone (`deleted_at`) y marca de propiedad cloud;
- mover usuario/rol/sucursal revoca la asignación anterior y crea o reactiva una
  identidad nueva dentro de la misma transacción;
- borrar es baja lógica versionada y reactivar un rol no revive asignaciones;
- los servicios de dominio bloquean filas, validan tenant/sucursal y registran
  exactamente un evento CT-01 por mutación;
- `X-RBAC-Revision` permite rechazar escrituras obsoletas con
  `409 rbac_revision_conflict`;
- cambios M2M add/remove/clear/set, incluido el lado reverso, avanzan revisión y
  timestamp;
- el perfil/login conserva `permisos` y `modulos` y añade
  `rbac.capabilities.v1`;
- el sync nuevo solicita `X-RBAC-Schema: rbac.sync.v2` y `snapshot=full`, valida
  `tenant_key` técnico y `scope.branch_code`, aplica revocaciones antes de
  capacidades desconocidas y solo reconcilia ausentes de propiedad cloud ante
  una respuesta completa válida;
- clientes sin header siguen recibiendo la lista legacy y el POS nuevo acepta
  respuestas de un cloud anterior;
- catálogo y presets quedaron separados: `sync_permisos` no pisa roles por
  defecto; seed/bootstrap son transaccionales, alias-aware y fallan ante negocio
  ambiguo;
- las data migrations 0002/0004/0007/0008 congelan sus datos históricos;
- Django Admin de RBAC queda read-only para no eludir los servicios auditados;
- notificaciones reutiliza `asignaciones_efectivas` del motor.

## Handoff consumidor de PER-013

Por la matriz de escritor único, A03 dejó exactamente como estaban en
`develop@e3635de` `apps/ventas/**`, `utils/impresoras/**`, `templates/base.html`
y sus tests/mapas. C02/C05 debe integrar, sobre `3e6cec1`, estos consumidores:

- el servicio de anulación debe volver a autorizar `ventas.anular` contra la
  sucursal bloqueada de la propia venta, no contra el rol legacy ni un scope del
  cliente;
- vista/listado de anulaciones deben usar el mismo scope de sucursal;
- reimpresión HTML/JSON y su enlace deben exigir `ventas.reimprimir` y consultar
  únicamente ventas del alcance operativo compatible;
- las pruebas deben cubrir rol custom positivo, permiso de otra sucursal
  negativo, acceso directo por URL y no exposición cross-branch.

Esto es una dependencia consumidora, no autorización para A de escribir esas
superficies ni para declarar CT-02 consumido por los checkpoints C existentes.

## Migración y configuración

`permisos.0011_ct02_identidad_revisiones` agrega las identidades/revisiones y
`EstadoRBAC`. Es aditiva.

`RBAC_LEGACY_ADMIN_BYPASS` nace en `True`. A03 **no** lo cambia en ninguna
instalación. Antes de configurarlo en `False` se debe ejecutar, para cada tenant:

```text
python manage.py preflight_rbac_admin_cutover --tenant <tenant_key>
```

El preflight es read-only y falla si algún ADMIN activo no tiene una asignación
explícita que otorgue `permisos.administrar`. Corregir los casos reportados y
repetirlo es gate de A08/A09, no parte de este commit.

## Evidencia automatizada

Entorno: Windows, CPython 3.11.14, Django 5.2.17, PostgreSQL 16 y venv aislado
`C:\Proyectos\.venvs\pos_cierre_codex_a01_20260910`.

```text
manage.py check --settings=config.settings_development
System check identified no issues (0 silenced).

manage.py makemigrations --check --dry-run --settings=config.settings_development
No changes detected

compileall apps config
OK

suite focal A03, incluidas 3 pruebas PostgreSQL de concurrencia
109 pruebas OK en 55.385 s

suite de concurrencia aislada: roles, asignación y bootstrap vacío
3 pruebas OK en 4.119 s

suite Django completa sin e-CF, con DB y dos tenants desechables nuevos
1244 pruebas OK en 448.738 s

pytest apps/facturacion_electronica/tests
72 passed en 16.17 s

git diff --check
OK; solo avisos de conversión LF/CRLF del checkout Windows
```

También se reconstruyó una PostgreSQL desde cero hasta `permisos.0011` y se
ejecutó `apps.permisos.tests.test_seed`: 7 pruebas OK. La regresión concurrente
reprodujo una colisión de secuencia durante dos bootstraps vacíos y quedó
cubierta por un advisory lock transaccional PostgreSQL. La suite completa creó
y destruyó `default` y dos BDs tenant aisladas. La BD desechable conservada por
pytest (`test_pos_cierre_codex_a03ecf2`) se verificó por nombre exacto y se
eliminó al terminar.

## Handoff de revisión Claude preservado

La copia de `docs/handoffs/cierre_prod/REVISION_MERGE_A02-C03.md` en la base
fija `e3635de` no fue modificada por A03 y sigue siendo el contrato que exigía
cerrar y probar estos tres bloqueadores antes de integrar Claude:

1. `MERGE-C01-ENVONLY` — instalación solo `.env` rechazada;
2. `MERGE-C01-SECRET-CAMPO` — lector genérico puede imprimir secretos;
3. `MERGE-C03-ALIAS-ATOMIC` — transacción de suscripciones abierta sobre el
   alias equivocado.

A03 no modifica esas superficies ni convierte sus observaciones no bloqueantes
en aceptación. Mientras A03 estaba aislado, otro flujo registró el cierre en
`8fd83a0`/`b3e8685`, la validación combinada en `cefea92` y el merge explícito a
`develop` en `484d080`; la versión actual del handoff en `develop@6ee21b8`
contiene esa evidencia. A03 no participó en esos commits ni los incorporó.

Por tanto, el siguiente integrador no debe mergear esta rama histórica de forma
directa: debe reconciliar `3e6cec1` con el `develop` vigente y repetir la matriz
combinada A03+C antes de acreditar los consumidores CT-02.

## Riesgos y rollback

- El retiro del bypass ADMIN depende de datos reales y permanece pendiente.
- La provisión de usuarios cross-DB sigue siendo explícitamente local; el sync
  omite usuarios ausentes y congela el cursor.
- `_pull_legacy` se conserva durante la transición de flota.

Rollback antes de despliegue: abandonar la rama A03; no hay estado externo que
revertir. Después de desplegar, seguir el runbook de rollback del candidato y
no intentar revertir migraciones o tombstones sin backup y plan de datos.

## Siguiente paso desbloqueado

Revisión de A03/CT-02 contra el `develop` vigente. Tras aceptarla, A04 puede
consumir identidad/revisión RBAC. Los consumidores C02–C05 solo se marcan
integrados después de incorporar `3e6cec1` y repetir sus suites; los cierres
previos de C01/C03 no acreditan por sí solos el consumo de CT-02.
