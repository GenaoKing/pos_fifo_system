"""
apps/configuracion/management/commands/verificar_instalacion.py

Diagnostico de una instalacion local. SOLO LECTURA.

Hermano de `verificar_sync` (que revisa el outbox). Este revisa que la
instalacion en si este sana: configuracion, base de datos, seeds y modulos.

Existe porque los fallos de instalacion de este sistema no se manifiestan como
un error, sino como sintomas raros semanas despues: una impresora que "dejo de
imprimir", una key truncada que nadie noto, un modulo que desaparecio. La idea
es que todo eso se vea en 5 minutos y no en una visita al cliente.

Uso:
    python manage.py verificar_instalacion
    python manage.py verificar_instalacion --json
"""
import json as json_lib
from collections import OrderedDict

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = ('Revisa configuracion, base de datos, seeds y modulos activos de '
            'esta instalacion. Solo lectura.')

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true',
                            help='Emite el reporte como JSON.')

    def handle(self, *args, **opts):
        reporte = OrderedDict()
        reporte['configuracion'] = self._revisar_configuracion()
        reporte['base_datos'] = self._revisar_base_datos()
        reporte['seeds'] = self._revisar_seeds()
        reporte['modulos'] = self._revisar_modulos()

        if opts['json']:
            self.stdout.write(json_lib.dumps(reporte, indent=2, default=str))
        else:
            self._imprimir(reporte)

    # ------------------------------------------------------------------
    # 1. Configuracion
    # ------------------------------------------------------------------

    def _revisar_configuracion(self):
        from config.env_check import validar_entorno

        env_file = getattr(settings, 'POS_ENV_FILE_CARGADO', None)
        problemas = validar_entorno()
        return {
            'archivo_env': str(env_file) if env_file else None,
            'problemas': [
                {'variable': p.variable, 'mensaje': p.mensaje, 'critico': p.critico}
                for p in problemas
            ],
        }

    # ------------------------------------------------------------------
    # 2. Base de datos
    # ------------------------------------------------------------------

    def _revisar_base_datos(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        info = {'conecta': False, 'nombre': None, 'migraciones_pendientes': []}
        try:
            connection.ensure_connection()
            info['conecta'] = True
            info['nombre'] = connection.settings_dict.get('NAME')
        except Exception as exc:
            info['error'] = str(exc)
            return info

        try:
            executor = MigrationExecutor(connection)
            plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
            info['migraciones_pendientes'] = [f'{m.app_label}.{m.name}' for m, _ in plan]
        except Exception as exc:  # pragma: no cover
            info['error_migraciones'] = str(exc)
        return info

    # ------------------------------------------------------------------
    # 3. Seeds
    # ------------------------------------------------------------------

    def _revisar_seeds(self):
        from apps.caja.models import Caja
        from apps.configuracion.models import ConfiguracionNegocio
        from apps.sucursales.models import get_sucursal_actual

        sucursal = get_sucursal_actual()
        config = ConfiguracionNegocio.objects.first()
        return {
            'sucursal_codigo_configurado': getattr(settings, 'SUCURSAL_CODIGO', None),
            'sucursal_resuelta': str(sucursal) if sucursal else None,
            'negocio': (
                str(sucursal.negocio)
                if sucursal is not None and sucursal.negocio_id else None
            ),
            'configuracion_negocio': config.nombre_negocio if config else None,
            'cajas': Caja.objects.count(),
            'usuarios': self._contar_usuarios(),
        }

    @staticmethod
    def _contar_usuarios():
        from django.contrib.auth import get_user_model
        return get_user_model().objects.count()

    # ------------------------------------------------------------------
    # 4. Modulos vendibles  <- la trampa de suscripciones
    # ------------------------------------------------------------------

    def _revisar_modulos(self):
        """
        Reporta el estado de aprovisionamiento del negocio.

        `modulo_activo()` resuelve asi:

            sucursal SIN negocio      -> fail-OPEN: lee el flag legacy de
                                         ConfiguracionNegocio. Todo funciona.
            negocio SIN aprovisionar  -> fail-OPEN tambien (ver docstring de
                                         `apps.suscripciones.engine`): sin
                                         suscripcion activa con plan NI una
                                         sola fila de NegocioModulo, se
                                         resuelve como "todo activo". Ya no
                                         apaga nada -- corrige la trampa que
                                         documentaba BUG-D.
            negocio CON aprovisionar  -> manda la suscripcion + overrides tal
                                         cual se configuraron. Un modulo
                                         puede quedar apagado a proposito.

        Este chequeo sigue senalando el estado "sin aprovisionar" porque el
        fail-open es una red de seguridad, no el estado deseado: sin una
        suscripcion o overrides reales, el negocio no tiene entitlements de
        verdad, solo el default abierto. `roto` ahora solo se enciende si algo
        vendible quedo apagado y NO deberia -- que es la unica situacion que
        de verdad rompe algo (imprimir, cobrar, etc.) en silencio.
        """
        from apps.suscripciones import registry
        from apps.sucursales.models import get_sucursal_actual

        sucursal = get_sucursal_actual()
        negocio = getattr(sucursal, 'negocio', None) if sucursal is not None else None

        vendibles = [m.key for m in registry.vendibles()]

        if negocio is None:
            return {
                'modo': 'legacy',
                'explicacion': ('La sucursal no tiene negocio asignado: los modulos '
                                'se resuelven por los flags de ConfiguracionNegocio '
                                '(fail-open). Es un estado valido.'),
                'apagados': [],
                'aprovisionado': None,
            }

        from apps.suscripciones.engine import modulos_activos
        from apps.suscripciones.models import NegocioModulo

        activos = modulos_activos(negocio, sucursal)
        apagados = sorted(set(vendibles) - set(activos))

        suscripcion = getattr(negocio, 'suscripcion', None)
        tiene_plan = bool(
            suscripcion is not None and suscripcion.activa and suscripcion.plan_id
        )
        tiene_overrides = NegocioModulo.objects.filter(negocio=negocio).exists()

        # Causa raiz mas comun: `bootstrap_suscripciones` deriva los modulos de
        # los flags de las ConfiguracionNegocio ligadas a las sucursales DEL
        # NEGOCIO. Si la config quedo sin sucursal, la derivacion no encuentra
        # nada y solo otorga `cuentas_por_cobrar`.
        from apps.configuracion.models import ConfiguracionNegocio
        configs_ligadas = ConfiguracionNegocio.objects.filter(
            sucursal__negocio=negocio
        ).count()

        return {
            'modo': 'suscripciones',
            'negocio': str(negocio),
            'plan': str(suscripcion.plan) if tiene_plan else None,
            'aprovisionado': tiene_plan or tiene_overrides,
            'configs_ligadas_a_sucursal': configs_ligadas,
            'activos': sorted(activos),
            'apagados': apagados,
            # Un modulo apagado a proposito (ej. e-CF) no es un problema; que se
            # haya apagado la impresion SI, porque nadie lo pidio. El caso "sin
            # aprovisionar" ya NO cuenta como roto: con el fail-open, no hay
            # nada apagado -- es solo un aviso de que falta configurar
            # entitlements de verdad.
            'roto': 'impresion_termica' in apagados,
        }

    # ------------------------------------------------------------------
    # Salida legible
    # ------------------------------------------------------------------

    def _imprimir(self, r):
        w = self.stdout.write
        ok, warn, err = self.style.SUCCESS, self.style.WARNING, self.style.ERROR

        w('')
        w('=' * 70)
        w('  VERIFICACION DE INSTALACION')
        w('=' * 70)

        # --- configuracion
        cfg = r['configuracion']
        w('')
        w('CONFIGURACION')
        w(f'  Archivo .env: {cfg["archivo_env"] or "(ninguno; se usan variables de entorno)"}')
        if not cfg['problemas']:
            w(ok('  OK: sin problemas de configuracion.'))
        for p in cfg['problemas']:
            estilo = err if p['critico'] else warn
            w(estilo(f'  ! {p["variable"]}: {p["mensaje"]}'))

        # --- base de datos
        db = r['base_datos']
        w('')
        w('BASE DE DATOS')
        if not db['conecta']:
            w(err(f'  ! No conecta: {db.get("error", "")}'))
        else:
            w(ok(f'  OK: conectada a {db["nombre"]}'))
            pend = db['migraciones_pendientes']
            if pend:
                w(err(f'  ! {len(pend)} migracion(es) pendiente(s): '
                      f'{", ".join(pend[:5])}{" ..." if len(pend) > 5 else ""}'))
                w('    Ejecutar: manage.py migrate')
            else:
                w(ok('  OK: sin migraciones pendientes.'))

        # --- seeds
        s = r['seeds']
        w('')
        w('DATOS INICIALES')
        w(f'  SUCURSAL_CODIGO:  {s["sucursal_codigo_configurado"] or "(no definido)"}')
        linea = f'  Sucursal:         {s["sucursal_resuelta"] or "(NO RESUELTA)"}'
        w(linea if s['sucursal_resuelta'] else err(linea))
        w(f'  Negocio:          {s["negocio"] or "(sin negocio)"}')
        w(f'  Config negocio:   {s["configuracion_negocio"] or "(no creada)"}')
        w(f'  Cajas:            {s["cajas"]}')
        w(f'  Usuarios:         {s["usuarios"]}')

        # --- modulos
        m = r['modulos']
        w('')
        w('MODULOS VENDIBLES')
        if m['modo'] == 'legacy':
            w(f'  Modo: flags de ConfiguracionNegocio (sin negocio asignado).')
            w(ok('  OK: no hay riesgo de apagado por suscripcion.'))
        else:
            w(f'  Modo: suscripciones | negocio: {m["negocio"]}')
            w(f'  Plan: {m["plan"] or "(ninguno)"}')
            if not m['aprovisionado']:
                w(warn('  AVISO: negocio sin suscripcion ni overrides. Fail-open: '
                       'todo funciona, pero no hay entitlements de verdad '
                       'configurados todavia (correr bootstrap_suscripciones).'))
            if not m['apagados']:
                w(ok('  OK: todos los modulos vendibles estan activos.'))
            elif not m['roto']:
                w(f'  Apagados (puede ser intencional): {", ".join(m["apagados"])}')
            else:
                w(err(f'  ! {len(m["apagados"])} modulo(s) APAGADOS: '
                      f'{", ".join(m["apagados"])}'))
                if 'impresion_termica' in m['apagados']:
                    w(err('    OJO: `impresion_termica` apagado significa que el POS '
                          'NO IMPRIME TICKETS, sin mostrar ningun error.'))
                if m['configs_ligadas_a_sucursal'] == 0:
                    w(err('    CAUSA: la ConfiguracionNegocio no esta ligada a ninguna'))
                    w(err('    sucursal del negocio, asi que bootstrap_suscripciones no'))
                    w(err('    encontro flags de donde derivar los modulos.'))
                    w(err('    Arreglo: ligar la config a la sucursal y re-ejecutar'))
                    w(err('             manage.py bootstrap_suscripciones'))
                elif not m['aprovisionado']:
                    w(err('    CAUSA: el negocio existe pero nunca se le aprovisionaron'))
                    w(err('    modulos. Arreglo: manage.py bootstrap_suscripciones'))

        # --- resumen
        criticos = [p for p in cfg['problemas'] if p['critico']]
        hay_problema = bool(
            criticos or not db['conecta'] or db['migraciones_pendientes']
            or m.get('roto')
        )
        w('')
        w('=' * 70)
        if hay_problema:
            w(err('  RESULTADO: la instalacion tiene problemas. Ver arriba.'))
        else:
            w(ok('  RESULTADO: instalacion sana.'))
        w('=' * 70)
        w('')
