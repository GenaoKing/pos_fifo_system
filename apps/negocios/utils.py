"""
apps/negocios/utils.py
Resolución del tenant (Negocio) del request.

`resolver_negocio(request)` es el ÚNICO punto de resolución de tenant. Usarlo
siempre para scopear querysets/permisos en vez de leer `request.user.negocio`
disperso por el código.

--------------------------------------------------------------------------
Por qué el resultado es tipado (NEG-001 y NEG-002)
--------------------------------------------------------------------------
La versión anterior devolvía `Negocio | None`, y `None` significaba TRES cosas
con autoridades opuestas:

    1. "Sos operador global: consultá sin filtro."
    2. "No pude resolver un tenant."           (usuario huérfano, inactivo)
    3. "Pediste un negocio que no existe o está inactivo."

Los tres consumidores —cartera, reportes y estado de sucursales— convertían
`None` en "queryset sin filtro". Es decir: los dos casos de FALLO se leían como
el permiso más amplio del sistema. Se reprodujo con una cuenta `ADMIN` activa,
no staff, no superusuario y con `negocio_id` nulo: recibió las sucursales de
los dos negocios de prueba. Y un SYSADMIN que pedía `?negocio=999999` —un typo,
un bookmark viejo— recibía TODOS los negocios en vez de un 404: exactamente lo
contrario de lo que intentaba hacer.

Ahora hay tres resultados distinguibles y ninguno se puede confundir por
accidente:

    Resolucion.tenant(negocio)  -> acotar a ese negocio
    Resolucion.global_()        -> operador global sin selector: sin filtro
    Resolucion.sin_acceso(...)  -> negar

`negocio_actual(request)` se conserva porque muchos llamadores solo quieren el
negocio, pero YA NO puede usarse para decidir alcance global: devuelve el
negocio o `None`, y `None` significa "no hay tenant", nunca "todos".
"""


class Resolucion:
    """Resultado de resolver el tenant de un request."""

    TENANT = 'TENANT'
    GLOBAL = 'GLOBAL'
    SIN_ACCESO = 'SIN_ACCESO'

    __slots__ = ('estado', 'negocio', 'motivo')

    def __init__(self, estado, negocio=None, motivo=''):
        self.estado = estado
        self.negocio = negocio
        self.motivo = motivo

    # -- Constructores -------------------------------------------------

    @classmethod
    def tenant(cls, negocio):
        return cls(cls.TENANT, negocio=negocio)

    @classmethod
    def global_(cls):
        return cls(cls.GLOBAL)

    @classmethod
    def sin_acceso(cls, motivo):
        return cls(cls.SIN_ACCESO, motivo=motivo)

    # -- Consultas -----------------------------------------------------

    @property
    def es_global(self):
        return self.estado == self.GLOBAL

    @property
    def permitido(self):
        return self.estado in (self.TENANT, self.GLOBAL)

    def filtrar(self, queryset, campo='negocio'):
        """
        Acota `queryset` según el resultado.

        Es el metodo que reemplaza al `if negocio is not None` disperso: un
        fallo de resolucion devuelve `none()`, no el queryset completo.
        """
        if self.estado == self.GLOBAL:
            return queryset
        if self.estado == self.TENANT:
            return queryset.filter(**{campo: self.negocio})
        return queryset.none()

    def __repr__(self):  # pragma: no cover - diagnostico
        if self.estado == self.TENANT:
            return f'<Resolucion TENANT {self.negocio}>'
        if self.estado == self.SIN_ACCESO:
            return f'<Resolucion SIN_ACCESO: {self.motivo}>'
        return '<Resolucion GLOBAL>'


def resolver_negocio(request):
    """
    Resuelve el tenant del request. Fail-closed.

    - Usuario con negocio activo         -> TENANT.
    - Principal global sin `?negocio=`   -> GLOBAL.
    - Principal global con `?negocio=<id>` valido y activo -> TENANT.
    - Cualquier otra cosa                -> SIN_ACCESO.
    """
    user = getattr(request, 'user', None)
    if user is None or not getattr(user, 'is_authenticated', False):
        return Resolucion.sin_acceso('No autenticado.')

    if not getattr(user, 'activo', True):
        return Resolucion.sin_acceso('Usuario desactivado.')

    if getattr(user, 'negocio_id', None):
        negocio = user.negocio
        if negocio is None or not getattr(negocio, 'activo', True):
            # Desactivar un negocio tiene que revocar tambien a sus cuentas mas
            # privilegiadas (NEG-003). Antes el resolver devolvia `user.negocio`
            # sin mirar `activo`, y un ADMIN del negocio desactivado seguia
            # iniciando sesion y consultando su sucursal mientras un rol
            # granular equivalente quedaba correctamente bloqueado.
            return Resolucion.sin_acceso('El negocio esta desactivado.')
        return Resolucion.tenant(negocio)

    if not es_principal_global(user):
        return _resolver_huerfano()

    seleccion = _query_param(request, 'negocio')
    if not seleccion:
        return Resolucion.global_()

    try:
        seleccion_id = int(seleccion)
    except (TypeError, ValueError):
        # Un selector no numerico levantaba excepcion aguas abajo (NEG-010).
        return Resolucion.sin_acceso('El negocio indicado no es valido.')

    from .models import Negocio

    negocio = Negocio.objects.filter(pk=seleccion_id, activo=True).first()
    if negocio is None:
        # NO se cae a GLOBAL: pedir un negocio concreto y recibir TODOS es el
        # opuesto exacto de lo que el operador intentaba hacer.
        return Resolucion.sin_acceso('El negocio indicado no existe o esta inactivo.')

    return Resolucion.tenant(negocio)


def _resolver_huerfano():
    """
    Usuario sin negocio y sin autoridad global.

    Aca hay dos situaciones que se ven igual y no lo son:

    - **Instalacion local de un solo negocio.** El bootstrap enlaza a todos los
      usuarios, pero una instalacion que nunca lo corrio los deja sin FK. No hay
      nada que aislar —no existe otro tenant al cual cruzar— y fallar cerrado
      dejaria el POS sin reportes ni cartera por un dato de aprovisionamiento.

    - **Base compartida con varios negocios.** Aca la misma cuenta huerfana SI
      es un riesgo horizontal: es la reproduccion de NEG-001, donde un `ADMIN`
      activo sin negocio recibio las sucursales de los dos negocios de prueba.

    La regla, entonces, no es "huerfano = denegar" sino "denegar donde hay algo
    que aislar". Bajo tenancy siempre se deniega: cada base es un negocio y un
    usuario sin FK es un error de aprovisionamiento, no una configuracion.
    """
    from apps.tenancy.context import tenancy_enabled

    if tenancy_enabled():
        return Resolucion.sin_acceso('El usuario no pertenece a ningun negocio.')

    from .models import Negocio

    negocios = list(Negocio.objects.filter(activo=True)[:2])

    if len(negocios) > 1:
        return Resolucion.sin_acceso(
            'El usuario no pertenece a ningun negocio y la instalacion tiene '
            'varios: no se puede determinar su alcance.'
        )

    if len(negocios) == 1:
        return Resolucion.tenant(negocios[0])

    # Instalacion sin negocios cargados: no hay nada que acotar.
    return Resolucion.global_()


def negocio_actual(request):
    """
    Negocio del request, o `None` si no hay tenant resuelto.

    OJO: `None` aca significa "no hay negocio", NO "todos los negocios". Para
    decidir alcance global usar `resolver_negocio()` y mirar `es_global`.
    """
    resolucion = resolver_negocio(request)
    return resolucion.negocio if resolucion.estado == Resolucion.TENANT else None


def es_principal_global(user):
    """
    True si `user` opera la plataforma, no un negocio.

    Bajo tenancy la autoridad global solo la concede el control plane: una
    `Identity` marcada `is_global`, o el superusuario de Django. El rol legacy
    `SYSADMIN` vive en una fila tenant-local y editable — una cuenta ordinaria,
    no staff y sin identidad global, podia seleccionar cualquier negocio solo
    por tener ese texto (NEG-004).

    Sin tenancy no hay control plane con el cual contrastar, y `SYSADMIN` sigue
    siendo la forma de identificar al operador de la instalacion.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if not getattr(user, 'activo', True):
        return False

    if getattr(user, 'is_superuser', False):
        return True

    if getattr(user, 'is_global_identity', False):
        return True

    identidad = getattr(user, 'identity', None)
    if getattr(identidad, 'is_global', False):
        return True

    from apps.tenancy.context import tenancy_enabled

    if tenancy_enabled():
        return False

    return getattr(user, 'rol', None) == 'SYSADMIN'


def _query_param(request, nombre):
    """Lee un query param tanto de un DRF Request como de un HttpRequest."""
    params = getattr(request, 'query_params', None)
    if params is None:
        params = getattr(request, 'GET', None)
    return params.get(nombre) if params is not None else None
