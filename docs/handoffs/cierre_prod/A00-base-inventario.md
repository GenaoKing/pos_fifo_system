# Handoff A00 — base común, inventario y tablero

Estado: **INTEGRADO localmente; no publicado en remoto para evitar deploy de
dev**. Fecha: **2026-09-10**.

## SHAs y alcance

- Base backend funcional: `origin/develop@45ca23afcdc5811d9e4d94556c1c05fc99949890`.
- Bootstrap documental común: `c4af604f48a9334cbf3cc35a9b866046e2ed052e`.
- Inventario/contratos: `eb5f6b05d6dbf649977dc56020de2dea022864d8`.
- Cierre documental: el commit que contiene este handoff; el tip de
  `develop` local es la referencia consumible hasta que haya autorización para
  publicar sin provocar un despliegue.
- Frontend común para Claude: `origin/develop@239da82d118697798f4fdc5cb339c97bc5c074c0`.
- IDs cubiertos: B00/A00, CT-01…CT-05; 124 hallazgos de auditoría, BUG-A…M,
  deuda transversal y roadmaps en `INVENTARIO.md`.

## Archivos

- `docs/handoffs/cierre_prod/INVENTARIO.md`
- `docs/handoffs/cierre_prod/CONTRATOS.md`
- `docs/handoffs/cierre_prod/fixtures/ct01_audit_event_v1.json`
- `docs/handoffs/cierre_prod/fixtures/ct02_capabilities_v1.json`
- este handoff y los deltas de estado del plan/proyecto

No se modificó código, requirements, infraestructura, frontend ni archivos de
propiedad de Claude.

## Entornos, BDs y migraciones

- Worktrees backend: Codex `codex/cierre-prod-A00`; Claude
  `claude/cierre-prod-C01`, ambos desde `c4af604`.
- Worktree frontend Claude: `claude/cierre-prod-C01-frontend` desde
  `origin/develop@239da82`.
- Venvs `.venv` separados, creados con Python 3.11.14 sin cambiar el conda
  compartido.
- BDs locales vacías: `pos_cierre_codex`, `pos_cierre_claude`; BDs Django de
  tests derivadas: `test_pos_cierre_codex`, `test_pos_cierre_claude`.
- No se aplicaron migraciones. Los tests tenant multi-DB no se ejecutarán en
  paralelo hasta que TEN-016/A02 proporcione aislamiento físico adicional.
- Puertos reservados: A 8101/8102; C 8201/8202. No hay servicios NSSM del POS en
  este host.

## Comandos/evidencia ejecutados

```powershell
git fetch --prune origin
git rev-list --left-right --count develop...origin/develop
git worktree list --porcelain
git log origin/staging..origin/develop
az containerapp list --query <inventario-sin-secretos>
az containerapp job list --query <inventario-sin-secretos>
az staticwebapp list --query <inventario-sin-secretos>
gh run list --repo GenaoKing/pos_fifo_system --json <metadatos>
gh run list --repo GenaoKing/pos-cloud-dashboard --json <metadatos>
git diff --check
ConvertFrom-Json fixtures/ct01_audit_event_v1.json
ConvertFrom-Json fixtures/ct02_capabilities_v1.json
```

Resultados:

- Backend remoto no divergía antes del bootstrap (`0/0`).
- Recuento staging→develop: 20 commits; staging→develop inverso: 2 merges.
- Fixtures JSON válidos y 124/124 IDs esperados presentes en el ledger.
- Worktree staging limpio/detached; `terraform.tfvars` canónico identificado por
  ruta/tamaño/hash sin leer valores.
- Azure se consultó en modo read-only: imágenes exactas registradas en el
  inventario. No se inició job ni se cambió recurso.

## Pruebas omitidas

- No se corrió suite Django: A00 solo cambia documentación y A01 sustituye el
  baseline antes de la nueva ejecución completa.
- No se consultaron PCs/BDs de Royal Plast o SK; su paquete exacto queda
  `OPERATIVO_PENDIENTE` para preflight autorizado.
- No se probó NSSM ni impresión física; quedan C01/C02/C06.
- CT-01/02 son interfaces publicadas, no implementación; A02/A03 deben entregar
  migraciones, helpers y tests antes de que un consumidor marque integración.

## Riesgos y rollback

- `develop` local está por delante de `origin/develop`; empujarlo dispararía CI
  de dev. Se conserva local hasta autorización o una vía que no despliegue.
- Alias tenant `tnt_*` siguen sin namespace por agente; la contención temporal es
  serializar esas suites. TEN-016 lo resuelve, no se oculta como cerrado.
- El rollback del bloque es un `git revert` de commits documentales. Las dos BDs
  locales son vacías/desechables, pero no se eliminan automáticamente.
- Ningún rollback toca el worktree staging ni sus `tfvars`.

## Dependencias publicadas para Claude

Claude puede comenzar C01 con:

- backend base `c4af604` y frontend `239da82`;
- worktrees/venv/BD/puertos propios ya preparados;
- CT-01 `audit.event.v1` y CT-02 `rbac.capabilities.v1`/`rbac.sync.v2` en
  `eb5f6b0`, para diseño y tests; consumo final espera A02/A03;
- precedencia reservada CT-03: proceso > `POS_ENV_FILE` > defaults,
  `override=False`, UTF-8;
- propiedad de `deploy/**`, launchers, conversor y documentación Windows; todo
  cambio a settings/requirements/CI se solicita a A.

Siguiente tarea desbloqueada: **A01 para Codex y C01 para Claude**.
