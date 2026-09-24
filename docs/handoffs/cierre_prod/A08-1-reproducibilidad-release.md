# Handoff A08.1 — reproducibilidad de release en worktree aislado

Estado: **REVISION LOCAL**. Fecha: **2026-09-23**. Este bloque no es un release
ni autoriza Terraform, pushes, despliegues, migraciones ni acceso a entornos o
datos reales.

## Base y aislamiento

- Base exacta: `4fd5c4665784be7ef52f4f6bd7f8cf5ef39f7426`
  (`docs(cierre): registrar integracion backend C04 p5`).
- Worktree: `C:\Proyectos\pos_fifo_system_a08_1`.
- Rama: `codex/cierre-prod-A08-1-reproducibilidad`.
- El worktree origen estaba limpio antes de crear este bloque. No se movio
  `develop`, no se uso el worktree de staging y no hubo push.

## Alcance entregado

| Superficie | Entrega |
| --- | --- |
| CI sin deploy | `.github/workflows/release-reproducibility.yml`: valida inputs, genera manifiesto, construye Docker y adjunta la evidencia; no contiene Azure, Terraform ni credenciales. |
| Locks | El generador exige los cuatro locks con hashes, Django 5.2.17, `--require-hashes` y base Docker por digest. No modifica pins existentes. |
| Artefacto Docker | `Dockerfile` recibe SHA y `SOURCE_DATE_EPOCH` como build args/labels. El manifest distingue `local_image_id` de digest OCI remoto. |
| Manifiesto | `scripts/release/release_manifest.py` genera/verifica JSON determinista por SHA: locks, inputs directos, Dockerfile, `.dockerignore`, 121 migraciones, hashes y dependencias declaradas por AST. No carga settings ni accede a una BD. |
| Restore/rollback | `docs/runbooks/RELEASE_REPRODUCIBLE_A08_1.md` establece el gate de restore aislado control-plane + todos los tenants y decisiones de rollback/fix-forward. |

## Contrato y migraciones

- CT-05 queda materializado como `posfifo.release-manifest.v1`.
- El estado `LOCAL_BUILD_EVIDENCE` registra solo un image ID local. No se puede
  promocionar.
- El estado `PROMOTABLE` exige una referencia que termine en
  `@sha256:<digest>` y pasa `--require-promotable`; un tag nunca sustituye ese
  digest.
- La lista de migraciones es una fotografia de fuentes. Antes de una operacion
  autorizada faltan `migrate --plan`, inventario de tenants activos y ledger por
  base; no se los infiere de este handoff.

## Evidencia local

Ejecutado en el worktree:

```powershell
python -m py_compile scripts/release/release_manifest.py `
  scripts/release/tests/test_release_manifest.py
python -m unittest discover -s scripts/release/tests -v
python scripts/release/release_manifest.py --write <temp-manifest> `
  --source-sha 4fd5c4665784be7ef52f4f6bd7f8cf5ef39f7426 `
  --image-reference local/pos-fifo-backend `
  --local-image-id sha256:<64-hex>
python scripts/release/release_manifest.py --verify <temp-manifest> `
  --expect-source-sha 4fd5c4665784be7ef52f4f6bd7f8cf5ef39f7426
git diff --check
```

Resultado: **3/3 tests OK**, manifiesto de fuentes **121 migraciones** y
`LOCAL_BUILD_EVIDENCE` valido; `git diff --check` sin errores. Esa ejecucion
prueba el generador sobre los inputs del worktree, no es un candidato de
`4fd5c46`: el modo `--assert-git-source` impide atribuir cambios sin commit a
ese SHA y se usa obligatoriamente en CI.

No ejecutado: `docker build` y `pip check` dentro de imagen. Docker CLI existe
pero el daemon `dockerDesktopLinuxEngine` no esta disponible en esta estacion.
El workflow nuevo cubre ambos pasos en un runner Linux; esta ausencia local no
se declara como build validado.

## Riesgos, restore y rollback

- `backup_tenant` verifica un dump, pero no sustituye restore completo. El
  restore gate sigue pendiente y debe usar bases clonadas, remapear `Tenant.db_name`
  dentro del control plane clonado y validar aliases antes de correr Django.
- Si hay escrituras posteriores al backup, no se restaura encima de una base
  activa: se preserva evidencia y se decide fix-forward/replay/reconciliacion.
- Rollback de API usa una imagen anterior por digest solo si el esquema es
  compatible. No hay downgrade de schema automatico.
- C06 conserva el rollback del paquete POS, sus servicios y wheelhouse. Este
  bloque no modifica `deploy/**` ni launchers Windows.

## Siguiente gate

Un integrador debe revisar este diff y ejecutar el workflow en una rama
publicada cuando corresponda. A08/G1 permanece **pendiente** hasta contar con
digest remoto promocionable, CI verde, planes de migracion reales, restore drill
aislado y los preflights/autorizaciones operativas requeridos.
