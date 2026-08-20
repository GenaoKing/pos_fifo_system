"""
apps/sync/registry.py

Registro unico que mapea cada tipo de evento con el modelo local que lo origina
y la funcion que lo serializa.

Existe porque tres cosas distintas necesitan la misma informacion y no deben
mantener copias divergentes:

    1. `SyncEngine.push_eventos`  -> re-serializa los eventos SIN_PAYLOAD antes
                                     de enviarlos.
    2. `verificar_sync`           -> detecta hechos de negocio sin evento y los
                                     re-encola con --backfill.
    3. Fase 3 (anti-entropia)     -> comparara conteos por entidad contra el
                                     cloud.

Regla de diseno: los tipos que NO tienen un objeto local propio (hoy solo
`INVENTARIO_SNAPSHOT`, que es una foto de estado y no un hecho discreto) NO
entran en el registro. No se pueden re-serializar desde una PK ni tiene sentido
hacerles backfill: el siguiente snapshot reemplaza al anterior.
"""
from collections import OrderedDict


class HechoSync:
    """
    Describe un hecho de negocio replicable.

    modelo_ruta   'app.models.Clase' (import diferido: el registro se importa
                  antes que las apps esten listas).
    tipo_evento   codigo en apps/sync/constants.py
    campo_fecha   campo temporal con el que se filtra la ventana de analisis
    campo_ref     campo legible para el reporte (numero_venta, etc.) o None
    serializador  nombre de la funcion en apps/sync/serializers.py
    emisor        nombre del helper en apps/sync/events.py (para el backfill)
    filtro_extra  filtro adicional del queryset (ej: turnos ya cerrados)
    """

    def __init__(self, clave, modelo_ruta, tipo_evento, campo_fecha,
                 serializador, emisor, campo_ref=None, filtro_extra=None):
        self.clave = clave
        self.modelo_ruta = modelo_ruta
        self.tipo_evento = tipo_evento
        self.campo_fecha = campo_fecha
        self.serializador = serializador
        self.emisor = emisor
        self.campo_ref = campo_ref
        self.filtro_extra = filtro_extra or {}

    # -- resolucion diferida -------------------------------------------------

    def modelo(self):
        """Importa y devuelve la clase del modelo, o None si no esta disponible."""
        modulo, _, clase = self.modelo_ruta.rpartition('.')
        try:
            mod = __import__(modulo, fromlist=[clase])
            return getattr(mod, clase)
        except (ImportError, AttributeError):
            return None

    def serializar(self, obj):
        """Serializa una instancia con la funcion declarada."""
        from apps.sync import serializers
        return getattr(serializers, self.serializador)(obj)

    def emitir(self, obj):
        """Encola el evento usando el helper publico de events.py."""
        from apps.sync import events
        return getattr(events, self.emisor)(obj)

    def queryset(self):
        modelo = self.modelo()
        if modelo is None:
            return None
        qs = modelo.objects.all()
        if self.filtro_extra:
            qs = qs.filter(**self.filtro_extra)
        return qs

    def __repr__(self):
        return f'<HechoSync {self.tipo_evento}>'


# Orden intencional: el mismo en que conviene reportarlos y re-enviarlos.
# Las ventas van primero porque otros hechos dependen de ellas en el cloud
# (una CxC se rechaza si su venta todavia no llego).
HECHOS = OrderedDict()


def _registrar(hecho):
    HECHOS[hecho.clave] = hecho
    return hecho


_registrar(HechoSync(
    clave='ventas',
    modelo_ruta='apps.ventas.models.Venta',
    tipo_evento='VENTA_CREADA',
    campo_fecha='fecha_venta',
    campo_ref='numero_venta',
    serializador='serializar_venta',
    emisor='evento_venta_creada',
))

_registrar(HechoSync(
    clave='aperturas_caja',
    modelo_ruta='apps.caja.models.TurnoCaja',
    tipo_evento='APERTURA_CAJA',
    campo_fecha='fecha_apertura',
    serializador='serializar_apertura_caja',
    emisor='evento_apertura_caja',
))

_registrar(HechoSync(
    clave='cierres_caja',
    modelo_ruta='apps.caja.models.TurnoCaja',
    tipo_evento='CIERRE_CAJA',
    campo_fecha='fecha_cierre',
    serializador='serializar_cierre_caja',
    emisor='evento_cierre_caja',
    # Solo los turnos efectivamente cerrados generan CIERRE_CAJA.
    filtro_extra={'fecha_cierre__isnull': False},
))

_registrar(HechoSync(
    clave='movimientos_caja',
    modelo_ruta='apps.caja.models.MovimientoCaja',
    tipo_evento='MOVIMIENTO_CAJA',
    campo_fecha='fecha',
    serializador='serializar_movimiento_caja',
    emisor='evento_movimiento_caja',
))

_registrar(HechoSync(
    clave='compras',
    modelo_ruta='apps.inventario.models.Compra',
    tipo_evento='COMPRA_REGISTRADA',
    campo_fecha='fecha_compra',
    campo_ref='numero_compra',
    serializador='serializar_compra',
    emisor='evento_compra_registrada',
))

_registrar(HechoSync(
    clave='ajustes_inventario',
    modelo_ruta='apps.inventario.models.AjusteInventario',
    tipo_evento='AJUSTE_INVENTARIO',
    campo_fecha='fecha_ajuste',
    serializador='serializar_ajuste_inventario',
    emisor='evento_ajuste_inventario',
))

_registrar(HechoSync(
    clave='cotizaciones',
    modelo_ruta='apps.cotizaciones.models.Cotizacion',
    tipo_evento='COTIZACION_CREADA',
    campo_fecha='fecha_creacion',
    campo_ref='numero_cotizacion',
    serializador='serializar_cotizacion',
    emisor='evento_cotizacion_creada',
))


def por_tipo(tipo_evento):
    """Devuelve el HechoSync de un tipo de evento, o None si no esta registrado.

    Que devuelva None es normal y esperado: los tipos derivados
    (INVENTARIO_SNAPSHOT, VENTA_ANULADA, CXC_*) no se re-serializan desde una PK
    con esta tabla. Quien llame debe tratar el None como "no re-serializable".
    """
    for hecho in HECHOS.values():
        if hecho.tipo_evento == tipo_evento:
            return hecho
    return None
