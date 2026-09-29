# Revisión de producción y limpieza — 2026-09-29

## Dictamen y premisa

**No ejecutar todavía el dispatch de producción con el procedimiento actual.**
Actualizar cloud hoy y los POS mañana es compatible con los contratos existentes,
pero se encontró un riesgo de confirmaciones falsas durante migraciones y
rollback, y un endpoint de salud defectuoso en el candidato publicado.

El responsable indicó dar por satisfactorias las pruebas de staging y las
validaciones. Esta revisión acepta esa premisa: no vuelve a exigir las viejas
evidencias G1/G2 ni transforma los checkboxes históricos en bloqueos. Los
hallazgos siguientes se comprobaron contra código y estado actual; son distintos
de las validaciones asumidas. El alcance de esta sesión es análisis y limpieza,
sin promoción, migraciones, modificación de datos ni cambios de infraestructura.

## Corte verificable

Referencias refrescadas con `git fetch origin --prune` en ambos repositorios.

| Componente | Producción actual | Staging candidato |
| --- | --- | --- |
| Backend | `bcb8621f931163e24606a80c665dc07ad99f8d34` | `d24f24e897e06cbcfb850ebbd5c1dc7cf99a84ee` |
| Portal | `c9116f5ea3535cf1d9bd6f0aeac3d84cedcdc6f0` | `cbd04787d6bbc47815e9f963452102d296f8cbdc` |

Backend staging activo y manifiesto descargado de
[CI/CD 36592002904](https://github.com/GenaoKing/pos_fifo_system/actions/runs/36592002904):
`sha256:bc4cb4f7f1aa760e35d882aebeda0febaac22c007e2683af4a4f65eeca1e52a4`.
Azure confirmó revisión `posfifo-staging-api--0000021` con esa imagen.
El artefacto `release-manifest-backend-staging-d24f24e897e06cbcfb850ebbd5c1dc7cf99a84ee`
no está expirado y su `source.git_sha` coincide.

Producción usa todavía el tag `bcb8621...` en API y job de migraciones,
revisión `posfifo-prod-api--0000008`. ACR resolvió su digest a
`sha256:1c5ff586371735ee8309d2a3e8fd5d108d925b023944ec9803551fe2bf2192e5`.
Ese digest identifica la imagen anterior; **no acredita compatibilidad de
rollback tras las nuevas migraciones**.

Portal staging tiene
[CI 36594308060](https://github.com/GenaoKing/pos-cloud-dashboard/actions/runs/36594308060)
y [deploy 36594308088](https://github.com/GenaoKing/pos-cloud-dashboard/actions/runs/36594308088)
exitosos. El portal productivo respondió 200 y su bundle identifica `c9116f5`
y la URL correcta de API prod. El token de despliegue y `VITE_API_URL_PROD`
existen; solo se consultaron nombres de secretos, nunca sus valores.

## Hallazgos que condicionan el pase

### 1. Migrar con la API antigua activa puede confirmar eventos no guardados

`apps/sync/migrations/0011_transporte_durable_bug_k.py:61` vuelve obligatorio
`EventoSync.event_id`, con `default=uuid.uuid4` de Python y sin default permanente
en PostgreSQL. El receptor de producción `bcb8621`, en
`apps/api/views/sync.py:107-137` de ese commit, crea eventos sin esa columna y
captura cualquier `IntegrityError` como `DUPLICADO`.

Después del cambio de esquema, un evento nuevo atendido por la imagen antigua
puede fallar por NOT NULL y recibir un ACK terminal de duplicado. El POS puede
marcarlo entregado aunque cloud no lo haya almacenado: es la firma del BUG-K
histórico aplicada a la ventana de despliegue.

El SQL se verificó sin abrir conexión a BD, con Django 5.2.17 y el schema editor
PostgreSQL: la transición emite `SET DEFAULT`, backfill, `SET NOT NULL` y
`DROP DEFAULT`. No debe confundirse un default Python con uno de la base.
Reproductor y salida local: `C:/Proyectos/_archivo_pos_fifo/2026-09-29/schema-default-proof.py`
y `schema-default-proof.txt`; resultado `Django 5.2.17`, `connection_opened False`.
Es una compilación aislada de esa transición, no una simulación de toda la migración.

El workflow `.github/workflows/backend-ci.yml:480-517` migra primero y cambia
la imagen después. No implementa por sí mismo mantenimiento/drain que cierre
esa ventana. Se necesita impedir escrituras al código antiguo **antes** del
DDL incompatible y reabrir únicamente con la API nueva.

No basta bloquear `/sync` dejando `/api/v1/health/` en 200: el POS antiguo
consume intentos y puede descartar eventos tras diez errores. Su daemon consulta
health antes del push; mantenimiento con health no exitoso, bloqueo de escrituras
y drenaje de peticiones, o pausa explícita de sync, debe preservar la cola.
La venta local puede seguir operando durante la pausa cloud.

### 2. El rollback de imagen no revierte el esquema

El workflow `:543` restaura la imagen anterior al fallar la API y valida health.
La imagen `bcb8621` no conoce los nuevos campos obligatorios de sync, auditoría,
permisos y catálogo. Ejemplos adicionales: `auditoria.0008`, `permisos.0011` y
`productos.0013/0014`. Volver a esa imagen sobre la BD migrada puede conservar
el fallo de escritura y el riesgo de ACK falso.

El health principal solo ejecuta `SELECT 1`; podría dar verde con las escrituras
rotas. Antes del pase debe estar definida y preparada una recuperación compatible:
imagen puente, corrección hacia adelante o restauración coordinada sin escrituras
posteriores al respaldo. Un backup por sí solo no resuelve el intervalo en que
el código antiguo atiende tráfico ni autoriza restaurar sobre datos nuevos.

### 3. El endpoint HTTP de liveness está roto

En `apps/api/views/health.py:58`, `health_live` usa `JsonResponse` sin importarlo.
Se reprodujo `NameError` en un proceso aislado sin BD y se verificó contra
staging el 2026-09-29 a las 16:48 UTC:

- `/api/v1/health/live/`: **500**.
- `/api/v1/health/`: **200**, DB `ok`.

Los probes Azure actuales de prod y staging son TCP en puerto 8000; no llaman
esa ruta HTTP, por lo que el defecto no demuestra caída general de la app ni
contradice la CI verde. Debe corregirse antes de presentar ese endpoint como
salud operativo. Si se incorpora la corrección al release, fijar el nuevo
SHA/digest: no atribuirla al artefacto `d24f24e` actual.

## Requisitos restantes y alcance

| Requisito | Resultado de la revisión |
| --- | --- |
| POS antiguo → API nueva | Compatible: `event_id` opcional en serializer, ACK conserva hash, RBAC sin header sirve listas legacy y config conserva `modulo_*`. |
| Ventana de versiones mixtas | Mantener `RBAC_LEGACY_ADMIN_BYPASS=True`, capacidades vigentes por tenant y posponer el cutover de roles/permisos hasta actualizar los clientes. El POS viejo difiere roles con permisos desconocidos. |
| API → portal | Orden obligatorio: API y comprobación funcional primero; después portal. El workflow frontend despliega al publicar `main` y no espera al backend. |
| Artefacto backend | Promoción por digest existente. `main` debe ser exactamente el SHA OCI aprobado mediante fast-forward; un merge/squash distinto falla el gate. `origin/main` sí es ancestro del staging actual en ambos repositorios. |
| Migraciones | `run_migrations=true` es obligatorio aunque la variable histórica `PROD_RUN_MIGRATIONS_ON_DEPLOY=false` siga presente. `migrate_cloud` recorre control y tenants, con ledger y fallo si un tenant falla. Preparar backups/preflight de la ventana. |
| Catálogo RBAC | `migrate_cloud` no ejecuta `sync_permisos`. Revisar/sembrar catálogo y asignaciones por tenant para capacidades nuevas; no asumir que el dispatch las concede. No se consultaron las filas productivas para afirmar que falten. |
| Notificaciones | Azure prod solo tiene `posfifo-prod-migrate`; no existe job de notificaciones ni variable `PROD_AZURE_NOTIFICATIONS_JOB_NAME`. API no tiene variables VAPID/Web Push. El backend puede operar con motor apagado, pero la funcionalidad completa requiere job, configuración y activación gradual por tenant. |
| Secrets/arranque | Existen los tres nombres de secrets OIDC de prod y las variables de API/ACR/job de migración. No se encontró una nueva variable obligatoria que por sí sola bloquee el arranque. |
| Auditorías/deuda aceptada | CLI-004 con contención 409, Redis, WORM, HA y e-CF nativo quedan fuera de este release. No se convierten en bloqueadores nuevos. |
| Aceptación local | Actualizar los POS mañana sigue siendo viable después de un cloud estable; no es requisito hacer las tres actualizaciones simultáneamente. |

Fuentes: `docs/PLAN_CIERRE_PROD.md`, `docs/planes/OBJETIVO_RELEASE_CLIENTES.md`,
`docs/ESTADO_AUDITORIAS.md`, `docs/TODO_AUDITORIAS.md`,
`docs/runbooks/RELEASE_PROMOTION_A08_2.md`, `RELEASE_REPRODUCIBLE_A08_1.md`,
`NOTIFICACIONES_WEB_PUSH.md`, contratos/código de sync y workflows de ambos repos.

El diff de código `origin/main..origin/staging` contiene 32 archivos nuevos de
migración numerada y seis archivos históricos modificados, sin eliminaciones
(se excluye el nuevo `__init__.py`). Esto no es un conteo de migraciones pendientes
en las BDs productivas: el ledger real debe verificarse en la ventana.

### Diferencias de ramas y trabajo preservado

- `origin/develop` backend está en `c1ad5d9`; su gate Admin SYSADMIN no está
  en `origin/staging@d24f24e`. Cloud no monta `/admin/`, por lo que no es bloqueo
  del pase cloud; debe entrar en el paquete local final.
- La rama de trabajo `codex/caja-dashboard-ingresos@3d2ee4f` contiene el cuadre
  `c7ff2e5`, todavía fuera de staging, y ocho archivos modificados sin commit.
  Son cambios de POS/QA y documentación; se preservaron íntegros. No asumir que
  el paquete de mañana se obtiene de staging y ya incluye todos esos cambios.
- Frontend `origin/develop@280ce68` no incluye los ingresos de staging. El
  candidato cloud es el SHA staging indicado, no la punta de develop por nombre.
- Cost controls `8655634` conserva tres commits propios no integrados;
  su worktree permanece. Frontend ingresos `e146aaa` conserva un acta posterior
  al deploy no integrada; su worktree también permanece.

## Secuencia requerida para habilitar el pase

1. Cerrar mantenimiento y recuperación compatible del backend; corregir la
   ruta de liveness. Fijar el SHA/digest resultante y el SHA de portal.
2. Preparar backups y preflight del día; bloquear y drenar escrituras cloud
   sin agotar reintentos de los POS. No enviar tráfico al receptor antiguo
   durante ni después de las migraciones incompatibles.
3. Migrar control y todos los tenants, revisar catálogo/asignaciones y arrancar
   API nueva. Verificar un recorrido de escritura/sync además de health.
4. Abrir tráfico y publicar portal; completar o diferir explícitamente la
   activación de notificaciones. Observar 60 minutos y dos ciclos por POS.
5. Actualizar clientes con el paquete local definitivo y sus respaldos.

La premisa de pruebas satisfactorias no elimina estos defectos de código y
transición recién comprobados. No se ejecutó esta secuencia en producción.

## Limpieza de worktrees

Inventario, plan y archivo local:
`C:/Proyectos/_archivo_pos_fifo/2026-09-29/`.

La eliminación solo comprende worktrees sin cambios tracked/untracked, sin
procesos/servicios detectados referenciándolos, y con integración por ancestría,
equivalencia de parches o sustitución documentada por C03 residual r2.
Se conservaron ramas y commits para reconstruir cualquier checkout.

Antes de cada eliminación se comprobó ruta absoluta bajo `C:/Proyectos`, HEAD
y estado Git; se excluyeron los repositorios raíz. Configuraciones ignoradas,
backups, media, logs y evidencia Playwright se copiaron al archivo y verificaron
por SHA-256. Solo se descartaron dependencias/cachés regenerables junto al
checkout. El manifiesto puede contener rutas de configuración sensible y queda
fuera de Git; no se incluyen valores de secretos en este documento.

Resultado verificado: **46 worktrees eliminados: 40 backend y seis frontend**.
Se archivaron **252 archivos, 2.256.062 bytes**, con todos sus SHA-256 comprobados
de nuevo después de eliminar los checkouts. Los ocho archivos que ya tenían
cambios tracked conservaron exactamente su contenido por hash.

Permanecen los dos repositorios raíz, `pos_fifo_system_cost_controls` y
`pos_cloud_dashboard_ingresos`, que conservan trabajo/documentación propios.
No se borraron ramas, bases, instalaciones QA ni paquetes/evidencias externos
a los worktrees inventariados. El código pendiente y los archivos no versionados
preexistentes del repositorio principal permanecen.

Resultado detallado: `cleanup-results.json`; manifiesto de copias:
`preserved-files.json`. Git ya no registra worktrees obsoletos en ninguno de
los dos repositorios.
