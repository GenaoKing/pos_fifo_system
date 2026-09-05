from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.negocios.utils import negocio_actual
from apps.notificaciones import push
from apps.notificaciones.catalogo import DEFINICIONES, catalogo_publico, normalizar_parametros
from apps.notificaciones.models import (
    DestinatarioNotificacion,
    EntregaPush,
    ExcepcionNotificacionUsuario,
    ReglaNotificacionRol,
    SuscripcionPush,
)
from apps.permisos.models import AsignacionRol, Rol

from ..pagination import NotificacionesPagination
from ..permissions import requiere_permiso
from ..serializers.notificaciones import (
    NotificacionSerializer,
    ReglaNotificacionEntradaSerializer,
    SuscripcionPushSerializer,
)

Usuario = get_user_model()
ADMIN_NOTIFICACIONES = [IsAuthenticated, requiere_permiso('notificaciones.administrar')]


class CatalogoNotificacionesView(APIView):
    permission_classes = ADMIN_NOTIFICACIONES

    def get(self, request):
        return Response(catalogo_publico())


class DestinatariosNotificacionesView(APIView):
    """Selectores administrativos sin depender de `permisos.administrar`."""
    permission_classes = ADMIN_NOTIFICACIONES

    def get(self, request):
        negocio = negocio_actual(request)
        if negocio is None:
            raise ValidationError('No se pudo determinar el negocio del request.')
        roles = Rol.objects.filter(negocio=negocio).order_by('nombre')
        usuarios = Usuario.objects.filter(negocio=negocio, activo=True).order_by('username')
        con_push = set(
            SuscripcionPush.objects.filter(
                usuario__negocio=negocio, activa=True,
            ).values_list('usuario_id', flat=True)
        )
        return Response({
            'roles': [
                {'id': rol.id, 'nombre': rol.nombre, 'activo': rol.activo}
                for rol in roles
            ],
            'usuarios': [
                {
                    'id': usuario.id,
                    'nombre': usuario.get_full_name() or usuario.username,
                    'username': usuario.username,
                    'push_activo': usuario.id in con_push,
                }
                for usuario in usuarios
            ],
        })


def _conteos_push(reglas, excepciones):
    """Cuenta destinatarios con push en dos queries, sin N+1 por regla.

    Devuelve (push_por_rol, usuarios_con_push): cuantos usuarios activos y
    asignados a cada rol tienen al menos un dispositivo push activo, y el set
    de usuarios de excepcion que tienen uno.
    """
    rol_ids = [obj.rol_id for obj in reglas]
    usuario_ids = [obj.usuario_id for obj in excepciones]
    push_por_rol = {}
    if rol_ids:
        push_por_rol = {
            fila['rol_id']: fila['n']
            for fila in AsignacionRol.objects.filter(
                activo=True,
                rol_id__in=rol_ids,
                usuario__activo=True,
                usuario__suscripciones_push__activa=True,
            )
            .values('rol_id')
            .annotate(n=Count('usuario_id', distinct=True))
        }
    usuarios_con_push = set()
    if usuario_ids:
        usuarios_con_push = set(
            SuscripcionPush.objects.filter(
                activa=True, usuario_id__in=usuario_ids,
            ).values_list('usuario_id', flat=True)
        )
    return push_por_rol, usuarios_con_push


def _conteos_para(obj):
    """Conteos de push para un solo objeto (POST/PATCH/detalle)."""
    if isinstance(obj, ReglaNotificacionRol):
        return _conteos_push([obj], [])
    return _conteos_push([], [obj])


def _regla_salida(obj, conteos):
    push_por_rol, usuarios_con_push = conteos
    if isinstance(obj, ReglaNotificacionRol):
        return {
            'id': f'rol-{obj.pk}',
            'destinatario_tipo': 'ROL',
            'rol': obj.rol_id,
            'rol_nombre': obj.rol.nombre,
            'usuario': None,
            'usuario_nombre': '',
            'tipo_evento': obj.tipo_evento,
            'activa': obj.activa,
            'modo': None,
            'enviar_push': obj.enviar_push,
            'parametros': normalizar_parametros(obj.tipo_evento, obj.parametros),
            'usuarios_push_activo': push_por_rol.get(obj.rol_id, 0),
        }
    return {
        'id': f'usuario-{obj.pk}',
        'destinatario_tipo': 'USUARIO',
        'rol': None,
        'rol_nombre': '',
        'usuario': obj.usuario_id,
        'usuario_nombre': obj.usuario.get_full_name() or obj.usuario.username,
        'tipo_evento': obj.tipo_evento,
        'activa': True,
        'modo': obj.modo,
        'enviar_push': obj.enviar_push,
        'parametros': normalizar_parametros(obj.tipo_evento, obj.parametros),
        'usuarios_push_activo': int(obj.usuario_id in usuarios_con_push),
    }


class ReglasNotificacionesView(APIView):
    permission_classes = ADMIN_NOTIFICACIONES

    def _negocio(self, request):
        negocio = negocio_actual(request)
        if negocio is None:
            raise ValidationError('No se pudo determinar el negocio del request.')
        return negocio

    def get(self, request, pk=None):
        negocio = self._negocio(request)
        if pk:
            obj = self._resolver(pk, negocio)
            return Response(_regla_salida(obj, _conteos_para(obj)))
        reglas = list(
            ReglaNotificacionRol.objects.filter(rol__negocio=negocio)
            .select_related('rol')
        )
        excepciones = list(
            ExcepcionNotificacionUsuario.objects.filter(usuario__negocio=negocio)
            .select_related('usuario')
        )
        conteos = _conteos_push(reglas, excepciones)
        return Response([
            _regla_salida(obj, conteos) for obj in [*reglas, *excepciones]
        ])

    def post(self, request, pk=None):
        if pk:
            raise ValidationError('Usa PATCH para modificar una regla existente.')
        negocio = self._negocio(request)
        entrada = ReglaNotificacionEntradaSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        obj, creada = self._guardar(entrada.validated_data, negocio)
        return Response(
            _regla_salida(obj, _conteos_para(obj)),
            status=status.HTTP_201_CREATED if creada else status.HTTP_200_OK,
        )

    def patch(self, request, pk=None):
        negocio = self._negocio(request)
        actual = self._resolver(pk, negocio)
        # Solo se leen campos no-push de `salida`, asi que no vale una query
        # de conteo aqui: se calcula sobre el objeto ya guardado, al responder.
        salida = _regla_salida(actual, ({}, set()))
        base = {
            'destinatario_tipo': salida['destinatario_tipo'],
            'tipo_evento': salida['tipo_evento'],
            'enviar_push': salida['enviar_push'],
            'parametros': salida['parametros'],
        }
        if salida['destinatario_tipo'] == 'ROL':
            base.update({'rol': salida['rol'], 'activa': salida['activa']})
        else:
            base.update({'usuario': salida['usuario'], 'modo': salida['modo']})
        entrada = ReglaNotificacionEntradaSerializer(
            data={**base, **request.data}, partial=False,
        )
        entrada.is_valid(raise_exception=True)
        obj, _ = self._guardar(entrada.validated_data, negocio, actual=actual)
        return Response(_regla_salida(obj, _conteos_para(obj)))

    def delete(self, request, pk=None):
        negocio = self._negocio(request)
        self._resolver(pk, negocio).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _resolver(self, pk, negocio):
        try:
            prefijo, valor = str(pk).split('-', 1)
            object_id = int(valor)
        except (AttributeError, TypeError, ValueError):
            raise NotFound('Regla no encontrada.')
        if prefijo == 'rol':
            obj = ReglaNotificacionRol.objects.filter(
                pk=object_id, rol__negocio=negocio,
            ).select_related('rol').first()
        elif prefijo == 'usuario':
            obj = ExcepcionNotificacionUsuario.objects.filter(
                pk=object_id, usuario__negocio=negocio,
            ).select_related('usuario').first()
        else:
            obj = None
        if obj is None:
            raise NotFound('Regla no encontrada.')
        return obj

    def _guardar(self, datos, negocio, actual=None):
        tipo_evento = datos['tipo_evento']
        if tipo_evento not in DEFINICIONES:
            raise ValidationError({'tipo_evento': 'Tipo de evento desconocido.'})
        try:
            parametros = normalizar_parametros(tipo_evento, datos.get('parametros'))
        except ValueError as exc:
            raise ValidationError({'parametros': str(exc)}) from exc

        if datos['destinatario_tipo'] == 'ROL':
            rol = Rol.objects.filter(pk=datos['rol'], negocio=negocio).first()
            if rol is None:
                raise ValidationError({'rol': 'El rol no pertenece a tu negocio.'})
            if actual is not None and not isinstance(actual, ReglaNotificacionRol):
                actual.delete()
                actual = None
            obj, creada = ReglaNotificacionRol.objects.update_or_create(
                rol=rol,
                tipo_evento=tipo_evento,
                defaults={
                    'activa': datos.get('activa', True),
                    'enviar_push': datos.get('enviar_push', True),
                    'parametros': parametros,
                },
            )
        else:
            usuario = Usuario.objects.filter(
                pk=datos['usuario'], negocio=negocio, activo=True,
            ).first()
            if usuario is None:
                raise ValidationError({'usuario': 'El usuario no pertenece a tu negocio.'})
            if actual is not None and not isinstance(actual, ExcepcionNotificacionUsuario):
                actual.delete()
                actual = None
            obj, creada = ExcepcionNotificacionUsuario.objects.update_or_create(
                usuario=usuario,
                tipo_evento=tipo_evento,
                defaults={
                    'modo': datos.get('modo', ExcepcionNotificacionUsuario.INCLUIR),
                    'enviar_push': datos.get('enviar_push', True),
                    'parametros': parametros,
                },
            )
        if actual is not None and actual.pk != obj.pk:
            actual.delete()
        return obj, creada


class NotificacionViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = NotificacionSerializer
    pagination_class = NotificacionesPagination

    def get_queryset(self):
        qs = (
            DestinatarioNotificacion.objects.filter(usuario=self.request.user)
            .select_related('evento__sucursal')
        )
        estado = self.request.query_params.get('estado')
        if estado == 'leida':
            qs = qs.filter(leida_en__isnull=False)
        elif estado == 'no_leida':
            qs = qs.filter(leida_en__isnull=True)
        tipo = self.request.query_params.get('tipo')
        if tipo:
            qs = qs.filter(evento__tipo_evento=tipo)
        sucursal = self.request.query_params.get('sucursal')
        if sucursal:
            try:
                sucursal_id = int(sucursal)
            except (TypeError, ValueError) as exc:
                raise ValidationError({'sucursal': 'Debe ser un id numerico.'}) from exc
            qs = qs.filter(evento__sucursal_id=sucursal_id)
        return qs.order_by('-evento__ocurrido_en', '-id')

    @action(detail=True, methods=['post'], url_path='marcar-leida')
    def marcar_leida(self, request, pk=None):
        notificacion = self.get_object()
        notificacion.marcar_leida()
        return Response(self.get_serializer(notificacion).data)

    @action(detail=False, methods=['post'], url_path='marcar-todas-leidas')
    def marcar_todas_leidas(self, request):
        actualizadas = self.get_queryset().filter(leida_en__isnull=True).update(
            leida_en=timezone.now(),
        )
        return Response({'actualizadas': actualizadas})

    @action(detail=False, methods=['get'])
    def resumen(self, request):
        qs = self.get_queryset()
        ultimas = qs[:5]
        return Response({
            'no_leidas': qs.filter(leida_en__isnull=True).count(),
            'ultimas': self.get_serializer(ultimas, many=True).data,
        })


class PushConfigView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({
            'habilitado': push.habilitado_cliente(),
            'clave_publica': push.clave_publica(),
        })


class SuscripcionPushViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = SuscripcionPushSerializer
    pagination_class = None
    http_method_names = ['get', 'post', 'delete', 'head', 'options']

    def get_queryset(self):
        return SuscripcionPush.objects.filter(usuario=self.request.user)

    def _registrar_suscripcion(self, usuario, endpoint, defaults):
        """Alta, re-alta o transferencia de un endpoint bajo lock.

        El endpoint de push es estable por navegador + applicationServerKey,
        no por usuario Django: pertenece al navegador. Si otra cuenta lo
        registro antes en el mismo equipo, el dispositivo cambia de dueno en
        lugar de rechazarse. Se descarta su cola pendiente para que el nuevo
        dueno no reciba avisos del anterior.
        """
        obj = (
            SuscripcionPush.objects.select_for_update()
            .filter(endpoint=endpoint)
            .first()
        )
        if obj is None:
            obj = SuscripcionPush.objects.create(
                usuario=usuario, endpoint=endpoint, **defaults,
            )
            return obj, True, False
        transferida = obj.usuario_id != usuario.id
        if transferida:
            EntregaPush.objects.filter(
                suscripcion=obj,
                estado__in=(EntregaPush.PENDIENTE, EntregaPush.EN_PROCESO),
            ).update(
                estado=EntregaPush.DESCARTADA,
                lease_hasta=None,
                ultimo_error='Dispositivo reasignado a otro usuario.',
            )
            obj.usuario = usuario
            obj.ultimo_exito_en = None
        for campo, valor in defaults.items():
            setattr(obj, campo, valor)
        obj.save()
        return obj, False, transferida

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        endpoint = serializer.validated_data['endpoint']
        keys = serializer.validated_data['keys']
        defaults = {
            'p256dh': keys['p256dh'],
            'auth': keys['auth'],
            'nombre_dispositivo': serializer.validated_data.get(
                'nombre_dispositivo', '',
            ),
            'user_agent': request.headers.get('User-Agent', '')[:300],
            'activa': True,
        }
        try:
            with transaction.atomic():
                obj, creada, transferida = self._registrar_suscripcion(
                    request.user, endpoint, defaults,
                )
        except IntegrityError:
            # Perdio una carrera de alta simultanea del mismo endpoint: la
            # fila ya existe y ahora converge sobre ella bajo lock.
            with transaction.atomic():
                obj, creada, transferida = self._registrar_suscripcion(
                    request.user, endpoint, defaults,
                )
        return Response(
            self.get_serializer(obj).data,
            status=(
                status.HTTP_201_CREATED
                if creada or transferida
                else status.HTTP_200_OK
            ),
        )

    def perform_destroy(self, instance):
        instance.activa = False
        instance.save(update_fields=['activa', 'actualizado_en'])
