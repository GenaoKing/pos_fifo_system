
from django.shortcuts import redirect, render
from django.contrib.auth.decorators import login_required
from apps.auditoria.models import Auditoria, get_client_ip, get_user_agent
from django.contrib.auth import authenticate, login
from django.contrib import messages
from django.contrib.auth.forms import AuthenticationForm as LoginForm


@login_required
def styleguide(request):
    return render(request, 'styleguide.html')

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    
    if request.method == 'POST':
        form = LoginForm(request.POST)
        
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            
            user = authenticate(request, username=username, password=password)
            
            if user is not None:
                if not user.activo:
                    # ✅ REGISTRAR INTENTO FALLIDO
                    Auditoria.registrar_login(
                        usuario=user,
                        ip_address=get_client_ip(request),
                        user_agent=get_user_agent(request),
                        exito=False
                    )
                    messages.error(request, 'Tu cuenta está desactivada.')
                    return render(request, 'usuarios/login.html', {'form': form})
                
                login(request, user)
                
                # ✅ REGISTRAR LOGIN EXITOSO
                Auditoria.registrar_login(
                    usuario=user,
                    ip_address=get_client_ip(request),
                    user_agent=get_user_agent(request),
                    exito=True
                )
                
                messages.success(request, f'¡Bienvenido/a {user.get_full_name() or user.username}!')
                
                next_url = request.GET.get('next')
                if next_url:
                    return redirect(next_url)
                elif user.es_admin():
                    return redirect('dashboard')
                else:
                    return redirect('ventas:pos')
            else:
                # ✅ REGISTRAR LOGIN FALLIDO
                Auditoria.registrar(
                    accion=Auditoria.TipoAccion.INTENTO_LOGIN_FALLIDO,
                    descripcion=f"Intento de login fallido - username: {username}",
                    usuario=None,
                    ip_address=get_client_ip(request),
                    user_agent=get_user_agent(request),
                    nivel_importancia='ALTA',
                    exito=False
                )
                messages.error(request, 'Usuario o contraseña incorrectos.')