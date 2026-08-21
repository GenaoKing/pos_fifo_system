"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.contrib import admin
from django.urls import path, include
from apps.api.views.health import health_live
from apps.usuarios.views import styleguide
from django.views.generic import RedirectView
from django.conf.urls.static import static  # ← AGREGAR
from django.views.static import serve


# Prefijos de MEDIA_ROOT que NUNCA se sirven sin autenticacion.
#
# `serve` publica todo MEDIA_ROOT sin login y sin condicionarlo a DEBUG. Eso
# esta bien para imagenes de producto; no para documentos financieros. Los
# cierres nuevos ya se escriben fuera de media (`apps/reportes/almacenamiento`),
# pero una instalacion existente tiene PDFs viejos ahi, con nombre predecible
# por fecha. Este guard los cubre sin depender de que alguien limpie el disco.
MEDIA_PRIVADO = ('reportes/',)


def serve_media(request, path):
    """`serve`, salvo para los prefijos privados."""
    from django.http import Http404

    normalizado = str(path or '').replace('\\', '/').lstrip('/')
    if any(normalizado.startswith(pref) for pref in MEDIA_PRIVADO):
        raise Http404('No disponible.')
    return serve(request, path, document_root=settings.MEDIA_ROOT)


urlpatterns = [
    path("api/v1/health/live/", health_live, name="api-health-live"),
    path("admin/", admin.site.urls),
    path('', RedirectView.as_view(pattern_name='reportes:dashboard'), name='home'),
    path('styleguide/', styleguide, name='styleguide'),
    path('inventario/', include('apps.inventario.urls')),
    path('pos/', include('apps.ventas.urls')),
    path('impresion/', include('utils.impresoras.urls')),
    path('productos/', include('apps.productos.urls')),
    path('clientes/', include('apps.clientes.urls')),
    path('cuentas-por-cobrar/', include('apps.cuentas_por_cobrar.urls')),
    path('cotizaciones/', include('apps.cotizaciones.urls')),
    path('reportes/', include('apps.reportes.urls')),
    path('caja/', include('apps.caja.urls')),
    path('auditoria/', include('apps.auditoria.urls')),

    path('', include('apps.usuarios.urls')),
    
    # API REST V1
    path('api/', include('apps.api.urls')),

    path('facturacion-electronica/', include('apps.facturacion_electronica.urls')),
    
]

urlpatterns += [
    path('media/<path:path>', serve_media),
]
