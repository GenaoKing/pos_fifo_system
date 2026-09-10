"""
MERGE-C03-ALIAS-ATOMIC: gate opt-in con una BD PostgreSQL fisica adicional
para probar que `seed.bootstrap` es atomico sobre el alias del tenant, no
sobre `default` (ver docs/handoffs/cierre_prod/REVISION_MERGE_A02-C03.md).

Sigue el mismo patron de `apps/tenancy/tests/test_multidb_isolation.py`
(TEN-016): opt-in via TENANT_TEST_DB_NAMESPACE, alias registrado a mano con
`TEST.MIGRATE=False` (schema actual via syncdb, sin correr el grafo
historico) y namespace por run/attempt para correr en paralelo sin chocar.
"""

import copy
import hashlib
import os
import re
from unittest import mock, skipUnless

from django.conf import settings
from django.db import connections
from django.test import TestCase, override_settings

from apps.configuracion.models import ConfiguracionNegocio
from apps.negocios.models import Negocio
from apps.suscripciones import seed
from apps.suscripciones.models import Modulo, NegocioModulo, Plan, SuscripcionNegocio
from apps.sucursales.models import Sucursal
from apps.tenancy.context import force_tenancy, reset_current_tenant, set_current_tenant

NAMESPACE = os.environ.get('TENANT_TEST_DB_NAMESPACE', '').strip()


def _identidad(namespace):
    safe = re.sub(r'[^a-z0-9]+', '_', namespace.casefold()).strip('_')[:24]
    digest = hashlib.sha256(namespace.encode('utf-8')).hexdigest()[:10]
    return f'{safe or "run"}_{digest}'


def _registrar_alias(alias, physical_name):
    config = copy.deepcopy(settings.DATABASES['default'])
    config['NAME'] = physical_name.removeprefix('test_')
    test_config = copy.deepcopy(config.get('TEST', {}))
    test_config.update({
        'NAME': physical_name,
        'DEPENDENCIES': [],
        # Prueba atomicidad/alias, no el grafo historico: schema actual via
        # syncdb, sin la siembra de la migracion 0002 (igual que TEN-016).
        'MIGRATE': False,
        'TENANT_ISOLATION_GATE': True,
    })
    config['TEST'] = test_config
    settings.DATABASES[alias] = config
    connections.databases[alias] = config


if NAMESPACE:
    RUN_ID = _identidad(NAMESPACE)
    ALIAS_TENANT = f'tnt_susseed_{RUN_ID}'
    _registrar_alias(ALIAS_TENANT, f'test_susseed_{RUN_ID}')
else:
    # La clase se salta y no solicita una BD adicional al test runner.
    ALIAS_TENANT = 'default'


class _Boom(Exception):
    """Fallo inyectado a mitad de `seed.bootstrap`."""


@skipUnless(NAMESPACE, 'MERGE-C03-ALIAS-ATOMIC requiere TENANT_TEST_DB_NAMESPACE aislado.')
@override_settings(TENANCY_DB_PER_TENANT_ENABLED=True)
class SeedBootstrapAtomicoPorAliasTests(TestCase):
    databases = {'default', ALIAS_TENANT}

    def _preparar_negocio(self, alias):
        negocio = Negocio.objects.using(alias).create(nombre='Gate Seed', slug='gate-seed')
        sucursal = Sucursal.objects.using(alias).create(
            codigo='S1', nombre='Principal', negocio=negocio, activa=True,
        )
        ConfiguracionNegocio.objects.using(alias).create(
            sucursal=sucursal, nombre_negocio='Gate Seed', modulo_ecf=True,
        )
        return negocio

    def _conteos(self, alias):
        return {
            'modulos': Modulo.objects.using(alias).count(),
            'planes': Plan.objects.using(alias).count(),
            'suscripciones': SuscripcionNegocio.objects.using(alias).count(),
            'negocio_modulos': NegocioModulo.objects.using(alias).count(),
        }

    def _bootstrap(self, alias):
        return seed.bootstrap(
            ModuloModel=Modulo,
            PlanModel=Plan,
            NegocioModel=Negocio,
            NegocioModuloModel=NegocioModulo,
            SuscripcionModel=SuscripcionNegocio,
            ConfiguracionModel=ConfiguracionNegocio,
            using=alias,
        )

    def test_bootstrap_exitoso_escribe_en_la_bd_tenant_no_en_default(self):
        tokens = set_current_tenant('gate_seed_ok', ALIAS_TENANT)
        try:
            with force_tenancy(True):
                self._preparar_negocio(ALIAS_TENANT)
                antes_default = self._conteos('default')

                resumen = self._bootstrap(ALIAS_TENANT)

                self.assertEqual(resumen['negocios'], 1)
                self.assertGreater(Modulo.objects.using(ALIAS_TENANT).count(), 0)
                self.assertGreater(Plan.objects.using(ALIAS_TENANT).count(), 0)
                self.assertEqual(SuscripcionNegocio.objects.using(ALIAS_TENANT).count(), 1)
                self.assertIn(
                    'ecf',
                    NegocioModulo.objects.using(ALIAS_TENANT)
                    .filter(modulo__key='ecf').values_list('modulo__key', flat=True),
                )
                # `default` no vio ni un INSERT de esta corrida.
                self.assertEqual(self._conteos('default'), antes_default)
        finally:
            reset_current_tenant(tokens)

    def test_fallo_a_mitad_de_bootstrap_revierte_en_la_bd_tenant_sin_tocar_default(self):
        """
        Aceptacion de MERGE-C03-ALIAS-ATOMIC: un fallo inyectado dentro del
        `transaction.atomic(using=alias)` revierte TODO lo escrito en esa BD
        (modulos, planes, suscripcion, overrides de negocio) y no deja rastro
        en `default`. Antes del fix, `transaction.atomic()` sin alias abria
        sobre `default` mientras los managers, bajo tenant activo, escribian
        en la BD del tenant: esas escrituras quedaban fuera de la transaccion
        y un fallo a mitad de camino no las revertia.
        """
        tokens = set_current_tenant('gate_seed_fail', ALIAS_TENANT)
        try:
            with force_tenancy(True):
                self._preparar_negocio(ALIAS_TENANT)
                antes_default = self._conteos('default')

                # Revienta DESPUES de sembrar modulos/planes/suscripcion y de
                # crear el NegocioModulo de 'ecf' (todos escritos dentro del
                # mismo atomic), para probar que ese trabajo previo tambien
                # se revierte -- no solo el ultimo paso.
                with mock.patch.object(
                    seed, '_preservar_overrides_por_sucursal', side_effect=_Boom,
                ):
                    with self.assertRaises(_Boom):
                        self._bootstrap(ALIAS_TENANT)

                # Nada sobrevivio en la BD tenant: ni el catalogo de modulos,
                # ni los planes, ni la suscripcion, ni el NegocioModulo.
                self.assertEqual(self._conteos(ALIAS_TENANT), {
                    'modulos': 0, 'planes': 0, 'suscripciones': 0, 'negocio_modulos': 0,
                })
                # Y `default` sigue exactamente como estaba: nada se escapo
                # de la transaccion del tenant hacia la BD equivocada.
                self.assertEqual(self._conteos('default'), antes_default)
        finally:
            reset_current_tenant(tokens)
