"""
Verifica que el historial de auditoria no haya sido alterado (AUD-002).

La aplicacion ya impide UPDATE y DELETE sobre `Auditoria`, y el Admin tampoco
los ofrece. Lo que este comando cubre es lo que la aplicacion no puede: una
modificacion hecha por fuera —un `UPDATE` en psql, una restauracion parcial, un
dump editado—.

Dos comprobaciones:

1. **Hash por fila.** Cada registro guarda un SHA-256 de sus campos inmutables.
   Si alguien cambio la accion, la descripcion, el actor o el resultado, el hash
   deja de coincidir.

2. **Huecos de secuencia.** El `id` es un serial: un salto significa que hubo
   filas que ya no estan. No prueba malicia —una purga por retencion tambien
   deja huecos, y se registra a si misma— pero senala donde mirar.

Lo que NO cubre, y sigue pendiente: una cadena de hashes o una firma por lote
detectarian tambien el borrado de la ULTIMA fila, y una exportacion periodica a
almacenamiento WORM protegeria contra un borrado total de la tabla.

Uso:
    python manage.py verificar_auditoria
    python manage.py verificar_auditoria --desde 2026-01-01
    python manage.py verificar_auditoria --tenant demo
"""
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from apps.auditoria.models import Auditoria
from apps.tenancy.management.base import TenantCommandMixin


class Command(TenantCommandMixin, BaseCommand):
    help = 'Verifica la integridad del historial de auditoria'

    def add_arguments(self, parser):
        parser.add_argument(
            '--desde', type=str,
            help='Solo verificar registros desde esta fecha (YYYY-MM-DD).',
        )
        self.add_tenant_argument(parser, required=False)

    def handle(self, *args, **options):
        desde = self._fecha(options.get('desde'))
        tenant_key = options.get('tenant')

        if tenant_key:
            tenant = self.get_tenant(tenant_key)
            return self.run_in_tenant(tenant, lambda: self._verificar(desde))

        self._verificar(desde)

    def _fecha(self, valor):
        if not valor:
            return None
        try:
            return datetime.strptime(valor, '%Y-%m-%d').date()
        except ValueError:
            raise CommandError(f'Fecha invalida: "{valor}". Formato YYYY-MM-DD.')

    def _verificar(self, desde):
        qs = Auditoria.objects.all().order_by('id')
        if desde:
            qs = qs.filter(fecha_hora__date__gte=desde)

        total = 0
        sin_hash = 0
        alterados = []
        ids = []

        for registro in qs.iterator(chunk_size=1000):
            total += 1
            ids.append(registro.id)
            estado = registro.integridad_ok()
            if estado is None:
                sin_hash += 1
            elif estado is False:
                alterados.append(registro.id)

        huecos = self._huecos(ids)

        self.stdout.write(f'Registros verificados: {total}')

        if sin_hash:
            self.stdout.write(self.style.WARNING(
                f'  {sin_hash} anteriores al hash de integridad: no se pueden '
                f'verificar ni descartar.'
            ))

        if huecos:
            self.stdout.write(self.style.WARNING(
                f'  {len(huecos)} hueco(s) de secuencia. Primeros: '
                f'{huecos[:5]}. Una purga por retencion tambien los produce; '
                f'buscar el evento AUDIT_PURGE correspondiente.'
            ))

        if alterados:
            self.stdout.write(self.style.ERROR(
                f'  {len(alterados)} REGISTRO(S) ALTERADO(S): {alterados[:20]}'
            ))
            raise CommandError(
                'El historial de auditoria fue modificado por fuera de la '
                'aplicacion. No usar como evidencia sin investigar.'
            )

        self.stdout.write(self.style.SUCCESS(
            '  Sin alteraciones detectadas.'
        ))

    def _huecos(self, ids):
        """Rangos de id ausentes entre el primero y el ultimo."""
        if len(ids) < 2:
            return []
        huecos = []
        for anterior, siguiente in zip(ids, ids[1:]):
            if siguiente > anterior + 1:
                huecos.append((anterior + 1, siguiente - 1))
        return huecos
