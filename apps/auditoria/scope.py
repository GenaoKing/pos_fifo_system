"""
apps/auditoria/scope.py

Alcance del historial de auditoria (AUD-001).

Mismo modelo de dos permisos que reportes, sobre la misma primitiva compartida
(`apps.permisos.alcance`):

    auditoria.ver             -> historial de MIS sucursales
    auditoria.consolidado.ver -> historial de TODAS (solo si la asignacion del
                                 rol es global, es decir sin sucursal)

Antes las dos vistas preguntaban `tiene_permiso('auditoria.ver')` sin sucursal
—que en el motor viejo significaba "en alguna"— y despues consultaban sin
ningun filtro. Un supervisor con el permiso solo en A recibia los registros, las
estadisticas y la lista de usuarios de B.
"""
from apps.permisos.alcance import alcance_de as _alcance_de

PERM_VER = 'auditoria.ver'
PERM_CONSOLIDADO = 'auditoria.consolidado.ver'


def alcance_de(usuario):
    """Resuelve el alcance de auditoria de `usuario`."""
    return _alcance_de(usuario, PERM_VER, PERM_CONSOLIDADO)
