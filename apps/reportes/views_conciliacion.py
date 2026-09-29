from datetime import timedelta

from django import forms
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.configuracion.decorators import requiere_modulo
from apps.permisos.alcance import Alcance
from apps.sucursales.models import Sucursal
from .conciliacion import generar_conciliacion, generar_pdf
from .scope import alcance_de


class PeriodoForm(forms.Form):
    inicio = forms.DateField(label='Desde', widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}, format='%Y-%m-%d'))
    fin = forms.DateField(label='Hasta', widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}, format='%Y-%m-%d'))
    sucursal = forms.ModelChoiceField(label='Sucursal', queryset=Sucursal.objects.none(), required=False,
                                     empty_label='Todas las permitidas', widget=forms.Select(attrs={'class': 'form-input'}))

    def clean(self):
        data = super().clean()
        inicio, fin = data.get('inicio'), data.get('fin')
        if inicio and fin:
            if inicio > fin:
                raise forms.ValidationError('La fecha inicial debe ser anterior o igual a la final.')
            if fin > timezone.localdate():
                raise forms.ValidationError('El período no puede incluir fechas futuras.')
            if (fin - inicio).days >= 366:
                raise forms.ValidationError('Selecciona un período de hasta 366 días.')
        return data


@login_required
@requiere_modulo('reportes_ondemand')
@never_cache
@require_GET
def conciliacion(request):
    alcance = alcance_de(request.user)
    if not alcance.permitido:
        return HttpResponseForbidden('No tienes permiso para consultar este reporte.')
    hoy = timezone.localdate()
    datos = request.GET.copy()
    if not datos:
        datos.update({'inicio': (hoy - timedelta(days=hoy.weekday())).isoformat(), 'fin': hoy.isoformat()})
    form = PeriodoForm(datos)
    sucursales = Sucursal.objects.all().order_by('nombre')
    if not alcance.es_global:
        sucursales = sucursales.filter(pk__in=alcance.sucursal_ids)
    form.fields['sucursal'].queryset = sucursales
    context = {'form': form, 'hoy': hoy.isoformat()}
    if not form.is_valid():
        return render(request, 'reportes/conciliacion.html', context, status=400)
    sucursal = form.cleaned_data['sucursal']
    if sucursal:
        # Selección explícita: no mezclar filas legacy de sucursal desconocida.
        class AlcanceExacto(Alcance):
            def filtrar(self, queryset, campo='sucursal'):
                return queryset.filter(**{f'{campo}_id': sucursal.pk})
        alcance = AlcanceExacto({sucursal.pk}, False)
        ambito = sucursal.nombre
    else:
        ambito = 'Todas las sucursales permitidas (incluye registros sin sucursal)'
        if not alcance.es_global and len(alcance.sucursal_ids) == 1:
            sucursal = sucursales.first()  # Encabezado de la sucursal autorizada.
    reporte = generar_conciliacion(form.cleaned_data['inicio'], form.cleaned_data['fin'], alcance)
    if request.GET.get('formato') == 'pdf':
        response = HttpResponse(generar_pdf(reporte, sucursal, ambito), content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="cuadre_{reporte["inicio"]}_{reporte["fin"]}.pdf"'
        return response
    parametros = datos.copy()
    parametros['formato'] = 'pdf'
    context.update({'reporte': reporte, 'ambito': ambito, 'pdf_query': parametros.urlencode()})
    return render(request, 'reportes/conciliacion.html', context)
