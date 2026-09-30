# Notificaciones Web Push: despliegue y operacion

Runbook para el framework de notificaciones del portal cloud. La V1 cubre
apertura, cierre, retiro, gasto e ingreso de caja. La bandeja es la fuente
durable; Web Push es un canal que puede fallar sin afectar el sync.

Para configurar una regla existente o incorporar un tipo de evento nuevo,
seguir [`EXTENDER_NOTIFICACIONES.md`](EXTENDER_NOTIFICACIONES.md).

## Contrato operativo

- El cloud proyecta un `EventoSync` confirmado una sola vez.
- El motor nace desactivado. Al activarlo guarda un corte temporal y no genera
  avisos anteriores a ese instante.
- Quien recibe: las reglas de rol definen la base; una excepcion `INCLUIR` es
  aditiva (si aplica, impone su nivel/push, pero nunca quita lo que el rol ya
  concedio); una excepcion `EXCLUIR` gana siempre y no evalua umbrales. Ninguna
  excepcion amplia el alcance de sucursales del RBAC. La resolucion de
  destinatarios respeta los mismos guards `activo` que el motor de permisos:
  rol, negocio y sucursal inactivos no generan destinatarios (una asignacion
  global sigue recibiendo aunque una sucursal concreta este inactiva).
- En modo `Schedule`, el cron de la V1 ejecuta cada minuto en UTC. Con un POS
  sincronizando cada 60 segundos, la meta normal es hasta dos minutos desde la
  operacion. Dev/staging usan `Manual` por defecto: no hay entrega automatica
  hasta iniciar un ciclo explicitamente.
- Cada tenant se procesa en contexto propio. El fallo de uno no frena los demas.
- Endpoints, claves de dispositivo, cuerpos y montos se excluyen de logs.
- El historial se purga a los 90 dias. El marcador de `EventoSync` procesado
  permanece y evita reproyectar hechos antiguos.

## Configuracion VAPID

API y job:

```text
WEB_PUSH_ENABLED=true
WEB_PUSH_VAPID_PUBLIC_KEY=<applicationServerKey base64url>
WEB_PUSH_VAPID_SUBJECT=mailto:operaciones@dominio.com
```

Solo el job recibe `WEB_PUSH_VAPID_PRIVATE_KEY`, mediante una referencia de
Key Vault al secreto `web-push-vapid-private-key`. La privada no va en tfvars,
GitHub Actions, outputs ni configuracion del portal. Cada ambiente usa un par
VAPID diferente.

Generar una sola vez por ambiente en una carpeta temporal protegida:

```powershell
vapid --gen
vapid --applicationServerKey
```

El segundo comando imprime la publica. Cargar el PEM privado directamente:

```powershell
az keyvault secret set `
  --vault-name <vault-del-ambiente> `
  --name web-push-vapid-private-key `
  --file .\private_key.pem
```

Eliminar de forma segura los PEM locales despues de comprobar el secreto. No
copiar la privada a tickets, logs o documentos.

## Contrato REST

Todo vive bajo `/api/v1/notificaciones/`:

- `GET catalogo/`: tipos y parametros configurables;
- `GET destinatarios/`: roles y usuarios seleccionables por el administrador;
- `GET|POST reglas/` y `GET|PATCH|DELETE reglas/<id>/`: configuracion protegida
  por `notificaciones.administrar`;
- `GET /`: bandeja propia paginada de 20, con `estado`, `tipo` y `sucursal`;
- `GET resumen/`: contador sin leer y cinco avisos recientes;
- `POST <id>/marcar-leida/` y `POST marcar-todas-leidas/`;
- `GET push/config/`: habilitacion y clave publica;
- `GET|POST|DELETE push/suscripciones/`: dispositivos del usuario autenticado.

La bandeja y los dispositivos propios no exigen permiso administrativo. Una
respuesta de notificacion contiene id, tipo, nivel, titulo, cuerpo, fecha del
hecho, sucursal, datos estructurados, ruta de detalle y `leida_en`; nunca
expone reglas, entregas o suscripciones ajenas.

Un endpoint de push ya registrado por otro usuario se **transfiere** al usuario
autenticado al darlo de alta (el endpoint es estable por navegador, no por
cuenta) y se descarta la cola pendiente del dueno anterior, para que el nuevo
no reciba sus avisos. Por eso el portal advierte activar el push solo en
equipos personales; en un equipo compartido, cada login reasigna el
dispositivo.

## Despliegue seguro

### Preparar produccion sin activar notificaciones

Se puede crear el job dormido antes del piloto, con las claves VAPID propias
del ambiente ya preparadas y la privada referenciada desde Key Vault:

```hcl
enable_notifications_job    = true
notifications_trigger_type = "Manual"
web_push_enabled           = false
enable_notifications_alerts = false
container_image_digest     = "sha256:<digest-aprobado-de-64-hex>"
```

`Manual` permite Web Push apagado, pero no omite Key Vault ni ninguna clave
VAPID. `Schedule` sigue exigiendo `web_push_enabled=true`. El job conserva
0.5 CPU / 1 GiB y no arranca al crearse. El digest se usa para recursos nuevos;
el plan debe conservar las imagenes actuales de API y migraciones.

Preparar no incluye `activar_notificaciones` ni `az containerapp job start`.
Los motores nuevos nacen apagados; si ya existen, consultar su estado antes
del piloto. No registrar `PROD_AZURE_NOTIFICATIONS_JOB_NAME` en GitHub hasta
habilitar las ejecuciones: el pipeline actual inicia un ciclo al encontrar
ese job. En un ambiente sin alerta previa, mantenerla sin crear; las alertas
existentes se conservan apagadas mientras el disparador sea Manual.

Guardar y revisar el plan completo: solo job, identidad, permisos ACR/Key
Vault y las variables publicas previstas; cero reemplazos/destrucciones y
cero ejecuciones. Las referencias de secretos no acreditan que el secreto
exista: comprobar su metadata y acceso antes de aplicar, sin mostrar valores.

### Activar el piloto autorizado

1. Desplegar backend y migrar control plane y todas las bases tenant:

   ```powershell
   python manage.py migrate_cloud --settings=config.settings_cloud --noinput
   ```

2. Configurar VAPID. Mantener `enable_notifications_job=false` y
   `web_push_enabled=false`.
3. Desplegar el portal PWA. En dev/staging habilitar `web_push_enabled=true`,
   registrar dispositivos personales y verificar:

   ```powershell
   python manage.py verificar_notificaciones --tenant demo --settings=config.settings_cloud
   ```

4. Activar solo el tenant demo autorizado; fija el corte desde ahora:

   ```powershell
   python manage.py activar_notificaciones --tenant demo --settings=config.settings_cloud
   ```

5. Ejecutar un ciclo manual, todavia sin job:

   ```powershell
   python manage.py procesar_notificaciones --tenant demo --settings=config.settings_cloud
   ```

6. Activar `enable_notifications_job=true` en Terraform y aplicar. La
   precondicion exige Key Vault y VAPID configurado; para Schedule exige tambien
   Web Push habilitado.
   El job tambien debe recibir `ALLOWED_HOSTS`, porque `settings_cloud` lo
   valida al importar incluso para comandos de gestion.
   En dev/staging conservar `notifications_trigger_type="Manual"` y ejecutar
   el smoke con `az containerapp job start`. Solo para operacion programada
   explicitamente acordada, mantener el SLA de la V1 con:

   ```hcl
   notifications_trigger_type = "Schedule"
   notifications_schedule_cron = "*/1 * * * *"
   ```

   El cron es UTC y el job conserva `0.5 CPU / 1 GiB` hasta medir staging.
7. Hacer el smoke fisico. Solo despues activar otros tenants, uno por uno, o
   con `--todos-los-tenants` durante una ventana controlada.
8. Actualizar los POS. `CIERRE_CAJA` nuevo lleva `schema_version=2` y
   `resumen_turno` exacto; payloads anteriores muestran
   `fuente_resumen=cloud_estimado`.

## Smoke de aceptacion

- iPhone: Safari, instalar en Inicio, abrir el icono y pulsar Activar. Probar
  con el portal cerrado y el telefono bloqueado.
- Android Chrome y Windows Edge: autorizar desde el boton y cerrar el portal.
- Apertura y cierre; cierre con y sin diferencia; retiro, gasto e ingreso.
- Regla apagada, movimiento bajo el minimo y usuario fuera de sucursal.
- Dos dispositivos del mismo usuario: un aviso por evento en cada dispositivo
  y una sola fila en su bandeja.
- Proveedor push inaccesible: el sync responde y el aviso queda en la bandeja.

El cierre debe cuadrar con la respuesta local: ventas, pagos por metodo,
cobros CxC separados, fondo, efectivo, movimientos, esperado, contado y
diferencia.

## Diagnostico

```powershell
python manage.py verificar_notificaciones --tenant demo --settings=config.settings_cloud
```

Comprobar en orden: motor/corte, VAPID dentro del job, dispositivos activos,
entregas pendientes y proyecciones en reintento/fallidas. Los reintentos de
**push** son a 1, 5, 15, 60 y 360 minutos. HTTP 404/410 desactiva el
dispositivo; red, 429 y 5xx reintentan; otros 4xx terminan esa entrega. Los
leases vencidos se recuperan tras cinco minutos. No imprimir payloads ni
suscripciones al diagnosticar.

### Ver logs remotos en Azure

Primero confirmar la suscripcion y fijarla de forma explicita:

```powershell
az account show --query "{name:name,id:id}" --output table
az account set --subscription e88372f6-b224-4d73-bf17-c61f32559c45
```

Para un 500 de la API, ver consola reciente o seguirla en vivo:

```powershell
az containerapp logs show `
  --resource-group posfifo-dev-rg `
  --name posfifo-dev-api `
  --type console `
  --tail 100

az containerapp logs show `
  --resource-group posfifo-dev-rg `
  --name posfifo-dev-api `
  --type console `
  --follow
```

Si una revision no inicia, cambiar `--type console` por `--type system`. Para el
procesador programado, listar ejecuciones y consultar la ultima sin mostrar
secretos ni payloads:

```powershell
az containerapp job execution list `
  --resource-group posfifo-dev-rg `
  --name posfifo-dev-notifications `
  --output table

az containerapp job logs show `
  --resource-group posfifo-dev-rg `
  --name posfifo-dev-notifications `
  --container notifications `
  --tail 100 `
  --format text
```

`Ctrl+C` detiene solamente el seguimiento local de `--follow`; no apaga el
servidor ni el job.

La **proyeccion** de un `EventoSync` que revienta al construirse (p.ej. un
payload malformado) no bloquea los hechos posteriores del tenant: se reintenta
con la misma escalera (1/5/15/60/360 min) y, agotada, el marcador queda en
`FALLIDO` y no se vuelve a proyectar. `verificar_notificaciones` reporta
`proyecciones_en_reintento` y `proyecciones_fallidas`; solo guarda el nombre de
la excepcion, nunca el payload. La purga de historial avanza en lotes de 1000
por ciclo (cada minuto) mientras el lote venga lleno, sin esperar 24 h entre
lotes; solo cierra la ventana diaria cuando el backlog quedo drenado.

## Alerta de salud del job

Cada ambiente puede crear un Action Group y una alerta de Log Analytics:

```hcl
enable_notifications_alerts = true
notifications_alert_email   = "genaosantiago001@gmail.com"
```

Solo crearla junto con `enable_notifications_job=true`. En modo `Manual` la
regla se conserva deshabilitada, aunque `enable_notifications_alerts=true`.
Al elegir `Schedule`, se habilita. La regla habilitada se evalua
cada minuto y alerta cuando no encuentra una ejecucion `Completed` exitosa en
cinco minutos. Si staging reutiliza el Container Apps Environment de dev, sus
system logs viven en el workspace de ese runtime compartido; Terraform lo
resuelve desde el Environment en vez de consultar el workspace logico de
staging.

Consulta sana (debe devolver cero filas mientras el job corre):

```kusto
ContainerAppSystemLogs_CL
| where TimeGenerated > ago(5m)
| where JobName_s == 'posfifo-dev-notifications'
| where Reason_s == 'Completed'
| where Log_s has 'Execution' and Log_s has 'successfully completed'
| summarize successful_executions = count()
| where successful_executions == 0
```

Para probar la condicion sin apagar nada, sustituir temporalmente el nombre por
uno inexistente en una consulta manual: debe devolver una fila. No cambiar la
regla real para esa prueba.

El primer correo de Azure exige confirmar el opt-in/OTP del receptor. Despues,
probar el Action Group:

```powershell
az monitor action-group test-notifications create `
  --resource-group posfifo-dev-rg `
  --action-group posfifo-dev-notifications-ag `
  --alert-type logalertv2 `
  --add-action email notifications-operations genaosantiago001@gmail.com usecommonalertschema
```

En un mantenimiento planificado, **deshabilitar la alerta antes de detener el
job** y rehabilitarla solo despues de observar una ejecucion exitosa.

## Desactivar

### QA bajo demanda: conservar recursos sin ejecuciones automaticas

Politica aplicada el 2026-09-28 en dev/staging:

```hcl
enable_notifications_job    = true
notifications_trigger_type  = "Manual"
enable_notifications_alerts = true
```

El ultimo flag conserva la regla y el Action Group existentes; el modulo
deshabilita la regla cuando el disparador es `Manual`. Si el ambiente nunca
tuvo alertas, dejarlo en `false`, sin crear recursos innecesarios. La identidad,
imagen, VAPID, bandeja y eventos se conservan. El registro de dispositivos puede
seguir habilitado, pero los avisos nuevos esperan el proximo ciclo manual.
Apagar solo Web Push o el motor por tenant no detiene el cron de Azure.

Ejecutar una prueba bajo demanda (seleccionar el ambiente autorizado):

```powershell
az containerapp job start --subscription e88372f6-b224-4d73-bf17-c61f32559c45 `
  --resource-group posfifo-staging-rg --name posfifo-staging-notifications
az containerapp job execution list --subscription e88372f6-b224-4d73-bf17-c61f32559c45 `
  --resource-group posfifo-staging-rg --name posfifo-staging-notifications --output table
```

El ciclo debe terminar `Succeeded`. El CI tambien inicia un ciclo explicitamente
cuando despliega y encuentra el job; esa ejecucion puntual no rehabilita el cron.
No es necesario desactivar suscripciones push ni tocar datos para ahorrar.

**Cambio de tipo en un recurso existente:** azurerm 4.75/4.76 marca
`manual_trigger_config`/`schedule_trigger_config` como reemplazo. No aplicar un
plan que recree el job sin revisar imagen y recursos: `ignore_changes` de imagen
no protege una recreacion y puede recuperar una etiqueta mutable del tfvars.
Para conservar el recurso, seguir este orden, con inputs del ambiente conciliados:

1. Configurar `Manual` en IaC, validar y revisar el plan; guardar el estado previo
   y la imagen exacta en una carpeta local ignorada por Git.
2. Deshabilitar primero la regla mediante PATCH a su recurso
   `Microsoft.Insights/scheduledQueryRules`, API `2023-12-01`, cuerpo
   `{"properties":{"enabled":false}}`. Verificar `enabled=false`.
3. PATCH al recurso `Microsoft.App/jobs`, API `2025-07-01`, cuerpo:

   ```json
   {"properties":{"configuration":{"triggerType":"Manual","scheduleTriggerConfig":null,"manualTriggerConfig":{"parallelism":1,"replicaCompletionCount":1}}}}
   ```

   Usar `az rest --method patch --url <URL-ARM-del-recurso> --body @<archivo.json>`.
   El ID debe pertenecer al ambiente autorizado. No enviar secretos, plantilla
   de contenedor, imagen ni identidad en este PATCH.
4. Consultar con la misma version REST y comprobar `Manual`, estado `Succeeded`
   y plantilla, identidad, registry y referencias de secretos identicos al previo.
5. Generar un nuevo plan completo con la IaC actualizada. Debe mostrar **cero
   cambios de recursos**. Aplicar ese plan guardado para persistir la reconciliacion
   en el state remoto. No aplicar el plan anterior que proponia reemplazo.
6. Repetir plan: cero cambios. Verificar un ciclo manual y ausencia de nuevos
   arranques programados durante al menos cinco minutos.

El rollback a cron requiere una ventana de pruebas acordada. Actualizar IaC a
`Schedule`, mantener inicialmente la alerta deshabilitada y usar el PATCH inverso
con `manualTriggerConfig=null` y el `scheduleTriggerConfig` acordado. Tras un ciclo
exitoso, rehabilitar la alerta y reconciliar Terraform. No restaurar el cron 24/7
solo por terminar una prueba o ejecutar un despliegue.

### Retirar el job o apagar el motor del tenant

```powershell
python manage.py activar_notificaciones --tenant demo --desactivar --settings=config.settings_cloud
```

Para detener toda la plataforma, aplicar Terraform con
`enable_notifications_job=false`. Antes, deshabilitar
`enable_notifications_alerts`; la bandeja existente sigue disponible.
Ese flag elimina el job, su identidad y sus asignaciones. Para QA en pausa,
preferir el modo manual anterior.

El usuario desvincula su dispositivo desde `/notificaciones`. Operaciones:

```powershell
python manage.py desactivar_dispositivos_push --tenant demo --usuario ana --settings=config.settings_cloud
python manage.py desactivar_dispositivos_push --tenant demo --todos-dispositivos --settings=config.settings_cloud
```

## Rotar VAPID

La rotacion obliga a volver a vincular los dispositivos:

1. detener el job;
2. generar/cargar el nuevo par y actualizar la publica;
3. desactivar todas las suscripciones afectadas;
4. desplegar y verificar;
5. pedir a los usuarios pulsar nuevamente Activar;
6. rehabilitar el job y repetir el smoke.

No borrar bandeja ni eventos durante la rotacion.
