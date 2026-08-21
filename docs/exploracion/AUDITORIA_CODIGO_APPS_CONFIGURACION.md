# Auditoría profunda de código - `apps/configuracion`

Fecha: 2026-08-20
Revisión de cierre: `3f22385`
Modo: lectura, pruebas y documentación; no se aplicaron correcciones funcionales.

## Resumen ejecutivo

`apps/configuracion` es un control plane transversal. Sus valores deciden si se
permite vender sin inventario, qué medios de pago acepta caja, cuántos tickets
se imprimen, cuánto tiempo puede anularse una venta, qué módulos aparecen y qué
identidad fiscal/comercial se usa en documentos. También alimenta el POS, PDFs,
sync, suscripciones, productos y facturación electrónica.

La aplicación tiene buenos fundamentos: una configuración por sucursal mediante
`OneToOneField`, invalidación del caché al guardar por el camino normal, archivo
`.env` real excluido de Git, allowlist explícita para el pull cloud y validación
de algunos parámetros individuales. Sin embargo, la resolución actual puede
entregar la configuración de otro tenant o de otra sucursal, y la interfaz de
administración opera en un plano de permisos distinto del RBAC del negocio.

Los riesgos más urgentes son:

- El caché usa únicamente `config_negocio_<codigo_sucursal>`. No incorpora
  tenant ni alias de base. Dos tenants con el código habitual `SD-001` comparten
  clave dentro del worker; se reprodujo que el segundo recibe la configuración
  del primero.
- Si `SUCURSAL_CODIGO` no existe, `get_config()` degrada silenciosamente a
  `ConfiguracionNegocio.objects.first()`. Un typo puede activar pagos,
  inventario negativo, identidad fiscal o módulos de otra sucursal.
- `configuracion.administrar` está declarado en RBAC, pero no tiene consumidor.
  La única UI es Django Admin: un staff con permiso Django y sin RBAC pudo ver
  y editar las configuraciones de todas las sucursales.
- `migrar_env_cliente --dry-run` imprime contraseñas y tokens completos en
  stdout, superficie que suele terminar en logs de soporte o CI.
- La configuración se cachea para siempre en `LocMemCache`. Un cambio en otro
  proceso/réplica no invalida este worker; incluso un `QuerySet.update()` en el
  mismo proceso dejó operando el valor anterior indefinidamente.

Se documentan **21 hallazgos**:

| Prioridad | Cantidad | Criterio |
| --- | ---: | --- |
| P1 | 5 | Puede mezclar tenants/sucursales, entregar control administrativo fuera de RBAC, filtrar secretos o mantener reglas financieras distintas entre workers. |
| P2 | 12 | Debilita invariantes, sync, diagnóstico, lifecycle, trazabilidad y consistencia entre flags, entitlement y administración. |
| P3 | 4 | Deuda de archivos, contratos, consumidores y cobertura que aumenta el costo de operar el control plane. |

La suite seleccionada terminó con **91/91 pruebas existentes aprobadas**.
`apps/configuracion` aporta **37 pruebas propias**. Una batería adversarial
temporal terminó con **17/17 reproducciones confirmadas** y fue retirada del
workspace. También pasaron `manage.py check` y
`makemigrations configuracion --check --dry-run` sobre una base de prueba
aislada.

## Alcance

Se inspeccionaron completamente:

- `apps/configuracion/models.py`
- `apps/configuracion/utils.py`
- `apps/configuracion/admin.py`
- `apps/configuracion/context_processors.py`
- `apps/configuracion/decorators.py`
- `apps/configuracion/views.py`
- `apps/configuracion/apps.py`
- `apps/configuracion/management/commands/crear_config_inicial.py`
- `apps/configuracion/management/commands/migrar_env_cliente.py`
- `apps/configuracion/management/commands/verificar_instalacion.py`
- `apps/configuracion/migrations/`
- `apps/configuracion/tests/`

También se trazaron las fronteras relevantes en:

- `config/settings.py`, `settings_production.py`, `settings_cloud.py`,
  `settings_azure_pg.py`, `settings_azure_sql.py` y `env_check.py`
- `server.py`
- `apps/tenancy/context.py`, `router.py` y `media.py`
- `apps/sucursales/models.py`
- `apps/api/views/sync.py`
- `apps/sync/engine.py`
- `apps/suscripciones/engine.py` y `registry.py`
- `apps/permisos/catalogo.py` y `engine.py`
- `apps/auditoria/models.py`
- `apps/ventas/views.py`, `models.py` y servicios
- `apps/productos/utils.py`
- `apps/facturacion_electronica/services/`
- `utils/impresoras/manager.py`
- `templates/base.html`, `templates/pos/` y consumidores de `config`
- scripts y runbooks de instalación que leen `deploy/env_cliente.env`

El núcleo de la aplicación suma **1,279 líneas Python**, sin contar migraciones
ni pruebas. Tiene ocho migraciones, **37 pruebas propias**, un `views.py` de tres
líneas y no publica URLs ni plantillas propias: la administración efectiva vive
en Django Admin y comandos.

La auditoría comenzó y cerró en `3f22385`; `apps/configuracion` permaneció sin
cambios. Durante la revisión había correcciones externas sin commit en API,
caja, clientes, cuentas por cobrar, inventario, permisos, sync, tenancy,
ventas y settings. No se revirtieron ni alteraron. Las referencias cross-app
corresponden al estado visible al cierre.

## Hallazgos P1

### CFG-001 - La clave de caché puede mezclar configuraciones entre tenants

- Severidad: crítica.
- Tipo: aislamiento tenant / caché / fuga de datos y reglas.
- Evidencia:
  - `get_config()` forma la clave únicamente con
    `config_negocio_<SUCURSAL_CODIGO>` (`apps/configuracion/utils.py:26-46`).
  - No incorpora `get_current_tenant_key()`, alias de base ni negocio.
  - El contexto tenant sí dispone de ambos identificadores
    (`apps/tenancy/context.py:23-34`).
  - Los códigos de sucursal son locales a cada base tenant; `SD-001` puede
    repetirse legítimamente entre negocios.
- Reproducción validada:
  - Se activó tenant A y se cacheó su configuración para `SD-001`.
  - Al cambiar al tenant B, también con `SD-001`, la segunda llamada devolvió la
    fila de A y ni siquiera ejecutó `ConfiguracionNegocio.load()` para B.
- Impacto:
  - Nombre, RNC, dirección, medios de pago, inventario negativo, flags fiscales
    y branding de un negocio pueden usarse al atender otro.
  - Una venta o documento puede calcularse con reglas ajenas sin tocar la base
    incorrecta, lo que vuelve el fallo difícil de detectar.
- Recomendación:
  - Incluir tenant key y alias en toda clave de caché tenant-aware, o alojar la
    configuración en un caché explícitamente particionado por tenant.
  - Invalidar por identidad completa y fallar cerrado si tenancy está activo sin
    contexto.
- Prueba de aceptación sugerida:
  - Alternar en un mismo worker dos tenants con el mismo código debe devolver
    siempre sus filas, y las claves observables deben ser distintas.

### CFG-002 - Una sucursal no resuelta recibe la primera configuración disponible

- Severidad: crítica.
- Tipo: resolución fail-open / aislamiento horizontal / error de instalación.
- Evidencia:
  - Con código configurado, `get_config()` llama
    `ConfiguracionNegocio.load(sucursal=get_sucursal_actual())`
    (`apps/configuracion/utils.py:37-44`).
  - Si el código no existe, `get_sucursal_actual()` devuelve `None`
    (`apps/sucursales/models.py:126-137`).
  - `load(None)` retorna `.objects.first()`, aunque esa fila esté ligada a otra
    sucursal (`apps/configuracion/models.py:276-285`).
  - El resultado ajeno se cachea bajo el código inválido.
- Reproducción validada:
  - Con configuraciones A y B existentes y `SUCURSAL_CODIGO='NO-EXISTE'`,
    `get_config()` devolvió la configuración A.
- Impacto:
  - Un typo o seed incompleto no detiene la caja: la hace operar con identidad,
    pagos, anulación, inventario y módulos de una sucursal arbitraria.
  - El error puede sobrevivir en caché aunque después se corrija parcialmente la
    base.
- Recomendación:
  - Si se proporcionó `SUCURSAL_CODIGO`, exigir resolución exacta y configuración
    ligada; no ejecutar fallback legacy.
  - Reservar `load(None)` para un modo legacy explícito sin código y exigir que
    exista exactamente una configuración nula.
- Prueba de aceptación sugerida:
  - Código inexistente, duplicado o sin configuración debe impedir el arranque o
    la operación con un error que nombre el código, nunca devolver otra fila.

### CFG-003 - Django Admin ignora `configuracion.administrar` y el ámbito de sucursal

- Severidad: crítica en despliegues multi-sucursal.
- Tipo: autorización paralela / escalada horizontal / control plane.
- Evidencia:
  - El catálogo declara `configuracion.administrar`
    (`apps/permisos/catalogo.py:49-51`).
  - No hay referencias ejecutables a ese permiso fuera del catálogo.
  - `ConfiguracionNegocioAdmin` usa permisos estándar de Django y no sobrescribe
    `has_view/change_permission` ni `get_queryset`
    (`apps/configuracion/admin.py:9-47`).
  - La app no tiene una vista RBAC alternativa (`apps/configuracion/views.py`).
- Reproducción validada:
  - Un usuario staff con `change_configuracionnegocio`, sin
    `configuracion.administrar`, abrió el changelist con HTTP 200.
  - La página incluyó configuraciones de sucursal A y B.
- Impacto:
  - Un permiso Django otorgado para una necesidad puntual entrega control de
    pagos, inventario negativo, módulos y datos fiscales de todo el tenant.
  - El panel RBAC comunica una capacidad que en la práctica no habilita ni
    revoca esta interfaz.
- Recomendación:
  - Crear una interfaz de servicio/vista que aplique RBAC y ámbito de negocio y
    sucursal, o integrar esas comprobaciones en Admin de forma explícita.
  - Separar permisos de identidad comercial, pagos, inventario, impresión y
    fiscalidad; no agrupar todo bajo un solo `change`.
- Prueba de aceptación sugerida:
  - Staff sin RBAC debe recibir 403; un administrador de sucursal no debe listar
    ni editar otra, y un permiso RBAC válido debe tener un flujo soportado.

### CFG-004 - `migrar_env_cliente --dry-run` imprime secretos completos

- Severidad: crítica.
- Tipo: secretos / salida de comandos / logs.
- Evidencia:
  - El comando documenta que el origen contiene contraseñas y tokens
    (`apps/configuracion/management/commands/migrar_env_cliente.py:4-13`).
  - En `--dry-run` escribe el contenido completo generado a stdout
    (`apps/configuracion/management/commands/migrar_env_cliente.py:72-79`).
- Reproducción validada:
  - Un `DB_PASSWORD` de prueba apareció literalmente en la salida capturada del
    dry-run.
- Impacto:
  - Consolas remotas, tickets, transcripciones, CI y logs de automatización
    pueden conservar credenciales operativas.
  - El nombre “dry-run” sugiere seguridad porque no escribe archivo, no que
    expone todo su contenido.
- Recomendación:
  - Mostrar solo nombres, conteos y hashes parciales; redactar por defecto todo
    campo sensible.
  - Si alguna vez se necesita revelar, exigir una opción explícita y advertencia
    interactiva, nunca apta para logs.
- Prueba de aceptación sugerida:
  - Ningún secreto real ni fragmento suficiente para reutilizarlo debe aparecer
    en stdout/stderr en modo normal, JSON o dry-run.

### CFG-005 - El caché eterno permite reglas distintas entre workers

- Severidad: alta-crítica según despliegue.
- Tipo: consistencia / invalidación distribuida / operación financiera.
- Evidencia:
  - El objeto se guarda con `timeout=None` (`apps/configuracion/utils.py:33-46`).
  - Azure usa `LocMemCache`, aislado por proceso
    (`config/settings_azure_pg.py:48-56` y
    `config/settings_azure_sql.py:55-63`).
  - `save()` elimina la clave solo en el proceso que ejecutó el cambio
    (`apps/configuracion/models.py:240-254`).
  - `QuerySet.update()`, SQL, importaciones y otros procesos no ejecutan ese
    método.
- Reproducción validada:
  - Se cacheó `pago_efectivo=True`, se actualizó la fila a `False` con
    `QuerySet.update()` y `get_config()` continuó devolviendo `True` aunque la
    base ya contenía `False`.
- Impacto:
  - Dos réplicas pueden discrepar indefinidamente sobre pagos, stock negativo,
    anulación, impuestos o módulos.
  - Reiniciar “cura” el síntoma y oculta la causa, favoreciendo recurrencia.
- Recomendación:
  - Usar caché compartido con versionado/pub-sub o una TTL corta más invalidación
    distribuida. Cachear datos inmutables/serializados con versión, no confiar en
    una señal local como única coherencia.
- Prueba de aceptación sugerida:
  - Un cambio confirmado debe ser visible en todos los workers dentro de un SLA
    medido y no depender de reinicio.

## Hallazgos P2

### CFG-006 - El modelo acepta combinaciones operativas y fiscales inseguras

- Severidad: alta.
- Tipo: validación cruzada / disponibilidad / fiscalidad.
- Evidencia:
  - No existe `clean()` en `ConfiguracionNegocio` ni constraints cruzados.
  - Los tres métodos de pago pueden quedar deshabilitados.
  - `modulo_ecf=True` puede coexistir con `emisor_activo=NULL`, aunque el propio
    help text dice que es requerido (`apps/configuracion/models.py:183-192`).
  - `itbis_porcentaje_global` no tiene mínimo ni máximo
    (`apps/configuracion/models.py:200-208`).
- Reproducción validada:
  - `full_clean()` aceptó simultáneamente cero métodos de pago, e-CF activo sin
    emisor e ITBIS `-5.00`.
- Impacto:
  - El POS puede quedar sin forma de cobrar; una venta puede entrar en un flujo
    fiscal sin emisor; importes fiscales pueden calcularse con tasas imposibles.
- Recomendación:
  - Definir reglas cruzadas por modo de despliegue y respaldar las que sean
    expresables con constraints.
  - Validar transición antes de activar un flag peligroso, no esperar al primer
    cobro o documento.
- Prueba de aceptación sugerida:
  - Debe rechazarse guardar una configuración sin pago utilizable, con tasa
    fuera de rango o con e-CF incompleto.

### CFG-007 - El pull de configuración omite validadores y `choices`

- Severidad: alta.
- Tipo: sincronización / validación / corrupción de parámetros.
- Evidencia:
  - `_pull_configuracion` asigna cada valor con `setattr()` y llama `save()` sin
    `full_clean()` (`apps/sync/engine.py:1116-1161`).
  - Los validadores de `dias_anulacion` y `cantidad_copias_ticket` solo se
    ejecutan en formularios/validación explícita.
  - Los `choices` de `ecf_proveedor` tampoco son constraints de base.
- Reproducción validada:
  - El pull persistió `dias_anulacion=0`, `cantidad_copias_ticket=0`, ITBIS
    negativo y `ecf_proveedor='otro'`.
- Impacto:
  - Una configuración corrupta en cloud se replica a todas las sucursales y
    puede romper anulación, impresión o fiscalidad.
  - El cursor puede avanzar después de aplicar valores que ningún formulario
    aceptaría.
- Recomendación:
  - Validar un DTO/serializer estricto antes de tocar el modelo, dentro de la
    transacción del cursor; rechazar el snapshot completo con diagnóstico claro.
- Prueba de aceptación sugerida:
  - Todo valor inválido debe dejar fila y cursor intactos; cloud y local deben
    compartir exactamente el mismo esquema.

### CFG-008 - Los controles e-CF no pueden administrarse como una unidad coherente

- Severidad: alta cuando se habilite e-CF; prioridad comercial actual diferida.
- Tipo: administración / configuración incompleta / disponibilidad.
- Evidencia:
  - El modelo define proveedor, emisor, modo de ITBIS, porcentaje y contingencia
    (`apps/configuracion/models.py:166-221`).
  - Ninguno aparece en los `fieldsets` de Admin
    (`apps/configuracion/admin.py:14-40`).
  - Sync envía proveedor, ITBIS y contingencia, pero excluye `emisor_activo`
    (`apps/api/views/sync.py:424-450` y `apps/sync/engine.py:1128-1153`).
  - `nativo` es un choice válido, mientras la factory lo trata como proveedor no
    soportado actualmente.
- Reproducción validada:
  - La inspección del formulario Admin confirmó que los cinco campos fiscales
    no forman parte de la UI.
- Impacto:
  - Se puede activar `modulo_ecf` desde Admin sin poder completar el emisor en la
    misma pantalla.
  - Una sucursal puede recibir e-CF activo y parámetros de cálculo sin el objeto
    necesario para emitir.
- Recomendación:
  - Diseñar un workflow fiscal atómico con preflight, permisos específicos y
    ownership claro de campos locales versus cloud.
- Prueba de aceptación sugerida:
  - Activar e-CF debe ser imposible hasta que proveedor soportado, emisor y tasa
    sean válidos; el pull debe producir una configuración utilizable completa.

### CFG-009 - Plantillas y gates consultan dos fuentes de verdad distintas

- Severidad: media-alta.
- Tipo: feature flags / entitlement / UX y control de acceso.
- Evidencia:
  - `modulo_activo()` usa suscripción cuando la sucursal tiene negocio
    (`apps/configuracion/utils.py:55-77`).
  - El context processor inyecta el modelo crudo como `config`
    (`apps/configuracion/context_processors.py:11-22`).
  - `templates/base.html` muestra cotizaciones, financiación y reportes según
    `config.modulo_*`, no según el entitlement efectivo
    (`templates/base.html:192-216`).
  - Pantallas POS hacen lo mismo con e-CF.
- Reproducción validada:
  - Con flag `modulo_cotizaciones=True` y negocio sin plan, el template veía
    `True` mientras `modulo_activo('cotizaciones')` devolvía `False`.
- Impacto:
  - La navegación puede ofrecer enlaces que terminan en 404 o esconder módulos
    comprados.
  - Operadores interpretan el fallo como avería, no como estado de entitlement.
- Recomendación:
  - Inyectar un mapa de capacidades efectivas calculado por el mismo motor que
    protege endpoints; conservar flags legacy solo como dato de migración.
- Prueba de aceptación sugerida:
  - Para cada módulo, menú, vista y API deben coincidir bajo plan, override,
    sucursal y fallback legacy.

### CFG-010 - `AccesoRapidoPOS` no tiene ámbito ni invariantes de base

- Severidad: media-alta.
- Tipo: multi-sucursal / modelo polimórfico / integridad.
- Evidencia:
  - El modelo no tiene sucursal ni negocio (`apps/configuracion/models.py:312-404`).
  - `clean()` exige exactamente producto o categoría según tipo, pero `save()`
    no lo invoca y no existen constraints.
  - El endpoint POS consulta todos los accesos activos
    (`apps/ventas/views.py:291-314`).
- Reproducción validada:
  - `objects.create(tipo='producto')` guardó una fila sin producto; luego
    `full_clean()` sí la rechazó.
  - Un acceso creado desde sucursal A apareció al consultar el POS como sucursal B.
- Impacto:
  - Botones locales se mezclan entre sucursales con catálogos o prioridades
    distintas; imports/scripts pueden crear filas ocultas o contradictorias.
- Recomendación:
  - Decidir explícitamente si el ámbito es negocio o sucursal y modelarlo.
  - Añadir `CheckConstraint` para la exclusión producto/categoría y validar
    estado activo de destino según política.
- Prueba de aceptación sugerida:
  - Cada sucursal debe recibir solo su conjunto efectivo y ninguna fila inválida
    debe persistir por ORM, Admin o importación.

### CFG-011 - La protección contra borrar configuración es ilusoria

- Severidad: media-alta.
- Tipo: lifecycle / API de modelo / caché.
- Evidencia:
  - `ConfiguracionNegocio.delete()` ejecuta `pass` y no informa que ignoró la
    operación (`apps/configuracion/models.py:256-257`).
  - Admin oculta el borrado, pero `QuerySet.delete()` no llama el override por
    instancia.
  - El borrado por queryset tampoco invalida el caché de configuración.
- Reproducción validada:
  - `obj.delete()` dejó la fila viva; `filter(pk=...).delete()` la eliminó.
- Impacto:
  - Un caller puede creer que borró y continuar con estado falso; otro camino
    elimina realmente y deja workers sirviendo una copia que ya no existe.
- Recomendación:
  - Levantar una excepción de dominio explícita y bloquear borrado con
    `PROTECT`/política de repositorio, permisos y checks; no usar un `pass` como
    control de integridad.
- Prueba de aceptación sugerida:
  - Todas las rutas de borrado deben fallar de forma uniforme y observable, o
    ejecutar una transición versionada soportada.

### CFG-012 - Leer configuración puede crearla y mutar la base

- Severidad: media.
- Tipo: side effect en lectura / bootstrap implícito.
- Evidencia:
  - `load(sucursal)` usa `get_or_create()` y `load(None)` puede crear `pk=1`
    (`apps/configuracion/models.py:259-285`).
  - El context processor llama `get_config()` en todos los templates
    (`apps/configuracion/context_processors.py:1-22`).
  - El endpoint GET de sync también llama `load(sucursal)`
    (`apps/api/views/sync.py:403-414`).
- Reproducción validada:
  - Con la tabla vacía, invocar el context processor creó una configuración
    ligada a la sucursal actual.
- Impacto:
  - Renderizar login, error o página informativa puede crear estado con defaults
    antes del bootstrap controlado.
  - Un GET autenticado de sync puede mutar cloud y hacer parecer provisionada
    una sucursal incompleta.
- Recomendación:
  - Separar `get` de `bootstrap`; las lecturas deben fallar con un error
    accionable si falta configuración.
- Prueba de aceptación sugerida:
  - Ninguna petición GET o render debe cambiar el conteo ni timestamps de la
    tabla.

### CFG-013 - El verificador declara sano el modo legacy aunque haya módulos apagados

- Severidad: media-alta.
- Tipo: diagnóstico / falso negativo / operación.
- Evidencia:
  - En modo sin negocio, `_revisar_modulos()` devuelve siempre
    `apagados=[]` y no consulta flags (`apps/configuracion/management/commands/verificar_instalacion.py:149-157`).
  - La salida imprime “OK: no hay riesgo”
    (`apps/configuracion/management/commands/verificar_instalacion.py:249-251`).
- Reproducción validada:
  - Con `modulo_impresion_termica=False`, el gate real devolvió `False`, pero el
    reporte legacy afirmó `apagados=[]`.
- Impacto:
  - La herramienta creada para diagnosticar tickets o módulos desaparecidos
    puede certificar precisamente el estado roto.
- Recomendación:
  - Calcular capacidades efectivas también en legacy y distinguir “apagado
    intencional” de “sin aprovisionar” con expectativas configuradas.
- Prueba de aceptación sugerida:
  - El reporte debe enumerar el mismo conjunto que `modulo_activo()` para todas
    las claves.

### CFG-014 - El diagnóstico puede mostrar la configuración de otra sucursal y termina con exit 0

- Severidad: media.
- Tipo: observabilidad / automatización / selección incorrecta.
- Evidencia:
  - `_revisar_seeds()` usa `ConfiguracionNegocio.objects.first()` en vez de la
    configuración de `get_sucursal_actual()`
    (`apps/configuracion/management/commands/verificar_instalacion.py:91-108`).
  - `handle()` imprime el reporte, pero no levanta `CommandError` ni fija un
    código no cero cuando `hay_problema=True` (`:33-43` y `:275-287`).
  - Avisos no críticos, como sync encendido sin token, tampoco entran en el
    resumen de fallo.
- Reproducción validada:
  - Con B como sucursal actual, el reporte mostró correctamente B como sucursal,
    pero `Negocio A` como configuración.
- Impacto:
  - Soporte valida la fila equivocada y scripts de despliegue no pueden usar el
    comando como gate confiable.
- Recomendación:
  - Resolver la configuración por la misma función estricta de runtime y ofrecer
    `--strict` por defecto en automatización con exit 1 ante críticos.
- Prueba de aceptación sugerida:
  - El JSON debe identificar PK/sucursal exactas y el proceso terminar distinto
    de cero ante cualquier condición clasificada como rota.

### CFG-015 - `crear_config_inicial` sin `--sucursal` pisa la primera sucursal

- Severidad: media-alta.
- Tipo: comando legacy / selección ambigua / escritura destructiva accidental.
- Evidencia:
  - Sin argumento, el comando selecciona `.objects.first()` sin exigir que sea
    una fila legacy nula (`apps/configuracion/management/commands/crear_config_inicial.py:65-79`).
  - Después sobrescribe identidad y preset y etiqueta la salida como “sin
    sucursal - legacy” (`:80-141`).
- Reproducción validada:
  - Con configuraciones A y B ligadas, ejecutar sin `--sucursal` cambió el nombre
    de A, conservó su FK a A y aun así informó “sin sucursal - legacy”.
- Impacto:
  - Una instrucción antigua puede modificar silenciosamente la sucursal de menor
    PK en una instalación multi-sucursal.
- Recomendación:
  - Exigir `--sucursal` si existe cualquier fila ligada; el modo legacy debe
    buscar exclusivamente `sucursal__isnull=True` y fallar si no es único.
- Prueba de aceptación sugerida:
  - En multi-sucursal, omitir el objetivo debe abortar sin escrituras y listar
    los códigos válidos.

### CFG-016 - La migración `.bat` a `.env` no garantiza round-trip ni archivo seguro

- Severidad: media-alta.
- Tipo: secretos / serialización / escritura de archivo.
- Evidencia:
  - `_render()` concatena `NOMBRE=valor` sin quoting de sintaxis dotenv
    (`apps/configuracion/management/commands/migrar_env_cliente.py:133-150`).
  - Valores con `%` se omiten por completo (`:118-121`).
  - `Path.write_text()` sobrescribe directamente; no usa archivo temporal,
    reemplazo atómico, backup ni endurecimiento de ACL (`:81`).
  - El comentario generado afirma que no hace falta escapar símbolos (`:140-143`).
- Reproducción validada:
  - El valor de origen `abc #fragmento-secreto` se escribió sin comillas y
    `python-dotenv` lo cargó como `abc`.
- Impacto:
  - Una migración “exitosa” puede cambiar contraseñas/tokens o eliminarlos;
    una interrupción durante `--forzar` puede truncar el único archivo real.
- Recomendación:
  - Serializar con una rutina dotenv con round-trip probado, escribir temporal,
    fsync/reemplazo y backup; restringir ACL al usuario del servicio.
- Prueba de aceptación sugerida:
  - Una matriz de espacios, `#`, `%`, comillas, Unicode y símbolos debe cargar
    exactamente el mismo valor después de convertir.

### CFG-017 - Los cambios de configuración no generan auditoría de dominio uniforme

- Severidad: media-alta.
- Tipo: trazabilidad / control administrativo / cambios sensibles.
- Evidencia:
  - `apps/auditoria` define la acción `CONFIGURACION`
    (`apps/auditoria/models.py:86-90`).
  - `apps/configuracion` no la emite desde modelo, Admin o comandos.
  - El pull de sync actualiza campos sensibles sin actor/auditoría de dominio.
  - Django Admin puede crear `LogEntry`, pero no cubre comandos, sync, ORM ni
    valores efectivos antes/después por tenant/sucursal.
- Impacto:
  - No puede reconstruirse quién habilitó inventario negativo, cambió métodos de
    pago, tasa fiscal o periodo de anulación, ni qué workers vieron el cambio.
- Recomendación:
  - Centralizar mutaciones en un servicio transaccional que registre actor,
    canal, tenant, sucursal, versión y diff redactado.
- Prueba de aceptación sugerida:
  - Cada cambio confirmado debe dejar exactamente un evento durable; secretos y
    material fiscal sensible deben redactarse según política.

## Hallazgos P3

### CFG-018 - El logo no tiene lifecycle de archivos ni propagación definida

- Severidad: media-baja.
- Tipo: media / residuos / branding distribuido.
- Evidencia:
  - `logo` usa `ImageField` con prefijo tenant, una buena base
    (`apps/configuracion/models.py:66-71` y `apps/tenancy/media.py:82-83`).
  - Reemplazar el campo no elimina automáticamente el archivo anterior.
  - El pull de configuración excluye logo; PDFs/tickets de cada nodo pueden usar
    assets distintos.
  - El borrado por queryset puede dejar archivos sin referencia.
- Impacto:
  - Acumula residuos y produce branding diferente entre cloud y sucursales.
- Recomendación:
  - Versionar el asset, borrar la versión anterior después del commit y definir
    si el logo es local, por sucursal o cloud-authoritative.
- Prueba de aceptación sugerida:
  - Reemplazo, rollback y sync deben dejar una sola versión efectiva y ninguna
    referencia rota.

### CFG-019 - Hay superficies declaradas pero sin flujo soportado

- Severidad: baja-media.
- Tipo: mantenibilidad / código dormido / expectativas.
- Evidencia:
  - `views.py` contiene solo tres líneas y no existen URLs propias.
  - `requiere_sysadmin` y `requiere_admin_o_sysadmin` no tienen consumidores en
    el proyecto (`apps/configuracion/decorators.py:35-69`).
  - `modo_contingencia` se describe explícitamente como placeholder sin efecto
    (`apps/configuracion/models.py:210-220`).
- Impacto:
  - Nombres y campos sugieren controles disponibles que no protegen ni cambian
    el runtime; aumentan el riesgo de una activación accidental.
- Recomendación:
  - Retirar/deprecar lo no soportado o exponerlo solo cuando tenga workflow,
    permisos, pruebas y runbook completos.
- Prueba de aceptación sugerida:
  - Cada campo administrable debe tener un consumidor identificado y cada
    decorador una ruta probada; placeholders no deben aparecer como opción real.

### CFG-020 - `formato_codigo_barras` promete más de lo que consume el generador

- Severidad: baja-media.
- Tipo: contrato de configuración / códigos operativos.
- Evidencia:
  - El campo acepta hasta 20 caracteres sin gramática
    (`apps/configuracion/models.py:146-151`).
  - `apps/productos/utils.py` toma solo el texto antes de `-` y siempre genera
    seis dígitos; ignora cantidad/posición de `X`.
  - El comando concatena cualquier prefijo recibido con `-XXXXXX` sin validar
    longitud o caracteres (`crear_config_inicial.py:131-134`).
- Impacto:
  - Un valor aceptado por configuración no se refleja en códigos y etiquetas,
    o excede el ancho del campo al guardar.
- Recomendación:
  - Modelar prefijo y longitud por separado o validar una gramática consumida de
    extremo a extremo.
- Prueba de aceptación sugerida:
  - Todo formato guardado debe producir exactamente el patrón documentado; los
    demás deben rechazarse antes del save.

### CFG-021 - La suite propia es amplia, pero no cubre las fronteras de mayor riesgo

- Severidad: media.
- Tipo: cobertura / regresión.
- Evidencia:
  - Existen 37 casos útiles para `.env`, migración, copias y diagnóstico.
  - Faltan pruebas de caché tenant-aware, fallback estricto, múltiples workers,
    RBAC/Admin, validación cruzada, pull inválido, borrado y alcance de accesos
    rápidos.
- Impacto:
  - La suite puede permanecer verde mientras se mezclan configuraciones o se
    habilitan combinaciones operativamente imposibles.
- Recomendación:
  - Convertir las reproducciones de esta auditoría en regresiones después de
    acordar los contratos correctos.
- Prueba de aceptación sugerida:
  - Ejecutar matriz tenant/sucursal/worker y canales Admin/comando/sync/ORM con
    las mismas invariantes.

## Validación ejecutada

### Suite existente seleccionada

Se creó únicamente para la corrida un settings aislado con base
`test_pos_fifo_auditoria_configuracion_20260820`. Django creó y destruyó esa
base; no se usó la base compartida del desarrollador.

```text
manage.py test \
  apps.configuracion \
  apps.ventas.tests.test_accesos_rapidos_pos \
  apps.ventas.tests.test_ventas_service \
  apps.api.tests.test_sync_extended \
  apps.suscripciones.tests.test_enforcement \
  apps.tenancy.tests.test_media \
  --settings=config.settings_auditoria_configuracion_temp --noinput -v 1
```

Resultado:

- **91 pruebas ejecutadas**.
- **91 aprobadas**.
- Duración: **12.793 s**.
- `System check identified no issues`.
- Base temporal destruida al terminar.

### Batería adversarial temporal

Se añadieron transitoriamente diecisiete casos para observar el comportamiento
actual. Resultado definitivo:

- **17 pruebas ejecutadas**.
- **17 reproducciones confirmadas**.
- Duración: **2.473 s**.
- `System check identified no issues`.

Los casos confirmaron:

1. Colisión de caché entre tenants con el mismo código de sucursal.
2. Fallback de código inexistente a la primera configuración.
3. Caché obsoleto después de `QuerySet.update()`.
4. Acceso Admin sin `configuracion.administrar` y con visibilidad de dos sucursales.
5. Ausencia de los cinco campos fiscales en Admin.
6. `full_clean()` aceptando cero pagos, e-CF sin emisor e ITBIS negativo.
7. Pull persistiendo valores fuera de validators y choices.
8. Acceso rápido inválido guardado sin `full_clean()`.
9. Acceso rápido de A visible desde B.
10. Diferencia entre `obj.delete()` y `QuerySet.delete()`.
11. Secreto completo impreso por `--dry-run`.
12. Corrupción round-trip de un valor con espacio y `#`.
13. Verificador legacy declarando vacío el conjunto apagado aunque el gate real
    devolvía `False`.
14. Verificador mostrando configuración A con sucursal actual B.
15. Context processor creando una fila durante lectura.
16. Flag de template activo y entitlement efectivo apagado.
17. Comando legacy sin sucursal sobrescribiendo la primera configuración ligada.

El archivo de pruebas y el settings temporal fueron eliminados después de la
validación. No se conservaron cambios funcionales.

### Chequeos estáticos de Django

```text
manage.py check --settings=config.settings_auditoria_configuracion_temp
System check identified no issues (0 silenced).

manage.py makemigrations configuracion --check --dry-run \
  --settings=config.settings_auditoria_configuracion_temp
No changes detected in app 'configuracion'
```

## Aspectos positivos observados

- La relación `OneToOneField` evita dos configuraciones no nulas para la misma
  sucursal por los caminos ordinarios.
- `save()` invalida la clave local de configuración y sucursal en el camino
  normal.
- `dias_anulacion` y `cantidad_copias_ticket` tienen validadores de rango para
  formularios y `full_clean()`.
- Admin bloquea el botón de borrado de configuración y valida `AccesoRapidoPOS`
  mediante ModelForm.
- El POS filtra accesos rápidos inactivos y destinos retirados antes de
  entregarlos al navegador.
- El pull usa una allowlist de campos y excluye explícitamente hardware local.
- `deploy/env_cliente.env` está ignorado por Git, y las variables reales del
  entorno prevalecen sobre el archivo (`override=False`).
- `server.py` valida la secret key y la conexión antes de levantar Waitress.
- `migrar_env_cliente` se niega a sobrescribir un destino existente sin
  `--forzar`.
- El upload del logo usa namespace tenant y los helpers PDF escapan textos.
- La suite existente documenta fallos reales de instalación y cubre caracteres
  como `&`, espacios, alias de impresora, copias de ticket y aprovisionamiento.

## Orden recomendado de remediación

1. **Aislar resolución y caché:** CFG-001, CFG-002 y CFG-005 como un único
   contrato tenant/sucursal/worker.
2. **Cerrar el control plane:** CFG-003 y CFG-017, con permisos granulares y
   auditoría uniforme.
3. **Proteger secretos y bootstrap:** CFG-004, CFG-012, CFG-015 y CFG-016.
4. **Unificar invariantes y sync:** CFG-006, CFG-007 y CFG-011.
5. **Alinear fuentes de verdad:** CFG-009, CFG-010, CFG-013 y CFG-014.
6. **Cuando e-CF vuelva a prioridad:** cerrar CFG-008 mediante workflow fiscal
   completo, no con campos sueltos.
7. **Completar lifecycle y cobertura:** CFG-018 a CFG-021.

No conviene resolver solo la clave de caché. Si el código de sucursal continúa
degradando a `.first()` o la invalidación sigue siendo local, todavía puede
aplicarse una configuración incorrecta aunque ya no haya colisión entre tenants.

## Criterios de cierre de la auditoría

La aplicación puede considerarse cerrada cuando, como mínimo:

- toda resolución incluye tenant, alias, negocio y sucursal exactos y falla
  cerrada ante ausencia;
- los cambios convergen entre workers dentro de un SLA comprobado;
- Django Admin o su reemplazo aplican RBAC y ámbito de sucursal;
- dry-run, JSON, logs y errores nunca revelan secretos;
- pagos, inventario, anulación y fiscalidad tienen validaciones cruzadas comunes
  a Admin, comando, sync y ORM;
- los pulls inválidos no cambian fila ni cursor;
- navegación y endpoints consultan la misma capacidad efectiva;
- accesos rápidos tienen ámbito e invariantes en base;
- las lecturas no crean configuración y los comandos exigen un objetivo exacto;
- el diagnóstico refleja la fila actual y termina no-cero ante estado roto;
- `.bat` → `.env` conserva exactamente valores y escribe de forma atómica con
  permisos mínimos;
- cada mutación sensible deja auditoría con actor, canal, tenant, sucursal,
  versión y diff;
- las diecisiete reproducciones adversariales se convierten en pruebas de
  rechazo, aislamiento o convergencia.

## Conclusión

El mayor riesgo de `apps/configuracion` no está en un campo aislado, sino en la
identidad de la configuración efectiva. Hoy tenant, sucursal y proceso no forman
parte completa de esa identidad: una clave puede cruzar negocios, un código
inválido puede caer en otra fila y un worker puede conservar reglas obsoletas
para siempre. Dado que esas reglas autorizan pagos, inventario y documentos, el
impacto es transversal.

La solución de mayor retorno es construir un resolutor estricto y versionado,
con caché particionado e invalidación distribuida, y hacer que todas las
mutaciones pasen por un servicio RBAC/auditado. Después de eso, validaciones,
diagnóstico y workflow fiscal pueden apoyarse en una fuente de verdad realmente
confiable.
