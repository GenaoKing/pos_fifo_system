"""
apps/sync/management/commands/verificar_sync.py

Diagnostico de integridad del outbox de sincronizacion. SOLO LECTURA.

Responde la pregunta que hoy nadie hace: "de los hechos de negocio que
ocurrieron en esta sucursal, cuales NO tienen su evento de sync?".

Motivacion (ver docs/BUGS.md BUG-A): la emision de eventos esta gateada por
`SYNC_ENABLED`. Si el servicio web arranca sin esa variable, las ventas se
guardan pero no encolan evento: no hay pendiente que reintentar ni error que
mirar. La venta simplemente no existe para el cloud. Este comando es el
detector.

Uso:
    python manage.py verificar_sync
    python manage.py verificar_sync --dias=30
    python manage.py verificar_sync --json > reporte.json

Fase 0 de docs/ROADMAP_SYNC_CONFIABLE.md. El `--backfill` que repara lo que
esto encuentra llega en la Fase 1.
"""
import json as json_lib
from collections import OrderedDict, defaultdict
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone


# El mapa de hechos vive en apps/sync/registry.py: lo comparten este comando,
# la re-serializacion del push y (mas adelante) la reconciliacion de la Fase 3.
from apps.sync import registry  # noqa: E402


class Command(BaseCommand):
    help = ('Reporta hechos de negocio locales que no tienen evento de sync, '
            'huecos en la numeracion de ventas y salud de la cola. Solo lectura.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--dias', type=int, default=90,
            help='Ventana de analisis en dias hacia atras. Default 90.',
        )
        parser.add_argument(
            '--json', action='store_true',
            help='Emite el reporte como JSON (para automatizar).',
        )
        parser.add_argument(
            '--detalle', action='store_true',
            help='Lista las referencias individuales de los objetos sin evento.',
        )
        parser.add_argument(
            '--backfill', action='store_true',
            help='REPARA: encola los eventos faltantes que este comando detecta.',
        )
        parser.add_argument(
            '--reintentar-descartados', action='store_true',
            help='REPARA: devuelve a la cola los eventos que agotaron reintentos.',
        )
        parser.add_argument(
            '--reserializar', action='store_true',
            help='Con --reintentar-descartados: descarta el payload guardado para '
                 'que se reconstruya con el serializador actual. Necesario cuando '
                 'el evento fallo por algo que el codigo nuevo ya resuelve.',
        )
        parser.add_argument(
            '--purgar-confirmados', type=int, metavar='DIAS',
            help='Borra eventos CONFIRMADO mas viejos que N dias. Nunca toca pendientes.',
        )
        parser.add_argument(
            '--ejecutar', action='store_true',
            help='Aplica las reparaciones. Sin este flag las opciones de reparacion '
                 'solo reportan que harian (dry-run por defecto).',
        )

    def handle(self, *args, **opts):
        self.dias = opts['dias']
        self.detalle = opts['detalle']
        # La ventana arranca a MEDIANOCHE LOCAL, no "hace N dias exactos".
        #
        # Si se corta a mitad de un dia, ese dia queda parcialmente incluido y
        # el detector de huecos reporta un falso positivo: las ventas de la
        # manana caen fuera de la ventana y parece que faltan correlativos.
        # (Paso de verdad: un --dias=90 corrido a las 12:19 dejo fuera una venta
        # de las 12:13 e invento un hueco en Royal Plast.)
        corte = timezone.localdate() - timedelta(days=self.dias)
        self.desde = timezone.make_aware(
            timezone.datetime.combine(corte, timezone.datetime.min.time()),
            timezone.get_current_timezone(),
        )

        reporte = OrderedDict()
        reporte['generado'] = timezone.now().isoformat()
        reporte['ventana_dias'] = self.dias
        reporte['configuracion'] = self._revisar_configuracion()
        reporte['parece_base_cloud'] = self._parece_base_cloud()
        reporte['sin_evento'] = self._revisar_hechos_sin_evento()
        reporte['huecos_numeracion'] = self._revisar_huecos_numeracion()
        reporte['cola'] = self._revisar_cola()
        reporte['cursores'] = self._revisar_cursores()

        if opts['json']:
            self.stdout.write(json_lib.dumps(reporte, indent=2, default=str))
        else:
            self._imprimir(reporte)

        # --- Reparaciones (opt-in). Dry-run salvo que venga --ejecutar.
        self.ejecutar = opts['ejecutar']
        if opts['backfill']:
            self._reparar_backfill()
        if opts['reintentar_descartados']:
            self._reparar_reintentar_descartados(opts['reserializar'])
        if opts['purgar_confirmados']:
            self._purgar_confirmados(opts['purgar_confirmados'])

        return None

    # ------------------------------------------------------------------
    # Reparaciones
    #
    # Reenviar de mas es SEGURO, verificado contra el codigo del cloud:
    # `recibir_eventos` deduplica por hash_payload y ademas cada handler hace
    # short-circuit por clave natural (una venta cuyo numero ya existe se
    # ignora; una CxC cuya venta ya tiene cuenta se ignora).
    # ------------------------------------------------------------------

    def _aviso_dry_run(self):
        if not self.ejecutar:
            self.stdout.write(self.style.WARNING(
                '  (dry-run: nada se escribio. Agregar --ejecutar para aplicar.)'
            ))

    def _reparar_backfill(self):
        """Encola los eventos de los hechos que quedaron sin evento (BUG-A)."""
        w = self.stdout.write
        w('')
        w('=' * 70)
        w('  BACKFILL de eventos faltantes')
        w('=' * 70)

        total_encolados = 0
        total_fallidos = 0

        for clave, pks in getattr(self, '_pks_faltantes', {}).items():
            if not pks:
                continue
            hecho = registry.HECHOS[clave]
            modelo = hecho.modelo()
            w(f'  {clave}: {len(pks)} objeto(s) a re-encolar como {hecho.tipo_evento}')

            if not self.ejecutar:
                continue

            for pk in pks:
                obj = modelo.objects.filter(pk=pk).first()
                if obj is None:
                    total_fallidos += 1
                    continue
                if hecho.emitir(obj) is None:
                    total_fallidos += 1
                    w(self.style.ERROR(f'    pk={pk}: no se pudo encolar'))
                else:
                    total_encolados += 1

        if not any(getattr(self, '_pks_faltantes', {}).values()):
            w(self.style.SUCCESS('  Nada que reparar: no hay hechos sin evento.'))
        elif self.ejecutar:
            w(self.style.SUCCESS(f'  Encolados: {total_encolados}'))
            if total_fallidos:
                w(self.style.ERROR(f'  Fallidos: {total_fallidos}'))
        self._aviso_dry_run()

    def _reparar_reintentar_descartados(self, reserializar=False):
        """Devuelve a la cola los eventos que agotaron reintentos.

        Es la reparacion de BUG-C: las cuentas por cobrar que el cloud rechazaba
        por no poder identificar al cliente quedaron en DESCARTADO. Con el
        resolutor nuevo desplegado, reintentarlas las aplica.
        """
        from apps.sync.models import EventoSync, reactivar_eventos

        w = self.stdout.write
        w('')
        w('=' * 70)
        w('  REINTENTO de eventos descartados')
        w('=' * 70)

        max_retries = getattr(settings, 'SYNC_MAX_RETRIES', 10)
        qs = EventoSync.objects.filter(
            estado__in=['DESCARTADO', 'ERROR'],
            intentos__gte=max_retries,
        )

        por_tipo = {}
        for tipo, in qs.values_list('tipo_evento'):
            por_tipo[tipo] = por_tipo.get(tipo, 0) + 1

        if not por_tipo:
            w(self.style.SUCCESS('  No hay eventos agotados.'))
            return

        for tipo, n in sorted(por_tipo.items()):
            w(f'  {tipo}: {n}')

        if reserializar:
            w(self.style.WARNING(
                '  Se descartara el payload guardado de los eventos '
                'reconstruibles: se rearman con el serializador actual.'
            ))
        else:
            w('  Se reenviara el payload GUARDADO. Si el evento fallo por algo '
              'que el codigo nuevo resuelve, usar --reserializar.')

        if self.ejecutar:
            actualizados = reactivar_eventos(qs, reserializar=reserializar)
            w(self.style.SUCCESS(f'  Devueltos a la cola: {actualizados}'))
        self._aviso_dry_run()

    def _purgar_confirmados(self, dias):
        """Valvula de crecimiento: borra eventos ya confirmados y viejos.

        Solo toca CONFIRMADO. Un pendiente puede ser la unica copia de un hecho
        que el cloud todavia no tiene.
        """
        from apps.sync.models import EventoSync

        w = self.stdout.write
        corte = timezone.now() - timedelta(days=dias)
        qs = EventoSync.objects.filter(estado='CONFIRMADO', created_at__lt=corte)
        n = qs.count()

        w('')
        w('=' * 70)
        w(f'  PURGA de eventos CONFIRMADO anteriores a {corte.date().isoformat()}')
        w('=' * 70)
        w(f'  Candidatos: {n}')

        if self.ejecutar and n:
            borrados, _ = qs.delete()
            w(self.style.SUCCESS(f'  Borrados: {borrados}'))
        self._aviso_dry_run()

    # ------------------------------------------------------------------
    # 1. Configuracion
    # ------------------------------------------------------------------

    def _revisar_configuracion(self):
        from apps.sucursales.models import get_sucursal_actual

        sync_enabled = bool(getattr(settings, 'SYNC_ENABLED', False))
        url = getattr(settings, 'CLOUD_API_URL', '') or ''
        token = getattr(settings, 'CLOUD_API_TOKEN', '') or ''
        codigo = getattr(settings, 'SUCURSAL_CODIGO', '') or ''

        sucursal = get_sucursal_actual()

        alertas = []
        # La bandera roja que explica BUG-A: cloud configurado pero emision apagada.
        if (url or token) and not sync_enabled:
            alertas.append(
                'CRITICO: hay CLOUD_API_URL/CLOUD_API_TOKEN configurados pero '
                'SYNC_ENABLED es false. Las operaciones NO estan encolando '
                'eventos y se pierden en silencio (BUG-A).'
            )
        if sync_enabled and not (url and token):
            alertas.append(
                'SYNC_ENABLED es true pero falta CLOUD_API_URL o CLOUD_API_TOKEN: '
                'los eventos se encolan pero nunca se envian.'
            )
        if not codigo:
            alertas.append(
                'SUCURSAL_CODIGO vacio: los eventos se encolan sin sucursal '
                '(se envian igual, pero se pierde trazabilidad local).'
            )
        elif sucursal is None:
            alertas.append(
                f'SUCURSAL_CODIGO="{codigo}" no corresponde a ninguna Sucursal '
                f'en esta base de datos.'
            )

        return {
            'sync_enabled': sync_enabled,
            'cloud_api_url': url or None,
            'cloud_api_token_definido': bool(token),
            'sucursal_codigo': codigo or None,
            'sucursal_resuelta': str(sucursal) if sucursal else None,
            'alertas': alertas,
        }

    # ------------------------------------------------------------------
    # 2. Hechos sin evento
    # ------------------------------------------------------------------

    def _ids_con_evento(self, tipo):
        from apps.sync.models import EventoSync
        return set(
            EventoSync.objects
            .filter(tipo_evento=tipo, objeto_id_local__isnull=False)
            .values_list('objeto_id_local', flat=True)
        )

    def _faltantes(self, hecho):
        """Devuelve (total, faltantes_por_dia, referencias, pks) para un hecho."""
        qs = hecho.queryset()
        if qs is None:
            return 0, {}, [], []
        qs = qs.filter(**{f'{hecho.campo_fecha}__gte': self.desde})

        con_evento = self._ids_con_evento(hecho.tipo_evento)

        campos = ['pk', hecho.campo_fecha]
        if hecho.campo_ref:
            campos.append(hecho.campo_ref)

        total = 0
        por_dia = defaultdict(int)
        refs = []
        pks = []
        for fila in qs.values(*campos).iterator():
            total += 1
            if fila['pk'] in con_evento:
                continue
            fecha = fila[hecho.campo_fecha]
            if fecha is None:
                continue
            dia = timezone.localtime(fecha).date().isoformat()
            por_dia[dia] += 1
            refs.append(fila.get(hecho.campo_ref) or f'pk={fila["pk"]}')
            pks.append(fila['pk'])

        return total, dict(sorted(por_dia.items())), refs, pks

    def _parece_base_cloud(self):
        """
        Detecta si estamos apuntando a una BD del lado CLOUD en vez de a una
        sucursal. En el cloud, `recibir_eventos` guarda los eventos recibidos
        sin `objeto_id_local` (las PKs son de la sucursal, no del cloud), asi
        que el analisis "sin evento" daria todo como faltante.

        Sirve de guardarrail: el comando esta pensado para correr EN la
        sucursal, que es la fuente de la verdad.
        """
        from apps.sync.models import EventoSync

        total = EventoSync.objects.count()
        if total == 0:
            return False
        con_id = EventoSync.objects.filter(objeto_id_local__isnull=False).count()
        return con_id == 0

    def _revisar_hechos_sin_evento(self):
        resultado = OrderedDict()
        # Guardamos las PKs para que --backfill sepa exactamente que re-encolar.
        self._pks_faltantes = {}

        # Solo los hechos primarios: para un derivado (VENTA_ANULADA, CXC_*)
        # "existe el objeto y no existe su evento" no implica que falte
        # encolarlo. Ver el docstring de apps/sync/registry.py.
        for clave, hecho in registry.hechos_backfilleables().items():
            if hecho.modelo() is None:
                resultado[clave] = {'estado': 'modelo no disponible'}
                continue
            total, por_dia, refs, pks = self._faltantes(hecho)
            self._pks_faltantes[clave] = pks
            resultado[clave] = {
                'tipo_evento': hecho.tipo_evento,
                'total_en_ventana': total,
                'sin_evento': sum(por_dia.values()),
                'por_dia': por_dia,
                'referencias': refs if self.detalle else refs[:10],
            }

        return resultado

    # ------------------------------------------------------------------
    # 3. Huecos en la numeracion de ventas
    # ------------------------------------------------------------------

    def _revisar_huecos_numeracion(self):
        """
        `numero_venta` es correlativo por dia (V-YYYYMMDD-NNNN). Si en un dia
        hay 3 ventas pero el maximo correlativo es 10, faltan 7 -- y como este
        comando corre en la sucursal (la fuente de la verdad), un hueco aqui
        significa que la venta se borro o que el correlativo salto.

        El mismo chequeo corrido contra el cloud detecta lo contrario: ventas
        que existen local pero nunca llegaron. Asi se encontro el hueco de
        Royal Plast del 2026-06-23.
        """
        from apps.ventas.models import Venta

        por_dia = defaultdict(lambda: {'seqs': set(), 'invalidos': 0})

        qs = (Venta.objects
              .filter(fecha_venta__gte=self.desde)
              .values_list('numero_venta', flat=True)
              .iterator())

        for numero in qs:
            partes = (numero or '').split('-')
            if len(partes) != 3 or not partes[2].isdigit():
                por_dia['(formato no reconocido)']['invalidos'] += 1
                continue
            dia = partes[1]
            por_dia[dia]['seqs'].add(int(partes[2]))

        huecos = []
        for dia, datos in sorted(por_dia.items()):
            seqs = datos['seqs']
            if not seqs:
                continue
            maximo = max(seqs)
            faltantes = sorted(set(range(1, maximo + 1)) - seqs)
            if faltantes:
                huecos.append({
                    'dia': dia,
                    'presentes': len(seqs),
                    'maximo': maximo,
                    'faltan': len(faltantes),
                    'numeros': faltantes[:20],
                })

        return huecos

    # ------------------------------------------------------------------
    # 4. Salud de la cola
    # ------------------------------------------------------------------

    def _revisar_cola(self):
        from django.db.models import Count, Min
        from apps.sync.models import EventoSync

        por_estado = dict(
            EventoSync.objects
            .values_list('estado')
            .annotate(n=Count('id'))
            .values_list('estado', 'n')
        )

        max_retries = getattr(settings, 'SYNC_MAX_RETRIES', 10)
        atascados = EventoSync.objects.filter(
            estado__in=['PENDIENTE', 'ERROR'],
            intentos__gte=max_retries,
        ).count()

        mas_viejo = (EventoSync.objects
                     .filter(estado__in=['PENDIENTE', 'ERROR'])
                     .aggregate(f=Min('created_at'))['f'])

        antiguedad_horas = None
        if mas_viejo:
            antiguedad_horas = round(
                (timezone.now() - mas_viejo).total_seconds() / 3600, 1
            )

        return {
            'por_estado': por_estado,
            'atascados_sobre_max_retries': atascados,
            'max_retries': max_retries,
            'pendiente_mas_viejo': mas_viejo.isoformat() if mas_viejo else None,
            'antiguedad_horas': antiguedad_horas,
        }

    # ------------------------------------------------------------------
    # 5. Cursores de pull
    # ------------------------------------------------------------------

    def _revisar_cursores(self):
        """
        Estado de los cursores de pull (cloud -> sucursal).

        Un cursor congelado significa que un registro del portal falla al
        aplicarse localmente y esta frenando la marca de agua. Antes ese
        registro se saltaba en silencio y se perdia para siempre (BUG-B); ahora
        el cursor se detiene, y esto lo hace visible.
        """
        from apps.sync.models import VersionMaestro

        filas = []
        for vm in VersionMaestro.objects.all():
            horas_bloqueado = None
            if vm.bloqueado_desde:
                horas_bloqueado = round(
                    (timezone.now() - vm.bloqueado_desde).total_seconds() / 3600, 1
                )
            filas.append({
                'tabla': vm.tabla,
                'ultima_version': vm.ultima_version.isoformat() if vm.ultima_version else None,
                'ultimo_id': vm.ultimo_id,
                'ultima_sync_exitosa': (
                    vm.ultima_sync_exitosa.isoformat() if vm.ultima_sync_exitosa else None
                ),
                'bloqueado': bool(vm.bloqueado_desde),
                'horas_bloqueado': horas_bloqueado,
                'bloqueado_detalle': vm.bloqueado_detalle or None,
            })
        return filas

    # ------------------------------------------------------------------
    # Salida legible
    # ------------------------------------------------------------------

    def _imprimir(self, r):
        w = self.stdout.write
        ok = self.style.SUCCESS
        warn = self.style.WARNING
        err = self.style.ERROR

        w('')
        w('=' * 70)
        w(f'  VERIFICACION DE SYNC  -  ventana: {r["ventana_dias"]} dias')
        w('=' * 70)

        # --- configuracion
        cfg = r['configuracion']
        w('')
        w('CONFIGURACION')
        w(f'  SYNC_ENABLED:      {cfg["sync_enabled"]}')
        w(f'  CLOUD_API_URL:     {cfg["cloud_api_url"] or "(no definido)"}')
        w(f'  CLOUD_API_TOKEN:   {"definido" if cfg["cloud_api_token_definido"] else "(no definido)"}')
        w(f'  SUCURSAL_CODIGO:   {cfg["sucursal_codigo"] or "(no definido)"}')
        w(f'  Sucursal resuelta: {cfg["sucursal_resuelta"] or "(ninguna)"}')
        for alerta in cfg['alertas']:
            estilo = err if alerta.startswith('CRITICO') else warn
            w(estilo(f'  ! {alerta}'))
        if not cfg['alertas']:
            w(ok('  OK: configuracion coherente.'))

        # --- hechos sin evento
        w('')
        w('HECHOS DE NEGOCIO SIN EVENTO DE SYNC')
        if r['parece_base_cloud']:
            w(warn('  AVISO: esta base parece ser del lado CLOUD (ningun evento '
                   'tiene objeto_id_local).'))
            w(warn('  El analisis "sin evento" NO aplica aqui: corre este comando '
                   'EN la sucursal.'))
            w(warn('  Los huecos de numeracion de abajo si son validos.'))
        total_sin_evento = 0
        for clave, datos in r['sin_evento'].items():
            if 'estado' in datos:
                w(f'  {clave:<20} {datos["estado"]}')
                continue
            n = datos['sin_evento']
            total_sin_evento += n
            linea = f'  {clave:<20} {n:>5} sin evento  de {datos["total_en_ventana"]:>5} en ventana'
            w(err(linea) if n else ok(linea))
            if n and datos['por_dia']:
                for dia, cantidad in datos['por_dia'].items():
                    w(f'        {dia}: {cantidad}')
                if datos['referencias']:
                    muestra = ', '.join(str(x) for x in datos['referencias'])
                    sufijo = '' if self.detalle else ' (usar --detalle para la lista completa)'
                    w(f'        refs: {muestra}{sufijo}')

        if total_sin_evento == 0:
            w(ok('  OK: todo hecho de negocio en la ventana tiene su evento.'))

        # --- huecos
        w('')
        w('HUECOS EN LA NUMERACION DE VENTAS (local)')
        if not r['huecos_numeracion']:
            w(ok('  OK: sin huecos.'))
        else:
            for h in r['huecos_numeracion']:
                w(warn(f'  {h["dia"]}: presentes {h["presentes"]}, maximo {h["maximo"]}, '
                       f'faltan {h["faltan"]} -> {h["numeros"]}'))

        # --- cola
        cola = r['cola']
        w('')
        w('SALUD DE LA COLA DE EVENTOS')
        if not cola['por_estado']:
            w('  (cola vacia)')
        for estado, n in sorted(cola['por_estado'].items()):
            w(f'  {estado:<14} {n}')
        if cola['atascados_sobre_max_retries']:
            w(err(f'  ! {cola["atascados_sobre_max_retries"]} evento(s) superaron '
                  f'max_retries={cola["max_retries"]} y ya no se reintentan.'))
        if cola['antiguedad_horas'] is not None:
            linea = f'  Pendiente mas viejo: {cola["antiguedad_horas"]} horas'
            w(warn(linea) if cola['antiguedad_horas'] > 24 else linea)

        # --- cursores de pull
        w('')
        w('CURSORES DE PULL (cloud -> sucursal)')
        if not r['cursores']:
            w('  (sin cursores todavia)')
        for c in r['cursores']:
            base = f'  {c["tabla"]:<16} hasta {c["ultima_version"] or "nunca"}'
            if c['bloqueado']:
                w(err(f'{base}  BLOQUEADO hace {c["horas_bloqueado"]} h'))
                w(err(f'        {c["bloqueado_detalle"]}'))
            else:
                w(base)

        w('')
        w('=' * 70)
        if r['parece_base_cloud']:
            n_huecos = sum(h['faltan'] for h in r['huecos_numeracion'])
            w(warn(f'  RESULTADO (BD cloud): {n_huecos} venta(s) ausente(s) segun '
                   f'la numeracion. Conteo "sin evento" no aplicable.'))
        elif total_sin_evento or any(a.startswith('CRITICO') for a in cfg['alertas']):
            w(err(f'  RESULTADO: {total_sin_evento} objeto(s) sin evento. '
                  f'Reparable con --backfill (Fase 1).'))
        else:
            w(ok('  RESULTADO: sin perdida detectada.'))
        w('=' * 70)
        w('')
