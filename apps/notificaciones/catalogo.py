from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db.models import Count, Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime


CAJA_APERTURA = 'caja.apertura'
CAJA_CIERRE = 'caja.cierre'
CAJA_RETIRO = 'caja.retiro'
CAJA_GASTO = 'caja.gasto'
CAJA_INGRESO = 'caja.ingreso'


@dataclass(frozen=True)
class DefinicionEvento:
    codigo: str
    nombre: str
    descripcion: str
    categoria: str
    parametros: tuple[dict, ...] = ()


PARAM_MONTO_MINIMO = {
    'clave': 'monto_minimo',
    'nombre': 'Monto minimo',
    'tipo': 'dinero',
    'default': '0.01',
}
PARAM_UMBRAL_DIFERENCIA = {
    'clave': 'umbral_diferencia',
    'nombre': 'Alertar diferencia mayor a',
    'tipo': 'dinero',
    'default': '0.00',
}

DEFINICIONES = {
    CAJA_APERTURA: DefinicionEvento(
        CAJA_APERTURA, 'Apertura de caja',
        'Avisa quien abrio la caja y con cuanto efectivo.', 'caja',
    ),
    CAJA_CIERRE: DefinicionEvento(
        CAJA_CIERRE, 'Cierre y cuadre de caja',
        'Resume ventas, cobros, efectivo esperado, contado y diferencia.',
        'caja', (PARAM_UMBRAL_DIFERENCIA,),
    ),
    CAJA_RETIRO: DefinicionEvento(
        CAJA_RETIRO, 'Retiro de caja',
        'Avisa retiros iguales o mayores al monto configurado.',
        'caja', (PARAM_MONTO_MINIMO,),
    ),
    CAJA_GASTO: DefinicionEvento(
        CAJA_GASTO, 'Gasto de caja',
        'Avisa gastos iguales o mayores al monto configurado.',
        'caja', (PARAM_MONTO_MINIMO,),
    ),
    CAJA_INGRESO: DefinicionEvento(
        CAJA_INGRESO, 'Ingreso de caja',
        'Avisa ingresos iguales o mayores al monto configurado.',
        'caja', (PARAM_MONTO_MINIMO,),
    ),
}

TIPOS_SYNC_RELEVANTES = ('APERTURA_CAJA', 'CIERRE_CAJA', 'MOVIMIENTO_CAJA')
MOVIMIENTO_A_EVENTO = {
    'RETIRO': CAJA_RETIRO,
    'GASTO': CAJA_GASTO,
    'INGRESO': CAJA_INGRESO,
}


def catalogo_publico():
    return [
        {
            'codigo': definicion.codigo,
            'nombre': definicion.nombre,
            'descripcion': definicion.descripcion,
            'categoria': definicion.categoria,
            'parametros': list(definicion.parametros),
        }
        for definicion in DEFINICIONES.values()
    ]


def tipo_desde_evento_sync(evento_sync):
    if evento_sync.tipo_evento == 'APERTURA_CAJA':
        return CAJA_APERTURA
    if evento_sync.tipo_evento == 'CIERRE_CAJA':
        return CAJA_CIERRE
    if evento_sync.tipo_evento == 'MOVIMIENTO_CAJA':
        return MOVIMIENTO_A_EVENTO.get((evento_sync.payload or {}).get('tipo'))
    return None


def _decimal(valor, default='0.00'):
    try:
        return Decimal(str(default if valor in (None, '') else valor))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def normalizar_parametros(tipo_evento, parametros):
    definicion = DEFINICIONES.get(tipo_evento)
    if definicion is None:
        raise ValueError('Tipo de evento de notificacion desconocido.')
    parametros = parametros or {}
    permitidos = {p['clave']: p for p in definicion.parametros}
    extras = set(parametros) - set(permitidos)
    if extras:
        raise ValueError(f'Parametros no permitidos: {", ".join(sorted(extras))}.')

    salida = {}
    for clave, metadata in permitidos.items():
        valor = parametros.get(clave, metadata['default'])
        numero = _decimal(valor, metadata['default'])
        if numero < 0:
            raise ValueError(f'{metadata["nombre"]} no puede ser negativo.')
        salida[clave] = str(numero.quantize(Decimal('0.01')))
    return salida


def regla_aplica(tipo_evento, parametros, datos):
    parametros = normalizar_parametros(tipo_evento, parametros)
    if tipo_evento in (CAJA_RETIRO, CAJA_GASTO, CAJA_INGRESO):
        return _decimal(datos.get('monto')) >= _decimal(parametros['monto_minimo'])
    return True


def nivel_para(tipo_evento, parametros, datos):
    if tipo_evento != CAJA_CIERRE:
        return 'NORMAL'
    parametros = normalizar_parametros(tipo_evento, parametros)
    diferencia = abs(_decimal(datos.get('diferencia')))
    umbral = _decimal(parametros['umbral_diferencia'])
    return 'ALERTA' if diferencia > umbral else 'NORMAL'


def _fecha(valor, fallback=None):
    if isinstance(valor, datetime):
        resultado = valor
    else:
        resultado = parse_datetime(str(valor)) if valor else None
    resultado = resultado or fallback or timezone.now()
    if timezone.is_naive(resultado):
        resultado = timezone.make_aware(resultado, timezone.get_current_timezone())
    return resultado


def _dinero(valor):
    numero = _decimal(valor).quantize(Decimal('0.01'))
    signo = '-' if numero < 0 else ''
    return f'{signo}RD${abs(numero):,.2f}'


def _resumen_cierre_estimado(evento_sync, payload):
    """Fallback transitorio para POS que aun no envia `resumen_turno`."""
    from apps.caja.models import TurnoCaja
    from apps.cuentas_por_cobrar.models import PagoCxC
    from apps.ventas.models import Pago, Venta

    desde = _fecha(payload.get('fecha_apertura'))
    hasta = _fecha(payload.get('fecha_cierre'), fallback=timezone.now())
    username = payload.get('usuario_username')

    # El handler de sync ya materializo el turno y sus movimientos. Preferirlo
    # permite completar retiros/gastos/ingresos; sigue marcado estimado porque
    # pagos legacy en cloud se atribuyen por usuario+ventana, no por FK local.
    turnos = TurnoCaja.objects.filter(
        caja__sucursal=evento_sync.sucursal,
        fecha_apertura=desde,
    )
    origen = payload.get('caja_origen_id')
    if origen:
        turnos = turnos.filter(caja__origen_id=origen)
    elif payload.get('caja_nombre'):
        turnos = turnos.filter(caja__nombre=payload['caja_nombre'])
    turno = turnos.first()
    if turno is not None:
        resumen = turno.resumen_operativo()
        return {
            **resumen,
            'total_ventas': str(resumen['total_ventas']),
            'pagos_por_metodo': {
                metodo: str(monto)
                for metodo, monto in resumen['pagos_por_metodo'].items()
            },
            'cobros_cxc_total': str(resumen['cobros_cxc_total']),
            'cobros_cxc_por_metodo': {
                metodo: str(monto)
                for metodo, monto in resumen['cobros_cxc_por_metodo'].items()
            },
            **{
                clave: str(resumen[clave])
                for clave in (
                    'fondo_apertura', 'efectivo_ventas', 'efectivo_cxc',
                    'retiros', 'gastos', 'ingresos', 'esperado', 'contado',
                    'diferencia',
                )
            },
            'fuente_resumen': 'cloud_estimado',
        }

    ventas = Venta.objects.filter(
        sucursal=evento_sync.sucursal,
        fecha_venta__gte=desde,
        fecha_venta__lte=hasta,
        estado='COMPLETADA',
    )
    if username:
        ventas = ventas.filter(usuario__username=username)
    ventas_agg = ventas.aggregate(cantidad=Count('id'), total=Sum('total'))

    pagos = Pago.objects.filter(venta__in=ventas)
    pagos_por_metodo = {
        fila['metodo']: str(fila['total'] or Decimal('0.00'))
        for fila in pagos.values('metodo').annotate(total=Sum('monto'))
    }

    cobros = PagoCxC.objects.filter(
        cuenta__sucursal=evento_sync.sucursal,
        fecha_pago__gte=desde,
        fecha_pago__lte=hasta,
        estado='APLICADO',
    )
    if username:
        cobros = cobros.filter(registrado_por__username=username)
    cobros_por_metodo = {
        fila['metodo']: str(fila['total'] or Decimal('0.00'))
        for fila in cobros.values('metodo').annotate(total=Sum('monto'))
    }
    cobros_total = sum((_decimal(v) for v in cobros_por_metodo.values()), Decimal('0.00'))

    return {
        'cantidad_ventas': ventas_agg['cantidad'] or 0,
        'total_ventas': str(ventas_agg['total'] or Decimal('0.00')),
        'pagos_por_metodo': pagos_por_metodo,
        'cobros_cxc_total': str(cobros_total),
        'cobros_cxc_por_metodo': cobros_por_metodo,
        'fondo_apertura': payload.get('fondo_apertura') or '0.00',
        'efectivo_ventas': pagos_por_metodo.get('EFECTIVO', '0.00'),
        'efectivo_cxc': cobros_por_metodo.get('EFECTIVO', '0.00'),
        'retiros': '0.00',
        'gastos': '0.00',
        'ingresos': '0.00',
        'esperado': payload.get('monto_esperado') or '0.00',
        'contado': payload.get('monto_contado') or '0.00',
        'diferencia': payload.get('diferencia') or '0.00',
        'fuente_resumen': 'cloud_estimado',
    }


def construir_desde_sync(evento_sync):
    tipo = tipo_desde_evento_sync(evento_sync)
    if tipo is None:
        return None
    payload = evento_sync.payload or {}
    sucursal = evento_sync.sucursal
    sucursal_nombre = getattr(sucursal, 'nombre', None) or getattr(sucursal, 'codigo', '')
    caja = payload.get('caja_nombre') or 'Caja'

    if tipo == CAJA_APERTURA:
        cajero = payload.get('usuario_username') or 'Usuario'
        fondo = payload.get('fondo_apertura') or '0.00'
        datos = {
            'caja': caja, 'cajero': cajero, 'sucursal': sucursal_nombre,
            'fondo_apertura': str(fondo),
            'notas': payload.get('notas_apertura') or '',
        }
        return {
            'tipo_evento': tipo,
            'titulo': f'Caja abierta — {caja}',
            'cuerpo': f'{sucursal_nombre}: {cajero} abrio con {_dinero(fondo)}.',
            'datos': datos,
            'ocurrido_en': _fecha(payload.get('fecha_apertura')),
        }

    if tipo == CAJA_CIERRE:
        cajero = payload.get('usuario_username') or 'Usuario'
        resumen = dict(payload.get('resumen_turno') or {})
        if not resumen:
            resumen = _resumen_cierre_estimado(evento_sync, payload)
        resumen.setdefault('fondo_apertura', payload.get('fondo_apertura') or '0.00')
        resumen.setdefault('esperado', payload.get('monto_esperado') or '0.00')
        resumen.setdefault('contado', payload.get('monto_contado') or '0.00')
        resumen.setdefault('diferencia', payload.get('diferencia') or '0.00')
        resumen.setdefault('fuente_resumen', 'pos_snapshot')
        datos = {
            **resumen,
            'caja': caja,
            'cajero': cajero,
            'cerrado_por': payload.get('cerrado_por_username') or cajero,
            'sucursal': sucursal_nombre,
            'notas': payload.get('notas_cierre') or '',
        }
        cuerpo = (
            f'Ventas {_dinero(datos.get("total_ventas"))} '
            f'({int(datos.get("cantidad_ventas") or 0)}) · '
            f'Cobros {_dinero(datos.get("cobros_cxc_total"))} · '
            f'Esperado {_dinero(datos.get("esperado"))} · '
            f'Contado {_dinero(datos.get("contado"))} · '
            f'Dif. {_dinero(datos.get("diferencia"))}'
        )
        return {
            'tipo_evento': tipo,
            'titulo': f'Cierre de caja — {caja}',
            'cuerpo': cuerpo,
            'datos': datos,
            'ocurrido_en': _fecha(payload.get('fecha_cierre')),
        }

    monto = payload.get('monto') or '0.00'
    etiqueta = DEFINICIONES[tipo].nombre
    operador = payload.get('registrado_por_username') or 'Usuario'
    datos = {
        'caja': caja,
        'sucursal': sucursal_nombre,
        'monto': str(monto),
        'descripcion': payload.get('descripcion') or '',
        'registrado_por': operador,
        'autorizado_por': payload.get('autorizado_por_username'),
    }
    detalle = f' · {datos["descripcion"]}' if datos['descripcion'] else ''
    return {
        'tipo_evento': tipo,
        'titulo': f'{etiqueta} — {caja}',
        'cuerpo': f'{sucursal_nombre}: {_dinero(monto)} por {operador}{detalle}',
        'datos': datos,
        'ocurrido_en': _fecha(payload.get('fecha')),
    }
