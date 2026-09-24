# Consolidación de avances disponibles — 2026-09-24

Alcance autorizado: revisar todas las ramas/worktrees locales e integrar los
avances pendientes al candidato. No equivale a cerrar el backlog aún no
implementado ni a aprobar un despliegue. `develop` permanece en `fffd02b`.

## Candidatos y cambios incorporados

Backend: `integration/cierre-prod-A06-C04-C05`, worktree
`C:\Proyectos\pos_fifo_system_integracion_a06_c04_c05`.
Partió de `94da41a`; preserva ese SHA y `9e6fb99` como ancestros.
El corte de código para la validación final es
`a15f45481016035fcc783bc96329150901809941`; el commit de cierre posterior
actualiza documentación y mapas, no el comportamiento de la aplicación.

| Origen | Resultado |
| --- | --- |
| `dec46a3` | Fast-forward: permisos.0011 nullable → UUID por fila → unique; regresión histórica con 2 roles y 4 asignaciones. |
| CT03-sync `f83f67d` + `d1d9c29` | Merge `9f24531`: checkpoint SUS-016 de solo lectura, tests y documentación. Conflictos documentales resueltos conservando SUS-014/CT-03 ya integrados. |
| Impresión `e846899` | Merge `57ce505`: apertura/cierre RAW, CP850, RD$ y wrapping. El equivalente previo `ee7b98d`/`d90b758` se conserva una sola vez. |
| Claude C06.1 `84b3595` | Merge `9d99418`: evidencia del bloqueo y del backup/restore original. Banner aclara que el fix ya está integrado y resta repetir el ensayo. |
| Claude C06.2 `2095104` | Merge `79a59a0`: revisión cliente de RBAC, sync, BUG-K y CT-04. CLI-004 y RM-OBSERVABILIDAD conservan sus límites. |
| Claude C06.3 `7408568` | Merge `d04e20e`: lista de visita y comunicación al operador. No programa visita. |
| Impresión `bc0b732` | Incorporación concurrente equivalente `4f3e03c`, preservada con `9cc3b4a`: confirmación física de ticket/PDF y exclusión de etiquetas documentadas por el otro flujo. No se repitieron aquí esas pruebas físicas. |
| Correctivo de integración `a15f454` | Fixtures A06/TEN-016 deterministas, validados junto con administración: 16/16. |

Frontend: `integration/cierre-prod-C04-admin@a9d960e3736067dfd3e2de0f1e3dff8b3e2e32e6`,
worktree `C:\Proyectos\pos_cloud_dashboard_c04_integration`.
Desde `4ff4247` solo faltaba `e18b562`, matriz de consumo p5.2 de Claude:
fusionada con banner histórico y enlaces a contratos/evidencia posteriores.
No cambia código frontend; p6, p5.2, Roles/Asignaciones y p5.3 ya estaban dentro.

## Ramas antiguas que no representan trabajo perdido

Se enumeraron las 78 ramas backend y las 16 frontend, con todos sus worktrees.
Todos estaban limpios al iniciar. Se compararon ancestry, patch-id y deltas de
archivos; un SHA no ancestro no implica contenido pendiente.

| Rama / SHA | Evidencia de reconciliación |
| --- | --- |
| C03 `312e78c` | Contrato/fixture/test CT-03 rebasados e integrados; `C03-CT03-configuracion-efectiva.md` lo identifica expresamente. Se preserva el contrato vigente, no la afirmación antigua de que CFG-012 era solo diseño. |
| C03 residual `3d29772` | Sustituido por r2 `1092756` ya ancestro; fixtures presentes. Deltas posteriores de cotizaciones son CT-01 y negocio coherente; credencial difiere solo en comentario. |
| C04 conflictos `0c7a070` | Ambos commits patch-equivalentes según `git cherry`. |
| SUS-014 `9c36dd9` | Patch-equivalente; prevención y PLAN_DRIFT ya están cableados. |
| Ledger `cc9a314` | Documentación antigua: todavía declara sin cablear la postcondición SUS-014 y pendiente CFG-007. Se mantiene la conciliación vigente y se actualizan las filas CT-03/SUS-016/CFG-012. |
| Review A02-C03 `6e15743` | Handoff histórico de una revisión bloqueada, superado por las integraciones posteriores; no aporta código. |

No se borraron ramas ni se reescribió historia para ocultar esas diferencias.

También se ejecutó `git fetch --all --no-tags` en ambos repositorios. Todas las
referencias remotas quedan cubiertas salvo el historial de promoción de
`origin/staging`: backend `89b30c4`/`bb37b2f` y frontend
`e0a2302`/`34c6db6`. Son merges sin commits de contenido faltantes ni deltas
de resolución (`git log HEAD..origin/staging --remerge-diff --stat` vacío).
No se incorpora historia de despliegue como si fuera desarrollo pendiente.

## Verificación de esta integración

Entorno backend: CPython 3.11.14 / Django 5.2.17, intérprete existente
`C:\Proyectos\pos_fifo_system_a06\.venv\Scripts\python.exe` usado sin modificar
dependencias. `DB_HOST=127.0.0.1`, `DB_NAME=pos_integration_20260924_gate`,
`TENANT_TEST_DB_NAMESPACE=integration_20260924_gate`, sync deshabilitado y
`POS_ENV_FILE` sintético. Se verificó que los destinos no existían antes del
runner. Las suites Django se ejecutan en serial; e-CF usa pytest por separado.

Resultado final sobre `a15f454`: **PASS**.

| Gate | Resultado |
| --- | --- |
| Django general, apps + utils, con namespace físico tenant | **1.654/1.654 OK**, 580,555 s |
| e-CF, pytest separado | **72/72 OK**, 23,32 s |
| Tooling release/paquete | **13/13 OK** |
| Frontend `a9d960e` | **143/143 OK**, lint y build PASS |

Los runners retiraron las BDs Django. Pytest usa `--reuse-db` desde
`pytest.ini`; se retiró después exclusivamente `test_pos_integration_20260924_gate`
tras verificar nombre, host local y ausencia de conexiones. La consulta final
de BDs con el namespace de este gate devolvió cero filas.

Evidencia local preservada en
`C:\Proyectos\_lab_integracion_total_20260924_a15f454` (incluye el `.env`
sintético, primera corrida, reproducciones y corrida final; sin datos reales).

| Archivo | SHA-256 |
| --- | --- |
| `django_final.log` | `3b9426d2dd17419f38d97c18b8233a7ecaf811a64bb0a6c0211defa416823668` |
| `ecf.log` | `2686a1e36cec45260bacfabfdabce0a358c1c46e273c3dd37aab975539152081` |
| `release.log` | `988e8f74f7fed5fce93fc0a3272eaba97a4976c571064bbd6a658947d4a51154` |

La primera corrida general ejecutó **1.654** casos en **591,999 s** con dos
fallos de precondiciones de fixture. La repetición focal aisló las causas:

- A06: dos `save()` contiguos recibieron exactamente
  `2026-09-24T17:34:05.225479+00:00`; el escenario no había creado una revisión
  posterior. El fixture ahora controla solo el reloj durante ese guardado y
  comprueba que la revisión cambió, antes de ejercer el CAS real y esperar 409.
  Esto no demuestra que el contrato basado en timestamps produzca revisiones
  monotónicas para dos escrituras dentro del mismo tick.
- TEN-016: administración consume la secuencia de ALIAS_A y el rollback de
  TestCase no la retrocede; el fixture obtenía PK 2 vs 1. Ahora crea PK 1
  explícita en las dos BDs para ejercer la colisión que la prueba exige.
  Conserva las comprobaciones de aislamiento físico y referencias CT-01.

La regresión conjunta de administración/A06/TEN-016 dio **16/16** en
**10,480 s** antes de repetir la suite general. No se modificó código runtime
para esconder estos fallos ni se sustituyó el resolver por un mock.

Frontend sobre `a9d960e`: lint PASS, Vitest **143/143**, build PASS.
Release: `python -m unittest discover -s scripts/release/tests -v`: **13/13**.
E2E p5.3 real **10/10** es evidencia anterior preservada, no reejecutada aquí.
`manage.py check`, compilación y `git diff --check`: PASS.
`makemigrations --check --dry-run`: sin cambios; avisó que la BD base sintética
no existe. El historial real de migraciones se ejercita en las BDs `test_*`
creadas/destruidas por el runner, incluida la regresión histórica de permisos.

Comando de descubrimiento final (PowerShell; e-CF se ejecuta después):

```powershell
$modules = rg --files apps utils |
  Where-Object { $_ -match '[\\/]tests[\\/]test_[^\\/]+\.py$' -and $_ -notmatch 'facturacion_electronica' } |
  ForEach-Object { ($_ -replace '[\\/]', '.') -replace '\.py$', '' }
python manage.py test @modules --settings=config.settings_development --noinput
python -m pytest apps/facturacion_electronica/tests --ds=config.settings_development -q
```

## Relevo C06.1

1. Tomar el SHA final de esta rama consolidada que acompañe este handoff.
   Confirmar `git merge-base --is-ancestor dec46a3 <SHA>` y checkout limpio.
2. Crear la continuación de Claude desde ese SHA, sin rebase del paquete viejo
   ni modificación del worktree fuente. Rehacer wheelhouse/paquete/manifiesto
   con `scripts/c06_construir_paquete.py`; conservar hashes y versión de locks.
3. Repetir C06.1 desde una copia restaurada limpia, con el dump y procedimiento
   ya documentados en `C06.1-paquete-windows-laboratorio-parte2.md`. Verificar
   nombres de BD y rutas antes de ejecutar. En esta integración no se tocó
   `pos_fifo_db`, `pos_c06_1_lab`, el dump ni los servicios de Claude.
4. Registrar el plan real completo (el ensayo anterior tenía 32), ejecutarlo
   entero y verificar roles/asignaciones UUID únicos, identidad, conteos,
   backup/restore y rollback. No continuar solamente desde la migración 22.
5. PASS requiere todo el ensayo con evidencia nueva sobre ese SHA. Un test
   verde de 0011 o un paquete reproducible por sí solos no completan C06.1.

Siguen siendo gates distintos: preflights operativos, identidad inicial y
multi-tenant C06 pendiente, resto de matriz física, CI/Docker/digest remoto,
restore completo cloud, CLI-004/RM-OBSERVABILIDAD según su alcance y G1-G4.
SEC-001 conserva rotación/revocación e historial pendientes. No hubo push,
promoción, cambios de `develop`, acceso a clientes ni despliegue.
