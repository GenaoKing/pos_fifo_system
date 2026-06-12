"""
Management command: python manage.py crear_config_inicial
Crea la configuracion del negocio con valores por defecto o personalizados.

FASE 2: Soporta --sucursal para vincular la config a una sucursal existente.

Uso:
    # Legacy (sin sucursal, backward compatible)
    python manage.py crear_config_inicial --nombre "Royal Plast EIRL"

    # Con sucursal (Fase 2)
    python manage.py crear_config_inicial --sucursal SD-001 --nombre "Royal Plast EIRL" --preset plasticos
"""
from django.core.management.base import BaseCommand, CommandError
from apps.configuracion.models import ConfiguracionNegocio


class Command(BaseCommand):
    help = 'Crea o actualiza la configuracion inicial del negocio'

    def add_arguments(self, parser):
        parser.add_argument('--nombre', type=str, default='Mi Negocio')
        parser.add_argument('--rnc', type=str, default='')
        parser.add_argument('--direccion', type=str, default='')
        parser.add_argument('--telefono', type=str, default='')
        parser.add_argument('--email', type=str, default='')
        parser.add_argument(
            '--sucursal',
            type=str,
            default=None,
            help='Codigo de sucursal existente para vincular la config (Fase 2)'
        )
        # Presets de negocio
        parser.add_argument(
            '--preset',
            type=str,
            choices=['plasticos', 'accesorios_auto', 'retail_general'],
            default=None,
            help='Preset de configuracion para tipo de negocio'
        )
        parser.add_argument(
            '--prefijo-codigo',
            type=str,
            default=None,
            help='Prefijo de los codigos de barra internos (ej: MN genera MN-000001). '
                 'Tiene prioridad sobre el preset.'
        )

    def handle(self, *args, **options):
        sucursal = None
        codigo_sucursal = options['sucursal']

        # Resolver sucursal si se especifico
        if codigo_sucursal:
            try:
                from apps.sucursales.models import Sucursal
                sucursal = Sucursal.objects.get(codigo=codigo_sucursal)
                self.stdout.write(f'  Sucursal: {sucursal}')
            except Exception:
                raise CommandError(
                    f'Sucursal "{codigo_sucursal}" no encontrada. '
                    f'Creala primero con: python manage.py crear_sucursal --codigo {codigo_sucursal} --nombre "..."'
                )

        # Obtener o crear config
        if sucursal:
            config, created = ConfiguracionNegocio.objects.get_or_create(
                sucursal=sucursal,
                defaults={'nombre_negocio': options['nombre']}
            )
        else:
            # Legacy: buscar primera config o crear
            config = ConfiguracionNegocio.objects.first()
            if config is None:
                config = ConfiguracionNegocio(pk=1)
                created = True
            else:
                created = False

        # Datos basicos
        config.nombre_negocio = options['nombre']
        if options['rnc']:
            config.rnc = options['rnc']
        if options['direccion']:
            config.direccion = options['direccion']
        if options['telefono']:
            config.telefono = options['telefono']
        if options['email']:
            config.email_negocio = options['email']

        # Aplicar preset si se especifica
        preset = options['preset']
        if preset == 'plasticos':
            config.modulo_etiquetas_zebra = True
            config.modulo_financiacion_coop = True
            config.modulo_cotizaciones = True
            config.modulo_impresion_termica = True
            config.modulo_barcode_scanner = True
            config.modulo_reportes_ondemand = True
            config.modulo_dashboard = True
            config.pago_efectivo = True
            config.pago_transferencia = True
            config.formato_codigo_barras = 'RP-XXXXXX'
            self.stdout.write('  Preset: Plasticos (todos los modulos)')

        elif preset == 'accesorios_auto':
            config.modulo_etiquetas_zebra = False
            config.modulo_financiacion_coop = False
            config.modulo_cotizaciones = True
            config.modulo_impresion_termica = True
            config.modulo_barcode_scanner = True
            config.modulo_reportes_ondemand = True
            config.modulo_dashboard = True
            config.pago_efectivo = True
            config.pago_transferencia = True
            config.pago_tarjeta = True
            self.stdout.write('  Preset: Accesorios Auto (sin Zebra ni financiacion)')

        elif preset == 'retail_general':
            config.modulo_etiquetas_zebra = False
            config.modulo_financiacion_coop = False
            config.modulo_cotizaciones = True
            config.modulo_impresion_termica = True
            config.modulo_barcode_scanner = True
            config.modulo_reportes_ondemand = True
            config.modulo_dashboard = True
            config.pago_efectivo = True
            config.pago_transferencia = True
            self.stdout.write('  Preset: Retail General')

        prefijo = options['prefijo_codigo']
        if prefijo:
            config.formato_codigo_barras = f'{prefijo.strip().upper()}-XXXXXX'
            self.stdout.write(f'  Prefijo de codigos internos: {config.formato_codigo_barras}')

        config.save()

        action = 'Creada' if created else 'Actualizada'
        suc_label = f' (sucursal: {sucursal.codigo})' if sucursal else ' (sin sucursal - legacy)'
        self.stdout.write(self.style.SUCCESS(
            f'  [OK] {action} configuracion para: {config.nombre_negocio}{suc_label}'
        ))