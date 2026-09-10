# Encargo del agente B — Claude

Fecha: **2026-09-09**. Estado inicial: **pendiente**.
Fuente de alcance/propiedad/gates: [plan maestro](../PLAN_CIERRE_PROD.md).
Contraparte: [encargo de Codex](CIERRE_PROD_CODEX.md).

Claude es dueño de Windows/dotenv, PDF/impresión, configuración/módulos,
operación comercial y portal React. **No es una segunda rama completa del
backend:** no debe rehacer RBAC, sync, auditoría común o maestros de Codex.

## Antes de empezar

1. Leer las instrucciones compartidas, este encargo, el plan maestro y los
   `AGENTS.md` de las apps que toques. El plan externo en `.claude/plans` es
   antecedente; las decisiones y correcciones vigentes están en el plan maestro.
2. Consumir el SHA base publicado por A00. Si aún falta, se puede inspeccionar
   C01 en solo lectura; no empezar una rama concurrente en el worktree original.
3. Trabajar en `C:/Proyectos/pos_fifo_system_cierre_claude` y en un worktree propio
   del frontend basado en su `origin/develop`. No tocar el worktree de staging
   ni sus `tfvars`; no empujar staging/main/develop ni ejecutar Terraform.
4. Venv/BD/tests/tenants/puertos y servicios de prueba propios. No instalar
   dependencias en el conda compartido ni ejecutar scripts NSSM contra los POS reales.
5. Seguir C01 → C02 → C03/C05 → C04 → C06, aprovechando las entradas disponibles.
   C04 puede maquetarse con fixtures una vez publicado CT-04; su aceptación exige
   backend real. No esperar sin avanzar cuando haya tareas independientes listas.

## C01 — Actualización Windows y dotenv

**Entrada:** A00. **Puede empezar mientras Codex trabaja en A01/A02.**
**Propiedad:** `deploy/**`, launchers Windows, `.env.example`, comandos/tests de
configuración para migrar/verificar entorno y documentación local. Settings,
dependencias y CI se solicitan a Codex mediante CT-03/05.

1. Leer los runbooks de instalación nueva/actualización y recorrer todos los
   entrypoints: instalar, actualizar, arrancar/detener, sync, backup, registrar
   servicios y comandos invocados solos. Inventariar cada consumidor del BAT viejo.
2. Usar BAT como launcher fino y Python/dotenv para interpretar el archivo.
   No implementar un parser `.env` con `for /f`, expansión de variables BAT o
   ejecución del contenido como comandos.
3. Aceptar instalación con BAT legado, solo `.env` o ambos. `.env` es canónico;
   detectar/discutir discrepancias sin mezcla silenciosa. Conversión atómica,
   validación antes de reemplazar, respaldo recuperable del BAT legado y fallo
   explícito sin continuar si no se pudo convertir. No imprimir secretos.
4. Probar `!`, `%`, `&`, `#`, `=`, comillas, Unicode y espacios, distinguiendo
   valores literales de comentarios admitidos por dotenv. Test de idempotencia
   y conservación de valores; no asumir que un caso se rompió sin reproducirlo.
5. Instalación/rotación de SECRET_KEY sobre el archivo canónico correcto, no sobre
   un BAT que ya no se consume. Documentar precedencia `override=False` con A01.
6. Re-registrar **ambos** servicios NSSM: web y sync. Inspeccionar y retirar el
   snapshot de variables antiguas de la aplicación que pisaría `.env`; conservar
   variables ajenas necesarias al servicio. Contrato previsto para la aplicación:
   `DJANGO_SETTINGS_MODULE` + `POS_ENV_FILE`, con ruta absoluta y lectura efectiva
   verificada desde el proceso del servicio, no solo desde la consola del operador.
7. Cada comando standalone carga la configuración; no depende de que otra consola
   haya hecho `call env_cliente.bat`. Fallar antes de arrancar por secretos/config
   inválidos, dependencias fallidas, migrate/seeds/checks/static incompletos.
8. Preparar nuevo entorno antes de parar servicios; backup de BD, código, entorno,
   configuración de servicios, media/private/reportes. Cambios de fase registrados,
   recuperación tras corte de energía/disco lleno y sin actualización medio exitosa.
9. Paquete con versión, hashes y wheelhouse offline del baseline A01. Sin `.env`,
   secretos, dumps o cachés de trabajo. Validar ACL y acceso de cuenta de servicio
   a BD, impresoras y archivos. Actualizar guías/runbooks para la secuencia real.

**Pruebas de aceptación:** matriz BAT / env / ambos; configuración inválida;
invocaciones solas; variables antiguas de NSSM; errores de pip/migrate/seed/static;
update repetido, reinstalación, interrupción y rollback en instalación aislada.
No usar servicios reales para la prueba. Repetir con baseline A01 final.

**Entrega:** `C01-windows-dotenv.md`, PR acotado, comandos/hashes/resultado,
cambios pedidos a settings/deps y lista de verificaciones físicas aún pendientes.

## C02 — Documentos, imágenes e impresión

**Entrada:** A00 para trabajo independiente; CT-01/02 para integrar auditoría/gates.
**Propiedad:** `apps/common/pdf`, `utils/imagenes.py`, `utils/impresoras`, PDF de
sus apps y tests; los hooks de Producto/Cliente y rutas globales los integra Codex.

1. Revalidar deuda COM y documentos frente al código actual; conservar arreglos
   recientes de comprobantes/caja que ya estén en develop.
2. Definir límites y formato: papel/ancho correcto, textos extensos sin truncado
   silencioso, caracteres soportados y moneda. Importes inválidos bloquean un
   documento financiero; logo inválido degrada con aviso/auditoría, no invalida
   el importe ni hace caer toda la operación.
3. Validar imágenes/logo con límite de 8 MiB; tratamiento coherente por ruta de
   carga. Reemplazo/borrado después de commit y recuperación/reintento de cleanup;
   entregar hooks a Codex para sus modelos, no modificarlos directamente.
4. PDFs de listas grandes paginados y consumo acotado; rechazar explícitamente
   rangos imposibles. No esconder resultados parciales como documento completo.
5. Impresión autorizada, con cuota y auditoría. Resultado incierto no dispara
   reimpresión automática que duplique tickets. Reimpresión explícita y trazable.
   Mantener gramática de códigos de barras y no regenerar códigos históricos.
6. Quitar dependencia externa innecesaria de Chart.js para operación offline;
   al tocar navegación/estáticos globales coordinar pedidos de Codex.
7. Añadir tests automatizados de medidas/contenido/errores y preparar matriz
   física: ticket, etiqueta, comprobante, reimpresión, nombres largos, imágenes
   ausentes, importes límite y drivers/cuenta NSSM de cada tienda.

**Aceptación:** tests en baseline nuevo y evidencia diferenciada entre PDF generado,
imagen inspeccionada e impresión física. Las impresoras reales se validan en C06
y la visita; no cerrar ese renglón por un mock.

## C03 — Configuración y módulos vendibles

**Entrada:** CT-01/02; CT-03 se diseña con Codex. **Propiedad:** apps configuracion,
suscripciones y sus APIs específicas; settings/loader global/sync son de Codex.

1. Cerrar todos los hallazgos CFG/SUS vigentes del inventario, no solo los de
   dotenv. Evitar crear configuración como efecto de una lectura.
2. Publicar un único resolutor efectivo consumible por UI, API, servicios y
   sync. BD + memo por petición, sin caché persistente incoherente entre workers;
   prueba de cambio visible en la siguiente petición de otro worker.
3. Separar catálogo inmutable de módulos, presets versionados y suscripciones.
   `Plan.activo=False` impide nuevas altas, no suspende clientes existentes;
   suspensión explícita. No sobreescribir personalizaciones por reseed.
4. Validar relaciones control-plane/tenant, pagos/porcentajes/copias, proveedor,
   emisor y MSeller. No activar proveedor nativo no implementado. Protección de
   borrados y permisos/ámbito en administración y accesos rápidos.
5. Commands con salidas no exitosas en error; integración con C01. Entregar
   interface/tests/fixtures a Codex para que conecte `apps/sync` y `config/**`.
6. Aplicar contrato CT-02 en sus APIs/servicios/UI; no mantener un segundo motor
   de permisos o saltarse restricciones por ocultar únicamente el botón.

**Aceptación:** tabla de permisos × módulos × suspensión × sucursal consistente
en UI/API/servicio; multi-worker, seeds repetidos, lecturas sin side effects,
validaciones negativas y configuración inválida que aborta preflight.

## C04 — Portal React y administración de maestros

**Entrada:** CT-02/03 y CT-04 publicado por A05/A06.
**Propiedad:** frontend completo salvo workflows; endpoints de maestros los hace
Codex. No editar API/backend del otro para acomodar un mock del frontend.

1. Inventariar la rama `origin/develop` del frontend y sus cambios pendientes de
   staging/main. Mantener worktree y branch propios, sin empujar ramas de deploy.
2. Reutilizar filtros Activos/Inactivos/Todos; default activos. Inactivos incluye
   producto activo con categoría inactiva e informa el motivo. Categorías con
   tratamiento equivalente; reactivar exige autorización.
3. Desactivar categoría no modifica flags individuales de productos. Mantener
   documentos históricos/stock administrativo; ocultar solo selección operativa.
4. Mostrar pendiente, rechazado y conflicto de mutación local; visualizar ambos
   valores y motivo. Resolver conservar cloud/aplicar local con versión actual
   y permiso. Una nueva colisión no se «soluciona» repitiendo PATCH sin revisión.
5. Incorporar pantallas necesarias para roles/asignaciones, configuración y
   operaciones que ya no dependerán del `/admin/` cloud. Respetar identidad y
   revocación del backend, sin lógica de autorización paralela.
6. Revisar todos los selectores, paginación y búsqueda; probar más de 200 filas,
   respuestas parciales/errores, permisos y ámbito. Conectar a backend integrado,
   no solo MSW/fixtures. Notificaciones se conservan para activación gradual.

**Aceptación:** build/lint/tests y recorrido integrado de inactivos, reactivación,
conflictos concurrentes, permisos y paginación. Entregar SHA frontend compatible
con SHA backend y advertencia de que main autodespliega; no fusionarlo aún.

## C05 — Ventas, inventario, caja, CxC, cotizaciones y reportes

**Entrada:** CT-01/02/03; CT-04 para estado efectivo de maestros y selectores.
Puede empezar por correcciones financieras independientes mientras A05 avanza.

1. Revalidar deuda de estas apps y cerrar cada ID vigente del inventario. Incluir
   COM/CFG/SUS asociados mediante C02/C03; no declarar deuda cerrada por omisión.
2. Constraints con preflight de datos; concurrencia e idempotencia en venta,
   inventario y abonos. Dos peticiones con la misma clave producen un solo efecto
   financiero. Negocio/sucursal y autorización verificados en servicio y API.
3. Mantener políticas decididas: cobrar efectivo sin turno abierto es válido;
   anular venta con abonos exige revertirlos antes; finalización contable manual.
   Retirar/reemplazar el launcher roto de cierre automático por documentación
   del comando manual. No instalar horario/NSSM one-shot ni inferir datos históricos.
4. Cotizaciones: cantidades, totales, numeración/concurrencia, cliente activo,
   conversión, auditoría y baja lógica según hallazgos vigentes. No caducar ni
   sanear cotizaciones antiguas como requisito de despliegue por iniciativa propia.
5. Selección y escaneo comerciales solo de maestros operativamente activos;
   revalidar al confirmar venta/cotización, no confiar en una lista cargada antes
   de una desactivación. Usar CT-04 para pendiente/conflicto; conservar snapshots.
6. Productores de auditoría CT-01 en transacciones del dominio. Entregar contratos
   de eventos y casos a Codex para sus handlers de sync/API sync; no editar esos
   archivos. Los conflictos de maestro no deben atascar hechos financieros.
7. Revalidar caja/PDF de los commits acumulados y BUG-I/J; paginación/reportes y
   consistencia de cierres con abonos y ventas. Distinguir tests de campo físicos.

**Aceptación:** pruebas concurrentes, rollback con auditoría, reversas/saldos,
permisos/sucursal, reintentos sync integrados, inactividad después de cargar la
pantalla y reglas manuales aprobadas. No cambios financieros históricos masivos.

## C06 — Ensayo Windows, integración y validación cruzada

**Entrada:** C01–C05 y baseline/contratos finales de Codex; coordinar A08/A09.

1. Ensayar paquete final en Windows limpio y actualizaciones de copias autorizadas
   representativas de RP y SK. Dumps viejos sirven de arranque, no sustituyen el
   ensayo final con copia reciente. Registrar versión inicial real por instalación.
2. Comprobar preflight completo antes de parada, migraciones necesarias por BD,
   adopción de identidades y pull antes de habilitar mutaciones offline. No asumir
   que «11 migraciones» aplica a todas las tiendas o a este candidato final.
3. Ensayar backup/restauración en aislamiento con código/env/servicios/media y
   datos. Diferenciar retorno antes de nuevas ventas de recuperación después:
   preservar operaciones posteriores al dump y definir reconciliación/fix-forward.
4. Revisar los bloques de Codex desde la perspectiva del cliente Windows/portal:
   revocaciones con permisos desconocidos, identidad inicial, errores/timeout,
   conflictos, multi-tenant y recuperación de falso ACK. Reportar hallazgos;
   el dueño corrige. No ejecutar reparación sobre clientes reales en este ensayo.
5. Participar en G1/G2: suite serial Windows, e-CF separado, frontend, rig
   cloud/POS, impresión física coordinada y matriz de negocio. Los tests
   compartidos requieren BDs aisladas, no dos suites pisando los mismos tenants.
6. Preparar lista de visita y comunicación al operador: `.env`, SECRET_KEY si
   corresponde, cambios visibles RD$, logout, inactivos y cifras de arqueo;
   no habilitar funciones aún incompatibles con la versión POS instalada.
7. En operación autorizada, ayudar a verificar cloud primero, luego RP y después
   SK tras un día real de RP. Registrar venta/crédito/abono/anulación, impresión,
   reimpresión, cierre/PDF, offline/reinicio, ambos servicios y saldos/sync.
   Atender específicamente el riesgo de doble impresión de SK.

**Aceptación:** evidencia por versión/tienda, runbook practicable, pruebas físicas
identificadas y cero hallazgos bloqueantes propios pendientes. Codex consolida el
acta; la autorización de producción la da el responsable, no un test verde.

## Forma de entregar cada bloque

- PR/commits propios y `docs/handoffs/cierre_prod/Cxx-<tema>.md` con el formato
  del plan maestro. Actualizar mapas de sus apps si cambian contratos/entrypoints.
- Incluir deltas propuestos a `TODO_AUDITORIAS`, `ESTADO_AUDITORIAS`, `BUGS` y
  `PROJECT_STATUS` en el handoff; los edita Codex para evitar conflictos.
- Si falta una ruta/helper/campo del núcleo, describir firma/caso/test requerido
  a Codex. No parchear temporalmente su archivo y dejarle resolver el conflicto.
- No pasar a otro bloque dependiente hasta tener commit de contrato integrado.
  Sí continuar con tareas independientes de su propiedad.

## Texto listo para iniciar a Claude

> Lee AGENTS.md, CLAUDE.md, docs/PROJECT_STATUS.md, docs/PLAN_CIERRE_PROD.md y
> docs/planes/CIERRE_PROD_CLAUDE.md. Trabaja como agente B en tu propio worktree,
> venv y BDs. Consume el SHA común publicado por A00; si aún no está, inspecciona
> C01 en solo lectura y solicita esa base. Empieza por C01 (Windows/dotenv),
> continúa C02 y sigue las dependencias del encargo. No modifiques archivos de
> Codex, tfvars reales ni ramas de despliegue. Entrega PR y handoff por bloque,
> con tests y solicitudes explícitas de integración. No despliegues ni repares
> datos de clientes; no marques pruebas físicas como hechas si solo se simularon.
