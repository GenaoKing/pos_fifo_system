"""
Genera el resumen diario de ventas y cobros (y su PDF) sin intervencion humana.

Que estaba roto (RPT-005):

- Auditaba con `Auditoria.objects.create(tabla=..., registro_id=...,
  importancia=...)`. Ninguno de esos tres campos existe en el modelo actual —usa
  `content_type`/`object_id`/`nivel_importancia`— asi que el comando lanzaba
  `TypeError` SIEMPRE, DESPUES de haber creado el cierre y escrito el PDF. El
  scheduler registraba fallo sobre un sistema que ya habia mutado datos y disco.
- El bloque de manejo de error repetia los mismos argumentos invalidos, de modo
  que ni siquiera se podia registrar el fallo.
- Heredaba de `BaseCommand` sin pedir tenant. Bajo DB-per-tenant, una consulta
  de reportes sin contexto la rechaza el router, asi que el cierre automatico no
  podia recorrer tenants.
- El reintento encontraba el cierre congelado (RPT-004) y volvia a fallar al
  auditar: dos formas distintas de no avanzar nunca.

Uso:
    python manage.py generar_cierre_diario                 # instalacion local
    python manage.py generar_cierre_diario --tenant demo   # un tenant
    python manage.py generar_cierre_diario --todos-los-tenants
    python manage.py generar_cierre_diario --fecha 2026-08-20 --finalizar
"""
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.auditoria.models import Auditoria
from apps.reportes.pdf_generator import PDFGenerator
from apps.reportes.report_manager import FechaFuturaError, ReporteManager
from apps.tenancy.management.base import TenantCommandMixin


class Command(TenantCommandMixin, BaseCommand):
    help = 'Genera el resumen diario de ventas y cobros automaticamente'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fecha',
            type=str,
            help='Fecha del cierre (formato: YYYY-MM-DD). Default: hoy',
        )
        self.add_tenant_argument(parser, required=False)
        parser.add_argument(
            '--todos-los-tenants',
            action='store_true',
            help='Recorre todos los tenants activos del control plane.',
        )
        parser.add_argument(
            '--finalizar',
            action='store_true',
            help=(
                'Congela el resumen (estado FINAL). Sin esto queda BORRADOR y '
                'se recalcula en la siguiente corrida, que es lo correcto para '
                'el dia en curso.'
            ),
        )

    # ------------------------------------------------------------------

    def handle(self, *args, **options):
        fecha = self._resolver_fecha(options.get('fecha'))
        finalizar = options.get('finalizar', False)

        if options.get('todos_los_tenants'):
            return self._todos_los_tenants(fecha, finalizar)

        tenant_key = options.get('tenant')
        if tenant_key:
            tenant = self.get_tenant(tenant_key)
            self.run_in_tenant(
                tenant, lambda: self._generar(fecha, finalizar, tenant_key)
            )
            return

        # Instalacion local (tenancy apagada): contexto directo.
        self._generar(fecha, finalizar, None)

    # ------------------------------------------------------------------

    def _resolver_fecha(self, valor):
        if not valor:
            return timezone.localdate()
        try:
            return datetime.strptime(valor, '%Y-%m-%d').date()
        except ValueError:
            raise CommandError(
                f'Fecha invalida: "{valor}". Formato esperado YYYY-MM-DD.'
            )

    def _todos_los_tenants(self, fecha, finalizar):
        """
        Recorre los tenants activos, aislando el fallo de cada uno.

        Un tenant que falle no puede impedir el cierre de los demas; pero el
        comando termina en error si alguno fallo, para que el scheduler lo vea.
        """
        from apps.tenancy.models import Tenant

        tenants = Tenant.objects.using('default').filter(activo=True).order_by(
            'tenant_key'
        )
        if not tenants:
            self.stdout.write(self.style.WARNING('No hay tenants activos.'))
            return

        fallidos = []
        for tenant in tenants:
            try:
                self.run_in_tenant(
                    tenant,
                    lambda t=tenant: self._generar(fecha, finalizar, t.tenant_key),
                )
            except Exception as exc:
                fallidos.append(tenant.tenant_key)
                self.stderr.write(
                    self.style.ERROR(f'[{tenant.tenant_key}] fallo: {exc}')
                )

        if fallidos:
            raise CommandError(
                f'Fallaron {len(fallidos)} tenant(s): {", ".join(fallidos)}'
            )

    # ------------------------------------------------------------------

    def _generar(self, fecha, finalizar, etiqueta):
        prefijo = f'[{etiqueta}] ' if etiqueta else ''
        self.stdout.write(
            self.style.WARNING(f'{prefijo}Generando resumen diario de {fecha}...')
        )

        try:
            cierre = ReporteManager.generar_cierre_diario(
                fecha=fecha, generado_automaticamente=True,
            )
        except FechaFuturaError as exc:
            raise CommandError(str(exc))
        except Exception as exc:
            # El fallo se audita con campos VALIDOS: antes esta rama repetia los
            # mismos argumentos inexistentes y tampoco se podia registrar.
            self._auditar_error(fecha, exc, etiqueta)
            raise

        advertencias = []

        # El PDF es accesorio. Que falle no invalida el resumen ya calculado,
        # pero tiene que quedar dicho: no se traga la excepcion en silencio.
        try:
            ruta = PDFGenerator.generar_cierre_caja(cierre.id)
            cierre.archivo_pdf = ruta
            cierre.save(update_fields=['archivo_pdf', 'fecha_calculo'])
        except Exception as exc:
            ruta = None
            advertencias.append(f'PDF no generado: {exc}')
            self._auditar_error(fecha, exc, etiqueta, contexto='PDF')

        if finalizar:
            cierre.finalizar()

        Auditoria.registrar(
            accion=Auditoria.TipoAccion.CIERRE_DIARIO,
            descripcion=(
                f'Resumen diario {fecha} generado automaticamente '
                f'(v{cierre.version}, {cierre.estado})'
            ),
            content_object=cierre,
            nivel_importancia='MEDIA',
            exito=not advertencias,
            mensaje_error='; '.join(advertencias),
            metadata={
                'tenant': etiqueta,
                'version': cierre.version,
                'estado': cierre.estado,
                'turnos_abiertos': cierre.turnos_abiertos,
            },
        )

        self.stdout.write(self.style.SUCCESS(
            f'{prefijo}Resumen generado\n'
            f'  ID: {cierre.id}\n'
            f'  Fecha: {cierre.fecha}\n'
            f'  Estado: {cierre.estado} (v{cierre.version})\n'
            f'  Total Ventas: ${cierre.total_ventas}\n'
            f'  Cantidad Ventas: {cierre.cantidad_ventas}\n'
            f'  Turnos abiertos: {cierre.turnos_abiertos}\n'
            f'  PDF: {ruta or "no generado"}'
        ))

        for advertencia in advertencias:
            self.stdout.write(self.style.WARNING(f'{prefijo}{advertencia}'))

    def _auditar_error(self, fecha, exc, etiqueta, contexto='cierre'):
        try:
            Auditoria.registrar(
                accion=Auditoria.TipoAccion.ERROR_SISTEMA,
                descripcion=f'Error en el {contexto} automatico de {fecha}: {exc}',
                nivel_importancia='ALTA',
                exito=False,
                mensaje_error=str(exc),
                metadata={'tenant': etiqueta, 'contexto': contexto},
            )
        except Exception:
            # Auditar no puede ser el motivo por el que se pierda el error real.
            self.stderr.write(self.style.ERROR(
                f'No se pudo auditar el fallo de {contexto}: {exc}'
            ))
