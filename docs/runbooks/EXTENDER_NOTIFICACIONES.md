# Extender notificaciones y configurar reglas

Este runbook cubre dos tareas distintas:

1. configurar destinatarios para un evento que ya existe en el catálogo; y
2. incorporar al producto un tipo de evento nuevo.

La operación completa sigue este recorrido:

```text
operación POS + EventoSync (misma transacción)
        -> sync cloud confirma el hecho idempotentemente
        -> job proyecta el evento notificable
        -> reglas + RBAC resuelven un destinatario por usuario
        -> bandeja persistente + una entrega por dispositivo push activo
```

El runbook de despliegue, VAPID, diagnóstico y rotación sigue siendo
[`NOTIFICACIONES_WEB_PUSH.md`](NOTIFICACIONES_WEB_PUSH.md).

## Configurar una regla para un evento existente

Requisito: el usuario que configura debe tener
`notificaciones.administrar`. La lectura de la bandeja propia y la gestión de
los dispositivos propios no requieren ese permiso.

1. Abrir **Configuración > Notificaciones** en el portal
   (`/configuracion/notificaciones`).
2. Elegir el destino:
   - **Rol**: crea la regla base para todos los usuarios con una asignación
     activa de ese rol.
   - **Usuario / Incluir**: fuerza el evento para esa persona dentro de las
     sucursales que ya le permite RBAC.
   - **Usuario / Excluir**: impide que esa persona lo reciba aunque alguno de
     sus roles tenga una regla activa.
3. Elegir el evento, activar o desactivar la regla, indicar si debe enviar Web
   Push y completar los parámetros que publique el catálogo.
4. Guardar y revisar el indicador de usuarios que tienen al menos un
   dispositivo push activo.
5. Generar un hecho nuevo y comprobar bandeja, push y ruta de detalle. No usar
   un hecho anterior como prueba.

Reglas importantes:

- Los cambios aplican solamente a eventos futuros; no hay backfill.
- Sin una regla de rol aplicable ni una inclusión personal, no se crea aviso.
- Una inclusión personal nunca amplía el negocio o la sucursal concedidos por
  RBAC. Una exclusión personal siempre gana.
- Varios roles coincidentes producen una sola fila de bandeja por usuario.
- Desactivar **Enviar Web Push** conserva la notificación en la bandeja.
- Un push activo crea una entrega por cada dispositivo activo del usuario, no
  una notificación de bandeja adicional.

Actualmente el portal renderiza de forma dinámica parámetros de tipo
`dinero`, como `monto_minimo` y `umbral_diferencia`. Agregar al catálogo otro
evento sin parámetros, o con parámetros monetarios, no requiere una pantalla
nueva. Un tipo de parámetro distinto sí requiere ampliar y probar
`src/lib/notifications.ts` y `src/pages/NotificationSettings.tsx` en el repo
`pos-cloud-dashboard`.

## Incorporar un evento nuevo al producto

### 1. Diseñar el contrato

Definir antes de programar:

- un código sync estable en mayúsculas, por ejemplo `STOCK_BAJO_DETECTADO`;
- un código público de notificación estable y con puntos, por ejemplo
  `inventario.stock_bajo`;
- la sucursal, fecha del hecho, actor, identificador local estable y datos que
  necesita el título, cuerpo y detalle;
- qué parámetros puede configurar una regla y cuándo el nivel es `ALERTA`;
- si el hecho es primario, una transición derivada o un snapshot reemplazable.

El payload debe ser inmutable y autosuficiente. Si una cifra debe conservar su
valor histórico, calcularla en el POS y guardar el snapshot en el evento; no
reconstruirla más tarde consultando estado mutable del cloud. Serializar dinero
como string decimal. No incluir credenciales, tokens ni datos que no deban
aparecer en la bandeja o en un push.

Agregar un tipo al catálogo no exige cambiar el esquema de notificaciones. Una
migración solo es necesaria si cambia un modelo o si se deben sembrar reglas
para tenants existentes.

### 2. Emitir y sincronizar el hecho desde el POS

1. Agregar el tipo a `apps/sync/constants.py::TIPOS_EVENTO`.
2. Crear su serializador en `apps/sync/serializers.py`.
3. Crear el helper público `evento_*` en `apps/sync/events.py`, reutilizando
   `_crear_evento` para conservar la clave idempotente.
4. Invocar el helper **dentro del mismo `transaction.atomic()`** que confirma
   la operación de negocio. Así el hecho y su fila del outbox se confirman o se
   revierten juntos. `transaction.on_commit()` queda reservado para snapshots
   costosos y reemplazables, como `INVENTARIO_SNAPSHOT`.
5. Si existe un objeto local desde el cual el payload puede reconstruirse,
   agregar un `HechoSync` a `apps/sync/registry.py`:
   - `backfill=True` solo para un hecho primario donde objeto sin evento
     significa realmente que falta encolarlo;
   - `backfill=False` para anulaciones y otras transiciones derivadas;
   - si es un snapshot sin PK reserializable, documentarlo y agregar su tipo a
     `TIPOS_NO_RESERIALIZABLES` en vez de fingir que puede recuperarse.

Probar payload vacío o legado cuando corresponda, reserialización de
`SIN_PAYLOAD`, reenvío del mismo hash y orden frente a hechos dependientes.

### 3. Recibir el hecho en cloud

Crear un handler idempotente en `apps/api/views/sync.py` y registrarlo en
`HANDLERS`. El assert al final del módulo compara `HANDLERS` con
`TIPOS_EVENTO_CODIGOS`; Django debe fallar al arrancar si falta o sobra un
handler.

El handler debe validar referencias, limitar toda consulta al tenant y a la
sucursal autenticada, y tolerar el reenvío del mismo hecho sin duplicar datos.
No debe enviar Web Push ni llamar al motor de notificaciones: primero el sync
confirma y persiste el `EventoSync`; el job lo proyecta después sin bloquear el
request.

### 4. Publicarlo en el catálogo de notificaciones

En `apps/notificaciones/catalogo.py`:

1. declarar la constante pública y su `DefinicionEvento` en `DEFINICIONES`;
2. agregar el tipo sync a `TIPOS_SYNC_RELEVANTES`;
3. mapearlo en `tipo_desde_evento_sync` —incluido cualquier subtipo del
   payload—;
4. extender `construir_desde_sync` para devolver `tipo_evento`, `titulo`,
   `cuerpo`, `datos` y `ocurrido_en`;
5. extender `normalizar_parametros` y `regla_aplica` si hay filtros; y
6. extender `nivel_para` si puede generar una alerta.

`titulo` admite 180 caracteres y `cuerpo` 500. `datos` conserva el desglose
estructurado para el detalle. La ruta por defecto del push es
`/notificaciones/<id-del-destinatario>`. Si un evento necesita otra ruta,
incorporarla explícitamente al constructor y a la creación de
`EventoNotificable` en `apps/notificaciones/services.py`, con prueba de
propiedad y deep link.

No agregar lógica específica del evento a `push.py`: ese archivo es solamente
el adaptador genérico Web Push.

### 5. Decidir los defaults

Un evento nuevo aparece en el selector del portal, pero no genera
destinatarios hasta que exista una regla aplicable.

Si debe venir activo para el rol Administrador de sistema:

1. agregarlo a `apps/notificaciones/seed.py::crear_reglas_default`; y
2. crear una data migration idempotente para los tenants existentes.

No ampliar roles personalizados y no activar el motor de un tenant como efecto
de la migración.

### 6. Pruebas mínimas

Backend:

- serialización, outbox transaccional y registro/re-serialización;
- handler idempotente, referencias inválidas y aislamiento tenant/sucursal;
- mapeo y constructor del catálogo;
- validación de parámetros, umbral y nivel;
- regla de rol, múltiples roles, inclusión/exclusión y usuario inactivo;
- corte temporal e idempotencia de proyección;
- una fila de bandeja por usuario y una entrega por dispositivo;
- 404/410, reintento y conservación de la bandeja si falla el push;
- compatibilidad con el POS anterior si el despliegue será gradual.

Comandos base:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test apps.notificaciones.tests `
  apps.caja.tests.test_resumen_notificaciones apps.sync.tests --keepdb
```

Agregar al comando el módulo de pruebas del nuevo handler en `apps/api/tests`.
Después ejecutar la suite Django completa según `docs/TESTING.md`.

Si cambió el portal:

```powershell
npm run test:run
npm run lint
npm run build
```

La aceptación física debe cubrir regla activa, regla apagada, umbral inferior
y borde exacto, usuario fuera de sucursal, dos o más dispositivos, deep link y
fallo de una suscripción sin pérdida de la bandeja.

### 7. Orden de despliegue

1. Desplegar backend cloud, migraciones y handler.
2. Actualizar el job con la misma imagen y observar un ciclo sano.
3. Desplegar el portal si cambió un tipo de parámetro o la presentación.
4. Confirmar que el evento aparece en `GET /api/v1/notificaciones/catalogo/` y
   crear una regla solo en el tenant de prueba.
5. Activar el motor con corte "desde ahora" si todavía estaba apagado.
6. Desplegar el POS que empieza a emitir el evento.
7. Ejecutar el smoke físico y luego habilitar las reglas tenant por tenant.

Nunca desplegar primero un POS que emita un tipo que el cloud aún no conoce.
Un fallo push nunca debe cambiar el resultado del sync ni de la operación POS.
