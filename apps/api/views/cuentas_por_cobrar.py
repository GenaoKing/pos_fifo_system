"""
apps/api/views/cuentas_por_cobrar.py
ViewSet de LECTURA de la cartera (cuentas por cobrar) para el portal cloud.

B15 / sub-fase 5.H. Sigue el patron canonico de ProductoViewSet pero
SOLO lectura: la cartera nace y se modifica en el POS de la sucursal y se
replica al cloud por eventos de sync. El portal nunca escribe CxC.

Contrato (frontend centraliza la ruta en src/lib/cxc.ts -> BASE_PATH):

    GET /api/v1/cuentas-por-cobrar/            lista paginada
        ?search=    cubre numero_venta / cliente.nombre / cliente.cedula_rnc
        ?estado=    filtra por estado almacenado (ABIERTA|PARCIAL|...)
        ?vencidas=  true -> solo cuentas abiertas con fecha_limite < hoy
        ?page= &page_size=
    GET /api/v1/cuentas-por-cobrar/<id>/        detalle + cuotas[] + pagos[]
    GET /api/v1/cuentas-por-cobrar/resumen/     agregados de toda la cartera
    GET /api/v1/cuentas-por-cobrar/aging/       buckets por cuota vencida (?sucursal=<codigo>)
    GET /api/v1/cuentas-por-cobrar/cartera_clientes/   saldo agrupado por cliente (paginado)
    GET /api/v1/cuentas-por-cobrar/cobros/      abonos por sucursal o cajero (?desde= &hasta= &agrupar=)
    GET /api/v1/cuentas-por-cobrar/proximos_vencimientos/  cuotas que vencen en ?dias= (default 7)
"""

from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.cuentas_por_cobrar.models import CuentaPorCobrar, CuotaCxC, PagoCxC
from apps.negocios.utils import resolver_negocio

from ..pagination import StandardPagination
from ..permissions import PuedeLeerMaestro, requiere_modulo
from ..serializers.cuentas_por_cobrar import (
    CuentaPorCobrarDetalleSerializer,
    CuentaPorCobrarSerializer,
)

# Estados con saldo "vivo" (cuentan para cartera/vencido). PAGADA y ANULADA
# quedan fuera. Coincide con CuentaPorCobrar.esta_abierta.
ESTADOS_ABIERTOS = (
    CuentaPorCobrar.ESTADO_ABIERTA,
    CuentaPorCobrar.ESTADO_PARCIAL,
    CuentaPorCobrar.ESTADO_VENCIDA,
)


def _scope_por_tenant(qs, request, *, prefijo=''):
    """Acota el queryset al tenant del request (aislamiento multi-negocio).

    - Token de servicio de sucursal (sync) -> esa sucursal.
    - Usuario con negocio activo            -> su negocio.
    - Principal global sin selector         -> sin filtro.
    - Cualquier fallo de resolucion         -> queryset VACIO.

    Ese ultimo caso es el hallazgo NEG-001: la version anterior devolvia el
    queryset sin filtro cuando el resolver no podia resolver el tenant, asi que
    un usuario huerfano o un `?negocio=` invalido veian la cartera de todos los
    negocios.

    `prefijo` ajusta el path del FK a Sucursal: '' para CuentaPorCobrar (filtra
    `sucursal`), 'cuenta__' para CuotaCxC/PagoCxC (filtra `cuenta__sucursal`).
    """
    sucursal = getattr(getattr(request, 'auth', None), 'sucursal', None)
    if sucursal is not None:
        return qs.filter(**{f'{prefijo}sucursal': sucursal})
    return resolver_negocio(request).filtrar(
        qs, campo=f'{prefijo}sucursal__negocio',
    )


class CuentaPorCobrarViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Cartera de cuentas por cobrar (solo lectura) para el portal.

    Lecturas (list, retrieve, resumen): autenticado + solo lectura, mismo
    patron que los maestros. Sin endpoints de escritura en v1.
    """

    pagination_class = StandardPagination
    throttle_scope = 'maestros'
    # Lectura: token de servicio de sucursal (sync) o permiso 'cuentas_por_cobrar.ver'.
    permiso_base = 'cuentas_por_cobrar'

    def get_permissions(self):
        # Modulo (suscripcion del tenant) + lectura granular: token de sucursal
        # (sync) o permiso 'cuentas_por_cobrar.ver' (PuedeLeerMaestro usa permiso_base).
        return [
            IsAuthenticated(),
            requiere_modulo('cuentas_por_cobrar')(),
            PuedeLeerMaestro(),
        ]

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return CuentaPorCobrarDetalleSerializer
        return CuentaPorCobrarSerializer

    def get_queryset(self):
        queryset = CuentaPorCobrar.objects.select_related(
            'cliente', 'venta', 'metodo_plazo', 'sucursal'
        )

        # Aislamiento multi-tenant: aplica antes de los filtros de query y cubre
        # tanto list como retrieve (un pk de otro negocio/sucursal -> 404).
        queryset = _scope_por_tenant(queryset, self.request)

        # El detalle serializa cuotas + abonos: prefetch solo cuando hace falta.
        if self.action == 'retrieve':
            queryset = queryset.prefetch_related('cuotas', 'pagos_cxc__registrado_por')

        # Los filtros solo aplican al listado; en retrieve se usa el lookup por pk.
        if self.action != 'list':
            return queryset

        estado = self.request.query_params.get('estado')
        if estado:
            queryset = queryset.filter(estado=estado.upper())

        vencidas = self.request.query_params.get('vencidas')
        if vencidas is not None and vencidas.lower() == 'true':
            queryset = queryset.filter(
                estado__in=ESTADOS_ABIERTOS,
                fecha_limite__lt=timezone.localdate(),
            )

        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(venta__numero_venta__icontains=search)
                | Q(cliente__nombre__icontains=search)
                | Q(cliente__cedula_rnc__icontains=search)
            )

        return queryset

    @action(detail=False, methods=['get'])
    def resumen(self, request):
        """
        Agregados de TODA la cartera (no de la pagina actual).

        Una sola consulta de agregacion condicional:
          - cartera_total / cuentas_abiertas / clientes_con_saldo: cuentas con
            saldo vivo (ESTADOS_ABIERTOS).
          - saldo_vencido / cuentas_vencidas: subconjunto con fecha_limite < hoy.
        """
        hoy = timezone.localdate()
        abiertas_q = Q(estado__in=ESTADOS_ABIERTOS)
        vencidas_q = abiertas_q & Q(fecha_limite__lt=hoy)

        base = _scope_por_tenant(CuentaPorCobrar.objects.all(), request)
        datos = base.aggregate(
            cartera_total=Sum('saldo', filter=abiertas_q),
            saldo_vencido=Sum('saldo', filter=vencidas_q),
            cuentas_abiertas=Count('id', filter=abiertas_q),
            cuentas_vencidas=Count('id', filter=vencidas_q),
            clientes_con_saldo=Count('cliente', filter=abiertas_q, distinct=True),
        )

        return Response({
            'cartera_total': datos['cartera_total'] or 0,
            'saldo_vencido': datos['saldo_vencido'] or 0,
            'cuentas_abiertas': datos['cuentas_abiertas'] or 0,
            'cuentas_vencidas': datos['cuentas_vencidas'] or 0,
            'clientes_con_saldo': datos['clientes_con_saldo'] or 0,
        })

    @action(detail=False, methods=['get'])
    def aging(self, request):
        """
        Aging de cartera por buckets, calculado POR CUOTA vencida (no por
        fecha limite de la cuenta): una cuenta con una sola cuota atrasada
        reporta solo esa cuota como vencida.

        Buckets de dias vencidos: al_dia (sin vencer), 0-30, 31-60, 61-90, 90+.
        Filtro opcional ?sucursal=<codigo>.
        """
        hoy = timezone.localdate()
        d30, d60, d90 = (hoy - timedelta(days=n) for n in (30, 60, 90))

        cuotas = CuotaCxC.objects.filter(
            saldo__gt=0,
            cuenta__estado__in=ESTADOS_ABIERTOS,
        ).exclude(estado=CuotaCxC.ESTADO_ANULADA)
        cuotas = _scope_por_tenant(cuotas, request, prefijo='cuenta__')

        # ?sucursal= acota DENTRO del scope del tenant; nunca lo amplia.
        sucursal = request.query_params.get('sucursal')
        if sucursal:
            cuotas = cuotas.filter(cuenta__sucursal__codigo=sucursal)

        datos = cuotas.aggregate(
            al_dia=Sum('saldo', filter=Q(fecha_vencimiento__gte=hoy)),
            b_0_30=Sum('saldo', filter=Q(fecha_vencimiento__lt=hoy, fecha_vencimiento__gte=d30)),
            b_31_60=Sum('saldo', filter=Q(fecha_vencimiento__lt=d30, fecha_vencimiento__gte=d60)),
            b_61_90=Sum('saldo', filter=Q(fecha_vencimiento__lt=d60, fecha_vencimiento__gte=d90)),
            b_90_mas=Sum('saldo', filter=Q(fecha_vencimiento__lt=d90)),
            total=Sum('saldo'),
        )

        return Response({
            'fecha_corte': hoy.isoformat(),
            'al_dia': datos['al_dia'] or 0,
            'b_0_30': datos['b_0_30'] or 0,
            'b_31_60': datos['b_31_60'] or 0,
            'b_61_90': datos['b_61_90'] or 0,
            'b_90_mas': datos['b_90_mas'] or 0,
            'total': datos['total'] or 0,
        })

    @action(detail=False, methods=['get'])
    def cartera_clientes(self, request):
        """
        Cartera agrupada por cliente (cuentas con saldo vivo), ordenada por
        saldo descendente. Paginada con el mismo StandardPagination del listado.
        """
        hoy = timezone.localdate()
        base = _scope_por_tenant(CuentaPorCobrar.objects.all(), request)
        filas = (
            base
            .filter(estado__in=ESTADOS_ABIERTOS)
            .values('cliente_id', 'cliente__nombre', 'cliente__cedula_rnc')
            .annotate(
                saldo_total=Sum('saldo'),
                cuentas=Count('id'),
                saldo_vencido=Sum('saldo', filter=Q(fecha_limite__lt=hoy)),
            )
            .order_by('-saldo_total')
        )

        resultados = [
            {
                'cliente_id': fila['cliente_id'],
                'cliente_nombre': fila['cliente__nombre'],
                'cliente_cedula_rnc': fila['cliente__cedula_rnc'] or '',
                'saldo_total': fila['saldo_total'] or 0,
                'cuentas': fila['cuentas'],
                'saldo_vencido': fila['saldo_vencido'] or 0,
            }
            for fila in filas
        ]

        page = self.paginate_queryset(resultados)
        if page is not None:
            return self.get_paginated_response(page)
        return Response(resultados)

    @action(detail=False, methods=['get'])
    def cobros(self, request):
        """
        Abonos CxC APLICADOS agrupados por sucursal (default) o cajero.

        ?desde=YYYY-MM-DD &hasta=YYYY-MM-DD (default: mes actual)
        ?agrupar=sucursal|cajero
        """
        hoy = timezone.localdate()
        desde = parse_date(request.query_params.get('desde') or '') or hoy.replace(day=1)
        hasta = parse_date(request.query_params.get('hasta') or '') or hoy
        agrupar = request.query_params.get('agrupar', 'sucursal')

        pagos = PagoCxC.objects.filter(
            estado=PagoCxC.ESTADO_APLICADO,
            fecha_pago__date__gte=desde,
            fecha_pago__date__lte=hasta,
        )
        pagos = _scope_por_tenant(pagos, request, prefijo='cuenta__')

        if agrupar == 'cajero':
            filas = (
                pagos.values('registrado_por_id', 'registrado_por__username')
                .annotate(total=Sum('monto'), cantidad=Count('id'))
                .order_by('-total')
            )
            resultados = [
                {
                    'clave': fila['registrado_por__username'] or '-',
                    'total': fila['total'] or 0,
                    'cantidad': fila['cantidad'],
                }
                for fila in filas
            ]
        else:
            filas = (
                pagos.values('cuenta__sucursal__codigo', 'cuenta__sucursal__nombre')
                .annotate(total=Sum('monto'), cantidad=Count('id'))
                .order_by('-total')
            )
            resultados = [
                {
                    'clave': fila['cuenta__sucursal__codigo'] or 'Sin sucursal',
                    'nombre': fila['cuenta__sucursal__nombre'] or '',
                    'total': fila['total'] or 0,
                    'cantidad': fila['cantidad'],
                }
                for fila in filas
            ]

        return Response({
            'desde': desde.isoformat(),
            'hasta': hasta.isoformat(),
            'agrupar': agrupar if agrupar in ('sucursal', 'cajero') else 'sucursal',
            'resultados': resultados,
        })

    @action(detail=False, methods=['get'])
    def proximos_vencimientos(self, request):
        """
        Cuotas con saldo que vencen entre hoy y hoy + ?dias= (default 7,
        max 90). Alertas de cobro para el portal; limitado a 100 filas.
        """
        try:
            dias = min(max(int(request.query_params.get('dias', 7)), 1), 90)
        except (TypeError, ValueError):
            dias = 7

        hoy = timezone.localdate()
        limite = hoy + timedelta(days=dias)

        cuotas = (
            _scope_por_tenant(CuotaCxC.objects.all(), request, prefijo='cuenta__')
            .filter(
                saldo__gt=0,
                cuenta__estado__in=ESTADOS_ABIERTOS,
                fecha_vencimiento__gte=hoy,
                fecha_vencimiento__lte=limite,
            )
            .exclude(estado=CuotaCxC.ESTADO_ANULADA)
            .select_related('cuenta__cliente', 'cuenta__venta', 'cuenta__sucursal')
            .order_by('fecha_vencimiento', 'cuenta_id', 'numero')[:100]
        )

        return Response({
            'dias': dias,
            'resultados': [
                {
                    'cuenta_id': cuota.cuenta_id,
                    'numero_venta': cuota.cuenta.venta.numero_venta,
                    'cliente_nombre': cuota.cuenta.cliente.nombre,
                    'sucursal_codigo': cuota.cuenta.sucursal.codigo if cuota.cuenta.sucursal_id else None,
                    'cuota_numero': cuota.numero,
                    'fecha_vencimiento': cuota.fecha_vencimiento.isoformat(),
                    'saldo': cuota.saldo,
                    'dias_restantes': (cuota.fecha_vencimiento - hoy).days,
                }
                for cuota in cuotas
            ],
        })
