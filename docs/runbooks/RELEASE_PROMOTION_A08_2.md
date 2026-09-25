# A08.2 - promocion backend por digest y gate de migraciones

Estado: **control de CI preparado; no ejecutado contra Azure ni una base real**.
Este runbook describe la politica implementada en
`.github/workflows/backend-ci.yml`; no autoriza un deploy, Terraform, un
backup, un restore ni la ejecucion de comandos operativos.

## Objetivo y limite

A08.2 cierra dos invariantes del camino backend:

1. Produccion consume un artefacto OCI existente por digest inmutable; nunca
   lo reconstruye ni lo publica durante la promocion.
2. La API nueva no se actualiza si el gate de migraciones requerido no termina
   en `Succeeded`. En produccion el gate siempre es obligatorio.

El alcance del manifiesto es solo backend. Los manifiestos equivalentes de
portal y paquete Windows, su matriz de compatibilidad y un release CT-05
completo siguen pendientes; no se infieren de un digest backend.

## Politica en CI

| Ambiente | Origen de imagen | Migraciones | Imagen consumida por API/jobs |
| --- | --- | --- | --- |
| dev/staging | `docker build` y `push`; ACR resuelve el digest remoto | Variable del ambiente en push o input manual | `<registry>/pos-fifo-backend@sha256:...` |
| prod | `approved_image_digest` existente; solo `docker pull`/`inspect` | `run_migrations=true` obligatorio | `<registry>/pos-fifo-backend@sha256:...` |

En prod, el workflow rechaza antes del cambio de API cualquiera de estas
condiciones:

- falta `approved_image_digest` o no tiene formato `sha256:<64 hex
  minusculas>`;
- `run_migrations` no es `true`;
- la imagen descargada no tiene `org.opencontainers.image.revision`, o su valor
  no es el SHA exacto de `main` que se selecciono para el dispatch;
- el job `migrate` falta, termina `Failed`/`Degraded`/`Cancelled`, o excede el
  timeout.

La imagen del job `migrate`, API y job de notificaciones es la misma referencia
por digest. Un fallo antes o durante migraciones evita el paso `Update API
image`; no se pretende deshacer un schema parcialmente aplicado.

## Evidencia de artefacto

Tras resolver el digest, CI ejecuta
`scripts/release/release_manifest.py` contra el checkout limpio del SHA y
adjunta `release-manifest-backend-<ambiente>-<sha>`. El manifiesto valida:

- SHA fuente y label OCI de revision para produccion;
- locks con hashes y Django fijado;
- receta Docker con base por digest;
- hash y dependencias declaradas de cada migracion;
- referencia de registro terminada en el digest consumido.

El artefacto se retiene 180 días. El workflow de reproducibilidad también se
dispara si cambia `backend-ci.yml`, para que una alteración del gate no quede
sin evidencia de locks, Docker y migraciones. Esta retención no es archivo
durable ni una atestación firmada; ambos siguen siendo gates externos.

El manifiesto no pregunta bases, tenants ni secretos. La prueba de que un
artefacto se puede promover no prueba que las migraciones se hayan aplicado.
Los hashes de sus inputs de texto normalizan finales de linea CRLF a LF, para
que el manifiesto generado en CI Linux se pueda verificar desde un checkout
Windows del mismo SHA sin confundir conversion de linea con un cambio de codigo.

## Gate operativo que permanece fuera de CI

Antes de autorizar una corrida real, adjuntar al ticket el plan de migracion de
control plane, el inventario de tenants activos, backups verificables y el
ledger por base. El migrate job ejecuta `migrate_cloud`, que encadena `migrate`
y `migrate_tenants`; su salida por tenant es el ledger de ejecucion. Un health
verde o el artefacto de GitHub no reemplazan ese ledger.

El plan de restore aislado, RPO/RTO y decisiones de rollback/fix-forward estan
en [RELEASE_REPRODUCIBLE_A08_1.md](RELEASE_REPRODUCIBLE_A08_1.md). Si una API
nueva falla despues de un schema compatible, el rollback de codigo usa la
imagen anterior por digest. No hay downgrade automatico de schema ni restore
sobre una base que recibio escrituras posteriores al backup.

## Verificacion local permitida

Sin credenciales ni servicios externos, verificar la politica como codigo:

```powershell
python -m unittest discover -s scripts/release/tests -v
python scripts/release/release_manifest.py --write "$env:TEMP\manifest.json" `
  --source-sha (git rev-parse HEAD) `
  --image-reference "registry.example/pos-fifo-backend@sha256:<64-hex>" `
  --image-digest "sha256:<64-hex>" `
  --assert-git-source
python scripts/release/release_manifest.py --verify "$env:TEMP\manifest.json" `
  --expect-source-sha (git rev-parse HEAD) `
  --require-promotable
git diff --check
```

No sustituir esos valores de ejemplo por datos de un ambiente real fuera de una
ventana autorizada. La validacion de Azure/ACR, el run del job, el restore drill
y cualquier Terraform permanecen fuera de A08.2.
