"""
Management command: python manage.py crear_sucursal
Crea una sucursal y opcionalmente su ConfiguracionNegocio asociada.

Uso en instalacion:
    python manage.py crear_sucursal --codigo SD-001 --nombre "Royal Plast - Sede Principal"
    python manage.py crear_sucursal --codigo STI-001 --nombre "Royal Plast - Santiago" --preset plasticos
"""
from django.core.management.base import BaseCommand, CommandError
from apps.sucursales.models import Sucursal


class Command(BaseCommand):
    help = 'Crea una nueva sucursal en el sistema'

    def add_arguments(self, parser):
        parser.add_argument(
            '--codigo',
            type=str,
            required=True,
            help='Codigo unico de sucursal. Ej: SD-001, STI-001'
        )
        parser.add_argument(
            '--nombre',
            type=str,
            required=True,
            help='Nombre descriptivo de la sucursal'
        )
        parser.add_argument(
            '--direccion',
            type=str,
            default='',
            help='Direccion fisica'
        )
        parser.add_argument(
            '--telefono',
            type=str,
            default='',
            help='Telefono de contacto'
        )
        parser.add_argument(
            '--preset',
            type=str,
            choices=['plasticos', 'accesorios_auto', 'retail_general'],
            default=None,
            help='Crear ConfiguracionNegocio con preset (requiere app configuracion)'
        )

    def handle(self, *args, **options):
        codigo = options['codigo'].upper().strip()
        nombre = options['nombre'].strip()

        # Verificar si ya existe
        if Sucursal.objects.filter(codigo=codigo).exists():
            sucursal = Sucursal.objects.get(codigo=codigo)
            self.stdout.write(self.style.WARNING(
                f'  Sucursal {codigo} ya existe: {sucursal.nombre}'
            ))
            return

        # Crear la sucursal
        sucursal = Sucursal.objects.create(
            codigo=codigo,
            nombre=nombre,
            direccion=options['direccion'],
            telefono=options['telefono'],
            activa=True,
        )

        self.stdout.write(self.style.SUCCESS(
            f'  [OK] Sucursal creada: {sucursal.codigo} - {sucursal.nombre}'
        ))
        self.stdout.write(f'        API Key: {sucursal.api_key}')

        # Crear ConfiguracionNegocio para esta sucursal si se pide preset
        preset = options['preset']
        if preset:
            try:
                from apps.configuracion.models import ConfiguracionNegocio
                config, created = ConfiguracionNegocio.objects.get_or_create(
                    sucursal=sucursal,
                    defaults={'nombre_negocio': nombre}
                )
                if created:
                    self._aplicar_preset(config, preset)
                    config.save()
                    self.stdout.write(self.style.SUCCESS(
                        f'  [OK] ConfiguracionNegocio creada con preset: {preset}'
                    ))
                else:
                    self.stdout.write(self.style.WARNING(
                        f'  ConfiguracionNegocio ya existia para esta sucursal'
                    ))
            except Exception as e:
                self.stdout.write(self.style.WARNING(
                    f'  No se pudo crear ConfiguracionNegocio: {e}'
                ))

    def _aplicar_preset(self, config, preset):
        """Aplica valores de preset a la config (misma logica que crear_config_inicial)"""
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