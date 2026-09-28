# Recorte de costes Azure ejecutado — 2026-09-28

Autorización: ejecutar el plan de reducción y dejar las decisiones de producto,
horarios y presupuesto para el final. Base: `staging@8213ba58564b293be094578edd941d0103e540e1`.
Trabajo aislado en `C:/Proyectos/pos_fifo_system_cost_controls`, rama
`codex/azure-cost-controls-20260928`; conserva cambios concurrentes del checkout principal.

## Resultado aplicado

| Recurso | Antes | Después |
| --- | --- | --- |
| `posfifo-dev-notifications` | Schedule, cada minuto 24/7 | Manual |
| `posfifo-staging-notifications` | Schedule, cada minuto 24/7 | Manual |
| `posfifo-dev-notifications-job-missed` | enabled=true, evaluación cada minuto | enabled=false |
| `posfifo-staging-notifications-job-missed` | enabled=true, evaluación cada minuto | enabled=false |

Los Action Groups y los jobs siguen existiendo. Se preservaron exactamente
plantillas de contenedor, imágenes por digest, identidades, referencias a
secretos, registries y ACA Environment. No hubo modificación remota de APIs,
PostgreSQL, media, producción, datos POS ni código desplegado.

Dev pasó a manual antes de 13:50:12 UTC y staging antes de 13:50:48 UTC
(09:50 hora de Santo Domingo). Los últimos arranques programados observados
fueron a las 13:50:00 UTC; se dejó finalizar esa ejecución en curso.

Smoke manual staging: `posfifo-staging-notifications-nuezy1h`,
13:53:37–13:54:08 UTC, **Succeeded**. El único arranque observado después del
cambio fue ese smoke explícito. La evidencia con la ventana final de observación
está en el [JSON de ejecución](../exploracion/EJECUCION_AHORRO_AZURE_2026-09-28.json).

## IaC y estado remoto

- `notifications_trigger_type`: acepta `Manual` y `Schedule`; dev/staging
  predeterminan Manual. Prod y el módulo reutilizable conservan Schedule como
  default compatible; no se hizo plan/apply remoto de prod.
- Bloques dinámicos excluyentes para ambos tipos. Existencia del recurso sigue
  gobernada por `enable_notifications_job`, independiente del disparador.
- La alerta de ausencia de ejecución se deshabilita automáticamente con Manual;
  `enable_notifications_alerts=true` conserva la regla y su Action Group.
- Se corrigió el acceso a un data source inexistente al deshabilitar alertas con
  un Environment compartido. Se normalizó KQL a LF para que Windows no produzca
  drift de alertas solo por CRLF.
- Se actualizaron ejemplos y [runbook](../runbooks/NOTIFICACIONES_WEB_PUSH.md).

El primer plan completo propuso reemplazar solamente el job y actualizar la
alerta en cada entorno. El proveedor azurerm 4.75/4.76 fuerza recreación cuando
cambia el tipo de disparador; además habría usado la etiqueta mutable del tfvars
en vez de la imagen por digest que dejó CI. **No se aplicó ese plan.**

Se usó el PATCH de Azure con alcance exacto: primero `enabled=false` en la regla,
después `triggerType=Manual`, `scheduleTriggerConfig=null` y bloque manual de una
réplica en el job. Se verificaron las invariantes con GET usando la misma versión
REST antes/después. La IaC ya describía ese estado deseado.

Los nuevos planes completos mostraron **cero cambios de recursos**. Se aplicaron
esos planes guardados para persistir la actualización del state: **0 added,
0 changed, 0 destroyed** en cada entorno. Una segunda ronda de planes completos
terminó con **detailed exit code 0 / No changes** en dev y staging.
State remoto: `azure/dev.tfstate` serial **101**, `azure/staging.tfstate` serial **21**.

Los tfvars operativos reconciliados viven en el checkout aislado y siguen
ignorados por Git. Los state, planes y snapshots completos están en su `.terraform/`;
no publicar esos archivos. Usar esta revisión y estos inputs para operar; otro
checkout anterior todavía puede proponer devolver los jobs a Schedule.
Los cambios de código quedan en la rama indicada; no se hizo push ni merge a
develop/staging. La ejecución real de Azure y su reconciliación sí están completas.

## Validación

- `terraform fmt` y `git diff --check`.
- `terraform validate` en dev, staging y prod (prod con `init -backend=false`).
- Dos planes completos posteriores al PATCH por entorno; el final sin cambios.
- GET de ambos jobs: Manual / Succeeded; identidad, imagen y configuración
  preservadas. GET de las dos reglas: enabled=false.
- Smoke real manual de staging: Succeeded.
- Observación de ejecuciones posteriores: sin nuevos ciclos de cron; solo smoke.

No se ejecutaron suites Django: no se modificó código de aplicación ni contratos
de API. La verificación relevante fue la configuración efectiva, los planes y
una ejecución real del procesador.

## Impacto y seguimiento

El Excel atribuía **US$30,091443 (59,69%)** a estos cuatro recursos en el corte de
septiembre. Se ha eliminado la programación que originaba ese gasto recurrente.
Los ciclos manuales y el smoke de CI siguen consumiendo mientras se ejecutan.
No es una devolución de US$30,09 ni un ahorro mensual ya medido.

El coste equivalente restante del corte es US$20,32, antes de considerar cambios
de uso y distribución de la franquicia compartida. Confirmar la reducción con
Cost Management después de 48–72 horas de facturación. Esa medición futura no se
ha ejecutado ni se ha programado una automatización de seguimiento.

Las notificaciones nuevas de pruebas esperan un ciclo manual. Los cron se
retiraron, no se desactivaron motores de tenants ni se borraron bandejas.
Las APIs siguen disponibles y pueden escalar a cero según su configuración previa.

## Decisiones reservadas al responsable

| Decisión | Ventaja | Coste o inconveniente | Recomendación inicial |
| --- | --- | --- | --- |
| Mantener QA manual o abrir ventanas programadas | Manual evita gasto por olvido; cron facilita pruebas continuas | Manual requiere iniciar el ciclo; cron exige vencimiento y política de alertas | Mantener manual mientras no haya QA activo |
| Tope orientativo de presupuesto mensual: US$25 total y US$5 de cómputo/monitorización de pruebas | Detecta regresiones con avisos tempranos | Son umbrales propuestos, requieren destinatario/alcance; el presupuesto no corta consumo | Elegir importe y receptor antes de crear alertas |
| 0,25 CPU/0,5 GiB frente a 0,5 CPU/1 GiB | Puede reducir coste por segundo | Puede aumentar duración, cold start u OOM; ahorro casi nulo si el job está parado | Mantener tamaño y medir cuando se reactive QA |
| Ventana nocturna o backoff adaptativo para sync/APIs | Puede reducir peticiones durante inactividad | Puede retrasar maestros, cierres y recuperación; exige medición por tienda | No cambiar ahora: prod ya mostró cero réplicas de madrugada |

Ni horarios ni cuantías fueron elegidos en nombre del usuario. No se creó un
presupuesto ni se enviaron correos de prueba. Se conserva ACR Basic compartido;
PostgreSQL y Log Analytics no tenían cargos en el corte, por lo que no se
redujeron capacidad o retención para obtener un ahorro no demostrado.
