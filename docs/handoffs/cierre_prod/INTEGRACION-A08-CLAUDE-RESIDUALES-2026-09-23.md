# Integración A08.1 y conciliación de residuales Claude

Estado: **CANDIDATO LOCAL**. Fecha: **2026-09-23**. Este registro no autoriza
push, despliegue, Terraform, migraciones, restore ni acceso a datos operativos.

## Bases y resultado

- Frente real inspeccionado: `integration/cierre-prod-A06-C04-C05` en
  `02c4fd280005c6ad5eb2999ad6cd61a6b1051b54`.
- `develop` sigue en `fffd02b`; no es la base de este candidato.
- Worktree aislado: `C:\Proyectos\pos_fifo_system_integracion_a08_claude`.
- Rama: `codex/cierre-prod-A08-1-reconciliacion-claude`.
- A08.1 se fusionó sin conflictos como
  `fd33006bcf872297e5017af626900ca49c6dd9fd`, con padres `02c4fd2` y
  `b6de998`.

El merge agrega solo el workflow de reproducibilidad, los scripts/tests de
manifiesto, la metadata Docker y sus runbooks/handoff. Conserva los documentos
de integración más recientes que ya estaban en `02c4fd2`.

## Ramas Claude verificadas

| Rama / commit | Verificación contra `02c4fd2` | Decisión |
| --- | --- | --- |
| `claude/cierre-prod-C04-conflictos` / `0c7a070` | `git cherry` marca sus dos commits como patch-equivalentes; el blob actual de `C04-conflictos-maestros-ct04.md` es idéntico. | No mergear otra vez. |
| `claude/cierre-prod-SUS014-postcondicion` / `9c36dd9` | `engine.py` y `test_sus014_divergencias_plan.py` tienen blobs idénticos. `divergencias_plan_operativo()` ya se agrega en `verificar_identidad_tenant` como `PLAN_DRIFT` read-only. | No mergear otra vez. |
| `claude/cierre-prod-sus014-ledger-fix` / `cc9a314` | Único patch no equivalente, pero es doc-only y parte de `609c98f`: marca como pendientes A05/A06, CT-04, A07 y otros estados ya acreditados en el frente. Produce conflictos en `TODO_AUDITORIAS.md` e `INVENTARIO.md`. | No mergear; el ledger actual ya refleja SUS-014 acreditado localmente. |

No se descartó ninguna rama ni se reescribió historia. Que un commit no sea
ancestro no prueba que falte su contenido: aquí se comprobó por patch-id, blob
y código consumidor actual.

## A08.1 en el candidato

- `.github/workflows/release-reproducibility.yml` construye evidencia local en
  CI, sin credenciales Azure ni deploy.
- `scripts/release/release_manifest.py` exige SHA de Git limpio, hashes de
  inputs/locks/migraciones y distingue image ID local de digest OCI remoto.
- `docs/runbooks/RELEASE_REPRODUCIBLE_A08_1.md` mantiene el restore de control
  plane + todos los tenants como gate posterior y prohíbe rollback destructivo
  tras escrituras nuevas.

## Relevo para Claude: C04 p5.3

Decisión recomendada y apta para continuar: **B + control-plane multi-DB real**.

1. Sobre el tip p5.2 `26e9bac`, fusionar
   `claude/cierre-prod-C04-p5-admin` (`bd1d021`).
2. En los únicos conflictos esperados (`errors.ts` y `AdminQueryError.tsx`),
   conservar la implementación p5.2; incorporar limpios Roles/Asignaciones.
3. Ajustar el assert de `errors.test.ts` al mensaje p5.2 si el test aún fija el
   texto previo.
4. Reapuntar p5.3 a ese tip y continuar con `global-setup.ts` y las specs.
   Usar dos tenants creados por `bootstrap_tenant` sobre aliases/BDs de prueba
   aislados; no usar un mock mono-DB que omita JWT y aislamiento real.

Este candidato backend no modifica el repositorio frontend ni el worktree de
Claude. El E2E sigue necesitando su propio entorno desechable, puertos, BDs y
evidencia; no se acredita por este merge.

## Gates pendientes

El candidato no es un release. Siguen pendientes CI remoto, build Docker
real, digest OCI promocionable, preflight autorizado, restore drill aislado,
C06 y los gates G1--G4. No se movió `develop` ni la rama de integración fuente.
