# apps/notificaciones — mapa para agentes

<!-- Última revisión: 2026-09-08 -->

> Mapa de orientación, no contrato. Apunta a código; la verdad del *cómo* está
> en los archivos enlazados. Si algo aquí no cuadra con el código, gana el código
> (y corregí este archivo en el mismo PR).

## Qué hace

Bandeja de notificaciones + **Web Push** en el cloud, proyectadas desde los
`EventoSync` de caja (`APERTURA_CAJA`, `CIERRE_CAJA`, `MOVIMIENTO_CAJA`).
Modelos (`models.py`): `MotorNotificaciones` (interruptor por tenant con corte
`activado_desde`), `ReglaNotificacionRol`, `ExcepcionNotificacionUsuario`,
`EventoNotificable`, `EventoSyncNotificacionProcesado` (tombstone),
`DestinatarioNotificacion`, `SuscripcionPush`, `EntregaPush`.

## Entrypoints

| Necesito… | Voy a… |
| --- | --- |
| **El ciclo** (proyectar + enviar + purgar) | `services.ejecutar_ciclo` → `proyectar_pendientes`, `despachar_push`, `purgar_historial_si_corresponde` |
| Qué eventos existen y sus parámetros | `catalogo.py` → `DEFINICIONES`, `regla_aplica`, `nivel_para`, `construir_desde_sync` |
| Enviar un push (adaptador) | `push.py` → `enviar`, `configurado`, `clave_publica` (pywebpush/VAPID encapsulado) |
| API del portal (catálogo, reglas, destinatarios, push config/suscripciones, bandeja) | `apps/api/views/notificaciones.py` |
| Operar por tenant | `manage.py activar_notificaciones`, `procesar_notificaciones` (el job), `verificar_notificaciones`, `desactivar_dispositivos_push` |
| Defaults para un negocio nuevo | `seed.crear_reglas_default` (apertura/cierre para Administrador) |

## Invariantes / trampas

- El motor **nace apagado** y nunca proyecta eventos anteriores a
  `activado_desde`. El tombstone sobrevive a la purga: un `EventoSync` no se
  reproyecta.
- Entregas con *lease* de 5 min y reintentos 1/5/15/60/360 min; retención 90 días.
- Permiso `notificaciones.administrar`. Job y comandos aíslan cada tenant
  (`tenant_context`).
- Runbook: `docs/runbooks/NOTIFICACIONES_WEB_PUSH.md`; promoción a staging:
  `docs/handoffs/STAGING_NOTIFICACIONES_2026-09-07.md`; infra en
  `infra/azure/modules/notifications-monitoring/`.
