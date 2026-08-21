# Auditoría profunda de código - `apps/usuarios`

Fecha: 2026-08-20
Revisión de cierre: `3f22385`
Modo: lectura, pruebas y documentación; no se aplicaron correcciones funcionales.

## Resumen ejecutivo

`apps/usuarios` es pequeño, pero define la identidad operativa que atraviesa el
POS local, Django Admin, los permisos RBAC, los overrides de caja, los tokens
DRF y el portal cloud. El modelo conserva además dos conceptos que parecen
equivalentes y no lo son: `activo`, que representa el estado comercial de la
cuenta, e `is_active`, que es el contrato que Django y sus autenticadores
consultan.

La implementación tiene elementos correctos: las contraseñas se almacenan con
los hashers de Django, el formulario local usa CSRF, el login evita entregar una
nueva sesión a una cuenta con `activo=False`, el portal cloud revalida el
usuario tenant activo y el motor RBAC niega por defecto a un usuario ordinario
sin asignaciones. Sin embargo, esas garantías no se sostienen en todos los
caminos de autenticación y administración.

Los riesgos más urgentes son:

- `Usuario` no define `is_active`; hereda el valor constante `True` de
  `AbstractBaseUser`. Desactivar `activo` no invalida sesiones existentes y el
  backend estándar todavía autentica sus credenciales. También siguen válidos
  los JWT legacy y tokens DRF, y un staff inactivo puede entrar a Django Admin.
- En cloud coexisten dos puertas: el portal autentica `Identity` + `Membership`,
  pero `/admin/` autentica un `Usuario` de la base `default`. Esa segunda puerta
  no consulta identity, membership ni tenant.
- `negocio=NULL` significa de hecho “global” para resolutores downstream, pero
  el admin crea usuarios sin negocio y borrar un negocio convierte todos sus
  usuarios a `NULL` mediante `SET_NULL`.
- No existe un flujo autoritativo para alta, edición, desactivación, asignación
  y revocación de usuarios tenant. El portal RBAC solo lista usuarios existentes
  y sync no los crea.
- El login local acepta un `next` externo, no limita intentos y escribe una fila
  de auditoría por fallo. Se reprodujeron redirección abierta y doce intentos
  consecutivos sin bloqueo.
- El logout acepta GET sin CSRF. Además, una caída de auditoría ocurre antes de
  `logout()` y deja la sesión activa mientras devuelve HTTP 500.
- El instalador fresco llama `create_superuser(..., email='')`, aunque el
  manager rechaza el email vacío. No comprueba ese código de salida y termina
  mostrando “INSTALACION COMPLETADA EXITOSAMENTE”.
- La cuenta cloud (`Identity`) y la cuenta operativa (`Usuario`) tienen hashes,
  estados y nombres independientes. Cambiar uno desde sus interfaces normales
  no cambia necesariamente el otro.

Se documentan **19 hallazgos**:

| Prioridad | Cantidad | Criterio |
| --- | ---: | --- |
| P1 | 6 | Puede conservar acceso revocado, abrir una puerta de autenticación paralela, escalar entre negocios, impedir logout seguro, dejar una instalación sin administrador o facilitar ataque de credenciales. |
| P2 | 10 | Debilita onboarding, validación, separación de privilegios, trazabilidad, seguridad de navegación o consistencia de identidad. |
| P3 | 3 | Deuda de cobertura, duración de sesión y superficie de desarrollo/UX. |

La validación seleccionada terminó con **47/47 pruebas existentes aprobadas**.
`apps/usuarios` no aportó pruebas propias. Una batería adversarial temporal
terminó con **14/14 reproducciones confirmadas** y fue eliminada del workspace.
Al cierre también pasaron `manage.py check` y
`makemigrations usuarios --check --dry-run`.

## Alcance

Se inspeccionaron completamente:

- `apps/usuarios/models.py`
- `apps/usuarios/views.py`
- `apps/usuarios/admin.py`
- `apps/usuarios/urls.py`
- `apps/usuarios/apps.py`
- `apps/usuarios/migrations/`
- `apps/usuarios/tests/`
- `templates/usuarios/login.html`

También se trazaron las fronteras relevantes en:

- `apps/tenancy/authentication.py`, `context.py`, `router.py` y `models.py`
- `apps/tenancy/management/commands/bootstrap_tenant.py`
- `apps/tenancy/management/commands/normalizar_import_tenant.py`
- `apps/api/auth_views.py`, `auth_urls.py` y `authentication.py`
- `apps/api/views/permisos.py` y `serializers/permisos.py`
- `apps/sync/engine.py`
- `apps/permisos/engine.py`
- `apps/negocios/utils.py`
- `apps/caja/views.py`
- `apps/auditoria/models.py` y `middleware.py`
- `apps/reportes/views.py`
- `config/settings.py`, `settings_cloud.py` y `urls.py`
- `deploy/instalar.bat`

El núcleo de `apps/usuarios` suma **300 líneas Python**, sin contar migraciones.
La plantilla de login suma **316 líneas**. El directorio de pruebas contiene
solo un `__init__.py` vacío: **0 casos propios**.

La auditoría comenzó y cerró en `3f22385`; `apps/usuarios` permaneció sin
cambios. Durante la revisión continuaron apareciendo correcciones externas sin
commit en API auth, tenancy, settings, inventario y sync. No se revirtieron ni
alteraron. Las referencias cross-app de este documento corresponden al estado
visible al cierre, que ya incluye parte de esas correcciones en curso.

## Hallazgos P1

### USR-001 - `activo` no es `is_active`: la desactivación no revoca autenticación

- Severidad: crítica.
- Tipo: autenticación / revocación / acceso persistente.
- Evidencia:
  - El modelo declara `activo` como un booleano separado
    (`apps/usuarios/models.py:86-90`), pero no define el atributo o propiedad
    `is_active`.
  - El login propio revisa `user.activo` solo después de que
    `AuthenticationForm` ya autenticó (`apps/usuarios/views.py:16-29`).
  - Las sesiones posteriores pasan por `AuthenticationMiddleware`
    (`config/settings.py:133-141`) y las vistas usan `login_required`; ninguna
    vuelve a consultar `activo`.
  - En modo no-tenancy, `TenantJWTAuthentication` delega al autenticador estándar
    de SimpleJWT (`apps/tenancy/authentication.py:49-54`).
  - El refresh corregido conserva explícitamente el comportamiento legacy si el
    token no trae `identity_id` (`apps/api/auth_views.py:155-175`).
  - `SucursalTokenAuthentication` también delega al `TokenAuthentication`
    estándar fuera de tenancy (`apps/api/authentication.py:35-47`).
  - El override de caja revisa `user.is_active`, no `user.activo`
    (`apps/caja/views.py:56-66`).
- Reproducción validada:
  - Un `Usuario(activo=False)` devolvió `is_active == True` y
    `authenticate(...)` devolvió ese usuario.
  - Una sesión creada antes de desactivarlo siguió entrando a una vista con
    `login_required` y conservó su session key.
  - Un usuario `activo=False`, staff y superuser pudo autenticarse y abrir
    `/admin/` con HTTP 200.
  - Un JWT legacy emitido mientras estaba activo siguió abriendo `/auth/me/`
    después de desactivar al usuario; el refresh también emitió otro access
    token con HTTP 200.
- Impacto:
  - “Desactivar usuario” no cumple la expectativa operacional de retirar acceso.
  - Sesiones, Django Admin, tokens humanos DRF y JWT legacy pueden sobrevivir al
    cambio.
  - Un administrador desactivado puede seguir autorizando operaciones sensibles
    si el consumidor confía en `is_active` y en su bypass legacy de permisos.
- Recomendación:
  - Adoptar un único campo compatible con Django: renombrar/migrar a
    `is_active`, o implementar una propiedad y un backend coherentes sin dejar
    dos estados editables.
  - Revalidar estado en cada autenticador y en los gates de override.
  - Incorporar revocación/versionado de sesiones y tokens al desactivar,
    transferir de negocio o reducir privilegios.
- Prueba de aceptación sugerida:
  - Tras desactivar una cuenta, deben fallar inmediatamente login nuevo, sesión
    existente, Admin, token DRF, access JWT, refresh JWT y override de caja.

### USR-002 - Cloud conserva una puerta Django Admin fuera de `Identity` y `Membership`

- Severidad: crítica si existe un staff/superuser en `default`.
- Tipo: autenticación paralela / control plane / bypass de onboarding tenant.
- Evidencia:
  - `/admin/` está publicado incondicionalmente en la urlconf base
    (`config/urls.py:31`).
  - Cloud hereda esa urlconf y también conserva `SessionAuthentication`
    (`config/settings.py:414-423` y `config/settings_cloud.py:158-165`).
  - El router fija `admin` y `sessions` en `default`, mientras `usuarios` es
    dual-home y cae en `default` si no hay tenant activo
    (`apps/tenancy/router.py:6-11` y `:52-64`).
  - Django Admin autentica `Usuario`; el portal cloud autentica primero
    `Identity` y luego exige `Membership` (`apps/api/auth_views.py:49-88`).
  - No existe un gate que exija identity, membership o tenant al login de Admin.
- Impacto:
  - Un staff de `default` es una credencial administrativa independiente de la
    identidad cloud. Desactivar su identity o membership no toca esa puerta.
  - Comandos como `createsuperuser` y el instalador pueden crear precisamente
    esa cuenta paralela.
  - El operador puede creer que el portal es la única frontera de acceso cuando
    el mismo deployment conserva autenticación de sesión tradicional.
- Recomendación:
  - Decidir y documentar si Admin forma parte del control plane productivo.
  - Si no, retirarlo o bloquearlo en settings/urls cloud.
  - Si sí, autenticarlo mediante la identidad global, restringirlo por red/MFA y
    auditarlo como una frontera distinta; no mantener credenciales locales
    invisibles al ciclo de revocación cloud.
- Prueba de aceptación sugerida:
  - En cloud, una cuenta `Usuario` de `default` sin identity/membership no debe
    abrir Admin, salvo una política de break-glass explícita, registrada y
    probada.

### USR-003 - `negocio=NULL` puede convertirse en identidad global por accidente

- Severidad: crítica en una base con más de un negocio.
- Tipo: aislamiento / ciclo de vida / escalada horizontal.
- Evidencia:
  - El comentario del modelo define `NULL` como usuario global
    (`apps/usuarios/models.py:63-72`).
  - La FK usa `on_delete=models.SET_NULL` (`apps/usuarios/models.py:65-69`).
  - `negocio_actual()` trata cualquier usuario sin negocio como global y permite
    elegir `?negocio=<id>`; no comprueba SYSADMIN ni superuser
    (`apps/negocios/utils.py:15-36`).
  - El admin de usuarios no muestra ni permite seleccionar `negocio` en alta o
    edición (`apps/usuarios/admin.py:17-41`).
- Reproducción relacionada ya confirmada en la auditoría de permisos:
  - Un usuario ordinario con `negocio=NULL` pudo seleccionar otro negocio y
    administrar su RBAC.
- Impacto:
  - Borrar un negocio no desactiva ni elimina sus usuarios: los transforma en
    usuarios “global-looking”.
  - Un alta por Admin queda en el mismo estado peligroso.
  - `NULL` mezcla tres significados incompatibles: global legítimo, huérfano y
    usuario aún no provisionado.
- Recomendación:
  - Representar la globalidad con una capacidad explícita en la identidad de
    control, no con ausencia de FK.
  - Usar `PROTECT` o un workflow transaccional de baja de negocio que desactive
    usuarios antes de cualquier cambio.
  - Hacer que `negocio_actual()` compruebe una autorización global real y que un
    usuario ordinario sin negocio falle cerrado.
- Prueba de aceptación sugerida:
  - Crear un usuario sin negocio o eliminar su negocio nunca debe habilitar
    selección de otro tenant ni permisos globales.

### USR-004 - Una caída de auditoría puede impedir el logout y conservar la sesión

- Severidad: alta.
- Tipo: disponibilidad de seguridad / acoplamiento transaccional.
- Evidencia:
  - El login crea la sesión y después registra auditoría de forma síncrona y sin
    manejo de error (`apps/usuarios/views.py:32-40`).
  - El logout registra antes de llamar `logout()`
    (`apps/usuarios/views.py:69-81`).
  - `Auditoria.registrar()` escribe directamente con `objects.create()`
    (`apps/auditoria/models.py:261-274`).
  - El middleware de excepciones también intenta auditar el error, por lo que la
    misma indisponibilidad puede encadenar otro fallo.
- Reproducción validada:
  - Forzando `Auditoria.registrar` a fallar, `/logout/` respondió 500, conservó
    la session key y la sesión siguió abriendo una vista autenticada.
  - Forzando el fallo en login, la respuesta fue 500. Django no persistió la
    sesión porque `SessionMiddleware` no guarda respuestas 500.
- Impacto:
  - Una tabla bloqueada, base degradada o error de serialización puede impedir
    cerrar sesión en el momento en que más se necesita.
  - La auditoría deja de ser observabilidad y pasa a controlar la disponibilidad
    del mecanismo de seguridad observado.
- Recomendación:
  - En logout, invalidar primero la sesión en un bloque que no dependa del sink
    de auditoría.
  - Definir una política explícita para auditoría crítica: outbox durable,
    fallback seguro o captura de error con logging; evitar recursión del
    middleware.
  - En login, decidir si falla cerrado y devolver un error controlado, sin una
    sesión parcialmente creada.
- Prueba de aceptación sugerida:
  - Con auditoría indisponible, logout debe invalidar la sesión y responder de
    forma controlada; no debe quedar autenticación residual.

### USR-005 - El instalador fresco no puede crear el SYSADMIN y aun declara éxito

- Severidad: alta operacional.
- Tipo: instalación / bootstrap / manejo de errores.
- Evidencia:
  - `create_user()` exige un email no vacío (`apps/usuarios/models.py:9-16`).
  - Fase 8 llama `create_superuser(..., email='')`
    (`deploy/instalar.bat:303-315`).
  - No comprueba `%errorlevel%` después de ese shell ni después de los otros dos
    comandos de la fase (`deploy/instalar.bat:323-338`).
  - La comprobación final solo ejecuta `manage.py check`, que no verifica que el
    usuario exista (`deploy/instalar.bat:372-380`).
  - Finalmente imprime “INSTALACION COMPLETADA EXITOSAMENTE” y muestra las
    credenciales esperadas (`deploy/instalar.bat:382-388`).
- Reproducción validada:
  - La misma llamada a `create_superuser` levantó `ValueError` por email vacío.
- Impacto:
  - Una instalación nueva puede terminar sin cuenta administrativa mientras el
    operador recibe un mensaje de éxito falso.
  - Reejecutar sobre una cuenta existente cambia flags y rol, pero tampoco
    restaura `activo`, negocio, email o contraseña (`deploy/instalar.bat:316-322`).
- Recomendación:
  - Exigir y validar email inicial o construir uno válido de configuración.
  - Comprobar el código de salida después de cada paso y abortar la instalación.
  - Añadir una postcondición que autentique/verifique exactamente el SYSADMIN
    esperado, sin imprimir su contraseña.
- Prueba de aceptación sugerida:
  - Sobre una base vacía, el instalador debe terminar con un único SYSADMIN
    activo y utilizable; cualquier fallo debe producir código no cero y nunca el
    banner de éxito.

### USR-006 - El login local permite fuerza bruta y amplificación de escritura

- Severidad: alta.
- Tipo: autenticación / abuso / disponibilidad.
- Evidencia:
  - Cada POST inválido ejecuta el hasher mediante `AuthenticationForm` y crea una
    fila de auditoría (`apps/usuarios/views.py:16-18` y `:51-61`).
  - La vista no declara throttle, lockout, backoff, CAPTCHA ni límite por
    usuario/IP.
  - Los throttles agregados durante esta revisión protegen el login API, no la
    vista Django `/login/`.
- Reproducción validada:
  - Doce passwords incorrectos consecutivos devolvieron la pantalla normal,
    crearon doce eventos `LOGIN_FAIL` y no bloquearon un login válido inmediato.
- Impacto:
  - Facilita password spraying y fuerza bruta contra usuarios conocidos.
  - Cada intento consume un hash costoso y una escritura; un atacante puede
    convertir el endpoint en presión simultánea de CPU y base de datos.
  - La auditoría ilimitada puede crecer precisamente durante un ataque.
- Recomendación:
  - Aplicar ventanas corta y sostenida por combinación de IP y username, con
    proxy confiable y respuesta genérica.
  - Añadir backoff/lock temporal proporcional y alertas agregadas.
  - Conservar evidencia útil sin requerir una fila síncrona por cada request de
    una ráfaga ilimitada.
- Prueba de aceptación sugerida:
  - Una ráfaga supera el umbral y recibe 429/backoff; otra IP no bloquea
    globalmente a la víctima y la telemetría no crece sin límite.

## Hallazgos P2

### USR-007 - No hay un flujo autoritativo para provisionar usuarios tenant

- Severidad: media-alta.
- Tipo: ciclo de vida / onboarding / consistencia RBAC.
- Evidencia:
  - Django Admin omite `negocio` tanto al crear como al editar
    (`apps/usuarios/admin.py:17-41`).
  - Tampoco crea una `AsignacionRol`.
  - La API RBAC declara expresamente que la gestión de usuarios vive fuera y
    expone un viewset de solo lectura (`apps/api/views/permisos.py:169-183`).
  - Sync de asignaciones no crea usuarios; difiere la asignación si el username
    no existe (`apps/sync/engine.py:987-1021`).
  - Los comandos tenant crean únicamente el admin y el usuario de servicio
    (`apps/tenancy/management/commands/bootstrap_tenant.py:330-357` y
    `:386-400`).
- Reproducción validada:
  - El admin creó una CAJERA con `negocio=None`, cero asignaciones RBAC y sin
    `ventas.crear`.
- Impacto:
  - No existe un camino soportado de extremo a extremo para incorporar una
    cajera tenant utilizable.
  - Shell, Admin y scripts pueden producir usuarios con estados distintos y
    resultados de permiso inesperados.
- Recomendación:
  - Crear un servicio transaccional de usuario que valide tenant, identidad,
    rol/asignaciones, estado y auditoría.
  - Exponer ese servicio por una UI/API tenant-scoped; hacer que Admin y comandos
    deleguen en el mismo contrato.
- Prueba de aceptación sugerida:
  - Alta, traslado, desactivación y baja deben producir el mismo estado desde
    UI, API, Admin y comando, o los caminos no soportados deben estar cerrados.

### USR-008 - El login acepta una redirección externa controlada por `next`

- Severidad: media-alta.
- Tipo: open redirect / phishing.
- Evidencia:
  - Después de autenticar, toma directamente `request.GET['next']` y ejecuta
    `redirect(next_url)` (`apps/usuarios/views.py:43-45`).
  - No usa `url_has_allowed_host_and_scheme` ni restringe esquema/host.
- Reproducción validada:
  - `POST /login/?next=https://evil.example/phishing` con credenciales válidas
    devolvió 302 hacia ese host externo.
- Impacto:
  - Un enlace con dominio legítimo del sistema termina en un sitio de phishing
    justo después de un login real.
  - Puede facilitar robo de credenciales secundarias o ingeniería social.
- Recomendación:
  - Aceptar solo URLs relativas o hosts explícitamente permitidos, respetando
    HTTPS.
  - Ante un `next` inválido, usar el destino por rol.
- Prueba de aceptación sugerida:
  - Rutas locales válidas redirigen; URLs absolutas externas, esquemas ambiguos
    y variantes `//host` se rechazan.

### USR-009 - Logout por GET permite cierre de sesión cross-site

- Severidad: media.
- Tipo: CSRF / semántica HTTP.
- Evidencia:
  - La URL expone `logout_view` sin restricción de método
    (`apps/usuarios/urls.py:7-8`).
  - La vista cierra sesión para cualquier método (`apps/usuarios/views.py:69-81`).
  - CSRF no protege GET.
- Reproducción validada:
  - Con `Client(enforce_csrf_checks=True)`, `GET /logout/` devolvió 302 y eliminó
    la sesión sin token CSRF.
- Impacto:
  - Cualquier sitio puede forzar el logout mediante imagen, enlace o navegación.
  - Aunque no entrega datos, interrumpe operación de caja y puede combinarse con
    phishing de reingreso.
- Recomendación:
  - Aceptar solo POST con CSRF y mantener el endpoint idempotente.
  - Usar un formulario/botón POST en la interfaz.
- Prueba de aceptación sugerida:
  - GET debe devolver 405; POST sin CSRF, 403; POST válido debe invalidar sesión.

### USR-010 - `Identity` y `Usuario` son credenciales independientes

- Severidad: media-alta.
- Tipo: identidad / rotación de secretos / soporte.
- Evidencia:
  - `Identity` almacena su propio hash y estado
    (`apps/tenancy/models.py:86-127`).
  - El portal verifica ese hash y luego mapea a un `Usuario` mediante el
    username de membership (`apps/api/auth_views.py:49-88`).
  - El login local y Admin verifican el hash de `Usuario`.
  - El Admin de usuarios cambia solo la contraseña operativa
    (`apps/usuarios/admin.py:17-25`).
  - Los comandos tenant pueden rotar ambas contraseñas de forma coordinada, pero
    solo con su flag explícito (`bootstrap_tenant.py:350-356` y `:428-434`).
- Impacto:
  - “Cambiar la contraseña del usuario” puede cambiar solo una de dos puertas.
  - Una credencial antigua puede seguir funcionando en el portal aunque se haya
    cambiado la local, o viceversa.
  - Los estados `Identity.activo`, `Membership.activo` y `Usuario.activo` también
    pueden divergir.
- Recomendación:
  - Definir cuál entidad es la autoridad humana y evitar dos contraseñas para la
    misma persona.
  - Si la separación es intencional, nombrar las cuentas como identidades
    distintas, prohibir password humano local en cloud y ofrecer rotación/revocación
    conjunta auditable.
- Prueba de aceptación sugerida:
  - Una operación de cambio o desactivación debe enumerar y afectar todas las
    credenciales de esa persona según una política explícita.

### USR-011 - El manager omite validación de contraseña, email, rol y modelo

- Severidad: media.
- Tipo: integridad / credenciales débiles.
- Evidencia:
  - `create_user()` solo comprueba presencia, normaliza email, llama
    `set_password` y guarda (`apps/usuarios/models.py:9-19`).
  - No llama `validate_password()`, `full_clean()` ni valida `rol` contra sus
    choices.
  - Aunque settings declara cuatro validadores de contraseña
    (`config/settings.py:202-218`), `set_password()` solo hashea.
  - `create_superuser()` exige únicamente `is_staff` e `is_superuser`; no exige
    estado activo, rol global ni negocio nulo (`apps/usuarios/models.py:22-34`).
- Reproducción validada:
  - El manager guardó contraseña `1`, email `no-es-email` y rol `OWNER`; el hash
    verificó correctamente esa contraseña inválida.
- Impacto:
  - Comandos, fixtures, scripts y servicios pueden saltarse controles que el
    formulario Admin sí aparenta aplicar.
  - Un rol fuera del catálogo produce comportamiento inconsistente y difícil de
    diagnosticar.
- Recomendación:
  - Centralizar validación en un servicio/manager y aplicarla a todos los caminos
    humanos.
  - Agregar constraints de base viables para estados/roles y reservar un camino
    explícito para usuarios de servicio con password inutilizable.
- Prueba de aceptación sugerida:
  - El mismo payload inválido debe fallar por manager, Admin, API, comando y
    modelo; un usuario de servicio debe usar una factoría separada.

### USR-012 - Hay tres fuentes de privilegio sin invariantes comunes

- Severidad: media-alta.
- Tipo: autorización / deuda de transición.
- Evidencia:
  - `Usuario.rol` conserva `SYSADMIN`, `ADMIN` y `CAJERA`, y sus propiedades
    siguen siendo consumidas (`apps/usuarios/models.py:74-84` y `:125-138`).
  - `tiene_permiso()` delega al RBAC (`apps/usuarios/models.py:140-153`).
  - `PermissionsMixin` añade `is_superuser`, grupos y permisos Django; Admin
    expone los tres (`apps/usuarios/admin.py:23-26` y `:45`).
  - El portal exige rol legacy ADMIN/SYSADMIN, mientras las operaciones internas
    pueden consultar RBAC o flags Django según el consumidor.
- Impacto:
  - El mismo usuario puede ser CAJERA en un campo, administrador por RBAC y
    superuser Django simultáneamente.
  - Cambiar una fuente puede dar apariencia de revocación sin retirar las otras.
  - Es difícil responder “qué puede hacer este usuario” y auditar el resultado.
- Recomendación:
  - Definir una matriz de autoridad: autenticación, acceso al portal, acceso a
    Admin y permisos de negocio.
  - Reducir bypasses legacy y derivar capacidades desde una única política por
    contexto.
  - Impedir combinaciones contradictorias mediante servicio y checks.
- Prueba de aceptación sugerida:
  - Cubrir todas las combinaciones de rol legacy, RBAC, staff y superuser y
    verificar una política documentada, especialmente downgrades.

### USR-013 - Las mutaciones de usuario no producen auditoría de dominio

- Severidad: media.
- Tipo: trazabilidad / no repudio.
- Evidencia:
  - `Auditoria.TipoAccion` define creación, modificación, activación y
    desactivación de usuarios (`apps/auditoria/models.py:79-84`).
  - No hay consumidores de esas acciones fuera de su declaración.
  - El Admin no sobrescribe `save_model`, `delete_model` ni acciones masivas
    (`apps/usuarios/admin.py:6-45`).
  - Los comandos de bootstrap/normalización modifican estado, rol, negocio y
    contraseña sin un evento de dominio asociado.
- Impacto:
  - No queda una traza uniforme de quién creó, activó, desactivó, trasladó o
    elevó una cuenta.
  - `django_admin_log` cubre solo mutaciones hechas desde Admin y no unifica
    comandos, shell, portal o sync.
- Recomendación:
  - Emitir eventos con actor real, tenant, estado anterior/nuevo y razón desde el
    servicio autoritativo de ciclo de vida.
  - Evitar registrar hashes, passwords o tokens.
- Prueba de aceptación sugerida:
  - Cada mutación soportada genera exactamente un evento durable y ninguna
    escritura de credenciales aparece en metadata.

### USR-014 - La IP de auditoría confía en cualquier `X-Forwarded-For`

- Severidad: media.
- Tipo: evidencia forense / proxy trust.
- Evidencia:
  - `get_client_ip()` toma siempre el primer valor de `HTTP_X_FORWARDED_FOR`
    (`apps/auditoria/models.py:478-494`).
  - No comprueba que el request venga de un proxy confiable ni la cantidad de
    saltos.
  - Login, logout y sesión usan ese helper.
- Reproducción validada:
  - Un cliente con `REMOTE_ADDR=127.0.0.1` y header
    `203.0.113.77, 10.0.0.5` hizo que auditoría almacenara `203.0.113.77`.
- Impacto:
  - Si el proxy no elimina el header entrante, un atacante elige la IP guardada.
  - Alertas de cambio de IP y análisis de intentos fallidos pueden producir
    falsos positivos o evidencia atribuida incorrectamente.
- Recomendación:
  - Resolver IP según la topología real y confiar el header solo desde proxies
    conocidos.
  - Guardar también peer inmediato/cadena normalizada cuando sea legal y útil.
- Prueba de aceptación sugerida:
  - Un cliente directo no puede fijar la IP; uno tras el proxy configurado
    conserva la IP correcta con uno y varios saltos.

### USR-015 - `last_login` y `ultimo_acceso` cuentan historias distintas

- Severidad: media-baja.
- Tipo: observabilidad / modelo duplicado.
- Evidencia:
  - `AbstractBaseUser` aporta `last_login`; el modelo agrega `ultimo_acceso`
    (`apps/usuarios/models.py:97-99`).
  - El login de Django actualiza `last_login` mediante su signal.
  - El portal actualiza solo `ultimo_acceso` con `_touch_user`
    (`apps/api/auth_views.py:322-325`).
  - Admin muestra únicamente `ultimo_acceso` como readonly
    (`apps/usuarios/admin.py:29-34`).
- Reproducción validada:
  - Tras login local, `last_login` tenía fecha y `ultimo_acceso` seguía `NULL`.
- Impacto:
  - Operadores pueden interpretar una cuenta activa como nunca usada.
  - Reportes y reglas futuras pueden discrepar según el campo elegido.
- Recomendación:
  - Mantener una sola semántica o nombrar explícitamente
    `ultimo_login_local`/`ultimo_login_portal`.
  - Actualizarla en un servicio/evento común y documentar zona horaria.
- Prueba de aceptación sugerida:
  - Cada canal actualiza los campos definidos por contrato y Admin presenta su
    significado sin ambigüedad.

### USR-016 - La unicidad de username/email depende de mayúsculas

- Severidad: media.
- Tipo: identidad / normalización / soporte.
- Evidencia:
  - `username` y `email` usan `unique=True` sin una restricción funcional
    case-insensitive (`apps/usuarios/models.py:50-59`).
  - `normalize_email()` normaliza el dominio, no necesariamente la parte local
    ni el username (`apps/usuarios/models.py:16`).
  - El login local identifica por username, mientras el portal busca identity
    con `email__iexact` y toma `.first()` (`apps/api/auth_views.py:53-58`).
- Impacto:
  - Pueden coexistir identidades visualmente equivalentes según collation/base,
    importación o canal.
  - Esto complica soporte, memberships y migraciones entre SQL Server/PostgreSQL.
- Recomendación:
  - Definir normalización canónica y una restricción case-insensitive compatible
    con las bases soportadas.
  - Auditar y resolver colisiones antes de migrar.
- Prueba de aceptación sugerida:
  - Variantes de mayúsculas, espacios y Unicode se aceptan/rechazan de forma
    idéntica en manager, Admin, portal e importaciones.

## Hallazgos P3

### USR-017 - La sesión de 12 horas tiene vencimiento deslizante sin máximo absoluto

- Severidad: baja-media.
- Tipo: gestión de sesión / política.
- Evidencia:
  - `SESSION_COOKIE_AGE` es 12 horas, `SESSION_SAVE_EVERY_REQUEST=True` y
    `SESSION_EXPIRE_AT_BROWSER_CLOSE=True` (`config/settings.py:264-266`).
  - Cada request activo renueva el vencimiento; no se define un máximo absoluto
    de jornada.
- Impacto:
  - Una terminal que mantenga tráfico y el navegador abierto puede conservar la
    sesión mucho más de 12 horas.
  - Combinado con USR-001, desactivar la cuenta tampoco corta esa sesión.
- Recomendación:
  - Definir timeout inactivo y máximo absoluto según operación de caja; renovar
    autenticación para acciones sensibles.
- Prueba de aceptación sugerida:
  - Simular actividad continua y confirmar que la sesión expira en el máximo
    absoluto acordado.

### USR-018 - `apps/usuarios` no tiene cobertura propia

- Severidad: baja como deuda; alta por los contratos que quedaron sin cubrir.
- Tipo: pruebas / regresión.
- Evidencia:
  - `apps/usuarios/tests/` contiene únicamente `__init__.py` vacío.
  - La suite seleccionada descubrió 47 pruebas cross-app, pero cero del módulo.
  - No había casos para activo/is_active, revocación, open redirect, logout,
    auditoría fallida, manager, Admin, installer o último acceso.
- Impacto:
  - Cambios de auth pueden pasar suites vecinas sin validar el ciclo completo de
    la identidad.
  - La política queda inferida de consumidores dispersos.
- Recomendación:
  - Convertir las reproducciones de esta auditoría en pruebas permanentes tras
    acordar el comportamiento objetivo.
  - Separar tests de modelo/manager, sesiones locales, Admin, JWT/token,
    provisioning tenant e instalación.
- Prueba de aceptación sugerida:
  - Una suite propia cubre todos los canales y falla ante cualquier regresión de
    revocación o aislamiento.

### USR-019 - Rutas de desarrollo y redirecciones no comparten una política de rol

- Severidad: baja.
- Tipo: superficie productiva / UX / contrato de navegación.
- Evidencia:
  - `styleguide` vive en `apps/usuarios/views.py:84-86`, pero se publica desde la
    urlconf global (`config/urls.py:32`) y solo requiere login.
  - Si un usuario ya autenticado abre `/login/`, siempre se redirige al dashboard
    (`apps/usuarios/views.py:11-13`).
  - Tras un login nuevo, en cambio, ADMIN va al dashboard y el resto al POS
    (`apps/usuarios/views.py:46-50`).
- Impacto:
  - La misma identidad obtiene destinos distintos según cómo llegue a login.
  - Una guía interna de UI permanece expuesta en todos los entornos a cualquier
    cuenta autenticada.
- Recomendación:
  - Centralizar `home_for_user(user)` y reutilizarlo en todos los caminos.
  - Publicar styleguide solo en desarrollo/staff o retirarlo de producción.
- Prueba de aceptación sugerida:
  - Cada rol obtiene el mismo home desde login nuevo, login ya autenticado y
    redirect de middleware; styleguide no existe para usuarios productivos.

## Validaciones ejecutadas

### Suite existente seleccionada

Comando:

```powershell
C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py test `
  apps.usuarios `
  apps.tenancy.tests.test_auth `
  apps.permisos.tests `
  apps.api.tests.test_rbac_admin `
  --settings=config.settings_development --keepdb -v 1
```

Resultado:

- **47/47 aprobadas**.
- 0 fallos y 0 errores.
- 36.692 s.
- System check sin problemas durante el discovery.
- `apps/usuarios`: 0 pruebas propias.

### Batería adversarial temporal

Resultado limpio:

- **14/14 reproducciones aprobadas**.
- 11.087 s.
- El módulo temporal se eliminó al terminar.

Casos confirmados:

1. `activo=False` mantiene `is_active=True` y el backend autentica.
2. Una sesión existente sobrevive a la desactivación.
3. Un staff/superuser inactivo abre Django Admin.
4. Un JWT legacy sigue autenticando y refrescando tras desactivar.
5. `next=https://...` produce redirect externo.
6. GET logout cierra sesión sin CSRF.
7. Fallo de auditoría en logout devuelve 500 y conserva la sesión.
8. Fallo de auditoría en login devuelve 500.
9. Doce intentos fallidos no bloquean y crean doce eventos.
10. El cliente puede fijar la IP auditada con `X-Forwarded-For`.
11. El manager acepta password, email y rol inválidos.
12. El instalador reproduce el `ValueError` por email vacío.
13. Login local actualiza `last_login`, no `ultimo_acceso`.
14. Admin crea una cajera sin negocio ni asignación RBAC.

### Checks de proyecto y migración

```powershell
C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py check `
  --settings=config.settings_development

C:\Users\Santiago\anaconda3\envs\pos_fifo\python.exe manage.py makemigrations `
  usuarios --check --dry-run --settings=config.settings_development
```

Resultado final:

- `manage.py check`: **sin problemas**.
- `makemigrations usuarios --check --dry-run`: **sin cambios detectados**.

### Condición concurrente observada

Mientras corrían las validaciones, `apps/api/auth_views.py` cambió en el
workspace y una invocación intermedia de `manage.py check` alcanzó un archivo
transitoriamente incompleto. Al estabilizarse ese archivo, el check final pasó.

Se intentó además una reproducción cloud aislada para la puerta de Django Admin,
pero no llegó a ejecutar los casos: la base compartida `test_pos_fifo_dev`
intentó reaplicar `permisos.0003` sobre una columna ya existente. No se alteró ni
recreó esa base porque pertenece al trabajo concurrente. El archivo temporal de
esa prueba también fue eliminado. USR-002 queda por tanto sustentado por trazado
estático de urlconf, router y autenticadores, no contado dentro de las 14
reproducciones.

## Fortalezas observadas

- El modelo usa `set_password()` y hashers de Django; no guarda passwords planos.
- El formulario local incluye CSRF y usa `autocomplete="current-password"`.
- El login propio rechaza nuevas sesiones locales cuando detecta
  `activo=False`; el defecto está en no convertir ese estado en el contrato
  general de Django.
- El autenticador tenant actual vuelve a cargar un `Usuario` con `activo=True` y
  las correcciones concurrentes ya revalidan identity/membership en access y
  refresh.
- Los usuarios de servicio creados por onboarding reciben password inutilizable.
- El motor RBAC niega por defecto a usuarios ordinarios sin asignación.
- Cloud configura cookies secure, redirect HTTPS y blacklist de refresh en el
  estado visible al cierre.
- `makemigrations` no detectó drift del esquema de `usuarios`.

## Orden de remediación sugerido

1. Unificar `activo` con `is_active` y cerrar todos los canales de revocación:
   sesión, Admin, token DRF, JWT, refresh y overrides.
2. Decidir la política productiva de Django Admin/default users en cloud y
   eliminar la puerta paralela o convertirla en break-glass controlado.
3. Eliminar el significado implícito global de `negocio=NULL` y proteger la baja
   de negocio.
4. Desacoplar la disponibilidad de auditoría del logout; hacer POST+CSRF.
5. Corregir el instalador con postcondiciones y manejo estricto de errores.
6. Añadir defensa de fuerza bruta al login local y corregir la redirección
   externa.
7. Implementar un servicio único de ciclo de vida tenant con RBAC y auditoría.
8. Resolver la duplicidad Identity/Usuario y la matriz de fuentes de privilegio.
9. Endurecer manager, normalización e integridad case-insensitive.
10. Convertir las reproducciones en pruebas permanentes y fijar política de
    sesión/último acceso.

## Criterio de cierre sugerido

La app puede considerarse cerrada cuando:

- desactivar o revocar una persona corta todos sus accesos inmediatamente;
- cloud tiene una sola frontera humana o un break-glass explícito y auditable;
- ningún usuario ordinario puede quedar o convertirse en “global” por `NULL`;
- existe un flujo tenant-scoped para alta, roles, traslado, desactivación y baja;
- logout funciona aun si auditoría falla y solo acepta POST con CSRF;
- login local limita abuso y no redirige fuera del sistema;
- el instalador falla cerrado y verifica el administrador creado;
- las credenciales Identity/Usuario tienen una autoridad y rotación definidas;
- todas las mutaciones producen auditoría sin secretos;
- una suite propia cubre sesiones, Admin, JWT/token, tenant y provisioning.
