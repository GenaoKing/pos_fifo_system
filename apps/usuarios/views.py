"""
apps/usuarios/views.py
Login y logout del POS local.

Cuatro hallazgos de la auditoria viven en este archivo:

USR-004  La auditoria controlaba la disponibilidad del mecanismo que observa.
         En logout se registraba ANTES de `logout()`: forzando un fallo del
         sink, la respuesta era 500, la sesion seguia viva y su session key
         intacta — justo en el momento en que mas importa cerrarla.

USR-006  El login no tenia freno. Doce intentos consecutivos devolvian la
         pantalla normal, creaban doce filas de auditoria y no bloqueaban nada.
         Cada intento paga ademas un hash de password: el endpoint se convierte
         en presion de CPU y de base a la vez, y la auditoria crece sin limite
         precisamente durante el ataque.

USR-008  `next` se pasaba tal cual a `redirect()`. Un enlace con
         `?next=https://sitio-externo/` llevaba al usuario recien autenticado
         fuera del sistema.

USR-009  El logout aceptaba GET, asi que una imagen o un enlace en otro sitio
         cerraba la sesion del operador sin CSRF.
"""
import logging

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.auditoria.models import Auditoria, get_client_ip, get_user_agent
from apps.configuracion.utils import get_config

from .throttling import limite_login

logger = logging.getLogger('usuarios')


def _auditar(registrar, **kwargs):
    """
    Registra auditoria sin que su fallo tumbe la operacion observada.

    La auditoria es observabilidad, no un prerequisito de la seguridad. Una
    tabla bloqueada o una base degradada no pueden impedir cerrar sesion. El
    fallo NO se traga en silencio: va al log con traza, que es donde un
    operador lo va a buscar.
    """
    try:
        registrar(**kwargs)
        return True
    except Exception:
        logger.exception('No se pudo registrar la auditoria de sesion')
        return False


def _destino_seguro(request, user):
    """
    A donde mandar tras un login exitoso.

    `next` solo se honra si apunta a este mismo host: la version anterior lo
    pasaba directo a `redirect()` y aceptaba un destino externo (USR-008).
    """
    destino = request.POST.get('next') or request.GET.get('next')
    if destino and url_has_allowed_host_and_scheme(
        url=destino,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return destino
    return 'reportes:dashboard' if user.es_admin else 'pos:punto_venta'


def login_view(request):
    if request.user.is_authenticated:
        return redirect('reportes:dashboard')

    form = AuthenticationForm()

    if request.method == 'POST':
        username = request.POST.get('username', '')

        bloqueo = limite_login.consultar(request, username)
        if bloqueo.excedido:
            # Respuesta identica exista o no el usuario: el freno no puede
            # convertirse en un oraculo de que cuentas existen.
            _auditar(
                Auditoria.registrar,
                accion=Auditoria.TipoAccion.INTENTO_LOGIN_FALLIDO,
                descripcion=(
                    f'Login bloqueado por exceso de intentos - username: {username}'
                ),
                usuario=None,
                ip_address=get_client_ip(request),
                user_agent=get_user_agent(request),
                nivel_importancia='ALTA',
                exito=False,
            )
            messages.error(
                request,
                'Demasiados intentos fallidos. Espera unos minutos e intenta de nuevo.',
            )
            return render(
                request, 'usuarios/login.html',
                {'form': AuthenticationForm(), 'config': get_config()},
                status=429,
            )

        # AuthenticationForm requiere request como primer arg
        form = AuthenticationForm(request, data=request.POST)

        if form.is_valid():
            user = form.get_user()

            if not user.activo:
                limite_login.registrar_fallo(request, username)
                _auditar(
                    Auditoria.registrar_login,
                    usuario=user,
                    ip_address=get_client_ip(request),
                    user_agent=get_user_agent(request),
                    exito=False,
                )
                messages.error(request, 'Tu cuenta esta desactivada.')
                return render(
                    request, 'usuarios/login.html',
                    {'form': form, 'config': get_config()},
                )

            login(request, user)
            limite_login.limpiar(request, username)

            _auditar(
                Auditoria.registrar_login,
                usuario=user,
                ip_address=get_client_ip(request),
                user_agent=get_user_agent(request),
                exito=True,
            )

            messages.success(request, f'Bienvenido/a {user.get_full_name()}!')
            return redirect(_destino_seguro(request, user))

        # Form invalido = credenciales incorrectas
        limite_login.registrar_fallo(request, username)
        _auditar(
            Auditoria.registrar,
            accion=Auditoria.TipoAccion.INTENTO_LOGIN_FALLIDO,
            descripcion=f'Intento de login fallido - username: {username}',
            usuario=None,
            ip_address=get_client_ip(request),
            user_agent=get_user_agent(request),
            nivel_importancia='ALTA',
            exito=False,
        )
        messages.error(request, 'Usuario o contrasena incorrectos.')

    return render(request, 'usuarios/login.html', {'form': form})


@require_POST
def logout_view(request):
    """
    Cierra la sesion. Solo POST.

    Con GET, cualquier sitio podia cerrarle la sesion al operador con una
    imagen o un enlace: no hay token CSRF que verificar en un GET (USR-009).
    """
    usuario = request.user if request.user.is_authenticated else None

    # PRIMERO invalidar. La sesion se cierra pase lo que pase con la auditoria.
    logout(request)

    if usuario is not None:
        _auditar(
            Auditoria.registrar,
            accion=Auditoria.TipoAccion.LOGOUT,
            descripcion=f'Logout: {usuario.username}',
            usuario=usuario,
            ip_address=get_client_ip(request),
            user_agent=get_user_agent(request),
            nivel_importancia='BAJA',
            exito=True,
        )

    return redirect('usuarios:login')


@login_required
def styleguide(request):
    return render(request, 'styleguide.html')
