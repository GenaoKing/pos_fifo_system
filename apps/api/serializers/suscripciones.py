"""
apps/api/serializers/suscripciones.py
Serializers para la administración de suscripciones/módulos (operador SaaS).
"""
from rest_framework import serializers

from apps.suscripciones.engine import modulos_negocio
from apps.suscripciones.models import Modulo, NegocioModulo, Plan, SuscripcionNegocio


class ModuloSerializer(serializers.ModelSerializer):
    class Meta:
        model = Modulo
        fields = ['id', 'key', 'nombre', 'descripcion', 'core']


class PlanSerializer(serializers.ModelSerializer):
    modulos = serializers.SlugRelatedField(slug_field='key', many=True, read_only=True)

    class Meta:
        model = Plan
        fields = ['id', 'nombre', 'slug', 'descripcion', 'activo', 'modulos']


class SuscripcionNegocioSerializer(serializers.ModelSerializer):
    plan = serializers.SlugRelatedField(
        slug_field='slug', queryset=Plan.objects.all(), allow_null=True, required=False,
    )
    negocio_nombre = serializers.CharField(source='negocio.nombre', read_only=True)
    modulos_activos = serializers.SerializerMethodField()

    class Meta:
        model = SuscripcionNegocio
        fields = [
            'id', 'negocio', 'negocio_nombre', 'plan', 'activa', 'modulos_activos',
        ]
        read_only_fields = ['negocio']

    def get_modulos_activos(self, obj):
        return sorted(modulos_negocio(obj.negocio))


class NegocioModuloSerializer(serializers.ModelSerializer):
    """Override à la carte: agrega (`incluido=True`) o quita (`False`) un módulo."""
    modulo = serializers.SlugRelatedField(
        slug_field='key', queryset=Modulo.objects.all(),
    )

    class Meta:
        model = NegocioModulo
        fields = ['id', 'negocio', 'modulo', 'incluido']
