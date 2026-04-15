from django.shortcuts import redirect, render
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib import messages
from apps.configuracion.utils import get_config

from apps.auditoria.models import Auditoria, get_client_ip, get_user_agent


def login_view(request):
    if request.user.is_authenticated:
        return redirect('reportes:dashboard')

    if request.method == 'POST':
        # AuthenticationForm requiere request como primer arg
        form = AuthenticationForm(request, data=request.POST)

        if form.is_valid():
            user = form.get_user()

            if not user.activo:
                Auditoria.registrar_login(
                    usuario=user,
                    ip_address=get_client_ip(request),
                    user_agent=get_user_agent(request),
                    exito=False,
                )
                messages.error(request, 'Tu cuenta esta desactivada.')
                return render(request, 'usuarios/login.html', {'form': form, 'config': get_config()})

            login(request, user)

            Auditoria.registrar_login(
                usuario=user,
                ip_address=get_client_ip(request),
                user_agent=get_user_agent(request),
                exito=True,
            )

            messages.success(request, f'Bienvenido/a {user.get_full_name()}!')

            next_url = request.GET.get('next')
            if next_url:
                return redirect(next_url)
            elif user.es_admin:
                return redirect('reportes:dashboard')
            else:
                return redirect('pos:punto_venta')
        else:
            # Form invalido = credenciales incorrectas
            username = request.POST.get('username', '')
            Auditoria.registrar(
                accion=Auditoria.TipoAccion.INTENTO_LOGIN_FALLIDO,
                descripcion=f'Intento de login fallido - username: {username}',
                usuario=None,
                ip_address=get_client_ip(request),
                user_agent=get_user_agent(request),
                nivel_importancia='ALTA',
                exito=False,
            )
            messages.error(request, 'Usuario o contrasena incorrectos.')
    else:
        form = AuthenticationForm()

    return render(request, 'usuarios/login.html', {'form': form})


def logout_view(request):
    if request.user.is_authenticated:
        Auditoria.registrar(
            accion=Auditoria.TipoAccion.LOGOUT,
            descripcion=f'Logout: {request.user.username}',
            usuario=request.user,
            ip_address=get_client_ip(request),
            user_agent=get_user_agent(request),
            nivel_importancia='BAJA',
            exito=True,
        )
    logout(request)
    return redirect('usuarios:login')


@login_required
def styleguide(request):
    return render(request, 'styleguide.html')