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
"""

from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.cuentas_por_cobrar.models import CuentaPorCobrar

from ..pagination import StandardPagination
from ..permissions import EsSoloLectura
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


class CuentaPorCobrarViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Cartera de cuentas por cobrar (solo lectura) para el portal.

    Lecturas (list, retrieve, resumen): autenticado + solo lectura, mismo
    patron que los maestros. Sin endpoints de escritura en v1.
    """

    pagination_class = StandardPagination
    throttle_scope = 'maestros'

    def get_permissions(self):
        return [IsAuthenticated(), EsSoloLectura()]

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return CuentaPorCobrarDetalleSerializer
        return CuentaPorCobrarSerializer

    def get_queryset(self):
        queryset = CuentaPorCobrar.objects.select_related(
            'cliente', 'venta', 'metodo_plazo', 'sucursal'
        )

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

        datos = CuentaPorCobrar.objects.aggregate(
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
