"""Verifica, sin corregir, la identidad control plane -> tenant DB."""

import json

from django.core.management.base import BaseCommand, CommandError

from apps.negocios.models import Negocio
from apps.tenancy.context import force_tenancy, tenant_context
from apps.tenancy.management.base import TenantCommandMixin
from apps.tenancy.registry import configure_tenant_database
from apps.tenancy.services import divergencias_identidad


class Command(TenantCommandMixin, BaseCommand):
    help = 'Detecta drift de identidad; no escribe ni intenta reconciliarlo.'

    def add_arguments(self, parser):
        self.add_tenant_argument(parser)

    def handle(self, *args, **options):
        tenant = self.get_tenant(options['tenant'])
        configure_tenant_database(tenant, permitir_inactivo=True)
        with force_tenancy(True), tenant_context(tenant, permitir_inactivo=True):
            from apps.configuracion.models import ConfiguracionNegocio

            negocio = Negocio.self_row()
            configuraciones = list(
                ConfiguracionNegocio.objects.select_related('sucursal').all()
            )
            diferencias = divergencias_identidad(
                tenant, negocio, configuraciones,
            )

        if diferencias:
            self.stdout.write(json.dumps(diferencias, ensure_ascii=False, sort_keys=True))
            raise CommandError(
                'Identidad divergente. Reanude bootstrap/normalizacion o '
                'prepare una migracion controlada; no se modifico ninguna fila.'
            )
        self.stdout.write(self.style.SUCCESS(
            f'Identidad consistente: {tenant.tenant_key}.'
        ))
