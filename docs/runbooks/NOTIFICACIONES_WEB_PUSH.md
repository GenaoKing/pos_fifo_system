# Notificaciones Web Push: despliegue y operacion

Runbook para el framework de notificaciones del portal cloud. La V1 cubre
apertura, cierre, retiro, gasto e ingreso de caja. La bandeja es la fuente
durable; Web Push es un canal que puede fallar sin afectar el sync.

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
- El job se ejecuta cada minuto en UTC. Con un POS sincronizando cada 60
  segundos, la meta normal es hasta dos minutos desde la operacion.
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
   precondicion exige Key Vault, Web Push habilitado y clave publica no vacia.
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

La **proyeccion** de un `EventoSync` que revienta al construirse (p.ej. un
payload malformado) no bloquea los hechos posteriores del tenant: se reintenta
con la misma escalera (1/5/15/60/360 min) y, agotada, el marcador queda en
`FALLIDO` y no se vuelve a proyectar. `verificar_notificaciones` reporta
`proyecciones_en_reintento` y `proyecciones_fallidas`; solo guarda el nombre de
la excepcion, nunca el payload. La purga de historial avanza en lotes de 1000
por ciclo (cada minuto) mientras el lote venga lleno, sin esperar 24 h entre
lotes; solo cierra la ventana diaria cuando el backlog quedo drenado.

## Desactivar

```powershell
python manage.py activar_notificaciones --tenant demo --desactivar --settings=config.settings_cloud
```

Para detener toda la plataforma, aplicar Terraform con
`enable_notifications_job=false`. La bandeja existente sigue disponible.

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
