# Costes Azure: notificaciones de pruebas y reducción de consumo

Actualización posterior: el recorte fue autorizado y ejecutado el mismo día.
Ambos jobs quedan Manual y sus alertas deshabilitadas; ver el
[acta de ejecución](../handoffs/AHORRO_AZURE_2026-09-28.md). El resto del documento
conserva el análisis previo y sus límites de medición.

Fecha: 2026-09-28. Revisión del código: `staging@8213ba58564b293be094578edd941d0103e540e1`.
Alcance: análisis del Excel, contraste con IaC y consultas de solo lectura a Azure.
No se aplicaron cambios de infraestructura, despliegues ni cambios de datos.

## Resultado

El Excel suma **US$50,415435**. Los jobs de notificaciones de dev y staging,
junto con sus dos alertas, acumulan **US$30,091443: 59,69% del total**.
Ambos siguen programados cada minuto, 24/7, aunque la muestra de logs de las
últimas 24 horas no registra trabajo útil en sus contadores.

El recorte prioritario es retirar la programación permanente de notificaciones
en los dos entornos de pruebas y sus alertas de ausencia de ejecución. Conservar
pruebas bajo demanda; staging programado solamente durante una sesión de QA.

## Fuente y límites

- Archivo: `CostManagement_Azure for Students_2026-09-28-1790602386158.xlsx`.
- SHA-256: `422abbb6a76434372e4f91500e9ffbd6ca92a6c02fa94bafcb28274d77fbb0b2`.
- Hoja Summary: período seleccionado 1–30 septiembre de 2026; generado el 28.
  Es un acumulado parcial de septiembre, sujeto al retraso de facturación;
  no es agosto, ni treinta días completos, ni una previsión de cierre.
- Hoja Data: 50 filas de cargos, 21 recursos, moneda USD. Se suma `CostUSD`
  una sola vez; `Cost` y `CostUSD` contienen el mismo importe en este archivo.
- El incremento comunicado de 327% no se puede verificar con un solo período.
  La consulta histórica agosto/septiembre a Cost Management devolvió HTTP 429
  en dos intentos. Si +327% usa exactamente esta misma base, implica un período
  anterior de US$11,81; es una inferencia aritmética, no una cifra observada.
- Los ahorros siguientes son comparaciones sobre el mismo consumo del Excel,
  no devolución de gastos pasados ni cotización mensual garantizada. Franquicias
  compartidas, fechas de alta, pruebas futuras y duración de ejecuciones pueden
  cambiar el importe efectivo. No extrapolar automáticamente por 30/28.

## Desglose

| Concepto | USD acumulados | Peso aproximado |
| --- | ---: | ---: |
| Job notificaciones dev | 13,15 | 26,08% |
| Job notificaciones staging | 13,06 | 25,90% |
| Alertas de esos dos jobs | 3,88 | 7,70% |
| API producción | 11,55 | 22,91% |
| ACR compartido Basic | 4,55 | 9,03% |
| API staging | 2,13 | 4,22% |
| API dev | 1,97 | 3,91% |
| Key Vault, migraciones y Storage | 0,12 | 0,25% |
| **Total** | **50,42** | **100%** |

El [CSV de costes](COSTES_AZURE_2026-09-28.csv) conserva los importes por recurso sin el redondeo de esta tabla.
Los dos jobs cuestan US$26,207438; sus alertas US$3,884005.
PostgreSQL y Log Analytics figuran con US$0 en este corte. PostgreSQL tiene
medidores `B1MS Compute - Free` y `Storage Data Stored - Free`; este dato no
acredita gratuidad indefinida ni fecha de vencimiento de beneficios.

## Contraste con IaC y Azure real

| Componente | Código | Azure consultado el 28 de septiembre |
| --- | --- | --- |
| Jobs de notificaciones | `modules/container-apps/main.tf:491–550`: recurso opcional, cron, 0,5 CPU/1 GiB fijos | Dev y staging: `Schedule`, `*/1 * * * *`, 0,5 CPU/1 GiB, timeout 300 s, un reintento |
| Alertas | `modules/notifications-monitoring/main.tf:30–60`: `PT1M`, ventana `PT5M`, habilitadas | Dos reglas habilitadas, misma frecuencia y ventana |
| APIs | `modules/container-apps/main.tf:168–175`: mínimos/máximos parametrizados | Dev, staging y prod: mínimo omitido/null (escala a cero), máximo 1, 0,5 CPU/1 GiB |
| Runtime y registry | Staging permite reutilizar recursos existentes | Dev/staging comparten ACA Environment; solo hay un ACR, `posfifodevacr` |
| Notificaciones prod | Recurso opcional | No existe un job de notificaciones de producción en el inventario consultado |
| Presupuestos | No se encontraron recursos de presupuesto en `infra/azure` | El listado de presupuestos a nivel de suscripción devuelve `[]`; no acredita ámbitos distintos |

Las rutas de la tabla son relativas a `infra/azure/`.

La duplicación costosa son **las ejecuciones**, no dos registries ni dos servidores
dedicados para notificaciones. La configuración permite 2.880 arranques diarios,
86.400 por cada 30 días entre ambos jobs, aunque no haya eventos.
En ocho ejecuciones recientes por job, los tiempos inicio/fin fueron 28–32 segundos.
No confundir esa duración de ejecución con una medición exacta de segundos facturados.

El comando `apps/notificaciones/management/commands/procesar_notificaciones.py`
inicializa Django, enumera tenants activos y ejecuta un ciclo por tenant.
El cron no consulta antes si hay eventos ni si hay una sesión de QA en curso.
Apagar el motor por tenant o Web Push no elimina los arranques de Azure.

Los defaults versionados `enable_notifications_job=false` y
`enable_notifications_alerts=false` no garantizan que el entorno desplegado esté
apagado. El tfvars local de staging no declara esos flags aunque Azure los tiene
activos; el de dev declara el job activo. Hay diferencias entre inputs locales y
runtime que deben reconciliarse con el state y los inputs operativos antes de un
apply. No se generó un plan Terraform en este análisis.

## Evidencia de uso

Consulta agregada de Log Analytics, ventana fija
`2026-09-27T13:39:00Z` a `2026-09-28T13:39:00Z`:

| Job | Resúmenes por tenant observados | Procesados | Generados | Enviadas | Reintentadas | Fallidas | Errores proyección | Purgadas |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Dev | 1.433 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Staging | 2.870 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

Son resúmenes por tenant, no un conteo de ejecuciones: un ciclo puede producir
varios. La muestra no prueba que todo septiembre estuviera vacío. Los resultados
agregados y la consulta están en el [JSON de evidencia](EVIDENCIA_COSTES_AZURE_2026-09-28.json); no se exportaron payloads,
suscripciones push ni secretos.

**La API de producción sí escaló a cero durante la madrugada examinada.** Azure
Monitor devolvió cero peticiones (`Requests/Total`) y cero réplicas
(`Replicas/Average`) en las seis franjas horarias de 04:00–10:00 UTC del 28,
equivalentes a 00:00–06:00 en Santo Domingo. Los logs consultados también estaban
vacíos. No atribuir a producción consumo nocturno permanente sobre esta evidencia.

El sync local tiene un intervalo predeterminado de 60 s
(`apps/sync/management/commands/sincronizar.py:98`). Puede mantener una API activa
si los POS siguen consultando; es un factor para futuras mediciones, no la causa
demostrada de esta madrugada. Con cooldown de 300 s, espaciar llamadas exactamente
a cinco minutos tampoco garantiza un tiempo significativo con cero réplicas.

## Medidas por prioridad

1. **Cesar programación permanente y alertas de notificaciones en dev/staging.**
   Ahorro directamente asociado al período: US$30,09 (59,69%); resto US$20,32.
   La ruta ya soportada es `enable_notifications_alerts=false` y después
   `enable_notifications_job=false`, siguiendo el runbook. Ese flag elimina el
   job, su identidad y sus asignaciones mediante `count=0`: no es un botón de
   pausa. Revisar el plan para que no alcance APIs, PostgreSQL, secretos ni media.
   Las bandejas existentes permanecen; nuevos avisos/envíos y purgas quedan
   pendientes mientras no corra el procesador.
2. **Añadir modo manual como política habitual de pruebas.** Separar en IaC
   existencia del job y tipo de disparador (`Manual`/`Schedule`), preservando
   identidad, VAPID e imagen. Hoy el módulo de notificaciones solo implementa
   disparador programado. Dev manual; staging manual salvo ventana de prueba
   explícita, con vuelta automática a manual al terminar. El CI ya contempla
   ausencia del job y ejecuta un ciclo explícito cuando existe; mantener esa
   verificación al incorporar el modo manual.
3. **Si hace falta cron de QA, usar solo staging y limitar horario/frecuencia.**
   Ejemplo propuesto, no aplicado: `*/5 13-20 * * 1-5`, lunes a viernes de
   09:00 a 16:55 en Santo Domingo. Son 480 ejecuciones semanales frente a las
   20.160 actuales entre ambos: 97,62% menos arranques. Cambia la latencia de
   prueba y deja avisos en espera fuera de horario. La alerta actual de cinco
   minutos dejaría de tener sentido: retirarla o adaptarla al horario y demora,
   no limitarse a silenciar su correo.
4. **Revisar recursos del contenedor después de reducir ejecuciones.** Parametrizar
   CPU/memoria de notificaciones y probar 0,25 CPU/0,5 GiB con una muestra real de
   tenants, backlog, arranque, memoria y duración. Reducir CPU puede alargar el
   ciclo; no prometer 50% de ahorro. Separar después la imagen del procesador si
   medir importaciones/arranque demuestra beneficio.
5. **APIs de pruebas bajo demanda.** Ya permiten escala a cero; detener rigs,
   polling del portal y health externos innecesarios cuando no se prueba.
   Medir peticiones/réplicas por hora antes de cambiar el escalado. Eliminar todo
   su consumo actual tiene un techo adicional de US$4,10 en este período;
   el coste equivalente restante sería US$16,23, pero las pruebas futuras cuestan.
6. **Presupuesto y cierre de QA.** Proponer inicialmente US$25/mes de suscripción
   y US$5/mes para cómputo/monitorización exclusivamente de pruebas, revisables
   tras siete días. Alertas al 50/80/100% y previsión; no cargar todo el ACR
   compartido a pruebas solo porque está en `posfifo-dev-rg`. Un presupuesto avisa,
   no limita ni apaga recursos. Cada activación de QA debe tener responsable,
   vencimiento y un mecanismo de desactivación comprobado.

Apagar solo ocho horas nocturnas conservaría dos tercios de los arranques:
ahorro proporcional orientativo de US$8,74 de cómputo; las alertas seguirían
costando si no se deshabilitan. Es una mejora menor que operar bajo demanda.

El ACR cuesta US$4,55 por `Basic Registry Unit`: borrar tags no elimina esa
cuota base. Ya está compartido; no es el primer candidato a migración.
Reducir PostgreSQL o retención de logs no ofrece ahorro facturado en este corte.
Los workspaces tienen 30 días de retención y sin límite diario configurado;
revisar volumen/alerta preventiva antes de imponer un límite que suprima diagnóstico.

A mayor escala, evaluar un consumidor activado por cola conservando outbox,
reintentos, aislamiento tenant y recuperación. No sustituir entrega durable por
un envío directo dentro de la petición HTTP, ni añadir ahora una cola solo para
resolver el desperdicio de QA que ya se puede evitar con modo manual.

## Verificación posterior a una implementación

- Validar el plan por entorno y la configuración efectiva tras aplicar; reconciliar
  cualquier cambio transitorio con IaC para que el siguiente apply no lo revierta.
- Comprobar que dejan de aparecer nuevas ejecuciones fuera de las ventanas, que
  las alertas quedan deshabilitadas y que un ciclo manual de staging sí funciona.
- Repetir medición durante 48–72 horas y comparar coste diario, ejecuciones y trabajo
  procesado. El histórico ya incurrido no desaparece al apagar un recurso.
- Obtener agosto y septiembre con la misma moneda, ámbito, filtros y días para
  separar incremento real, altas de recursos y consumo de franquicias gratuitas.

## Referencias

- [Runbook de notificaciones](../runbooks/NOTIFICACIONES_WEB_PUSH.md): cron de la V1,
  deshabilitar alerta antes del job y conservación de bandeja.
- [Facturación Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/billing):
  se cobran recursos asignados durante ejecuciones; terminar sin eventos no evita
  el coste de arrancar. El Excel incluye US$0,294804 etiquetados como Idle en los
  dos jobs, aunque la documentación describe jobs como Active: conservar esos
  importes y aclarar el medidor en un export de uso si se audita facturación exacta.
- [Precios Container Apps](https://azure.microsoft.com/en-us/pricing/details/container-apps/):
  la franquicia mensual se comparte por suscripción, no se multiplica por ambiente.
- [Jobs y cron UTC](https://learn.microsoft.com/en-us/azure/container-apps/jobs):
  disparadores manuales, programados y por eventos.
- [Precios Azure Monitor](https://azure.microsoft.com/en-us/pricing/details/monitor/):
  reglas de logs cobradas por frecuencia; reglas deshabilitadas sin cargo de regla.
- [Presupuestos](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets):
  notificaciones de coste y previsión; no interrumpen consumo por sí solas.
