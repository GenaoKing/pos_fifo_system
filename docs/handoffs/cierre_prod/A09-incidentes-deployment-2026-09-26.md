# A09 — incidentes del despliegue staging y de la PC de QA

**Corte de evidencia:** 2026-09-26 11:33 UTC. Este registro distingue defectos
corregidos, obstáculos de instalación y pruebas todavía pendientes. No convierte
un smoke, un respaldo o una CI verde en aceptación de G1–G4. El recorrido
anterior está en [A09 dev](A09-dev-preflight-2026-09-24.md),
[A09 staging](A09-staging-2026-09-25.md),
[QA en PC](A09-qa-pc-staging-2026-09-25.md) y
[C06.1 Windows](C06.1-procedimiento-windows-completo.md).

## Corte exacto y dónde está la evidencia

| Superficie | Corte comprobado | Límite |
| --- | --- | --- |
| Backend integrado | `develop@47ec784776ce1d496bd028aa9d2f6dab18c39bc9` (PR #31 y #32) | El árbol de código coincide con `staging` en este corte; los SHAs difieren por el merge de promoción. |
| Backend staging | `staging@8213ba58564b293be094578edd941d0103e540e1`, [PR #33](https://github.com/GenaoKing/pos_fifo_system/pull/33), [workflow 36161811256](https://github.com/GenaoKing/pos_fifo_system/actions/runs/36161811256) verde | Revisión `posfifo-staging-api--0000020`, imagen OCI `sha256:83803cb6350c4b6d55fba3b7b7fd40919394f3a40067c010a383bb40dafa9ce1`. El manifiesto de CI pasó `--verify --assert-git-source --require-promotable` contra checkout limpio del SHA exacto. |
| POS QA de Santiago | Código local `5f3e89b1fcb2`, BD **nueva** `pos_stage_qa`, servicios `POSFifoStagingQA`/`POSFifoStagingQASync`, sucursal `QA-PC-01` en `staging_demo` | Se mantuvo funcionando sin actualizar el código local durante el ensayo. No llamar a esta PC instalación del paquete final `8213ba5`. |
| Paquete Windows del nuevo SHA | Dos construcciones desde `git archive` y 36 wheels: ZIP SHA-256 `5576FBA7A8505572BCF2E67BAC49B46C665F3CEB1DE01FF0B6A81F33470B2045` | Sigue siendo candidato local, no paquete aprobado para Royal Plast/SK. |
| Bases de staging | `pos_fifo_staging`, `tnt_staging_demo` y `tnt_staging_royalplast` respaldadas antes del PR #33; dumps custom validados con `pg_restore --list` y restaurados completos en tres BDs locales nuevas | Tras el job `posfifo-staging-migrate-pjbjkkl` (`Succeeded`), consulta directa de solo lectura: **155 filas `django_migrations` en cada BD**; dos tenants activos. No hubo migraciones nuevas en el diff #33. |

Los dumps están fuera de Git bajo
`C:\Proyectos\_lab_a09_rig_20260924\staging_backup_pre_qa_fix_20260925`;
SHA-256 control `CDD196593131125F351ADDAD703B1D5EB3C0A2C9E96695BFDD9C3BEC4E4D3152`,
demo `E06FA17033F5BAB5F0A194AC2245861A40BBDC5AD6C47BB790757192912F48F1`,
royalplast `BBEB518530993E106B0AB75CA7B9CC03487E3A5ADA95996933305CBED2413014`.
El respaldo base **local** de QA está en
`C:\Proyectos\pos_fifo_staging_qa\baseline_before_manual_qa.dump`, SHA-256
`30F6F7B8B26703FBFDD6FCF8664AE1534F4568EFEF49D444F75D272583721F3C`.
Ninguno de estos artefactos ni los tokens/contraseñas se versiona.

## Defectos reproducidos y corregidos

### A09-DEP-001 — token de sucursal válido en tenant, pero pull HTTP 401

- **Síntoma/reproducción:** `vincular_sucursal_token --sucursal QA-PC-01`
  entregaba un DRF Token en `tnt_staging_demo`; el primer pull de un POS nuevo
  respondía 401 aunque el token y la sucursal coincidían.
- **Causa:** en cloud DB-per-tenant la autenticación busca primero el hash en
  `tenancy_sync_tokens` del control plane. El comando anterior solo creaba el
  token en la BD tenant. Registrar manualmente ese hash en staging desbloqueó
  el ensayo, pero fue una reparación puntual, no el procedimiento reusable.
- **Corrección:** [PR #32](https://github.com/GenaoKing/pos_fifo_system/pull/32)
  registra ambos lados, comprueba el contexto técnico y permite rotar ambos con
  `--regenerar`; [el runbook de instalación](../../runbooks/INSTALACION_CLIENTE_NUEVO.md)
  exige `with_tenant --tenant <tenant_key> --` en DB-per-tenant. Los tests del
  comando pasaron y el POS descargó 273 productos; posteriormente el sync local
  quedó `EXITOSO` y `verificar_sync` dijo «sin pérdida detectada».
- **Si reaparece:** comprobar tenant/sucursal y existencia/estado del hash en
  control plane **sin imprimir el token**. No pegar tokens en tickets ni
  insertarlos a mano en clientes; usar el comando corregido, o rotar ambos
  registros de forma controlada si difieren.

### A09-DEP-002 — alta de asignación RBAC devolvía 500 y hacía rollback

- **Síntoma/reproducción:** `POST /api/v1/permisos/asignaciones/` para dar a
  `qa_santiago` visibilidad global de `staging_demo` devolvía 500. El log
  registraba `AuditContractError: AUDIT_CONTEXT_INVALID: El tenant explicito no
  coincide con el contexto.` No quedó asignación después del fallo.
- **Causa:** cinco productores RBAC de CT-01 pasaban `Negocio.slug`
  (`pos-fifo-staging-demo`) como tenant de auditoría dentro de `tnt_staging_demo`,
  cuyo identificador técnico es `staging_demo`.
- **Corrección:** [PR #31](https://github.com/GenaoKing/pos_fifo_system/pull/31)
  usa el contexto técnico activo en las mutaciones RBAC y prueba el contrato.
  Promovido por PR #33: la asignación se creó por API y se descargó al POS;
  con el usuario QA, productos, clientes, ventas de `QA-PC-01` y configuración
  respondieron 200. Chrome real abrió dashboard, 273 productos y clientes.
- **Si reaparece:** verificar el `tenant_key` del contexto y el alias `tnt_*`
  antes de reintentar; no desactivar CT-01 ni sustituirlo por el slug comercial.
  La asignación global de QA solo vale dentro de `staging_demo` y no es un
  patrón automático para operadores reales.

### A09-DEP-003 — registro Windows del daemon ignoraba `.env` correcto

- **Síntoma/reproducción:** `deploy/registrar_sync_servicio.bat` detenía el alta
  diciendo que `SYNC_ENABLED` no era `true` o que faltaba `CLOUD_API_TOKEN`,
  aunque ambos estaban en `deploy/env_cliente.env`. Revisaba variables BAT
  heredadas del proceso y mensajes que todavía pedían `env_cliente.bat`.
- **Corrección:** PR #32 añadió `deploy/check_sync_env.py`, parser de stdlib
  que valida únicamente cuatro claves sin mostrar el token, y lo conectó con
  el BAT. Se probó con `.env` sintéticos y el `.env` real de QA; el servicio
  NSSM de esta PC funciona con `POS_ENV_FILE` y
  `DJANGO_SETTINGS_MODULE=config.settings_production`.
- **Si reaparece:** ejecutar
  `venv\Scripts\python.exe deploy\check_sync_env.py deploy\env_cliente.env`
  desde la instalación, comprobar la salida `[OK]` y después revisar NSSM.
  No volcar el archivo de configuración a logs ni usar `set`/`echo` para
  diagnosticar el token.

### A09-DEP-004 — el primer CI de la corrección Windows falló sin dependencias

La primera versión de `check_sync_env.py` importaba `python-dotenv`, pero el
job de tooling release instala solo la stdlib antes de probar el paquete.
El [PR #32](https://github.com/GenaoKing/pos_fifo_system/pull/32) sustituyó
ese import por un parser mínimo de las cuatro claves y añadió regresiones;
la CI completa final quedó verde. **Regla para futuros preflights de release:**
si corren antes del `pip install`, deben ser de stdlib o declarar explícitamente
su bootstrap. Un test Django en un venv rico no prueba esa fase.

### A09-DEP-013 — el runbook copiaba el directorio equivocado del paquete

Durante la revisión documental del 2026-09-26 se cotejó el comando de copia
con `deploy/preparar_paquete.bat`: el BAT construye `dist\pos_fifo_system\`,
pero el runbook mandaba `xcopy /E /I dist C:\pos_fifo_system`. Eso deja
`manage.py` en `C:\pos_fifo_system\pos_fifo_system\`, mientras el paso siguiente
lo busca en la raíz. **Es una inconsistencia reproducible por rutas; no se
ejecutó de nuevo una instalación física para este cambio documental.** Se
corrigió el runbook para copiar `dist\pos_fifo_system` y verificar
`C:\pos_fifo_system\manage.py` antes de continuar. El mismo paso de dependencias
ahora utiliza el wheelhouse offline real y `--require-hashes`; el comando
anterior `pip install -r requirements.txt` habría consultado internet.

## Obstáculos operativos y observaciones que siguen abiertos

| ID | Observación comprobada | Estado y respuesta si reaparece |
| --- | --- | --- |
| A09-DEP-005 | Una asignación Administrador **solo de sucursal** dejaba hacer login al portal, pero catálogo/reportes respondían 403 porque esas vistas requieren permiso global. | **Contrato de alcance, no 500.** Se dio rol global únicamente a la identidad QA en `staging_demo` después del fix 002. Para usuarios de tienda, decidir el alcance necesario; no ampliar permisos globales para ocultar un 403. |
| A09-DEP-006 | En un POS limpio se difirieron dos asignaciones globales heredadas de `admin` y `sucursal_service_STG-01`: `_pull_asignaciones` no crea usuarios locales por seguridad. | **Mitigado solo en la BD QA**, con identidades inactivas y contraseña inutilizable; 0 diferidos después. Sigue abierta la decisión de onboarding/scope para instalaciones reales. No crear cuentas utilizables automáticamente ni llamar al ciclo `PARCIAL` un éxito. Verificar `DiferidoSync` y el usuario esperado antes de tocar cursores. |
| A09-DEP-007 | El plan de `staging_demo` anunciaba e-CF, pero el pull de configuración de una instalación nueva no tenía `emisor_activo` local. | **Prerequisito fiscal, no defecto resuelto.** Se apagó e-CF con `SucursalModuloOverride` solo para `QA-PC-01`, donde no habrá emisión fiscal. Una tienda con e-CF requiere su emisor y configuración real; nunca copiar este override como solución general. |
| A09-DEP-008 | El monitor viejo de `staging@5f3e89b` guardó 60 muestras, 12 errores de conexión/timeout. El nuevo de `8213ba5` tuvo 3 respuestas sanas y luego 12 `ConnectionError` inmediatos (0–16 ms) hasta 2026-09-25 18:08 UTC; **no escribió más hasta 2026-09-26 11:26 UTC**, cuando reanudó con dos respuestas 200 a los cinco minutos de intervalo. | **Causa no determinada; G2 no acreditado.** Azure reportó revisión sana y la API respondió después desde la misma PC, por lo que no se puede atribuir el fallo al servicio ni declararlo inocuo. Repetir 24 h desde un runner que no duerma, correlacionar con Azure y red local, registrar arranque en frío y exigir cobertura temporal continua. Un proceso vivo o 288 intentos separados por una pausa no prueban 24 h. |
| A09-DEP-009 | La conexión directa al PostgreSQL de Azure tuvo un timeout desde la PC antes de leer/escribir; en otro intento funcionó. Además, un hostname supuesto para «staging PG» no resolvía: el servidor real es compartido entre ambientes. | **Diagnóstico, no causa raíz confirmada.** Obtener `DB_HOST` de la Container App del ambiente, identificar explícitamente las tres BD autorizadas, y verificar red/SSL/Key Vault antes de operar. No inferir host por nombre de resource group ni usar un comando que afecte todo el servidor compartido. |
| A09-DEP-010 | Azure limpió la réplica del job de migración antes de consultar su log detallado. El workflow conservó `Succeeded` y «Migrations OK», pero eso por sí solo no enumera cada tenant. | **Evidencia recompuesta:** consulta de solo lectura a las tres BDs mostró 155 migraciones en cada una y control plane con `staging_demo` y `staging_royalplast` activos. Para una promoción con migraciones nuevas, capturar el ledger por base durante la ejecución, además de backup/restore y estado del job. |
| A09-DEP-011 | `POS-80C` en USB002 figuraba `Normal`; `2connect pos` en CP003 figuraba `Error`. El POST real a `/impresion/test/` del servicio Windows devolvió éxito para `POS-80C`. | **Papel aún sin confirmación visual.** El usuario eligió `POS-80C`; comprobar salida física, cola y cuenta del servicio. Un HTTP 200 no prueba ticket impreso. La Zebra quedó fuera de este objetivo. |
| A09-DEP-012 | La PC QA sigue con código `5f3e89b` mientras cloud y ZIP nuevo son `8213ba5`. | **Compatibilidad observada, actualización local no acreditada.** No sobrescribir código bajo servicios de QA activos sin una ventana y respaldo fresco. Antes de cerrar G2 probar la actualización de esa instalación, mismo SHA/paquete, reinicio/offline y flujos comerciales completos. |

## Hallazgos anteriores del mismo cierre que no deben perderse

| Tema | Resultado y referencia |
| --- | --- |
| `permisos.0011` en BD poblada | El `unique=True` directo repetía UUID y fallaba al crear `roles_cloud_id_key`; `dec46a3` separó alta nullable, backfill por fila y constraint. El ensayo C06.1 debía usar ese fix. Ver [integración total](INTEGRACION-TOTAL-2026-09-24.md). |
| Orfandad de `sucursales_sucursal` en control plane dev | `django_migrations` indicaba aplicadas `sucursales.0001–0003` sin tabla física. La reparación antigua rompía dependencias; la dirigida se probó en restore y luego en dev con precondiciones exactas. Ver [preflight dev](A09-dev-preflight-2026-09-24.md). |
| `.bat` del procedimiento Windows | Claude detectó `instalar.bat` obsoleto/roto, wheelhouse desconectado de `preparar_paquete.bat`/`actualizar.bat` y paréntesis inválidos dentro de `if`; quedaron corregidos y las ocho fases pasaron en copia histórica RP con `settings_production`. La invocación literal con UAC y ese dump no se hizo. Ver [C06.1 Windows](C06.1-procedimiento-windows-completo.md). |
| Paquete offline real | El cierre posterior evitó que `pip install --upgrade pip` buscara PyPI en la ruta offline, exigió CPython 3.11 x64 y no ignoró fallo de `collectstatic`. Ver [rig local](A09-rig-local-2026-09-24.md). |
| Portal CT-01 | Un cambio de límite de cliente no dejaba auditoría; PR #27 lo corrigió. En `staging_demo` se verificó un evento por mutación de cliente y producto, sin filas homólogas en `staging_royalplast`. Las rutas HTML locales siguen fuera de esa garantía. Ver [acta staging](A09-staging-2026-09-25.md). |
| Matriz de versiones y fixtures | POS nuevo contra cloud anterior recibía 404 en resoluciones CT-04 y dejaba ciclo parcial: orden elegido, cloud primero. Una copia demo tenía `sync_diferidosync` marcada migrada sin tabla y se excluyó. Un evento sintético de apertura para `admin` chocó con turno abierto; se repitió con cuenta sin turno y deduplicó correctamente. Las imágenes 404 del dump RP antiguo no prueban fallo de código porque faltaba `media/`. Ver [rig local](A09-rig-local-2026-09-24.md) y [acta staging](A09-staging-2026-09-25.md). |
| Seguridad | [SEC-001](../../BUGS.md#sec-001--credenciales-de-prueba-en-un-archivo-versionado): archivo actual saneado, pero rotación/revocación e historial requieren decisión autorizada. No copiar ningún secreto de laboratorios al repositorio. |

## Trampas de Git y de evidencia

- `staging` recibió merges de promoción: no exige que `origin/staging` sea
  ancestro lineal de `origin/develop`. Comparar árbol/diff y usar PR; **no**
  rebase o force-push para «arreglar» la divergencia de merges. Al corte del
  PR #33, `git diff origin/develop origin/staging` estaba vacío.
- `gh pr merge 32 --merge --delete-branch` **fusionó remotamente** aunque avisó
  que no podía borrar la rama local usada por un worktree. Verificar
  `gh pr view --json state,mergeCommit` antes de interpretar ese aviso como
  merge fallido o repetirlo.
- El manifiesto OCI por digest y el ZIP Windows determinista son **artefactos
  distintos**. Ninguno prueba por sí solo copia reciente de RP/SK, restore en
  cliente, papel impreso o aceptación del operador.

## Trabajo pendiente para cerrar esta simulación

1. Confirmar en papel la prueba `POS-80C` y ejecutar desde pantalla apertura,
   venta de contado, ticket, venta a crédito, abono y cierre con datos sintéticos.
   Conciliar cada hecho en `QA-PC-01` y `verificar_sync`; registrar hora y número
   de venta si falla. Al corte del 2026-09-26, `verificar_sync` aún veía **0
   ventas/aperturas/cierres** en esta instalación y cola vacía.
2. Repetir la medición continua de 24 h desde una máquina/runner encendido y
   correlacionar los errores con Azure. Probar offline/reinicio y revocación RBAC.
3. Actualizar la PC QA desde el paquete exacto del SHA promovido bajo respaldo
   fresco y ventana sin uso; repetir instalación/sync. Mantener aparte la
   autorización de G3/G4 y las copias actuales por tienda.

Producción, Royal Plast y SK Performance no se modificaron en este despliegue.
