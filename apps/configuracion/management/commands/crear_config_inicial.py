"""
Management command: python manage.py crear_config_inicial
Crea la configuracion del negocio con valores por defecto o personalizados.
Uso en instalacion:
    python manage.py crear_config_inicial --nombre "Royal Plast EIRL" --rnc "123456789" --telefono "829-986-6443"
"""
from django.core.management.base import BaseCommand
from apps.configuracion.models import ConfiguracionNegocio


class Command(BaseCommand):
    help = 'Crea o actualiza la configuracion inicial del negocio'

    def add_arguments(self, parser):
        parser.add_argument('--nombre', type=str, default='Mi Negocio')
        parser.add_argument('--rnc', type=str, default='')
        parser.add_argument('--direccion', type=str, default='')
        parser.add_argument('--telefono', type=str, default='')
        parser.add_argument('--email', type=str, default='')
        # Presets de negocio
        parser.add_argument(
            '--preset',
            type=str,
            choices=['plasticos', 'accesorios_auto', 'retail_general'],
            default=None,
            help='Preset de configuracion para tipo de negocio'
        )

    def handle(self, *args, **options):
        config, created = ConfiguracionNegocio.objects.get_or_create(pk=1)

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

        config.save()

        action = 'Creada' if created else 'Actualizada'
        self.stdout.write(self.style.SUCCESS(
            f'  [OK] {action} configuracion para: {config.nombre_negocio}'
        ))