"""
apps/reportes/scope.py

Alcance de datos de los reportes: quien puede ver que, y de que sucursales.

El problema que resuelve (RPT-003): `es_admin` preguntaba
`tiene_permiso('reportes.consolidado.ver')` SIN sucursal. El motor RBAC, cuando
no recibe una, considera todas las asignaciones activas del usuario — asi que un
rol concedido unicamente en la sucursal A abria la puerta, y detras de la puerta
los querysets no filtraban por sucursal en absoluto. Un supervisor de A
consultaba un periodo y recibia las ventas, el inventario y los usuarios de B.

El modelo que se aplica ahora tiene dos permisos con significados distintos:

    reportes.sucursal.ver     -> reportes de MIS sucursales
    reportes.consolidado.ver  -> consolidar TODAS (solo si la asignacion es
                                 global, es decir sin sucursal)

`alcance_de(request)` devuelve un `AlcanceReportes` que responde dos cosas:
si el usuario entra, y sobre que sucursales. Todos los querysets de la app se
filtran con el MISMO objeto, para que el alcance declarado por RBAC y el alcance
de los datos no puedan divergir.
"""
from django.db.models import Q

from apps.permisos.engine import sucursales_con_permiso

PERM_VER = 'reportes.ver'
PERM_SUCURSAL = 'reportes.sucursal.ver'
PERM_CONSOLIDADO = 'reportes.consolidado.ver'


class AlcanceReportes:
    """
    Alcance efectivo de un usuario sobre los datos de reportes.

    `sucursal_ids is None` significa "todas" (consolidacion global). Un `set`
    vacio significa que no tiene acceso a ninguna.
    """

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

        Las filas con `sucursal` nula (anteriores a la Fase 2, o creadas por un
        canal sin sucursal) quedan visibles: excluirlas volveria invisible la
        historia de una instalacion sin migrar, que es peor que mostrarla de mas
        en una instalacion de una sola sucursal.
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
        """
        Usuarios visibles: los que tienen alguna asignacion en el alcance.

        La pantalla on-demand listaba TODOS los usuarios activos de la
        instalacion, que en una BD compartida es la nomina de las otras
        sucursales.
        """
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
        return f'<AlcanceReportes {donde} consolidado={self.consolidado}>'


def alcance_de(usuario):
    """Resuelve el alcance de reportes de `usuario`."""
    consolidado = sucursales_con_permiso(usuario, PERM_CONSOLIDADO)

    # Consolidar es una facultad global por definicion. Un rol "consolidado"
    # asignado SOLO a la sucursal A no consolida nada: vale por A y punto.
    if consolidado is None:
        return AlcanceReportes(None, True)

    propias = sucursales_con_permiso(usuario, PERM_SUCURSAL)
    if propias is None:
        return AlcanceReportes(None, False)

    return AlcanceReportes(set(consolidado) | set(propias), False)


def puede_ver_reportes(usuario):
    """Gate de entrada a los reportes on-demand."""
    return alcance_de(usuario).permitido
