# Estado de las auditorías de código — punto único de consulta

Última actualización: **2026-08-21** · Rama: `develop`

Este documento centraliza lo que salió de la ronda de auditorías: **qué hay que
hacer al desplegar**, **qué decisiones te quedan pendientes a vos** y **qué
quedó fuera de alcance a propósito**. El detalle técnico de cada hallazgo vive
en su documento de `docs/exploracion/`; acá está lo que hace falta para operar.

> **Lista accionable:** [TODO_AUDITORIAS.md](TODO_AUDITORIAS.md) — los mismos
> pendientes en formato de checklist, ordenados por urgencia.

---

## 1. Resumen de avance

**135 hallazgos verificados y mitigados en 10 módulos.** En todos los casos se
releyó cada hallazgo contra el código antes de tocar nada: no hubo falsos
positivos ni hallazgos obsoletos.

| Módulo | Hallazgos | Estado | Documento |
|---|---:|---|---|
| `apps/ventas` | 14 | Mitigado | [AUDITORIA_CODIGO_APPS_VENTAS.md](exploracion/AUDITORIA_CODIGO_APPS_VENTAS.md) |
| `apps/inventario` | 14 | Mitigado | [AUDITORIA_CODIGO_APPS_INVENTARIO.md](exploracion/AUDITORIA_CODIGO_APPS_INVENTARIO.md) |
| `apps/sync` | 12 | Mitigado | [AUDITORIA_CODIGO_APPS_SYNC.md](exploracion/AUDITORIA_CODIGO_APPS_SYNC.md) |
| `apps/tenancy` | 18 | Mitigado (17 corregidos, 1 pospuesto) | [AUDITORIA_CODIGO_APPS_TENANCY.md](exploracion/AUDITORIA_CODIGO_APPS_TENANCY.md) |
| `apps/cuentas_por_cobrar` | 16 | Mitigado | [AUDITORIA_CODIGO_APPS_CUENTAS_POR_COBRAR.md](exploracion/AUDITORIA_CODIGO_APPS_CUENTAS_POR_COBRAR.md) |
| `apps/caja` | 13 | Mitigado | [AUDITORIA_CODIGO_APPS_CAJA.md](exploracion/AUDITORIA_CODIGO_APPS_CAJA.md) |
| `apps/reportes` | 16 | Mitigado | [AUDITORIA_CODIGO_APPS_REPORTES.md](exploracion/AUDITORIA_CODIGO_APPS_REPORTES.md) |
| `apps/permisos` | 21 | **P1 mitigado (10/10 + PER-011)**; P2/P3 abiertos | [AUDITORIA_CODIGO_APPS_PERMISOS.md](exploracion/AUDITORIA_CODIGO_APPS_PERMISOS.md) |
| `apps/usuarios` | 19 | **P1 mitigado (6/6 + USR-008/009/018)**; resto abierto | [AUDITORIA_CODIGO_APPS_USUARIOS.md](exploracion/AUDITORIA_CODIGO_APPS_USUARIOS.md) |
| `apps/auditoria` | 22 | **P1 mitigado (6/6 + 6 P2/P3)**; resto abierto | [AUDITORIA_CODIGO_APPS_AUDITORIA.md](exploracion/AUDITORIA_CODIGO_APPS_AUDITORIA.md) |

**Suite completa, serial: 944 tests, OK.**

### Auditorías escritas pero todavía sin procesar

Estos documentos existen y describen hallazgos reales, pero **nadie los verificó
ni los corrigió todavía**:

| Módulo | Hallazgos documentados |
|---|---:|
| `apps/productos` | 22 |
| `apps/configuracion` | 21 |
| `apps/cotizaciones` | 18 |
| `apps/api` | 8 |

---

## 2. Despliegue

### 2.1 Migraciones

**18 migraciones** en total (13 de la ronda de auditorías + 5 del gate de descuentos, §2.6). Ninguna es destructiva; las tres marcadas con ⚠️
transforman datos y merecen leerse antes de correrlas en producción.

| Migración | Qué hace | Riesgo |
|---|---|---|
| `sync.0008_eventosync_hash_unico` | ⚠️ Colapsa hashes duplicados y agrega índice único parcial | **Aborta** si encuentra colisiones reales — es deliberado |
| `sync.0009_dedup_ledger_compras` | ⚠️ Colapsa el ledger cloud duplicado de compras | Borra filas redundantes; la autoridad pasa a ser `MovimientoLote` |
| `permisos.0005_autorizacionoverride` | Tabla nueva (autorizaciones de un solo uso) | Ninguno |
| `caja.0003_caja_origen_id` | UUID estable por caja, en 3 pasos | Escrita a mano: un `default=uuid.uuid4` en un paso dejaría todas las cajas con el **mismo** valor |
| `ventas.0007_pago_turno_caja` | FK nullable `Pago → TurnoCaja` | Ninguno |
| `cuentas_por_cobrar.0006_…clave_idempotencia…` | Campo + índice único **parcial** | Ninguno: solo aplica a claves presentes |
| `cuentas_por_cobrar.0007_pagocxc_turno_caja` | FK nullable `PagoCxC → TurnoCaja` | Ninguno |
| `inventario.0006_alter_lote_detalle_compra` | Ajuste de FK | Ninguno |
| `tenancy.0002_alter_tenant_media_prefix_and_more` | Unicidad de `media_prefix` y de `Lower(email)` | Falla si hay duplicados reales — hay que resolverlos a mano |
| `tenancy.0003_sesionimpersonacion` | Tabla nueva | Ninguno |
| `productos.0009_categoria_origen_cloud_id` | Identidad de sync | Ninguno |
| `clientes.0005_cliente_origen_cloud_id` | Identidad de sync | Ninguno |
| `reportes.0003_reportes_alcance_y_ciclo_de_vida` | ⚠️ Alcance por sucursal, ciclo de vida, 6 índices únicos parciales | **Deduplica** snapshots antes del ALTER |
| `configuracion.0009_descuento_autorizacion` | 5 campos de política de descuentos, todos con default inocuo | Ninguno: el gate nace apagado |
| `permisos.0006_credencial_fisica_y_descuento` | Tabla `CredencialFisica` + operación `ventas.descuento` | Ninguno |
| `permisos.0007_permiso_autorizar_descuento` | Data migration: agrega `ventas.autorizar_descuento` al rol Administrador | Ninguno; idempotente |
| `ventas.0008_venta_descuento_autorizacion` | 2 campos nullable en `Venta` (quién autorizó, motivo) | Ninguno |
| `auditoria.0004_alter_auditoria_accion` | Nueva opción `DESC_AUTH` en `TipoAccion` | Ninguno: solo cambia `choices` |
| `productos.0011_producto_imagen_origen_url_producto_origen_sucursal_and_more` | 3 campos nuevos en `Producto` (`origen_sucursal`, `pendiente_revision`, `imagen_origen_url`) para el patrón de stub — ver BUG-H en `docs/BUGS.md` | Ninguno: todos con default inocuo |
| `auditoria.0005_auditoria_inmutable_y_actor` | Snapshot del actor + hash de integridad | No transforma datos. Los registros previos quedan **sin hash**: el verificador los reporta como no verificables, no como buenos |
| `usuarios.0004_usuario_negocio_protect` | `Usuario.negocio` pasa de `SET_NULL` a `PROTECT` | No transforma datos. **Borrar un negocio con usuarios ahora falla** con `ProtectedError` |
| `permisos.0009_asignacion_unicidad_efectiva` | ⚠️ Indices unicos parciales sobre `AsignacionRol` | **Deduplica** antes del ALTER, y **gana la revocacion**: si un grupo duplicado tiene alguna fila inactiva, la superviviente queda inactiva |
| `permisos.0008_permisos_productos_portal_cajera` | Data migration: agrega `productos.ver` + `productos.fotografiar` (nuevo) al rol Cajero de sistema | Ninguno; idempotente |

**Por qué `reportes.0003` deduplica y `sync.0008` aborta.** No es inconsistencia:
un `EventoSync` es un **hecho** —perder uno es perder información—, mientras que
un snapshot de reportes es **dato derivado y regenerable**. Cuando se puede
reconstruir, se limpia; cuando no, se detiene y te pide decidir.

### 2.2 Configuración nueva

**`REPORTES_PRIVATE_ROOT`** (opcional). Directorio donde se escriben los PDFs de
cierre. Por defecto `BASE_DIR/private/reportes`.

- **Debe existir y ser escribible** por el usuario del servicio.
- **No puede estar dentro de `MEDIA_ROOT`** — el código lo rechaza con
  `ValueError`, porque `MEDIA_ROOT` se sirve públicamente.
- **Debe incluirse en el backup.** Es el único lugar donde viven los cierres en
  PDF. Ya está en `.gitignore`.
- Bajo tenancy, los archivos se separan solos por prefijo de tenant.

**Backend de cache compartido (Redis) para el cloud** — recomendado, no
obligatorio. El motor de permisos detecta que `LocMemCache` no se comparte
entre los tres workers de Gunicorn y, para no autorizar con datos revocados,
**deja de cachear entre requests**: funciona correctamente pero paga una
consulta por request y usuario. Con Redis configurado recupera el cache y la
invalidacion por version alcanza a los tres workers a la vez.

**`AUDITORIA_CONFIAR_EN_PROXY`** (opcional, default `False`). Ponerlo en
`True` **solo** si hay un proxy delante que reescribe `X-Forwarded-For` y
descarta la cabecera del cliente. Es una afirmacion sobre el despliegue: sin
el, la IP de auditoria sale de `REMOTE_ADDR`, que el cliente no controla.

### 2.3 Permisos nuevos en el catálogo RBAC

Se agregan solos con `sembrar_catalogo` (corre en la data migration de permisos).

| Código | Para qué | ¿Va en el rol cajero por defecto? |
|---|---|---|
| `clientes.editar_limite_credito` | Separa "corregir un teléfono" de "ampliar crédito" | No |
| `cuentas_por_cobrar.autorizar_exceso_credito` | Emitir la autorización puntual de exceso | No |
| `caja.operar` | Entrar al módulo de caja | **Sí** |
| `reportes.ver` | Dashboard personal | **Sí** |
| `reportes.sucursal.ver` | Reportes on-demand de las sucursales asignadas | No |
| `ventas.autorizar_descuento` | Autorizar un descuento sobre la tolerancia (§2.6) | No, y es el punto |
| `productos.fotografiar` | Subir/cambiar la foto de un producto desde el portal cloud (no precio/categoría) | **Sí** |
| `auditoria.consolidado.ver` | Ver el historial de TODAS las sucursales | No, y es el punto |

> **Revisá los roles existentes después de desplegar.** `caja.operar` y
> `reportes.ver` entran en `PERMISOS_CAJERO_DEFAULT` para que ninguna
> instalación pierda pantallas, pero **un rol custom que hayas creado a mano no
> los tiene**. Sin `caja.operar` el módulo de caja da 403; sin `reportes.ver`,
> el dashboard redirige al POS.
>
> `productos.fotografiar` (+ `productos.ver`, que ya existía pero no estaba en
> el rol cajero) es distinto: la data migration `permisos.0008` ya se lo agrega
> al rol Cajero **de sistema** en cada negocio existente, no solo a los nuevos.
> Sigue sin tocar roles custom creados a mano — mismo caveat de arriba.

### 2.4 Cambios de contrato que rompen clientes viejos

1. **El override de admin ya no viaja como `admin_id`.** `/caja/api/validar-admin/`
   devuelve un `token` de un solo uso, ligado a operación, operador, sucursal y
   monto, con **motivo obligatorio**. El POS y el modal de caja ya están
   migrados; cualquier otro cliente que use `admin_id` recibirá **403**.
2. **`/media/reportes/…` devuelve 404.** Los PDFs de cierre solo se bajan por
   `/reportes/pdf/cierre/<id>/`.
3. **El inventario valorizado cambió de significado.** Una fecha pasada devuelve
   el **inventario reconstruido de ese día**, no el stock actual con esa
   etiqueta. Si alguien venía usando el endpoint como "stock de hoy con fecha
   bonita", las cifras van a cambiar — correctamente. Una fecha futura ahora da
   400.
4. **Un ADMIN de tenant ya no administra suscripciones.** Los endpoints de
   planes, modulos, suscripciones y overrides exigen ahora un principal
   global (SYSADMIN, superusuario o identidad global del control plane). El
   catalogo ya describia esa capacidad como del operador del SaaS; el acceso
   total legacy se la concedia igual, y en una BD por tenant eso permitia
   editarse el propio plan. **Si el portal React muestra esa seccion a un
   ADMIN, ahora recibira 403.**
5. **Llamar a `tiene_permiso` sin sucursal cambio de significado.** Antes
   unia las asignaciones de TODAS las sucursales; ahora consulta solo las
   globales. La union sigue disponible como `sucursal=TODAS`. En una
   instalacion de una sola sucursal no cambia nada.
6. **Un codigo de permiso con typo deniega**, incluso para ADMIN.
7. **El logout solo acepta POST.** `GET /logout/` devuelve **405**;
   cualquier integracion o marcador que lo use hay que cambiarlo.
8. **Desactivar un usuario retira el acceso de inmediato** en todos los
   caminos: sesion abierta, Django Admin, token DRF y JWT. Antes solo lo
   frenaba el login local, y solo al iniciar sesion.
9. **`Auditoria.objects.update()` y `.delete()` lanzan `AuditoriaInmutable`.**
   Si algun script hacia limpieza asi, va a fallar — a proposito. La via es
   `Auditoria.objects.purgar_hasta(fecha, motivo=...)`, que registra su
   propia ejecucion.
10. **Las fechas del dashboard de auditoria se mueven a hora local.** Hasta
    ahora se mostraban en UTC: cuatro horas corridas en Santo Domingo.
11. **Los errores de reportes traen `codigo`** y los 500 ya no incluyen el texto
   de la excepción.

### 2.6 Feature nuevo: descuentos con autorización

> No sale de la auditoría — es un requerimiento de Royal Plast (se va el cajero
> de confianza y entra alguien nuevo). Se documenta acá porque trae migraciones,
> un permiso y configuración que hay que desplegar.

**Está apagado por defecto.** Ninguna instalación cambia de comportamiento hasta
que alguien active el flag. Se implementó en el código global, no como un bloque
para un solo cliente: el mecanismo (`AutorizacionOverride`) ya existía para
crédito y caja, y "se me va el cajero de confianza" le pasa a todos.

**Cómo funciona.** Si el descuento de la venta supera la tolerancia configurada,
el POS pide una autorización: un supervisor pasa su carnet por el lector (o
teclea usuario y contraseña). Eso emite un token de un solo uso, de vida corta,
ligado al monto y al operador, que el service consume dentro de la misma
transacción de la venta. Queda escrito en `Venta` quién autorizó y por qué, y
eso viaja al portal con el sync normal de ventas.

**Configuración** (`ConfiguracionNegocio`, por sucursal):

| Campo | Default | Para qué |
|---|---|---|
| `descuento_requiere_autorizacion` | `False` | Interruptor maestro |
| `descuento_tolerancia_monto` | `0.00` | RD$ libres sin pedir autorización |
| `descuento_tolerancia_porcentaje` | `0.00` | % del subtotal libre |
| `descuento_motivo_modo` | `NINGUNO` | `NINGUNO` / `OPCIONAL` / `OBLIGATORIO` |
| `descuento_vigencia_minutos` | `5` | Vida del token |

La tolerancia se evalúa con un **`or`**: el descuento pasa libre si cae dentro de
*cualquiera* de las dos. Con un `and`, dejar una en `0` anularía la otra y no se
podría configurar "hasta RD$100" sin configurar también un porcentaje. Con las
dos en `0`, cualquier descuento pide autorización.

**El motivo es opcional a propósito.** En un negocio donde se regatea, casi toda
venta termina con descuento: exigir texto libre en cada una produce 400 filas
que dicen "descuento", que da la ilusión de control sin aportar nada. Se afloja
**solo** para descuentos — `caja.retiro` y `credito.exceder_limite` conservan el
motivo obligatorio, que fue un hallazgo de auditoría deliberado.

**Puesta en marcha en un cliente:**

1. Correr las 5 migraciones de §2.1.
2. Activar `descuento_requiere_autorizacion` y fijar la tolerancia en la config
   de la sucursal.
3. Dar `ventas.autorizar_descuento` a quien corresponda. **Ojo:** la data
   migration se lo da al rol `administrador` de sistema; un rol custom hecho a
   mano no lo tiene.
4. Dar de alta el carnet del supervisor en el admin de Django
   (Permisos → Credenciales físicas). El código se hashea y no se puede volver a
   leer: si se pierde, se da de baja y se emite otro.
5. Al mes, mirar los descuentos por autorizador y ajustar la tolerancia con
   datos reales.

**Límite conocido, decilo cuando lo entregues.** Un carnet con código de barras
es una credencial *portadora*: se puede prestar, dejar en la gaveta o copiar con
una foto y una impresora. Contra un cajero nuevo y descuidado funciona; contra
uno que se propone robar, el control real no es la tarjeta sino que **cada
descuento queda con nombre, monto y hora, y el dueño lo ve en el portal**. Si
hace falta más, el endpoint ya acepta usuario+contraseña como segunda forma.

### 2.5 Después de desplegar

```bash
python manage.py verificar_instalacion   # config, BD, seeds, módulos activos
python manage.py verificar_sync          # outbox, huecos, cursores de pull
```

Y para el cierre diario automático:

```bash
python manage.py generar_cierre_diario                    # local
python manage.py generar_cierre_diario --tenant demo      # un tenant
python manage.py generar_cierre_diario --todos-los-tenants
```

---

## 3. Decisiones que te tocan a vos

Ninguna de estas está tomada. Todas son de negocio, no de código.

### 3.1 ¿Una tienda sin caja abierta puede cobrar en efectivo?

**Contexto (CAJA-002).** La auditoría pedía "rechazar efectivo cuando no exista
un turno operable". **No lo implementé**, porque frenaría las ventas de una
tienda que no abrió caja.

**Estado actual:** cada pago en efectivo nace con su `turno_caja` cuando hay un
turno abierto. Si no lo hay, la venta procede y el pago queda sin turno —
distinguible de los que sí lo tienen. La atribución del arqueo ya es exacta.

**Si querés el rechazo:** es un `if` en `_resolver_turno_caja`
(`apps/ventas/services/ventas_service.py`).

### 3.2 ¿Qué pasa al anular una venta con abonos ya aplicados?

**Contexto (CXC-006).** La auditoría daba tres opciones: bloquear, revertir
automáticamente en LIFO con egreso de caja, o convertir el saldo en crédito a
favor.

**Elegí bloquear** (devuelve 409), porque es la única que no inventa un asiento
contable. El operador revierte los abonos con `anular_pago_cxc_service` —que ya
existía, es LIFO, exige motivo y deja auditoría— y después anula la venta.

**Si preferís reversa automática o saldo a favor:** es un cambio en
`anular_cuenta_por_venta` más el asiento correspondiente.

### 3.3 ¿Cuándo queda cerrado contablemente un día?

**Contexto (RPT-004).** El primer cierre de una fecha quedaba congelado para
siempre: ventas tardías, anulaciones y reversas nunca lo tocaban, y reintentar
el comando parecía idempotente sirviendo datos obsoletos.

**Elegí BORRADOR por defecto**, con `--finalizar` explícito. Congelar
automáticamente *era* el bug; no congelar nunca dejaría el cierre sin punto
fijo. Ahora cerrar el día es un acto deliberado.

**Falta decidir:** a qué hora corre el cierre y si debe finalizar solo cuando
todos los turnos estén cerrados. El resumen ya reporta `turnos_abiertos`.

### 3.4 Hora y zona del cierre automático

**`instalar_cierre.ps1` está roto y lo dejé así a propósito.** Apunta a
`scripts/ejecutar_servicio_cierre.bat`, que **no está en el repositorio**;
configura un servicio de autoarranque (NSSM) para una tarea one-shot; y su
descripción dice **7 PM** mientras el modelo documenta **10 PM**.

El comando ya es correcto, reintentable y recorre tenants. Versionar un launcher
real y pasarlo a Task Scheduler es trabajo de despliegue, y necesita que digas
la hora y la zona.

### 3.5 ¿Backfill de datos históricos?

Tres backfills posibles, ninguno hecho, todos opcionales:

- **`turno_caja` en pagos históricos.** Se puede inferir por usuario + ventana
  del turno, pero sería reconstruir una atribución que nunca existió. Conviene
  decidirlo con datos reales delante.
- **`sucursal` en cierres diarios históricos.** Quedaron como consolidados
  (`sucursal = NULL`), que es lo correcto: nacieron sin filtro de sucursal.
  Reasignarlos requeriría recalcularlos.
- **Movimientos de inventario duplicados históricos.** El doble registro está
  corregido hacia adelante; conciliar el pasado es un ejercicio aparte.

---

## 4. Lo que quedó fuera de alcance (y por qué)

Ninguno bloquea el despliegue.

### Seguridad y aislamiento

- **Claim durable del push de sync** (`IN_FLIGHT` + lease). El claim local
  todavía no es durable ante un crash a mitad de envío.
- **Matriz PostgreSQL multi-DB en CI** (TEN-016). Requiere levantar dos bases en
  el pipeline; es el único hallazgo de tenancy sin corregir.
- **Drill de restauración.** `backup_tenant` produce y verifica un artefacto,
  pero nadie probó restaurarlo end-to-end.
- **Auditoría de mutaciones API bajo tenancy.** `SesionImpersonacion` registra
  el acceso, no cada mutación hecha durante la sesión.

### Robustez

- **`CheckConstraint` de respaldo** en ventas e inventario. Las invariantes de
  importes y cantidades se validan en la aplicación; falta el cinturón en la BD.
- **Cola durable de diferidos** en sync. Hoy un ítem diferido congela la marca
  de agua.
- **`_pull_legacy` sigue existiendo** como fallback para clouds pre-Fase 2.
- **Idempotencia concurrente del cobro CxC.** La constraint garantiza un solo
  pago por clave; falta el test de N reintentos concurrentes.

### Deuda de contrato

- **`_puede_anular` usa el rol legacy** (`ADMIN`/`SYSADMIN`) en vez de RBAC.
- **Scope por sucursal en los gates de inventario.** `tiene_permiso` se llama
  sin sucursal en varios puntos de esa app.
- **Identidad compuesta en el cloud** para `_handler_venta_creada`.

### Presentación y rendimiento

- **Paginación real** en tres listados: cartera CxC (corta a 300), historial de
  turnos (corta a 50, sin avisar) e inventario valorizado (corta a 500). Los dos
  primeros son los que faltan; el de inventario ya declara `productos_ocultos`.
- **Chart.js viene de CDN** sin integridad ni fallback local
  (`templates/reportes/on_demand.html`). En un POS sin Internet estable los
  gráficos fallan aunque los datos estén.

---

## 5. Patrones que se repitieron

Vale la pena tenerlos presentes al revisar los módulos que faltan: los mismos
errores aparecieron en casi todos.

1. **IDs falsificables usados como prueba de autorización.** Un `admin_id`
   entero y secuencial "probaba" que un administrador aprobó una excepción.
   Reemplazado por tokens de un solo uso ligados a operación, operador,
   sucursal y monto.
2. **Read-modify-write sin lock.** Leer un saldo, decidir y escribir, sin
   `select_for_update`. Aparece en crédito, FIFO, ajustes y cierre de turno.
3. **`count()` como generador de correlativos.** Un borrado deja huecos y el
   siguiente número colisiona. Reemplazado por MAX + reintento.
4. **`tiene_permiso(codigo)` sin sucursal.** El motor sin sucursal mira *todas*
   las asignaciones: un rol de la sucursal A abría la puerta en B.
5. **Django Admin editando hechos financieros** sin auditoría ni evento de sync.
6. **Degradación silenciosa.** `except Exception: pass` sobre operaciones que
   sí importan, y respuestas `success: true` sobre trabajo que nunca ocurrió.
7. **Cero tratado como ausencia.** `if monto:` convierte un `0.00` legítimo en
   "sin dato".
8. **Identidad por nombre mutable.** Renombrar una caja partía su turno en dos.

---

## 6. Cómo verificar

```bash
# Suite completa (serial — con --parallel hay ruido falso en Windows)
python manage.py test --settings=config.settings_development

# Solo la regresión de auditoría de un módulo
python manage.py test apps.reportes.tests.test_auditoria_reportes --settings=config.settings_development
```

Módulos de regresión creados en esta ronda:

- `apps/ventas/tests/test_ventas_service.py`, `test_anulaciones.py`, `test_concurrencia.py`
- `apps/inventario/tests/test_auditoria_inventario.py`, `test_concurrencia_inventario.py`
- `apps/sync/tests/test_auditoria_sync.py` · `apps/api/tests/test_sync_auditoria.py`
- `apps/tenancy/tests/test_auditoria_tenancy.py`
- `apps/cuentas_por_cobrar/tests/test_auditoria_cxc.py`
- `apps/caja/tests/test_auditoria_caja.py`
- `apps/reportes/tests/test_auditoria_reportes.py`

Las correcciones críticas están **verificadas por mutación**: se revierte el
arreglo y se comprueba que el test falle reproduciendo el síntoma exacto que
describe la auditoría. Cada documento registra cuáles y con qué error fallan.
