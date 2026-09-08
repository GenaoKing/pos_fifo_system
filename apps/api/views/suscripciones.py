"""
apps/api/views/suscripciones.py
Endpoints de administración de suscripciones/módulos para el operador del SaaS.

Gated por el permiso `suscripciones.administrar` (lo tiene el sysadmin/admin vía
acceso total). Permite consultar el catálogo de módulos/planes y asignar plan +
overrides à la carte a cada negocio, respetando `puede_desactivarse`.

    GET    /api/v1/suscripciones/modulos/      catálogo de módulos (RO)
    GET    /api/v1/suscripciones/planes/       planes y sus módulos (RO)
    GET    /api/v1/suscripciones/negocios/     suscripciones por negocio
    PATCH  /api/v1/suscripciones/negocios/<id>/  cambiar plan / activa
    GET/POST/PATCH/DELETE /api/v1/suscripciones/overrides/  à la carte
"""
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from apps.suscripciones.engine import puede_desactivarse
from apps.suscripciones.models import Modulo, NegocioModulo, Plan, SuscripcionNegocio

from ..permissions import requiere_permiso
from ..serializers.suscripciones import (
    ModuloSerializer,
    NegocioModuloSerializer,
    PlanSerializer,
    SuscripcionNegocioSerializer,
)

ADMIN_SUSCRIP = [IsAuthenticated, requiere_permiso('suscripciones.administrar')]


class ModuloViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = ADMIN_SUSCRIP
    serializer_class = ModuloSerializer
    pagination_class = None
    queryset = Modulo.objects.all()


class PlanViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = ADMIN_SUSCRIP
    serializer_class = PlanSerializer
    pagination_class = None
    queryset = Plan.objects.prefetch_related('modulos').all()


# ---------------------------------------------------------------------------
# Guard de degradacion (SUS-004)
# ---------------------------------------------------------------------------
#
# `_validar()` solo actuaba si `validated_data` traia SIMULTANEAMENTE
# `incluido=False`, `modulo` y `negocio`. Un PATCH parcial de solo `incluido`
# retornaba sin validar; `destroy` no estaba sobrescrito; y cambiar el plan o
# `activa` usaba el `ModelViewSet` sin calcular que modulos se retiraban. Es
# decir: el guard existia y las tres rutas oficiales del operador lo esquivaban.
#
# La correccion no es agregar tres validaciones sino UNA: comparar el set
# efectivo antes y despues, y validar cada modulo que desaparece. Asi da igual
# por donde llegue el cambio.


def _validar_degradacion(negocio, antes, despues):
    """Bloquea si alguna transicion retira un modulo que no puede irse."""
    retirados = antes - despues
    for key in sorted(retirados):
        ok, motivo = puede_desactivarse(negocio, key)
        if not ok:
            raise ValidationError({'modulo': motivo})


class GuardDegradacionMixin:
    """
    Envuelve create/update/destroy comparando el entitlement efectivo.

    El calculo se hace DENTRO de una transaccion y la escritura se revierte si
    el guard rechaza: sin eso, la comprobacion pasaria sobre datos que ya
    cambiaron.
    """

    def _negocio_de(self, instance):
        raise NotImplementedError

    def _aplicar(self, guardar, negocio):
        from django.db import transaction

        from apps.suscripciones.engine import invalidar_cache, modulos_negocio

        with transaction.atomic():
            antes = modulos_negocio(negocio)
            guardar()
            # El cache se invalida para que `modulos_negocio` recalcule con el
            # estado nuevo dentro de la misma transaccion.
            invalidar_cache()
            despues = modulos_negocio(negocio)
            _validar_degradacion(negocio, antes, despues)
        invalidar_cache()

    def perform_create(self, serializer):
        negocio = serializer.validated_data.get('negocio')
        if negocio is None:
            serializer.save()
            return
        self._aplicar(serializer.save, negocio)

    def perform_update(self, serializer):
        negocio = self._negocio_de(serializer.instance)
        self._aplicar(serializer.save, negocio)

    def perform_destroy(self, instance):
        negocio = self._negocio_de(instance)
        self._aplicar(instance.delete, negocio)


class SuscripcionNegocioViewSet(GuardDegradacionMixin, viewsets.ModelViewSet):
    """Consulta y cambia el plan/estado de la suscripción de cada negocio."""
    permission_classes = ADMIN_SUSCRIP
    serializer_class = SuscripcionNegocioSerializer
    pagination_class = None
    http_method_names = ['get', 'patch', 'head', 'options']
    queryset = SuscripcionNegocio.objects.select_related('negocio', 'plan').all()

    def _negocio_de(self, instance):
        return instance.negocio


class NegocioModuloViewSet(GuardDegradacionMixin, viewsets.ModelViewSet):
    """Overrides à la carte. Toda transicion pasa por `puede_desactivarse`."""
    permission_classes = ADMIN_SUSCRIP
    serializer_class = NegocioModuloSerializer
    pagination_class = None
    queryset = NegocioModulo.objects.select_related('negocio', 'modulo').all()

    def _negocio_de(self, instance):
        return instance.negocio

    # La comparacion de sets no alcanza sola aca, y el motivo es interesante:
    # excluir `ventas` mientras `cuentas_por_cobrar` sigue activo NO retira
    # `ventas` del set efectivo, porque el cierre de dependencias vuelve a
    # agregarlo. O sea que la exclusion no produce ninguna baja... y tampoco
    # tiene efecto. Rechazarla con un motivo claro es mejor que aceptarla y que
    # no haga nada.
    #
    # Por eso se valida la INTENCION ademas del EFECTO: la primera cubre la
    # exclusion explicita, la segunda cubre el cambio de plan y el DELETE.

    def _validar_intencion(self, data, instance=None):
        incluido = data.get(
            'incluido', getattr(instance, 'incluido', True),
        )
        if incluido:
            return
        modulo = data.get('modulo') or getattr(instance, 'modulo', None)
        negocio = data.get('negocio') or getattr(instance, 'negocio', None)
        if modulo is None or negocio is None:
            return
        ok, motivo = puede_desactivarse(negocio, modulo.key)
        if not ok:
            raise ValidationError({'modulo': motivo})

    def perform_create(self, serializer):
        self._validar_intencion(serializer.validated_data)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self._validar_intencion(serializer.validated_data, serializer.instance)
        super().perform_update(serializer)
