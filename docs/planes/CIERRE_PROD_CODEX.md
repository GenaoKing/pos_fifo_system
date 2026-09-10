# Encargo del agente A — Codex

Fecha: **2026-09-09**. Estado inicial: **pendiente**.
Fuente de alcance/propiedad/gates: [plan maestro](../PLAN_CIERRE_PROD.md).
Contraparte: [encargo de Claude](CIERRE_PROD_CLAUDE.md).

Codex es el integrador y dueño del núcleo backend. Este encargo autoriza el
reparto del trabajo; la ejecución operativa sigue las autorizaciones del plan
maestro. No modificar archivos de Claude ni resolver su cola de tareas por él.

## A00 — Base común, inventario y tablero

**Entrada:** ninguna. **Desbloquea:** A01 y C01.

1. Leer instrucciones del repo y verificar cambios concurrentes en ambos repos.
   Commitear la documentación de coordinación en un cambio aislado y registrar
   los SHAs base. Preparar worktrees y entornos según G0, sin tocar staging.
2. Contrastar `develop`, remotos y versiones instaladas. Clasificar los commits
   adicionales de develop, incluidas caja/PDF/notificaciones; no fusionarlos
   otra vez si ya forman parte de la base.
3. Crear `docs/handoffs/cierre_prod/INVENTARIO.md`: una fila por hallazgo vigente
   de `TODO_AUDITORIAS`, `ESTADO_AUDITORIAS`, `BUGS` y deuda pendiente de roadmaps.
   Campos: ID, fuente, reproducción actual, dueño A/C, bloque, severidad,
   estado, commit y prueba. Hallazgo ya corregido requiere referencia verificable.
4. Registrar decisiones del plan donde la documentación antigua siga diciendo
   «pendiente»: offline, inactivos, caja, CxC, cierre manual y exclusiones.
   Distinguir código pendiente de código terminado que solo falta desplegar.
5. Crear el registro `CONTRATOS.md` con CT-01…05, productor/consumidor, estado y
   commit. Registrar el dueño de cada archivo compartido/fixture antes de trabajo.
6. Inventariar los `tfvars` canónicos del worktree de staging sin exponer valores
   ni copiarlos a prod. Registrar que no se usará la rama local staging atrasada
   ni se hará reset/force-push como parte de esta preparación.

**Aceptación:** Claude tiene una base consumible y puede comenzar C01; ningún
hallazgo queda sin dueño ni se fuerza a dos agentes a usar una BD/servicio común.

## A01 — Baseline de ejecución y dependencias

**Entrada:** A00. **Desbloquea:** baseline definitivo para todos los tests.

1. Preparar la transición del baseline a Django 5.2 LTS; verificar el parche y
   compatibilidad al fijarlo, documentando la diferencia con el stack actual.
   Matriz objetivo: Python 3.11 Windows y Python 3.12 cloud; comprobar CI también.
2. Fijar dependencias reproducibles por destino, incluidas las que C01/C02 pidan
   para dotenv, ReportLab/Pillow y Windows. No actualizar el conda compartido.
3. Ajustar Docker/CI, locks y requisitos de plataforma; entregar los metadatos
   necesarios para el wheelhouse/paquete offline de Claude.
4. Definir con C01 el contrato de `POS_ENV_FILE`, precedencia y errores. Codex
   es el escritor de `config/settings*` y `config/env_check.py`; Claude es el
   escritor de comandos de configuración, launchers y conversor.
5. Ejecutar checks, suite pertinente, e-CF separado y build de imagen en el
   baseline nuevo; corregir deprecaciones/regresiones dentro de su propiedad.
   Pasar a Claude las regresiones de sus apps, no editarlas simultáneamente.

**Aceptación:** entornos instalables y reproducibles; todos conocen el mismo
baseline. C01/C02 repiten aceptación si se desarrollaron contra la base anterior.

## A02 — Auditoría, usuarios, negocios y tenancy

**Entrada:** A01. **Entrega temprana:** CT-01 para que Claude integre productores.

1. Publicar la interfaz de auditoría: actor estable, tenant/sucursal, canal,
   entidad/ID, before/after, resultado y correlación. Redactar contraseñas,
   tokens y secretos. Mutación y evento de auditoría en la misma transacción
   de su BD; logout debe seguir invalidando sesión aunque falle registrar el log.
2. Cerrar los hallazgos AUD vigentes del inventario, con consultas/indexación
   para al menos 90 días locales sin purga. No implementar WORM ni prometer
   resistencia completa a modificaciones de la cola de la cadena de auditoría.
3. Resolver USR/NEG vigentes: identidad del operador, aislamiento, validaciones,
   credenciales local/portal explícitas y no sincronizadas en claro, y sesiones
   con límite absoluto de 12 horas según el contrato de este release.
4. Provisioning control-plane/tenant recuperable e idempotente: estados y
   reanudación, no una falsa transacción atómica entre BDs. Preflight de IDs y
   colisiones; no adjudicar huérfanos al primer usuario ni fusionar identidades
   por aproximación. Proteger la identidad/slug del tenant.
5. Cerrar `/admin/` cloud, mantener local y dejar comandos administrativos
   autorizados/auditados. Coordinar con C04 cualquier pantalla del portal.
6. Resolver infraestructura de tests multi-BD/TEN-016; no crear o borrar BDs
   tenant compartidas durante tests concurrentes.

**Aceptación:** tests de aislamiento, reintento de provisioning, rollback del
dominio/auditoría, errores de log y permisos. Contrato CT-01 integrado, no solo prosa.

## A03 — RBAC y compatibilidad de revocaciones

**Entrada:** A02. **Desbloquea:** CT-02 para C02/C03/C04/C05.

1. PER-006: identidad cloud estable; terna usuario/rol/sucursal inmutable.
   Cambiarla es revocar la relación anterior y crear otra en una transacción.
2. PER-007: baja lógica versionada de roles/asignaciones. Reactivar el rol no
   reactiva asignaciones revocadas. Cambios M2M actualizan versión/timestamp.
3. Reconciliación de snapshot completo solo sobre filas de propiedad cloud;
   no convertir ausencias de una respuesta parcial/fallida en revocaciones.
4. Separar catálogo de permisos, presets y roles custom. Seeds idempotentes,
   sin sobreescribir permisos personalizados o revocaciones. Eliminar bypass
   ADMIN solo tras demostrar que no se deja sin acceso al operador legítimo.
5. Construir y probar la compatibilidad con las versiones POS instaladas:
   permiso desconocido no puede impedir aplicar `activo=False`. Ensayar mover,
   borrar y revocar con payloads reales viejos/nuevos. Mantener shim/capacidades
   hasta terminar la flota; no aceptar cursores bloqueados como «fail-safe».
6. Cerrar PER restante, auth/API y helpers comunes de notificaciones. Mantener
   reproducibles las migraciones históricas; no hacerlas depender de helpers
   vivos cuya semántica luego cambie.

**Aceptación:** matriz de autorización por acción/sucursal, role move/delete,
revocación con permiso nuevo desconocido, custom roles, snapshot incompleto y
seed repetido. Claude consume un único helper/contrato de autorización.

## A04 — Transporte durable y reparación de BUG-K

**Entrada:** A01 y contrato de identidad; integrar A03 para pruebas de RBAC.

1. Reclamo de trabajo con lease de 5 minutos y recuperación de lease vencido;
   probar procesos concurrentes, muerte entre pasos y reintento.
2. Diferidos durables: cursor avanza solo si el ítem quedó aplicado o guardado
   para resolver después. Estado PARCIAL mientras haya pendientes relevantes;
   nunca contabilizarlo como sincronización completa.
3. Idempotencia por identidad estable y ámbito tenant/sucursal; errores reales
   no son duplicados exitosos. Mantener compatibilidad de protocolo/capacidades.
4. Sustituir el guard fijo de 3 segundos por timeout/retry configurados y
   tolerancia a arranque en frío. No exigir ping online para editar maestros.
5. Crear reconciliación **dry-run por defecto** de BUG-K: cruzar eventos locales
   `CONFIRMADO` con hecho realmente persistido en cloud por ID de negocio/hash
   y ámbito. Producir lista exacta de ausentes/conflictivos y plan dirigido.
   No resetear todos los confirmados ni suponer que backfill/reintentar los verá.
6. Separar faltantes de evento, falso ACK, duplicado real y divergencia de
   contenido. La ejecución de una reparación sobre clientes requiere autorización,
   lista revisada y evidencia antes/después; este bloque entrega herramienta/tests.

**Aceptación:** crash/restart, concurrencia, cursor, replay, tenant cruzado,
falso ACK histórico y fallo de red después del commit remoto. La reparación
repetida no duplica hechos ni borra historial.

## A05 — Maestros offline: identidad, cola y receptor

**Entrada:** A03/A04 y CT-03 para gates efectivos. Entregar en subbloques pequeños.

- **A05.1 Identidad/adopción:** agregar identidad estable a Producto donde falte;
  no confundir el campo existente de Categoría con Producto. SKU inmutable tras
  alta. Adoptar filas locales existentes mediante coincidencia exacta aun cuando
  el payload nuevo ya traiga ID cloud; ambigüedad es conflicto, no un alta duplicada.
- **A05.2 Cola local:** mutación de maestro separada de `EventoSync` financiero;
  UUID idempotente, actor/sucursal, entidad, revisión base, delta y estado.
  Guardar maestro + auditoría + cola en una transacción. Edición offline con
  permisos locales; pendiente utilizable, conflicto visible y bloqueado para
  nuevo uso comercial sin alterar operaciones históricas.
- **A05.3 Receptor cloud:** autenticar sucursal y actor registrado en su ámbito;
  reevaluar RBAC vigente, no confiar en el rol declarado por el cliente. CAS de
  revisión, ledger de replay, orden por entidad y reintento de ACK incierto.
  Una revocación recibida al reconectar puede rechazar una propuesta pendiente;
  conservarla/auditarla, no fingir que se aprobó.
- **A05.4 Publicar CT-04:** schemas y fixtures versionados para listado de
  conflictos, cambios propuestos, motivos, acciones y permisos. C04 comienza
  integración React solo contra este contrato.

**Aceptación:** dos escritores portal/POS, offline con reinicio, repetición,
actor inválido, revocación entre envío/recepción, primera adopción y orden por
entidad; no pérdida de cambios por pull ni duplicación del maestro.

## A06 — Conflictos, maestros y superficies backend/POS

**Entrada:** A05; coordinar CT-04 con C04/C05.

1. API y UI POS de conflictos: conservar ambas propuestas. Resolver «conservar
   cloud» o «aplicar local» con motivo, permiso y revisión actual; si cambió de
   nuevo, producir conflicto otra vez, no sobrescribir silenciosamente.
2. Los hechos financieros pueden sincronizar aunque exista conflicto de maestro.
   Referencias mínimas/stubs no aprueban ni pisan atributos del catálogo;
   conservar UUID común y snapshots históricos de la venta.
3. Estado operativo efectivo producto/categoría, motivo de inactividad, baja
   lógica, reactivación y paginación/filtros. Sync debe transportar inactivos;
   filtrar el endpoint de pull a solo activos rompería la propagación de bajas.
4. Cerrar PRO/CLI vigentes: modelos, forms, API, URLs y templates locales de
   maestros. Claude cubre portal y selectores de ventas/cotizaciones. No impedir
   ver stock o documentos históricos por ocultar un producto operativo.
5. Integrar hooks de imagen/PDF/auditoría pedidos por C02 dentro de sus modelos.
   Los handlers de eventos financieros/quotes en sync los integra Codex usando
   el contrato que entregue C05; Claude no modifica `engine.py`.

**Aceptación:** UI POS + portal + cloud reales; conflicto/resolución concurrente,
inactivos en listados/escaneo/checkout, paginación >200 filas, reactivación sin
resucitar flags individuales y preservación de stock/documentos.

## A07 — Cierre exhaustivo del inventario y documentación

**Entrada:** A02–A06 y entregas C01–C05 progresivas.

1. Revalidar cada fila del inventario. Familias iniciales de A: AUD, USR, NEG,
   PER, TEN, sync, PRO y CLI; no es una whitelist que deje fuera IDs nuevos.
   Claude entrega COM/CFG/SUS y dominios financieros/documentales pendientes.
2. Eliminar discrepancias de estado en fuentes vivas con evidencia y fecha:
   imagen realmente desplegada, tabla real de usuarios, decisiones ya tomadas,
   Redis aplazado, gates nuevos y límites de los ensayos anteriores.
3. Revisar cambios de Claude; resolver hallazgos propios antes de integrar.
   Toda desviación del alcance vuelve al responsable, no se decide unilateralmente.

**Aceptación:** cero hallazgos del alcance sin resolución/evidencia, cero hooks
pendientes entre agentes y ninguna prueba física marcada como realizada por mock.

## A08 — CI, infraestructura y artefacto de release

**Entrada:** A01; cierre con A07 y C06.

1. Pipeline prod con migración obligatoria: fallar si se intenta omitirla cuando
   corresponde; control plane y todos los tenants activos con reporte por BD.
   No actualizar API tras migración parcial ni ocultar el fallo por un health verde.
2. Promover el digest aprobado, sin rebuild que cambie dependencias; manifiesto
   backend/frontend/Windows con SHAs, hashes, migraciones y contratos.
3. CI PostgreSQL multi-BD y protección de orden backend/portal. El frontend
   main autodespliega: no fusionar ambas puntas a la vez.
4. Verificar la interpretación de proxies/X-Forwarded-For contra documentación
   oficial de la plataforma antes de cerrar USR-014; no confiar en cabeceras
   arbitrarias del cliente ni asumir posición sin comprobar el proxy real.
5. Preparar Terraform/variables de prod usando su ambiente, tomando staging como
   referencia de funcionalidades, no copiando sus valores. Job/VAPID/alertas,
   identidad/secretos, min replicas cero y topología actual. `apply` fuera de esta fase.
6. Diseñar y ensayar restauración aislada de control plane + todos los tenants;
   evaluar tiempo, capacidad compartida y consistencia. Coordinar con C06 el
   rollback POS/paquete y la preservación de escrituras posteriores al backup.

**Aceptación:** G1 completo, configuración sin secretos en Git y release
reproducible. Infraestructura preparada no equivale a recursos aplicados en prod.

## A09 — Staging, acta y operación autorizada

**Entrada:** G1. Seguir G2/G3/G4 y runbooks, sin sustituirlos por este resumen.

1. Congelar RC, desplegar staging cuando esté autorizado y ejecutar con Claude
   la matriz integrada y observación. Registrar digest y pruebas por versión.
2. Reabrir cualquier caso diferido que el nuevo gate exige; las aceptaciones de
   riesgo del staging anterior no certifican nuevas correcciones.
3. Redactar acta de pase: hallazgos, pruebas, backups restaurados, rollback,
   compatibilidad, ventanas y responsables. Pedir autorización para producción.
4. Cloud primero, luego RP, un día real de observación, luego SK. Operador único
   por entorno; Claude verifica procedimiento local, portal y operación comercial.
5. Cerrar inventario/estado/handoff con versiones reales y seguimiento de
   notificaciones graduales. No cerrar el gate con tareas obligatorias pendientes.

## Primer mensaje de trabajo sugerido

> Lee AGENTS.md, CLAUDE.md, docs/PLAN_CIERRE_PROD.md y este encargo. Ejecuta A00,
> confirma la base común y publica las dependencias para Claude. Continúa con A01
> y entrega CT-01/CT-02 temprano. Respeta la propiedad de archivos, worktrees/BDs
> aisladas y handoffs por bloque. No despliegues ni modifiques datos operativos.
