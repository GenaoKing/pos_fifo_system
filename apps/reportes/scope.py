"""
apps/reportes/scope.py

Alcance de datos de los reportes: quien puede ver que, y de que sucursales.

El problema que resuelve (RPT-003): `es_admin` preguntaba
`tiene_permiso('reportes.consolidado.ver')` SIN sucursal. El motor RBAC, cuando
no recibe una, consideraba todas las asignaciones activas del usuario — asi que
un rol concedido unicamente en la sucursal A abria la puerta, y detras de la
puerta los querysets no filtraban por sucursal en absoluto. Un supervisor de A
consultaba un periodo y recibia las ventas, el inventario y los usuarios de B.

El modelo que se aplica tiene dos permisos con significados distintos:

    reportes.sucursal.ver     -> reportes de MIS sucursales
    reportes.consolidado.ver  -> consolidar TODAS (solo si la asignacion es
                                 global, es decir sin sucursal)

La mecanica vive en `apps.permisos.alcance`, compartida con auditoria, que tuvo
el mismo hallazgo por separado (AUD-001). Todos los querysets de esta app se
filtran con el MISMO objeto, para que el alcance declarado por RBAC y el
alcance de los datos no puedan divergir.
"""
from apps.permisos.alcance import Alcance, alcance_de as _alcance_de

PERM_VER = 'reportes.ver'
PERM_SUCURSAL = 'reportes.sucursal.ver'
PERM_CONSOLIDADO = 'reportes.consolidado.ver'

# Nombre historico; la implementacion es la compartida.
AlcanceReportes = Alcance


def alcance_de(usuario):
    """Resuelve el alcance de reportes de `usuario`."""
    return _alcance_de(usuario, PERM_SUCURSAL, PERM_CONSOLIDADO)


def puede_ver_reportes(usuario):
    """Gate de entrada a los reportes on-demand."""
    return alcance_de(usuario).permitido
