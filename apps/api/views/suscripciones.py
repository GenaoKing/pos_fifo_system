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


class SuscripcionNegocioViewSet(viewsets.ModelViewSet):
    """Consulta y cambia el plan/estado de la suscripción de cada negocio."""
    permission_classes = ADMIN_SUSCRIP
    serializer_class = SuscripcionNegocioSerializer
    pagination_class = None
    http_method_names = ['get', 'patch', 'head', 'options']
    queryset = SuscripcionNegocio.objects.select_related('negocio', 'plan').all()


class NegocioModuloViewSet(viewsets.ModelViewSet):
    """Overrides à la carte. Al excluir un módulo se valida `puede_desactivarse`."""
    permission_classes = ADMIN_SUSCRIP
    serializer_class = NegocioModuloSerializer
    pagination_class = None
    queryset = NegocioModulo.objects.select_related('negocio', 'modulo').all()

    def perform_create(self, serializer):
        self._validar(serializer.validated_data)
        serializer.save()

    def perform_update(self, serializer):
        self._validar(serializer.validated_data)
        serializer.save()

    def _validar(self, data):
        # Si se está quitando un módulo (incluido=False), respetar el bloqueo por
        # dependientes activos / datos.
        if data.get('incluido', True):
            return
        modulo = data.get('modulo')
        negocio = data.get('negocio')
        if modulo is None or negocio is None:
            return
        ok, motivo = puede_desactivarse(negocio, modulo.key)
        if not ok:
            raise ValidationError({'modulo': motivo})
