"""
Backfill de miniaturas para el catalogo existente.

El modelo genera la miniatura sola cada vez que se guarda una imagen, asi que
todo lo que entre desde ahora ya viene cubierto. Este comando es para lo que
YA estaba: en Royal Plast, 73 productos con fotos de 3.2 MB de promedio
subidas antes de que existiera la miniatura.

Sirve igual en la sucursal (sin tenancy) y en el cloud (con `--tenant`).
"""
from django.db.models import Q
from django.core.management.base import BaseCommand

from apps.productos.models import Producto
from apps.tenancy.management.base import TenantCommandMixin


class Command(TenantCommandMixin, BaseCommand):
    help = 'Genera las miniaturas faltantes de los productos con imagen.'

    def add_arguments(self, parser):
        # Opcional a proposito: en la sucursal no hay tenant que indicar.
        self.add_tenant_argument(parser, required=False)
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Genera y guarda. Sin esto solo reporta que haria.',
        )
        parser.add_argument(
            '--forzar',
            action='store_true',
            help='Regenera tambien las que ya existen. Para cuando cambia el '
                 'tamano o la calidad estandar.',
        )
        parser.add_argument(
            '--limite',
            type=int,
            help='Procesa como maximo N productos. Util para probar contra una '
                 'instalacion real sin comprometerse con el catalogo entero.',
        )

    def handle(self, *args, **options):
        aplicar = options['apply']
        forzar = options['forzar']

        def trabajo():
            return self._procesar(aplicar, forzar, options.get('limite'))

        tenant_key = options.get('tenant')
        if tenant_key:
            tenant = self.get_tenant(tenant_key)
            self.stdout.write(f'Tenant: {tenant.tenant_key} ({tenant.db_name})')
            return self.run_in_tenant(tenant, trabajo)
        return trabajo()

    def _procesar(self, aplicar, forzar, limite):
        queryset = Producto.objects.exclude(imagen='').exclude(imagen__isnull=True)
        if not forzar:
            # `__in=('', None)` NO sirve: en SQL nada iguala a NULL, asi que el
            # comando reportaba "nada pendiente" con el catalogo entero
            # pendiente. El campo es null=True y ademas puede quedar en cadena
            # vacia, asi que hay que cubrir los dos.
            queryset = queryset.filter(Q(imagen_miniatura='') | Q(imagen_miniatura__isnull=True))
        queryset = queryset.order_by('id')
        if limite:
            queryset = queryset[:limite]

        total = queryset.count()
        if not total:
            self.stdout.write(self.style.SUCCESS(
                'No hay productos con imagen pendientes de miniatura.'
            ))
            return

        self.stdout.write(
            f'{total} producto(s) con imagen '
            f'{"a regenerar" if forzar else "sin miniatura"}. '
            f'Modo: {"APPLY" if aplicar else "DRY-RUN"}'
        )

        generadas = fallidas = 0
        bytes_origen = bytes_miniatura = 0

        for producto in queryset.iterator():
            if not aplicar:
                self.stdout.write(f'DRY-RUN {producto.sku}: {producto.imagen.name}')
                continue

            origen = self._tamano(producto.imagen)
            try:
                producto.sincronizar_miniatura(forzar=True)
            except Exception as exc:
                fallidas += 1
                self.stdout.write(self.style.ERROR(
                    f'FALLO {producto.sku}: {type(exc).__name__}: {exc}'
                ))
                continue

            if not producto.imagen_miniatura:
                # `guardar_miniatura` degrada a cadena vacia cuando el original
                # no se puede leer o no es una imagen. Contarlo como fallo y no
                # como exito silencioso es lo que hace util el resumen final.
                fallidas += 1
                self.stdout.write(self.style.WARNING(
                    f'SIN MINIATURA {producto.sku}: no se pudo procesar '
                    f'{producto.imagen.name}'
                ))
                continue

            destino = self._tamano(producto.imagen_miniatura)
            bytes_origen += origen
            bytes_miniatura += destino
            generadas += 1
            self.stdout.write(
                f'OK {producto.sku}: {self._mb(origen)} -> {self._mb(destino)}'
            )

        self._resumen(aplicar, total, generadas, fallidas, bytes_origen, bytes_miniatura)

    def _resumen(self, aplicar, total, generadas, fallidas, bytes_origen, bytes_miniatura):
        self.stdout.write('')
        if not aplicar:
            self.stdout.write(self.style.WARNING(
                f'DRY-RUN: {total} pendiente(s). No se genero nada.'
            ))
            return

        self.stdout.write(f'generadas: {generadas}')
        self.stdout.write(f'fallidas:  {fallidas}')
        if bytes_origen:
            ahorro = 100 * (1 - bytes_miniatura / bytes_origen)
            self.stdout.write(
                f'peso: {self._mb(bytes_origen)} -> {self._mb(bytes_miniatura)} '
                f'({ahorro:.1f}% menos por vista de catalogo)'
            )
        estilo = self.style.SUCCESS if not fallidas else self.style.WARNING
        self.stdout.write(estilo('Miniaturas generadas.'))

    @staticmethod
    def _tamano(campo):
        try:
            return campo.size
        except Exception:
            # Un original ausente no debe tumbar el conteo; ya se reporta como
            # fallo por la via de la miniatura vacia.
            return 0

    @staticmethod
    def _mb(cantidad):
        if cantidad >= 1_000_000:
            return f'{cantidad / 1_000_000:.1f} MB'
        return f'{cantidad / 1_000:.0f} KB'
