# Handoff A08.2 - promocion por digest y gate de migraciones

Estado: **REVISION LOCAL**. Fecha: **2026-09-23**. No autoriza publicar el
workflow, ejecutar Azure/ACR, Terraform, migraciones, backups/restores ni usar
un entorno o dato real.

## Base y aislamiento

- Candidato de entrada: `fc42fca4381d22b2dae8ddc779f15b586ecccb8f` sobre el
  frente backend `integration/cierre-prod-A06-C04-C05`.
- Commit de implementacion A08.2: `063d74a` (`build(release): gate prod
  promotion by digest`).
- Worktree aislado: `C:\Proyectos\pos_fifo_system_integracion_a08_claude`.
- Rama candidata: `codex/cierre-prod-A08-1-reconciliacion-claude`.
- No se movio `develop`, `main` ni la rama de integracion; no hubo push.

## Entrega

| Superficie | Control A08.2 |
| --- | --- |
| `.github/workflows/backend-ci.yml` | Prod exige `run_migrations=true` y `approved_image_digest=sha256:<64 hex>` antes de login/deploy efectivo. La ruta prod no tiene `docker build` ni `docker push`; hace `pull`/`inspect` del artefacto existente y exige que el label OCI `org.opencontainers.image.revision` coincida con el SHA de `main` seleccionado. |
| Imagen consumida | Tras publicar dev/staging o resolver el digest aprobado prod, migrate job, API y notifications job reciben exactamente `<registry>/pos-fifo-backend@sha256:...`, no un tag mutable. |
| Migraciones | Cuando el gate es requerido —siempre prod— el workflow espera el resultado de migrate job. La API solo puede actualizarse si el gate no era requerido o el step termino `success`; un fallo/timeout no continua al swap. |
| Manifiesto | CI escribe, verifica y adjunta `release-manifest-backend-<ambiente>-<sha>` con `--require-promotable`. Usa checkout limpio y el SHA de la corrida, locks, Docker/migraciones y el digest que se consume. |
| Documentacion y regresion | Nuevo runbook A08.2 y pruebas estaticas de invariantes del workflow, junto a los tests del manifiesto A08.1. |

El comando configurado en el job de migraciones sigue siendo `migrate_cloud`;
el control plane y tenants activos requieren su ledger por tenant. El estado
`Succeeded` del job habilita el swap de API, pero no reemplaza la evidencia de
backup, plan, inventario y salida por base requerida por A08.1.

## Evidencia local

Ejecutado sin credenciales ni conexiones externas:

```powershell
python -m py_compile scripts/release/release_manifest.py `
  scripts/release/tests/test_release_manifest.py `
  scripts/release/tests/test_backend_ci_policy.py
python -m unittest discover -s scripts/release/tests -v
git diff --check
```

Resultado: **6/6 tests OK**. La prueba adicional del bloque shell de
`Resolve deploy target` acepto el caso prod con digest valido y
`run_migrations=true`, y rechazo el mismo caso con `run_migrations=false`.
La ayuda instalada de `az acr repository show` confirma que `--image` acepta
una imagen por tag o por `name@digest`; no se hizo login ni consulta a ACR.
Despues del commit, `release_manifest.py --write/--verify
--require-promotable --assert-git-source` paso para `063d74a` con **121
migraciones** y una referencia digest sintetica; `git status --short` y
`git diff --check` quedaron limpios.

No ejecutado: GitHub Actions remoto, Docker build/push/pull contra ACR,
migrate job, API/jobs, health check, Terraform, backup ni restore. Docker local
sigue sin daemon Linux disponible, por lo que no se acredita un build local.

## Lectura para el integrador

1. Revisar que el artefacto candidato haya sido construido desde el SHA exacto
   de `main`; el label de revision es la comprobacion que hace el workflow.
2. En una ventana autorizada, adjuntar el manifiesto backend, `migrate --plan`,
   inventario de tenants activos, backups verificables y ledger de control
   plane + tenants. El detalle esta en
   `docs/runbooks/RELEASE_REPRODUCIBLE_A08_1.md`.
3. Ejecutar prod solo con los cinco inputs documentados en
   `docs/runbooks/RELEASE_PROMOTION_A08_2.md`. El workflow no permite usar
   `run_migrations=false` como bypass.
4. Si falla antes/durante migraciones, mantener API anterior y decidir
   fix-forward o recuperacion con evidencia. Si la API nueva falla con schema
   compatible, el rollback de codigo usa la imagen anterior por digest; no hay
   downgrade automatico de schema.

## Pendientes fuera de este bloque

- Ejecucion y evidencia de CI/Azure en una corrida autorizada.
- Manifest/artefacto equivalente para portal y paquete Windows, mas
  compatibilidad CT-05/C06.
- Restore drill aislado con control plane y todos los tenants.
- Approvals de GitHub Environments, firma/atestacion y retencion durable del
  manifiesto.

A08/G1 permanece pendiente. Este handoff agrega controles versionados; no
declara un release ni transforma una rama candidata en una integracion.
