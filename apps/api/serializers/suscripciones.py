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

    def validate_plan(self, plan):
        """
        SUS-013 — `Plan.activo` tiene semantica: `activo=False` = "no vendible a
        NUEVAS altas", NO suspende clientes existentes (esa es la suspension
        explicita, `SuscripcionNegocio.activa`). Antes el serializer aceptaba
        cualquier `Plan.objects.all()`, incluidos inactivos, y el flag no
        significaba nada. Ahora se rechaza asignar un plan inactivo —salvo que la
        suscripcion YA este en ese plan: re-guardar a un suscriptor existente no
        es un alta nueva y no debe bloquearse.
        """
        if plan is None or plan.activo:
            return plan
        instance = getattr(self, 'instance', None)
        ya_en_ese_plan = instance is not None and instance.plan_id == plan.id
        if not ya_en_ese_plan:
            raise serializers.ValidationError(
                f"El plan '{plan.slug}' esta inactivo: no se puede asignar a una "
                f"nueva alta ni a un cambio de plan. Reactivalo o elegi otro."
            )
        return plan

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

    def validate(self, attrs):
        """
        SUS-013 — excluir un modulo core es un no-op enganoso: el cierre de
        dependencias del resolutor siempre lo vuelve a agregar. Rechazarlo con un
        motivo claro es mejor que persistir una fila que no hace nada.
        """
        from apps.suscripciones import registry

        incluido = attrs.get('incluido', getattr(self.instance, 'incluido', True))
        modulo = attrs.get('modulo') or getattr(self.instance, 'modulo', None)
        if modulo is not None and not incluido and modulo.key in registry.core_keys():
            raise serializers.ValidationError({
                'modulo': (
                    f"'{modulo.key}' es un modulo core: no se puede excluir "
                    f"(el resolutor siempre lo reactiva)."
                )
            })
        return attrs
