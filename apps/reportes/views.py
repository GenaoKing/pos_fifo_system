"""
apps/reportes/views.py
Dashboard y vistas de reportes
"""

import json
import logging
import os
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, DecimalField, F, Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.cuentas_por_cobrar.models import PagoCxC
from apps.inventario.models import Compra, Lote
from apps.productos.models import Categoria, Producto
from apps.usuarios.models import Usuario
from apps.ventas.models import DetalleVenta, Pago, Venta

from .almacenamiento import es_ruta_privada
from .models import CierreCaja, InventarioValorizado, TopProducto
from .pdf_generator import PDFGenerator
from .report_manager import CierreFinalizadoError, FechaFuturaError, ReporteManager
from .scope import PERM_VER, alcance_de

logger = logging.getLogger('reportes')

# Tope de filas que devuelve el inventario en una sola respuesta. Sin el, el
# endpoint serializaba TODOS los lotes activos con todos sus detalles en una
# respuesta sincrona: un catalogo grande agota el timeout o la memoria del
# worker (RPT-012).
MAX_PRODUCTOS_INVENTARIO = 500


def _error(mensaje, status=400, codigo=None, **extra):
    """
    Respuesta de error con contrato estable.

    Los handlers genericos devolvian `str(e)` con 500: el cliente recibia el
    texto de una excepcion interna —nombres de campo, rutas, SQL— y no tenia
    ningun codigo con el que decidir que hacer.
    """
    cuerpo = {'success': False, 'error': mensaje}
    if codigo:
        cuerpo['codigo'] = codigo
    cuerpo.update(extra)
    return JsonResponse(cuerpo, status=status)


def _error_interno(contexto):
    """500 sin filtrar el interior; el detalle va al log."""
    logger.exception('Error inesperado en reportes: %s', contexto)
    return _error(
        'Error inesperado generando el reporte.',
        status=500,
        codigo='error_interno',
    )


def _leer_json(request):
    """Body JSON o `None` si viene mal formado."""
    try:
        return json.loads(request.body or b'{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _periodo(data):
    """(fecha_inicio, fecha_fin) validadas; lanza ValueError con mensaje util."""
    inicio = date.fromisoformat(data.get('fecha_inicio', ''))
    fin = date.fromisoformat(data.get('fecha_fin', ''))
    if inicio > fin:
        raise ValueError('La fecha de inicio debe ser anterior a la fecha fin.')
    if inicio > timezone.localdate():
        raise ValueError('El periodo no puede empezar en el futuro.')
    return inicio, fin


def _sucursal_del_alcance(alcance, pedida=None):
    """
    Sucursal sobre la que se generan los snapshots persistidos.

    `None` significa consolidado y solo lo puede pedir un alcance global. Un
    usuario acotado a una sola sucursal genera el snapshot de ESA sucursal;
    acotado a varias, debe elegir cual.
    """
    from apps.sucursales.models import Sucursal

    if pedida is not None:
        if not alcance.es_global and int(pedida) not in alcance.sucursal_ids:
            raise PermissionError('Sucursal fuera de alcance.')
        return Sucursal.objects.filter(pk=pedida).first()

    if alcance.es_global:
        return None
    if len(alcance.sucursal_ids) == 1:
        return Sucursal.objects.filter(
            pk=next(iter(alcance.sucursal_ids))
        ).first()
    raise ValueError(
        'Tu alcance cubre varias sucursales: indica `sucursal_id` para generar '
        'el reporte.'
    )

# ============================================================================
# DASHBOARD PRINCIPAL
# ============================================================================

@login_required
def dashboard(request):
    """
    Dashboard principal - muestra version Admin o Cajera segun el rol.

    `reportes.ver` existia en el catalogo pero no lo aplicaba nadie: revocarlo
    no revocaba nada y el permiso declarado no describia el enforcement real
    (RPT-014). Ahora se exige. Va incluido en `PERMISOS_CAJERO_DEFAULT`, asi que
    una instalacion existente no pierde su pantalla de inicio.
    """
    if not request.user.tiene_permiso(PERM_VER):
        return redirect('pos:punto_venta')

    hoy = timezone.localdate()
    ahora = timezone.localtime()
    inicio_semana = hoy - timedelta(days=hoy.weekday())
    inicio_mes = hoy.replace(day=1)

    # ------------------------------------------------------------------
    # METRICAS COMUNES (ambos roles)
    # ------------------------------------------------------------------

    # Filtro base: ventas completadas de hoy
    ventas_hoy_qs = Venta.objects.filter(
        fecha_venta__date=hoy,
        estado='COMPLETADA'
    )

    # Para cajera: solo sus ventas
    if request.user.es_cajera:
        ventas_hoy_qs = ventas_hoy_qs.filter(usuario=request.user)

    resumen_hoy = ventas_hoy_qs.aggregate(
        total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField()),
        cantidad=Count('id'),
        descuentos=Coalesce(Sum('descuento_total'), Decimal('0.00'), output_field=DecimalField()),
    )

    # Desglose por metodo de pago (hoy)
    pagos_hoy = Pago.objects.filter(
        venta__in=ventas_hoy_qs
    ).values('metodo').annotate(
        total=Coalesce(Sum('monto'), Decimal('0.00'), output_field=DecimalField()),
        cantidad=Count('id'),
    ).order_by('metodo')

    pagos_dict = {p['metodo']: p for p in pagos_hoy}
    efectivo_hoy = pagos_dict.get('EFECTIVO', {}).get('total', Decimal('0.00'))
    transferencia_hoy = pagos_dict.get('TRANSFERENCIA', {}).get('total', Decimal('0.00'))
    tarjeta_hoy = pagos_dict.get('TARJETA', {}).get('total', Decimal('0.00'))
    credito_facturado_hoy = ventas_hoy_qs.filter(
        condicion_pago='CREDITO'
    ).aggregate(total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField()))['total']
    cobros_cxc_qs = PagoCxC.objects.filter(fecha_pago__date=hoy, estado='APLICADO')
    if request.user.es_cajera:
        cobros_cxc_qs = cobros_cxc_qs.filter(registrado_por=request.user)
    cobros_cxc_hoy = cobros_cxc_qs.aggregate(
        total=Coalesce(Sum('monto'), Decimal('0.00'), output_field=DecimalField())
    )['total']

    # Ultimas ventas
    ultimas_ventas = Venta.objects.filter(
        estado='COMPLETADA'
    ).select_related('usuario').order_by('-fecha_venta')

    if request.user.es_cajera:
        ultimas_ventas = ultimas_ventas.filter(usuario=request.user)

    ultimas_ventas = ultimas_ventas[:10]

    # ------------------------------------------------------------------
    # METRICAS SOLO ADMIN
    # ------------------------------------------------------------------
    context_admin = {}

    if request.user.es_admin:
        # Ventas de la semana
        ventas_semana_qs = Venta.objects.filter(
            fecha_venta__date__gte=inicio_semana,
            estado='COMPLETADA'
        )
        resumen_semana = ventas_semana_qs.aggregate(
            total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField()),
            cantidad=Count('id'),
        )

        # Ventas del mes
        ventas_mes_qs = Venta.objects.filter(
            fecha_venta__date__gte=inicio_mes,
            estado='COMPLETADA'
        )
        resumen_mes = ventas_mes_qs.aggregate(
            total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField()),
            cantidad=Count('id'),
        )

        # Comparativa con ayer
        ayer = hoy - timedelta(days=1)
        total_ayer = Venta.objects.filter(
            fecha_venta__date=ayer,
            estado='COMPLETADA'
        ).aggregate(
            total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField()),
        )['total']

        if total_ayer > 0:
            variacion_diaria = ((resumen_hoy['total'] - total_ayer) / total_ayer) * 100
        else:
            variacion_diaria = Decimal('0.00')

        # Top 5 productos vendidos (mes actual)
        top_productos = DetalleVenta.objects.filter(
            venta__fecha_venta__date__gte=inicio_mes,
            venta__estado='COMPLETADA'
        ).values(
            'producto__nombre', 'producto__sku'
        ).annotate(
            total_vendido=Sum('cantidad'),
            total_monto=Sum('total_linea'),
        ).order_by('-total_vendido')[:5]

        # Productos con stock bajo.
        #
        # Antes esto era un SUM por producto dentro de un for: el numero de
        # queries del dashboard crecia linealmente con el catalogo. Ahora es
        # una sola agregacion filtrada.
        productos_activos = Producto.objects.filter(
            activo=True, stock_minimo__gt=0,
        ).annotate(
            stock_actual=Coalesce(
                Sum(
                    'lotes__cantidad_actual',
                    filter=Q(lotes__activo=True, lotes__cantidad_actual__gt=0),
                ),
                0,
            ),
        ).filter(stock_actual__lte=F('stock_minimo'))

        productos_bajo_stock = [{
            'producto': prod,
            'stock_actual': prod.stock_actual,
            'stock_minimo': prod.stock_minimo,
            'porcentaje': (
                int((prod.stock_actual / prod.stock_minimo) * 100)
                if prod.stock_minimo > 0 else 0
            ),
        } for prod in productos_activos]
        productos_bajo_stock.sort(key=lambda x: x['porcentaje'])

        # Inventario valorizado total
        lotes_activos = Lote.objects.filter(
            cantidad_actual__gt=0, activo=True
        )
        inventario_total = lotes_activos.aggregate(
            valor=Coalesce(
                Sum(F('cantidad_actual') * F('costo_unitario')),
                Decimal('0.00'),
                output_field=DecimalField()
            ),
            items=Coalesce(Sum('cantidad_actual'), 0),
        )

        # Anulaciones de hoy
        anulaciones_hoy = Venta.objects.filter(
            fecha_anulacion__date=hoy,
            estado='ANULADA'
        ).count()

        # Ventas por cajero (hoy)
        ventas_por_cajero = Venta.objects.filter(
            fecha_venta__date=hoy,
            estado='COMPLETADA'
        ).values(
            'usuario__first_name', 'usuario__last_name', 'usuario__username'
        ).annotate(
            cantidad=Count('id'),
            total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField()),
        ).order_by('-total')

        # Categorias con mas ventas (mes)
        categorias_ventas = DetalleVenta.objects.filter(
            venta__fecha_venta__date__gte=inicio_mes,
            venta__estado='COMPLETADA'
        ).values(
            'producto__categoria__nombre'
        ).annotate(
            total=Sum('total_linea'),
            cantidad=Sum('cantidad'),
        ).order_by('-total')[:5]

        # Ultimas compras (inventario)
        ultimas_compras = Compra.objects.select_related(
            'registrado_por'
        ).order_by('-fecha_compra')[:5]

        context_admin = {
            'resumen_semana': resumen_semana,
            'resumen_mes': resumen_mes,
            'variacion_diaria': variacion_diaria,
            'total_ayer': total_ayer,
            'top_productos': top_productos,
            'productos_bajo_stock': productos_bajo_stock,
            'cantidad_bajo_stock': len(productos_bajo_stock),
            'inventario_total': inventario_total,
            'anulaciones_hoy': anulaciones_hoy,
            'ventas_por_cajero': ventas_por_cajero,
            'categorias_ventas': categorias_ventas,
            'ultimas_compras': ultimas_compras,
            'total_productos': Producto.objects.filter(activo=True).count(),
            'total_categorias': Categoria.objects.filter(activa=True).count(),
        }

    # ------------------------------------------------------------------
    # CONTEXTO FINAL
    # ------------------------------------------------------------------
    context = {
        'fecha_hoy': hoy,
        'hora_actual': ahora,
        'resumen_hoy': resumen_hoy,
        'efectivo_hoy': efectivo_hoy,
        'transferencia_hoy': transferencia_hoy,
        'tarjeta_hoy': tarjeta_hoy,
        'credito_facturado_hoy': credito_facturado_hoy,
        'cobros_cxc_hoy': cobros_cxc_hoy,
        'ultimas_ventas': ultimas_ventas,
        **context_admin,
    }


    # Hidratación segura para Alpine
    metricas_init = {
        'total_ventas': float(resumen_hoy['total']),
        'cantidad_ventas': resumen_hoy['cantidad'],
        'efectivo': float(efectivo_hoy),
        'transferencia': float(transferencia_hoy),
        'tarjeta': float(tarjeta_hoy),
        'credito_facturado': float(credito_facturado_hoy),
        'cobros_cxc': float(cobros_cxc_hoy),
    }
    context['metricas_init_json'] = json.dumps(metricas_init)


    if request.user.es_cajera:
        return render(request, 'reportes/dashboard_cajera.html', context)


    return render(request, 'reportes/dashboard.html', context)


# ============================================================================
# API - DATOS EN TIEMPO REAL (para Alpine.js polling)
# ============================================================================

@login_required
def api_metricas_hoy(request):
    """
    Endpoint JSON para actualizar metricas en tiempo real via Alpine.js
    """
    if not request.user.tiene_permiso(PERM_VER):
        return _error('Sin permisos', status=403, codigo='sin_permiso')

    hoy = timezone.localdate()

    ventas_qs = Venta.objects.filter(
        fecha_venta__date=hoy,
        estado='COMPLETADA'
    )

    if request.user.es_cajera:
        ventas_qs = ventas_qs.filter(usuario=request.user)

    resumen = ventas_qs.aggregate(
        total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField()),
        cantidad=Count('id'),
    )

    pagos = Pago.objects.filter(
        venta__in=ventas_qs
    ).values('metodo').annotate(
        total=Coalesce(Sum('monto'), Decimal('0.00'), output_field=DecimalField()),
    )

    pagos_dict = {p['metodo']: float(p['total']) for p in pagos}
    credito_facturado = ventas_qs.filter(
        condicion_pago='CREDITO'
    ).aggregate(total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField()))['total']
    cobros_cxc_qs = PagoCxC.objects.filter(fecha_pago__date=hoy, estado='APLICADO')
    if request.user.es_cajera:
        cobros_cxc_qs = cobros_cxc_qs.filter(registrado_por=request.user)
    cobros_cxc = cobros_cxc_qs.aggregate(
        total=Coalesce(Sum('monto'), Decimal('0.00'), output_field=DecimalField())
    )['total']

    return JsonResponse({
        'total_ventas': float(resumen['total']),
        'cantidad_ventas': resumen['cantidad'],
        'efectivo': pagos_dict.get('EFECTIVO', 0),
        'transferencia': pagos_dict.get('TRANSFERENCIA', 0),
        'tarjeta': pagos_dict.get('TARJETA', 0),
        'credito_facturado': float(credito_facturado),
        'cobros_cxc': float(cobros_cxc),
    })




def es_admin(user):
    """
    Compat: "¿puede entrar a los reportes on-demand?".

    Se conserva el nombre porque lo usan las plantillas y las pruebas, pero ya
    NO decide el alcance de los datos: para eso esta `alcance_de(user)`. La
    version anterior preguntaba `tiene_permiso('reportes.consolidado.ver')` sin
    sucursal, y el motor RBAC sin sucursal mira todas las asignaciones del
    usuario — asi que un rol concedido solo en A abria reportes consolidados de
    todo el negocio.
    """
    return alcance_de(user).permitido


# ============================================================================
# PAGINA PRINCIPAL REPORTES ON-DEMAND
# ============================================================================

@login_required
def reportes_on_demand(request):
    """
    Pagina principal de reportes on-demand.
    Solo ADMIN puede acceder.
    """
    alcance = alcance_de(request.user)
    if not alcance.permitido:
        return redirect('reportes:dashboard')

    # Solo los usuarios del alcance: la version anterior listaba TODOS los
    # activos de la instalacion, que en una BD compartida es la nomina de las
    # otras sucursales.
    cajeros = alcance.filtrar_usuarios(
        Usuario.objects.filter(activo=True)
    ).values('id', 'username', 'first_name', 'last_name', 'rol')

    context = {
        # Se entrega por `json_script`, no interpolado dentro de <script>.
        # La plantilla hacia `cajeros: {{ cajeros|safe }}`: un username con
        # `</script><script>...` cerraba el bloque y ejecutaba JavaScript en la
        # sesion del administrador que abriera la pagina (RPT-010).
        'cajeros': list(cajeros),
        'fecha_hoy': timezone.localdate().isoformat(),
        'alcance_global': alcance.es_global,
    }
    return render(request, 'reportes/on_demand.html', context)


# ============================================================================
# SERIALIZER UNICO DEL RESUMEN DIARIO
# ============================================================================

def serializar_cierre(cierre):
    """
    Representacion unica del resumen diario (RPT-011).

    Pantalla, API y PDF mostraban cierres distintos: la API omitia tarjeta,
    descuentos y anulaciones; la pantalla tampoco mostraba tarjeta ni cobros de
    cartera; el PDF si los incluia. Dos representaciones del mismo dia no se
    podian reconciliar. Ahora las tres consumen esta funcion.
    """
    flujo = (
        cierre.total_efectivo
        + cierre.total_transferencia
        + cierre.total_tarjeta
        + cierre.total_cobros_cxc
    )
    return {
        'id': cierre.id,
        'fecha': cierre.fecha.isoformat(),
        'sucursal': cierre.sucursal.nombre if cierre.sucursal_id else None,
        'estado': cierre.estado,
        'version': cierre.version,
        'fecha_calculo': cierre.fecha_calculo.isoformat() if cierre.fecha_calculo else None,
        'cantidad_ventas': cierre.cantidad_ventas,
        'total_ventas': str(cierre.total_ventas),
        'total_descuentos': str(cierre.total_descuentos),
        'total_efectivo': str(cierre.total_efectivo),
        'total_transferencia': str(cierre.total_transferencia),
        'total_tarjeta': str(cierre.total_tarjeta),
        'total_cobros_cxc': str(cierre.total_cobros_cxc),
        'total_flujo': str(flujo),
        'cantidad_anulaciones': cierre.cantidad_anulaciones,
        'total_anulaciones': str(cierre.total_anulaciones),
        'resumen_cajeros': cierre.resumen_cajeros or {},
        'arqueo': {
            'turnos_cerrados': cierre.turnos_cerrados,
            'turnos_abiertos': cierre.turnos_abiertos,
            'diferencia': str(cierre.diferencia_arqueo),
            'conciliado': cierre.conciliado,
        },
        'generado_automaticamente': cierre.generado_automaticamente,
        'tiene_pdf': bool(cierre.archivo_pdf),
    }


# ============================================================================
# API: GENERAR CIERRE DE CAJA MANUAL
# ============================================================================

@login_required
def api_cierre_manual(request):
    """
    POST: Genera (o recalcula) el resumen diario de una fecha.
    """
    alcance = alcance_de(request.user)
    if not alcance.permitido:
        return _error('Sin permisos', status=403, codigo='sin_permiso')

    if request.method != 'POST':
        return _error('Metodo no permitido', status=405, codigo='metodo')

    data = _leer_json(request)
    if data is None:
        return _error('JSON invalido en el request.', codigo='json_invalido')

    try:
        fecha_str = data.get('fecha')
        if not fecha_str:
            return _error('Fecha requerida', codigo='fecha_requerida')

        fecha = date.fromisoformat(fecha_str)
        sucursal = _sucursal_del_alcance(alcance, data.get('sucursal_id'))

        cierre = ReporteManager.generar_cierre_diario(
            fecha=fecha,
            generado_automaticamente=False,
            usuario=request.user,
            sucursal=sucursal,
            forzar=bool(data.get('forzar')),
        )
    except FechaFuturaError as exc:
        return _error(str(exc), codigo='fecha_futura')
    except PermissionError as exc:
        return _error(str(exc), status=403, codigo='fuera_de_alcance')
    except CierreFinalizadoError as exc:
        return _error(str(exc), status=409, codigo='cierre_final')
    except ValueError as exc:
        return _error(f'Datos invalidos: {exc}', codigo='datos_invalidos')
    except Exception:
        return _error_interno('api_cierre_manual')

    # El PDF es accesorio: que falle no invalida el resumen, pero el cliente
    # tiene que enterarse. Antes se silenciaba con `except Exception: pass` y
    # la UI declaraba exito sin documento (RPT-016).
    pdf_ok = True
    try:
        ruta = PDFGenerator.generar_cierre_caja(cierre.id)
        if ruta:
            cierre.archivo_pdf = ruta
            cierre.save(update_fields=['archivo_pdf', 'fecha_calculo'])
    except Exception:
        pdf_ok = False
        logger.exception('No se pudo generar el PDF del cierre %s', cierre.id)

    return JsonResponse({
        'success': True,
        'estado_generacion': 'completo' if pdf_ok else 'parcial',
        'advertencias': [] if pdf_ok else ['No se pudo generar el PDF.'],
        'cierre': serializar_cierre(cierre),
    })


# ============================================================================
# API: VENTAS POR PERIODO
# ============================================================================

@login_required
def api_ventas_periodo(request):
    """
    POST: Consulta ventas filtradas por periodo y cajero opcional
    """
    alcance = alcance_de(request.user)
    if not alcance.permitido:
        return _error('Sin permisos', status=403, codigo='sin_permiso')

    if request.method != 'POST':
        return _error('Metodo no permitido', status=405, codigo='metodo')

    data = _leer_json(request)
    if data is None:
        return _error('JSON invalido en el request.', codigo='json_invalido')

    try:
        fecha_inicio, fecha_fin = _periodo(data)
        cajero_id = data.get('cajero_id')

        # Query base, acotada al alcance del usuario.
        ventas_qs = alcance.filtrar(Venta.objects.filter(
            fecha_venta__date__gte=fecha_inicio,
            fecha_venta__date__lte=fecha_fin,
            estado='COMPLETADA'
        ))

        if cajero_id:
            ventas_qs = ventas_qs.filter(usuario_id=cajero_id)

        # Totales generales
        totales = ventas_qs.aggregate(
            cantidad=Count('id'),
            total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField()),
            descuentos=Coalesce(Sum('descuento_total'), Decimal('0.00'), output_field=DecimalField()),
        )

        # Totales por metodo de pago
        pagos = Pago.objects.filter(
            venta__in=ventas_qs
        ).values('metodo').annotate(
            total=Coalesce(Sum('monto'), Decimal('0.00'), output_field=DecimalField()),
            cantidad=Count('id'),
        )

        pagos_resumen = {p['metodo']: {'total': str(p['total']), 'cantidad': p['cantidad']} for p in pagos}

        # Ventas por dia (para grafico)
        ventas_por_dia = ventas_qs.annotate(
            dia=TruncDate('fecha_venta')
        ).values('dia').annotate(
            total=Sum('total'),
            cantidad=Count('id'),
        ).order_by('dia')

        # Ultimas ventas del periodo
        ultimas = ventas_qs.select_related('usuario').order_by('-fecha_venta')[:20]
        ventas_lista = [{
            'numero': v.numero_venta,
            'fecha': v.fecha_venta.strftime('%d/%m/%Y %H:%M'),
            'cajero': v.usuario.get_short_name() if v.usuario else 'N/A',
            'total': str(v.total),
            'descuento': str(v.descuento_total or Decimal('0.00')),
        } for v in ultimas]

        return JsonResponse({
            'success': True,
            'periodo': {
                'fecha_inicio': fecha_inicio.isoformat(),
                'fecha_fin': fecha_fin.isoformat(),
            },
            'totales': {
                'cantidad': totales['cantidad'],
                'total': str(totales['total']),
                'descuentos': str(totales['descuentos']),
            },
            'pagos': pagos_resumen,
            'ventas_por_dia': [{
                'dia': v['dia'].isoformat(),
                'total': str(v['total']),
                'cantidad': v['cantidad'],
            } for v in ventas_por_dia],
            'ventas': ventas_lista,
        })

    except ValueError as exc:
        return _error(f'Datos invalidos: {exc}', codigo='datos_invalidos')
    except Exception:
        return _error_interno('api_ventas_periodo')


# ============================================================================
# API: TOP PRODUCTOS
# ============================================================================

@login_required
def api_top_productos(request):
    """
    POST: Genera ranking de productos mas vendidos
    """
    alcance = alcance_de(request.user)
    if not alcance.permitido:
        return _error('Sin permisos', status=403, codigo='sin_permiso')

    if request.method != 'POST':
        return _error('Metodo no permitido', status=405, codigo='metodo')

    data = _leer_json(request)
    if data is None:
        return _error('JSON invalido en el request.', codigo='json_invalido')

    try:
        fecha_inicio, fecha_fin = _periodo(data)
        limite = int(data.get('limite', 10))
        if limite not in [5, 10, 20]:
            limite = 10

        sucursal = _sucursal_del_alcance(alcance, data.get('sucursal_id'))

        # El snapshot es la UNICA fuente de verdad de la respuesta (RPT-009).
        # Antes la vista calculaba su propio ranking, llamaba al manager y
        # silenciaba su excepcion: el manager fallaba SIEMPRE por un campo
        # inexistente, la tabla quedaba vacia y la respuesta decia success.
        top = ReporteManager.generar_top_productos(
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            limite=limite,
            sucursal=sucursal,
        )

        productos = [{
            'posicion': idx + 1,
            'nombre': t.producto.nombre,
            'sku': t.producto.sku,
            'cantidad': str(t.cantidad_vendida),
            'total': str(t.total_ventas),
            'costo': str(t.costo_total),
            'margen': str(t.margen_promedio),
            'transacciones': t.numero_transacciones,
        } for idx, t in enumerate(top)]

        return JsonResponse({
            'success': True,
            'periodo': {
                'fecha_inicio': fecha_inicio.isoformat(),
                'fecha_fin': fecha_fin.isoformat(),
                'limite': limite,
            },
            'sucursal': sucursal.nombre if sucursal else None,
            'productos': productos,
        })

    except PermissionError as exc:
        return _error(str(exc), status=403, codigo='fuera_de_alcance')
    except ValueError as exc:
        return _error(f'Datos invalidos: {exc}', codigo='datos_invalidos')
    except Exception:
        return _error_interno('api_top_productos')


# ============================================================================
# API: INVENTARIO VALORIZADO
# ============================================================================

@login_required
def api_inventario_valorizado(request):
    """
    POST: Inventario valorizado a una fecha de corte.

    El contrato cambio (RPT-002). Antes el endpoint aceptaba cualquier fecha
    —pasada o futura— y respondia con el stock de AHORA rotulado con esa fecha:
    un corte etiquetado 2020-01-01 mostraba lotes creados esta semana, y
    2099-12-31 se aceptaba y se persistia. Ademas la vista calculaba por su
    cuenta y ADEMAS llamaba al manager, asi que la respuesta y la fila guardada
    para la misma fecha podian decir cosas distintas.

    Ahora hay una sola verdad: el snapshot. Una fecha pasada se reconstruye
    desde el ledger de movimientos; una futura se rechaza.
    """
    alcance = alcance_de(request.user)
    if not alcance.permitido:
        return _error('Sin permisos', status=403, codigo='sin_permiso')

    if request.method != 'POST':
        return _error('Metodo no permitido', status=405, codigo='metodo')

    data = _leer_json(request)
    if data is None:
        return _error('JSON invalido en el request.', codigo='json_invalido')

    try:
        fecha_str = data.get('fecha')
        fecha = date.fromisoformat(fecha_str) if fecha_str else timezone.localdate()
        sucursal = _sucursal_del_alcance(alcance, data.get('sucursal_id'))

        snapshot = ReporteManager.generar_inventario_valorizado(
            fecha=fecha,
            sucursal=sucursal,
            # Un corte de hoy se recalcula: el inventario "actual" cambia todo
            # el dia y devolver el de la manana seria otra etiqueta mentirosa.
            # Un corte pasado es inmutable por definicion.
            recalcular=(fecha >= timezone.localdate()),
        )
    except FechaFuturaError as exc:
        return _error(str(exc), codigo='fecha_futura')
    except PermissionError as exc:
        return _error(str(exc), status=403, codigo='fuera_de_alcance')
    except ValueError as exc:
        return _error(f'Datos invalidos: {exc}', codigo='datos_invalidos')
    except Exception:
        return _error_interno('api_inventario_valorizado')

    productos = snapshot.datos_inventario or []
    total = len(productos)
    mostrados = productos[:MAX_PRODUCTOS_INVENTARIO]

    return JsonResponse({
        'success': True,
        'fecha': snapshot.fecha.isoformat(),
        # Que sustenta la respuesta: id del snapshot y el instante REAL que
        # representa. Dos consultas del mismo snapshot dan lo mismo.
        'snapshot_id': snapshot.id,
        'momento_corte': (
            snapshot.momento_corte.isoformat() if snapshot.momento_corte else None
        ),
        'historico': snapshot.fecha < timezone.localdate(),
        'sucursal': snapshot.sucursal.nombre if snapshot.sucursal_id else None,
        'resumen': {
            'total_productos': snapshot.total_productos,
            'total_unidades': str(snapshot.total_unidades),
            'valor_total': str(snapshot.valor_total_inventario),
        },
        'productos': [{
            'nombre': p['nombre'],
            'sku': p['sku'],
            'cantidad_total': p['cantidad_total'],
            'costo_promedio': p['costo_promedio_fifo'],
            'valor_total': p['valor_total'],
            'lotes': p['lotes'],
        } for p in mostrados],
        # El corte se declara en vez de aplicarse en silencio.
        'productos_ocultos': max(0, total - len(mostrados)),
    })


# ============================================================================
# API: VENTAS POR CAJERO
# ============================================================================

@login_required
def api_ventas_cajero(request):
    """
    POST: Comparativa de ventas entre cajeros
    """
    alcance = alcance_de(request.user)
    if not alcance.permitido:
        return _error('Sin permisos', status=403, codigo='sin_permiso')

    if request.method != 'POST':
        return _error('Metodo no permitido', status=405, codigo='metodo')

    data = _leer_json(request)
    if data is None:
        return _error('JSON invalido en el request.', codigo='json_invalido')

    try:
        fecha_inicio, fecha_fin = _periodo(data)

        ventas_qs = alcance.filtrar(Venta.objects.filter(
            fecha_venta__date__gte=fecha_inicio,
            fecha_venta__date__lte=fecha_fin,
            estado='COMPLETADA'
        ))

        # Agrupar por cajero
        por_cajero = ventas_qs.values(
            'usuario__id',
            'usuario__username',
            'usuario__first_name',
            'usuario__last_name',
        ).annotate(
            cantidad=Count('id'),
            suma_total=Coalesce(Sum('total'), Decimal('0.00'), output_field=DecimalField()),
            descuentos=Coalesce(Sum('descuento_total'), Decimal('0.00'), output_field=DecimalField()),
        ).order_by('-suma_total')

        cajeros_lista = []
        total_general = Decimal('0')

        for c in por_cajero:
            nombre = c['usuario__first_name'] or c['usuario__username']
            if c['usuario__last_name']:
                nombre += f" {c['usuario__last_name']}"

            promedio = (c['suma_total'] / c['cantidad']) if c['cantidad'] > 0 else Decimal('0.00')

            cajeros_lista.append({
                'nombre': nombre.strip(),
                'cantidad': c['cantidad'],
                'total': str(c['suma_total']),
                'promedio': str(promedio.quantize(Decimal('0.01'))),
                'descuentos': str(c['descuentos']),
            })
            total_general += c['suma_total']

        # Calcular porcentajes
        for c in cajeros_lista:
            if total_general > 0:
                pct = (Decimal(c['total']) / total_general * 100).quantize(Decimal('0.1'))
                c['porcentaje'] = str(pct)
            else:
                c['porcentaje'] = '0.0'

        # Desglose por metodo de pago por cajero
        pagos_por_cajero = Pago.objects.filter(
            venta__in=ventas_qs
        ).values(
            'venta__usuario__username',
            'metodo'
        ).annotate(
            total=Sum('monto')
        )

        pagos_desglose = {}
        for p in pagos_por_cajero:
            user = p['venta__usuario__username']
            if user not in pagos_desglose:
                pagos_desglose[user] = {}
            pagos_desglose[user][p['metodo']] = str(p['total'])

        return JsonResponse({
            'success': True,
            'periodo': {
                'fecha_inicio': fecha_inicio.isoformat(),
                'fecha_fin': fecha_fin.isoformat(),
            },
            'total_general': str(total_general),
            'cajeros': cajeros_lista,
            'pagos_desglose': pagos_desglose,
        })

    except ValueError as exc:
        return _error(f'Datos invalidos: {exc}', codigo='datos_invalidos')
    except Exception:
        return _error_interno('api_ventas_cajero')


# ============================================================================
# DESCARGA PDF GENERICO
# ============================================================================

@login_required
def descargar_pdf_cierre(request, cierre_id):
    """
    Descarga el PDF de un resumen diario.

    Esta es la UNICA via para obtener el documento. Los archivos viven fuera de
    `MEDIA_ROOT` (`apps/reportes/almacenamiento`), de modo que el control de
    permiso de aca no se puede rodear pidiendo `/media/...` con una fecha
    adivinada (RPT-001).
    """
    alcance = alcance_de(request.user)
    if not alcance.permitido:
        return _error('Sin permisos', status=403, codigo='sin_permiso')

    cierre = get_object_or_404(
        alcance.filtrar(CierreCaja.objects.all()), id=cierre_id,
    )

    # Un resumen consolidado solo lo baja quien consolida.
    if cierre.sucursal_id is None and not alcance.es_global:
        return _error(
            'El resumen consolidado requiere alcance global.',
            status=403, codigo='fuera_de_alcance',
        )

    ruta = str(cierre.archivo_pdf or '')

    # Un PDF viejo, guardado bajo MEDIA_ROOT por la version anterior, se
    # regenera en la ubicacion privada en vez de servirse desde la publica.
    if not ruta or not es_ruta_privada(ruta) or not os.path.exists(ruta):
        try:
            ruta = PDFGenerator.generar_cierre_caja(cierre.id)
            cierre.archivo_pdf = ruta
            cierre.save(update_fields=['archivo_pdf', 'fecha_calculo'])
        except Exception:
            return _error_interno(f'PDF del cierre {cierre.id}')

    if not os.path.exists(ruta):
        return _error('Archivo PDF no encontrado', status=404, codigo='sin_archivo')

    return FileResponse(
        open(ruta, 'rb'),
        content_type='application/pdf',
        as_attachment=True,
        filename=f"resumen_diario_{cierre.fecha}.pdf"
    )
