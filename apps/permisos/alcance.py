"""
apps/permisos/alcance.py

Alcance de datos: sobre que sucursales puede ver un usuario.

Vive aca, y no dentro de la app que lo consume, porque duplicar una primitiva
de autorizacion es como aparecen estos bugs: en `apps/caja` convivian tres
respuestas distintas a "¿este usuario es admin?" (CAJA-011), y en reportes y
auditoria el mismo hallazgo —un permiso acotado a la sucursal A abriendo los
datos de B— aparecio por separado (RPT-003 y AUD-001). Una sola implementacion
significa que corregirla una vez la corrige en todas partes.

    alcance_de(usuario, PERM_PROPIO, PERM_CONSOLIDADO) -> Alcance

`Alcance.sucursal_ids is None` significa "todas" (consolidacion global). Un set
vacio significa que no tiene acceso a ninguna.
"""
from django.db.models import Q

from .engine import sucursales_con_permiso


class Alcance:
    """Alcance efectivo de un usuario sobre los datos de un modulo."""

    __slots__ = ('sucursal_ids', 'consolidado')

    def __init__(self, sucursal_ids, consolidado):
        self.sucursal_ids = sucursal_ids
        self.consolidado = consolidado

    @property
    def permitido(self):
        return self.sucursal_ids is None or bool(self.sucursal_ids)

    @property
    def es_global(self):
        return self.sucursal_ids is None

    def filtrar(self, queryset, campo='sucursal'):
        """
        Acota `queryset` a las sucursales del alcance.

        Las filas con `sucursal` nula quedan visibles: excluirlas volveria
        invisible la historia de una instalacion sin migrar, que es peor que
        mostrarla de mas en una instalacion de una sola sucursal.
        """
        if self.es_global:
            return queryset
        if not self.sucursal_ids:
            return queryset.none()
        return queryset.filter(
            Q(**{f'{campo}_id__in': self.sucursal_ids})
            | Q(**{f'{campo}__isnull': True})
        )

    def filtrar_usuarios(self, queryset):
        """Usuarios con alguna asignacion dentro del alcance."""
        if self.es_global:
            return queryset
        if not self.sucursal_ids:
            return queryset.none()
        return queryset.filter(
            Q(asignaciones_rol__sucursal_id__in=self.sucursal_ids)
            | Q(asignaciones_rol__sucursal__isnull=True)
        ).distinct()

    def __repr__(self):  # pragma: no cover - diagnostico
        donde = 'GLOBAL' if self.es_global else sorted(self.sucursal_ids)
        return f'<Alcance {donde} consolidado={self.consolidado}>'


def alcance_de(usuario, permiso_propio, permiso_consolidado):
    """
    Resuelve el alcance combinando los dos permisos de un modulo.

    Consolidar es una facultad GLOBAL por definicion: un rol "consolidado"
    asignado SOLO a la sucursal A no consolida nada — vale por A y punto.
    """
    consolidado = sucursales_con_permiso(usuario, permiso_consolidado)
    if consolidado is None:
        return Alcance(None, True)

    propias = sucursales_con_permiso(usuario, permiso_propio)
    if propias is None:
        return Alcance(None, False)

    return Alcance(set(consolidado) | set(propias), False)
