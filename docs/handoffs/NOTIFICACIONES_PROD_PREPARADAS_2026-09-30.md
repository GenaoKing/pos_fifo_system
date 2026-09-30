# Notificaciones prod preparadas y apagadas

Fecha: 2026-09-30. Alcance: infraestructura preparada para revision; **sin
apply, sin ejecuciones y sin activar motores de tenant**.

## Cambio

- El modulo permite un job `Manual` con `WEB_PUSH_ENABLED=false`, pero mantiene
  obligatorios Key Vault, publica VAPID y referencia al secreto privado.
- `Schedule` sigue exigiendo Web Push habilitado. En prod el disparador por
  defecto pasa a `Manual`; `enable_notifications_job` sigue siendo opt-in.
- Prod acepta `container_image_digest` para crear el job por digest aprobado.
  Las imagenes de API y migraciones existentes siguen protegidas por
  `ignore_changes` y se promueven por CI/CD.
- La alerta de falta de ejecuciones permanece apagada en modo Manual. Prod no
  tenia alertas de este job y el plan no crea ninguna.

## Validacion

Terraform 1.15.5, azurerm 4.77.0 (lock de prod): `terraform validate` y
`terraform fmt -check -recursive infra/azure` correctos. Cinco pruebas HCL
con provider simulado pasaron sin consultas Azure ni recursos reales:

- Manual + Web Push apagado conserva disparador, 0.5 CPU / 1 GiB y variable.
- Schedule + Web Push apagado es rechazado.
- Manual sin publica VAPID es rechazado.
- Manual sin referencia al secreto privado es rechazado.
- La alerta de ausencia de ejecucion queda deshabilitada en Manual.

Los tests estan en `infra/azure/modules/container-apps/tests/` y
`infra/azure/modules/notifications-monitoring/tests/`. Para repetirlos, copiar
el lock de `infra/azure/environments/prod/.terraform.lock.hcl` al modulo elegido,
ejecutar `terraform init -backend=false -input=false -lockfile=readonly` y
`terraform test -no-color` desde ese modulo. No versionar esa copia del lock.

## Plan revisado

Backend remoto `azure/prod.tfstate`, suscripcion y variables del entorno prod
actuales. El operador preparo un par VAPID propio; el plan solo contiene la
publica y la referencia de Key Vault, nunca la privada.

Resultado: **4 altas, 1 actualizacion, 0 destrucciones**, sin reemplazos:

1. `posfifo-prod-notifications`, Manual, 0.5 CPU / 1 GiB, Web Push apagado.
2. Su identidad administrada.
3. Permiso AcrPull para esa identidad.
4. Permiso Key Vault Secrets User para esa identidad.
5. API: agrega `WEB_PUSH_ENABLED=false`, publica y contacto VAPID. Conserva su
   imagen `bcb8621f931163e24606a80c665dc07ad99f8d34`; migrate es `no-op` y
   conserva esa misma imagen.

El plan inicial usa el digest staging
`sha256:bc4cb4f7f1aa760e35d882aebeda0febaac22c007e2683af4a4f65eeca1e52a4`.
Antes de aplicar, sustituirlo por el digest final aprobado y regenerar/revisar
el plan completo. El plan queda fuera de Git en el worktree de costes:
`infra/azure/environments/prod/notifications-prepared.tfplan`, junto a las
variables ignoradas `notifications-prepared.tfvars`. SHA-256 del plan inicial:
`AE49E0A67EFA283345D81529A41A2CAE21D08513A7ABE5F1084DE2F1D3A30341`.

No registrar la variable GitHub del job ni arrancarlo mientras deba permanecer
dormido: el workflow actual hace un ciclo explicito cuando encuentra el job.
Los motores por tenant se revisan durante el preflight/migracion productiva;
este trabajo no consulta ni cambia datos operativos. El runbook de
notificaciones distingue esta preparacion de la activacion posterior.
