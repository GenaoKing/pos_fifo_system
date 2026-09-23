# A08.1 — candidato reproducible, manifiesto y restore/rollback

Estado: **preparacion local; no es una autorizacion de despliegue**. Este
runbook no ejecuta Terraform, Azure, POS ni bases reales. El alcance es el
backend desde un SHA inmutable, sus locks, la imagen Docker y el ensayo de
restauracion que debera realizarse despues en copias autorizadas.

## Invariantes

- Un candidato identifica el SHA Git completo, los cuatro locks, la receta
  Docker, cada migracion y su hash SHA-256.
- Una imagen candidata se promueve solo como `registry/repo@sha256:...`. Un tag
  (`main`, `prod` o SHA) ayuda a encontrarla, pero no es la identidad aprobada.
- El manifiesto es evidencia sin secretos ni datos operativos. Se genera en CI,
  se adjunta a la corrida y no se inventa a mano.
- `pg_restore --list` valida el indice de un dump; **no** acredita un restore.
- La API nunca se actualiza despues de una migracion parcial. Una migracion
  fallida conserva la imagen anterior y deja un ledger de control plane/tenant
  para decidir el siguiente paso.
- Un rollback de codigo no deshace esquema ni datos. Restaurar sobre una base
  que recibio escrituras posteriores al backup esta prohibido sin un plan de
  preservacion, replay y reconciliacion aprobado.

## CI de candidato local

`.github/workflows/release-reproducibility.yml` no contiene credenciales de
Azure ni pasos de deploy. En cada cambio de Docker, locks o migraciones hace lo
siguiente:

1. Genera un manifiesto de fuentes con el SHA de la corrida. La lista de
   migraciones se obtiene por AST sin arrancar Django ni abrir una base.
2. Verifica los cuatro locks, `--require-hashes` y la base Docker fijada por
   digest.
3. Construye `pos-fifo-backend:ci-<sha>` con la base fijada, registra el image
   ID local (en un campo distinto del digest OCI) y corre `pip check` dentro de
   ella.
4. Adjunta `release-manifest.json` como artefacto retenido 30 dias. Su estado
   `LOCAL_BUILD_EVIDENCE` prueba el build, pero no permite promover nada.

Para repetirlo en un worktree limpio, sin DB ni servicios externos:

```powershell
$sha = git rev-parse HEAD
python scripts/release/release_manifest.py `
  --write "$env:TEMP\release-manifest-prebuild.json" `
  --source-sha $sha `
  --assert-git-source
python scripts/release/release_manifest.py `
  --verify "$env:TEMP\release-manifest-prebuild.json" `
  --expect-source-sha $sha

$epoch = git show -s --format=%ct $sha
docker build `
  --iidfile "$env:TEMP\pos-fifo-backend.iid" `
  --build-arg VCS_REF=$sha `
  --build-arg SOURCE_DATE_EPOCH=$epoch `
  -t "pos-fifo-backend:ci-$sha" .
docker run --rm --entrypoint python "pos-fifo-backend:ci-$sha" -m pip check
```

El `iidfile` identifica un build local y no sustituye el digest del registry.
Al publicar un candidato autorizado, el pipeline que haga push debe consultar
el digest remoto, regenerar el manifiesto con la referencia
`<registry>/<repo>@sha256:<digest>` y validarlo con
`--require-promotable`. El job de deploy consume exactamente esa referencia; no
vuelve a ejecutar `docker build`.

## Manifiesto de migraciones

El generador es `scripts/release/release_manifest.py`. El campo
`migrations.files` contiene ruta, SHA-256 y la expresion de dependencias
declarada de cada migracion. Es una fotografia de codigo, no una declaracion de
migraciones aplicadas.

Antes de una corrida autorizada se adjuntan al ticket los siguientes cuatro
elementos, sin secretos:

1. Manifiesto promocionable del backend y, cuando exista, los manifiestos
   equivalentes de frontend y paquete Windows (CT-05/C06).
2. `manage.py migrate --plan` para el control plane y un listado de los
   `tenant_key` activos obtenido por el operador autorizado. No guardar el
   inventario de clientes en Git.
3. Ledger por base: control plane y cada `tnt_<tenant_key>`, con backup, hash,
   tamano, resultado de migracion, hora de inicio/fin y responsable.
4. Decision de compatibilidad backend/portal/POS. El frontend y el actualizador
   Windows no se fusionan/actualizan en la misma ventana por defecto.

`migrate_tenants` ya imprime resultados por tenant y falla si faltan tablas
fisicas. Usar su salida como ledger de ejecucion; no reemplazarla por un health
verde. Ante el primer error se detiene por defecto. `--continuar-ante-fallo`
solo se usa con decision explicita para diagnosticar la flota, nunca para
declarar exito parcial.

## Plan de restore aislado (gate pendiente)

Este plan requiere una ventana autorizada, dumps recientes y capacidad para
crear bases **descartables**. No se ha ejecutado por A08.1.

1. Declarar los objetivos de copia antes de escribir: un control plane y una
   copia de cada tenant activo. Los nombres de destino deben incluir ticket y
   fecha; verificar que no sean una base existente ni una ruta de backup real.
2. Producir dumps custom con `backup_tenant` para tenants y con `pg_dump` para
   control plane. Registrar SHA-256, bytes y la salida de `pg_restore --list`.
   Si un dump no se puede verificar, el gate falla.
3. Crear BDs de restauracion vacias y restaurar con `pg_restore --exit-on-error
   --no-owner --no-privileges`. La conexion de restore solo puede tener permiso
   sobre esas BDs descartables. Nunca usar `--clean` contra una base fuente.
4. En el control plane clonado, remapear los `db_name` de `Tenant` a las BDs
   clonadas mediante un script revisado dentro de la misma copia. Confirmar el
   mapa `tenant_key -> alias -> DB clonada` antes de invocar Django; de otro
   modo `migrate_tenants` puede apuntar a la base real.
5. Cargar exclusivamente el entorno de restore y ejecutar, en orden,
   `migrate --noinput` para control plane y `migrate_tenants --noinput` para
   todos los tenants clonados. Capturar el ledger entero; comparar
   `tablas_faltantes == []` y el plan de migraciones esperado del manifiesto.
6. Correr checks de identidad/tenancy y comparaciones de conteos, saldos y
   hechos acordadas antes del ejercicio. Las consultas y comandos de prueba se
   ejecutan sobre aliases clonados, nunca sobre `default` operativo.
7. Registrar RPO (antiguedad del backup), RTO medido por fase, errores y limpieza
   de todas las BDs/archivos **de prueba** al final. La limpieza solo ocurre
   despues de conservar el ledger y con los nombres validados del paso 1.

El resultado aprobado es un restore completo de control plane **y todos los
tenants** en aislamiento, no una lista de objetos. Si se requiere restaurar un
solo tenant en produccion, es una incidencia distinta: exige analizar la
consistencia entre control plane, tenant, media y escrituras posteriores.

## Decidir rollback o fix-forward

| Situacion | Accion permitida | No hacer |
| --- | --- | --- |
| Falla antes de migrar | Mantener la imagen anterior; investigar el candidato. | Actualizar API para probar si “arregla” la migracion. |
| Falla una migracion, sin API nueva | Parar el gate, conservar backups y ledger; reparar/avanzar solo con revision. | Marcar el resto de tenants como exitoso. |
| API nueva falla, esquema compatible | Volver a la imagen anterior por digest y verificar health. | Rebuild con el mismo tag. |
| Ya hubo escrituras despues del backup | Preservar evidencia y preferir fix-forward/replay/reconciliacion. | Restaurar el backup encima de la base activa. |
| Restore total aprobado, sin escrituras posteriores | Restaurar control plane, tenants y media como conjunto en una ventana autorizada. | Restaurar un tenant aislado ignorando identidades o media. |

El rollback de paquete POS, sus servicios y el wheelhouse sigue siendo C06. Su
artefacto debe referenciar el mismo CT-05, declarar compatibilidad de esquema y
aportar su propio ensayo; A08.1 no modifica `deploy/**` ni servicios Windows.

## Criterio de cierre de A08.1

A08.1 entrega un gate local reproducible y el procedimiento de ensayo. No
cierra A08/G1 ni acredita Docker remoto, restore fisico, migraciones de una
instalacion, Terraform, preflight operativo o despliegue. Esos pasos requieren
autorizacion y evidencia nueva por candidato.
