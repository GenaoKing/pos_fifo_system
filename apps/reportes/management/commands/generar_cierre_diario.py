from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.reportes.report_manager import ReporteManager
from apps.reportes.pdf_generator import PDFGenerator
from apps.auditoria.models import Auditoria


class Command(BaseCommand):
    help = 'Genera el cierre de caja diario automaticamente'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fecha',
            type=str,
            help='Fecha del cierre (formato: YYYY-MM-DD). Default: hoy',
        )

    def handle(self, *args, **options):
        try:
            # Determinar fecha
            if options['fecha']:
                from datetime import datetime
                fecha = datetime.strptime(options['fecha'], '%Y-%m-%d').date()
            else:
                fecha = timezone.localdate()
            
            self.stdout.write(
                self.style.WARNING(f'Generando cierre de caja para {fecha}...')
            )
            
            # Generar el cierre
            cierre = ReporteManager.generar_cierre_diario(
                fecha=fecha,
                generado_automaticamente=True
            )
            
            # Generar PDF
            pdf_path = PDFGenerator.generar_cierre_caja(cierre.id)
            
            # Guardar referencia
            cierre.archivo_pdf = pdf_path
            cierre.save()
            
            # Log exitoso
            self.stdout.write(
                self.style.SUCCESS(
                    f'Cierre generado exitosamente\n'
                    f'  ID: {cierre.id}\n'
                    f'  Fecha: {cierre.fecha}\n'
                    f'  Total Ventas: ${cierre.total_ventas}\n'
                    f'  Cantidad Ventas: {cierre.cantidad_ventas}\n'
                    f'  PDF: {pdf_path}'
                )
            )
            
            # Registrar en auditoría
            Auditoria.objects.create(
                usuario=None,
                accion='CIERRE_AUTOMATICO',
                tabla='cierres_caja',
                registro_id=cierre.id,
                descripcion=f'Cierre automatico generado para {fecha}',
                importancia='MEDIA'
            )
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error generando cierre: {str(e)}')
            )
            
            Auditoria.objects.create(
                usuario=None,
                accion='CIERRE_AUTOMATICO_ERROR',
                tabla='cierres_caja',
                registro_id=None,
                descripcion=f'Error en cierre automatico: {str(e)}',
                importancia='ALTA'
            )
            
            raise
