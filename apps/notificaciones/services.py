"""Proyeccion durable de eventos y despacho Web Push."""
import logging
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.permisos.models import AsignacionRol
from apps.sync.models import EventoSync
from apps.tenancy.context import get_current_tenant_alias

from . import push
from .catalogo import (
    TIPOS_SYNC_RELEVANTES,
    construir_desde_sync,
    nivel_para,
    regla_aplica,
)
from .models import (
    DestinatarioNotificacion,
    EntregaPush,
    EventoNotificable,
    EventoSyncNotificacionProcesado,
    ExcepcionNotificacionUsuario,
    MotorNotificaciones,
    ReglaNotificacionRol,
    SuscripcionPush,
)


REINTENTOS_MINUTOS = (1, 5, 15, 60, 360)
LEASE_MINUTOS = 5
RETENCION_DIAS = 90
PURGA_LOTE = 1000
logger = logging.getLogger('notificaciones')


def _database_alias():
    """Base donde viven evento, bandeja y entregas del tenant actual."""
    return get_current_tenant_alias() or 'default'


@dataclass
class PreferenciaResuelta:
    usuario_id: int
    nivel: str
    enviar_push: bool


def _asignaciones_en_alcance(sucursal):
    """Asignaciones activas globales o locales, sin cruzar negocio.

    Espejo de `permisos.engine._resolver_permisos`: mismos guards `activo`
    (rol, negocio y sucursal). Si cambian los filtros alli, cambiar aqui —
    de otro modo el motor generaria destinatarios que el RBAC ya no autoriza.
    Una asignacion global sigue recibiendo aunque la sucursal este inactiva;
    la acotada a una sucursal inactiva, no.
    """
    return AsignacionRol.objects.filter(
        activo=True,
        rol__activo=True,
        rol__negocio__activo=True,
        usuario__activo=True,
        usuario__negocio_id=sucursal.negocio_id,
        rol__negocio_id=sucursal.negocio_id,
    ).filter(
        Q(sucursal__isnull=True)
        | Q(sucursal_id=sucursal.id, sucursal__activa=True)
    )


def resolver_destinatarios(*, tipo_evento, datos, sucursal):
    asignaciones = _asignaciones_en_alcance(sucursal)
    roles_por_usuario = {}
    for usuario_id, rol_id in asignaciones.values_list('usuario_id', 'rol_id'):
        roles_por_usuario.setdefault(usuario_id, set()).add(rol_id)

    reglas = ReglaNotificacionRol.objects.filter(
        rol_id__in={rol for roles in roles_por_usuario.values() for rol in roles},
        rol__negocio_id=sucursal.negocio_id,
        tipo_evento=tipo_evento,
        activa=True,
    )
    reglas_por_rol = {regla.rol_id: regla for regla in reglas}
    resultado = {}
    for usuario_id, roles in roles_por_usuario.items():
        aplicables = [
            reglas_por_rol[rol_id] for rol_id in roles
            if rol_id in reglas_por_rol
            and regla_aplica(tipo_evento, reglas_por_rol[rol_id].parametros, datos)
        ]
        if not aplicables:
            continue
        resultado[usuario_id] = PreferenciaResuelta(
            usuario_id=usuario_id,
            nivel=(
                DestinatarioNotificacion.ALERTA
                if any(
                    nivel_para(tipo_evento, regla.parametros, datos)
                    == DestinatarioNotificacion.ALERTA
                    for regla in aplicables
                )
                else DestinatarioNotificacion.NORMAL
            ),
            enviar_push=any(regla.enviar_push for regla in aplicables),
        )

    # Precedencia de excepciones sobre las reglas de rol, siempre dentro del
    # alcance RBAC (el filtro usuario_id__in=roles_por_usuario lo garantiza):
    #   - EXCLUIR: quita al usuario siempre, ignorando parametros.
    #   - INCLUIR: es ADITIVA. Si aplica, gana al rol (impone su nivel/push).
    #     Si no aplica, no hace nada: el usuario conserva lo que su rol le dio.
    #     Un INCLUIR nunca puede quitar una notificacion que el rol ya concedio.
    excepciones = ExcepcionNotificacionUsuario.objects.filter(
        usuario_id__in=roles_por_usuario,
        usuario__negocio_id=sucursal.negocio_id,
        usuario__activo=True,
        tipo_evento=tipo_evento,
    )
    for excepcion in excepciones:
        if excepcion.modo == ExcepcionNotificacionUsuario.EXCLUIR:
            resultado.pop(excepcion.usuario_id, None)
            continue
        if not regla_aplica(tipo_evento, excepcion.parametros, datos):
            continue
        resultado[excepcion.usuario_id] = PreferenciaResuelta(
            usuario_id=excepcion.usuario_id,
            nivel=nivel_para(tipo_evento, excepcion.parametros, datos),
            enviar_push=excepcion.enviar_push,
        )
    return list(resultado.values())


def _clave_fuente(evento_sync):
    return evento_sync.hash_payload or f'id:{evento_sync.pk}'


def _marcar_procesado(evento_sync, *, genero_evento):
    """Cierra el tombstone en PROCESADO (idempotente ante un reintento previo)."""
    EventoSyncNotificacionProcesado.objects.update_or_create(
        evento_sync=evento_sync,
        defaults={
            'genero_evento': genero_evento,
            'estado': EventoSyncNotificacionProcesado.PROCESADO,
            'procesado_en': timezone.now(),
            'proximo_intento_en': None,
            'ultimo_error': '',
        },
    )


def proyectar_evento_sync(evento_sync_id):
    """Convierte un evento confirmado una sola vez, aun bajo concurrencia."""
    with transaction.atomic(using=_database_alias()):
        evento_sync = (
            EventoSync.objects.select_for_update()
            .get(pk=evento_sync_id)
        )
        # Un tombstone PROCESADO o FALLIDO cierra el evento; solo REINTENTO
        # sigue siendo reproyectable.
        if EventoSyncNotificacionProcesado.objects.filter(
            evento_sync=evento_sync,
        ).exclude(
            estado=EventoSyncNotificacionProcesado.REINTENTO,
        ).exists():
            return False

        construido = construir_desde_sync(evento_sync)
        if construido is None or evento_sync.sucursal_id is None:
            _marcar_procesado(evento_sync, genero_evento=False)
            return False

        preferencias = resolver_destinatarios(
            tipo_evento=construido['tipo_evento'],
            datos=construido['datos'],
            sucursal=evento_sync.sucursal,
        )
        genero = bool(preferencias)
        if genero:
            evento = EventoNotificable.objects.create(
                tipo_evento=construido['tipo_evento'],
                fuente='sync',
                clave_fuente=_clave_fuente(evento_sync),
                sucursal=evento_sync.sucursal,
                titulo=construido['titulo'],
                cuerpo=construido['cuerpo'],
                datos=construido['datos'],
                ocurrido_en=construido['ocurrido_en'],
            )
            suscripciones = {}
            usuarios_push = [p.usuario_id for p in preferencias if p.enviar_push]
            for suscripcion in SuscripcionPush.objects.filter(
                usuario_id__in=usuarios_push, activa=True,
            ):
                suscripciones.setdefault(suscripcion.usuario_id, []).append(suscripcion)

            for preferencia in preferencias:
                destinatario = DestinatarioNotificacion.objects.create(
                    evento=evento,
                    usuario_id=preferencia.usuario_id,
                    nivel=preferencia.nivel,
                    push_habilitado=preferencia.enviar_push,
                )
                EntregaPush.objects.bulk_create([
                    EntregaPush(destinatario=destinatario, suscripcion=suscripcion)
                    for suscripcion in suscripciones.get(preferencia.usuario_id, ())
                ])

        _marcar_procesado(evento_sync, genero_evento=genero)
        return genero


def _registrar_fallo_proyeccion(evento_sync_id, exc):
    """Registra un fallo de proyeccion con reintentos acotados (dead-letter).

    Se llama FUERA del atomic de `proyectar_evento_sync` (que hizo rollback),
    por eso persiste por si mismo. Reutiliza la escalera REINTENTOS_MINUTOS
    del push; agotada, deja el evento en FALLIDO y no vuelve a seleccionarlo.
    Nunca propaga: fallar al registrar un fallo no debe frenar el ciclo, y
    solo guarda el nombre de la excepcion, jamas el payload.
    """
    ahora = timezone.now()
    try:
        with transaction.atomic(using=_database_alias()):
            marcador, _ = EventoSyncNotificacionProcesado.objects.get_or_create(
                evento_sync_id=evento_sync_id,
                defaults={'genero_evento': False},
            )
            marcador.intentos += 1
            marcador.ultimo_error = type(exc).__name__[:200]
            marcador.procesado_en = ahora
            if marcador.intentos > len(REINTENTOS_MINUTOS):
                marcador.estado = EventoSyncNotificacionProcesado.FALLIDO
                marcador.proximo_intento_en = None
                logger.error(
                    'Proyeccion de EventoSync id=%s en FALLIDO tras %s intentos (%s).',
                    evento_sync_id, marcador.intentos, type(exc).__name__,
                )
            else:
                marcador.estado = EventoSyncNotificacionProcesado.REINTENTO
                marcador.proximo_intento_en = ahora + timedelta(
                    minutes=REINTENTOS_MINUTOS[marcador.intentos - 1],
                )
                logger.warning(
                    'Proyeccion de EventoSync id=%s fallo (%s); reintento %s programado.',
                    evento_sync_id, type(exc).__name__, marcador.intentos,
                )
            marcador.save(update_fields=[
                'intentos', 'ultimo_error', 'procesado_en', 'estado',
                'proximo_intento_en',
            ])
    except Exception:
        logger.exception(
            'No se pudo registrar el fallo de proyeccion de EventoSync id=%s.',
            evento_sync_id,
        )


def proyectar_pendientes(*, limite=100):
    motor = MotorNotificaciones.actual()
    if not motor.activo or not motor.activado_desde:
        return {'procesados': 0, 'generados': 0, 'errores_proyeccion': 0}
    ahora = timezone.now()
    ids = list(
        EventoSync.objects.filter(
            estado='CONFIRMADO',
            tipo_evento__in=TIPOS_SYNC_RELEVANTES,
            confirmed_at__gte=motor.activado_desde,
        ).filter(
            Q(proyeccion_notificacion__isnull=True)
            | Q(
                proyeccion_notificacion__estado=(
                    EventoSyncNotificacionProcesado.REINTENTO
                ),
                proyeccion_notificacion__proximo_intento_en__lte=ahora,
            )
        ).order_by('confirmed_at', 'id').values_list('id', flat=True)[:limite]
    )
    procesados = generados = errores = 0
    for evento_id in ids:
        try:
            generado = proyectar_evento_sync(evento_id)
        except IntegrityError:
            # Otra instancia gano la carrera protegida tambien por constraints.
            continue
        except Exception as exc:
            # Un payload malformado no debe bloquear los hechos posteriores del
            # tenant: se registra el fallo con reintentos acotados y se sigue.
            errores += 1
            _registrar_fallo_proyeccion(evento_id, exc)
            continue
        procesados += 1
        generados += int(generado)
    return {'procesados': procesados, 'generados': generados, 'errores_proyeccion': errores}


def _recuperar_leases_vencidos(ahora):
    """Devuelve a PENDIENTE las entregas cuyo lease expiro (crash a mitad).

    Es un barrido de ciclo, no de entrega: se ejecuta una sola vez al inicio
    de `despachar_push`, no en cada reclamo.
    """
    EntregaPush.objects.filter(
        estado=EntregaPush.EN_PROCESO,
        lease_hasta__lt=ahora,
    ).update(
        estado=EntregaPush.PENDIENTE,
        lease_hasta=None,
        ultimo_error='Lease vencido; entrega recuperada.',
    )


def _reclamar_entrega():
    ahora = timezone.now()
    with transaction.atomic(using=_database_alias()):
        entrega = (
            EntregaPush.objects.select_for_update(skip_locked=True)
            .select_related('suscripcion', 'destinatario__evento')
            .filter(
                estado=EntregaPush.PENDIENTE,
                proximo_intento_en__lte=ahora,
                suscripcion__activa=True,
            )
            .order_by('proximo_intento_en', 'id')
            .first()
        )
        if entrega is None:
            return None
        entrega.estado = EntregaPush.EN_PROCESO
        entrega.lease_hasta = ahora + timedelta(minutes=LEASE_MINUTOS)
        entrega.save(update_fields=['estado', 'lease_hasta', 'actualizado_en'])
        return entrega


def _registrar_exito(entrega):
    ahora = timezone.now()
    EntregaPush.objects.filter(pk=entrega.pk).update(
        estado=EntregaPush.ENVIADA,
        intentos=entrega.intentos + 1,
        enviada_en=ahora,
        lease_hasta=None,
        ultimo_error='',
    )
    SuscripcionPush.objects.filter(pk=entrega.suscripcion_id).update(
        ultimo_exito_en=ahora,
    )


def _registrar_error(entrega, error):
    ahora = timezone.now()
    intentos = entrega.intentos + 1
    comunes = {
        'intentos': intentos,
        'lease_hasta': None,
        'ultimo_error': str(error)[:500],
    }
    if error.status_code in (404, 410):
        SuscripcionPush.objects.filter(pk=entrega.suscripcion_id).update(activa=False)
        EntregaPush.objects.filter(pk=entrega.pk).update(
            estado=EntregaPush.DESCARTADA, **comunes,
        )
        EntregaPush.objects.filter(
            suscripcion_id=entrega.suscripcion_id,
            estado__in=(EntregaPush.PENDIENTE, EntregaPush.EN_PROCESO),
        ).exclude(pk=entrega.pk).update(
            estado=EntregaPush.DESCARTADA,
            lease_hasta=None,
            ultimo_error='Suscripcion desactivada por HTTP 404/410.',
        )
        return 'descartada'
    if error.reintentable and intentos <= len(REINTENTOS_MINUTOS):
        EntregaPush.objects.filter(pk=entrega.pk).update(
            estado=EntregaPush.PENDIENTE,
            proximo_intento_en=ahora + timedelta(
                minutes=REINTENTOS_MINUTOS[intentos - 1],
            ),
            **comunes,
        )
        return 'reintentada'
    EntregaPush.objects.filter(pk=entrega.pk).update(
        estado=EntregaPush.FALLIDA, **comunes,
    )
    return 'fallida'


def despachar_push(*, limite=100):
    if not push.configurado():
        return {'enviadas': 0, 'reintentadas': 0, 'fallidas': 0, 'descartadas': 0}
    _recuperar_leases_vencidos(timezone.now())
    conteos = {'enviadas': 0, 'reintentadas': 0, 'fallidas': 0, 'descartadas': 0}
    for _ in range(limite):
        entrega = _reclamar_entrega()
        if entrega is None:
            break
        try:
            push.enviar(entrega.suscripcion, entrega.destinatario)
        except push.ErrorEntregaPush as error:
            resultado = _registrar_error(entrega, error)
            conteos[f'{resultado}s'] += 1
        else:
            _registrar_exito(entrega)
            conteos['enviadas'] += 1
    return conteos


def purgar_historial_si_corresponde():
    motor = MotorNotificaciones.actual()
    ahora = timezone.now()
    if motor.ultima_purga and motor.ultima_purga > ahora - timedelta(hours=24):
        return 0
    limite = ahora - timedelta(days=RETENCION_DIAS)
    ids = list(
        EventoNotificable.objects.filter(ocurrido_en__lt=limite)
        .order_by('ocurrido_en', 'id')
        .values_list('id', flat=True)[:PURGA_LOTE]
    )
    _, detalle = EventoNotificable.objects.filter(id__in=ids).delete()
    # Solo cerrar la ventana de 24h cuando el backlog quedo drenado. Un lote
    # lleno deja `ultima_purga` intacta para que el ciclo siguiente continue,
    # en vez de esperar un dia entre lotes de 1000.
    if len(ids) < PURGA_LOTE:
        motor.ultima_purga = ahora
        motor.save(update_fields=['ultima_purga', 'actualizado_en'])
    return detalle.get('notificaciones.EventoNotificable', 0)


def ejecutar_ciclo(*, limite_eventos=100, limite_push=100):
    proyeccion = proyectar_pendientes(limite=limite_eventos)
    entregas = despachar_push(limite=limite_push)
    purgadas = purgar_historial_si_corresponde()
    return {**proyeccion, **entregas, 'purgadas': purgadas}
