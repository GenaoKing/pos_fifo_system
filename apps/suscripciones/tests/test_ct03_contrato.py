"""Contrato CT-03: fixture versionado y resolutor efectivo real."""
import json
from pathlib import Path

from django.test import TestCase

from apps.negocios.models import Negocio
from apps.sucursales.models import Sucursal
from apps.suscripciones import engine, seed
from apps.suscripciones.models import (
    Modulo,
    NegocioModulo,
    Plan,
    SucursalModuloOverride,
    SuscripcionNegocio,
)


FIXTURE = (
    Path(__file__).resolve().parents[3]
    / 'docs'
    / 'handoffs'
    / 'cierre_prod'
    / 'fixtures'
    / 'C03-ct03_capacidades_efectivas_v1.json'
)


def cargar_fixture():
    with FIXTURE.open(encoding='utf-8') as archivo:
        return json.load(archivo)


class CT03ContratoCapacidadesTests(TestCase):
    """El escenario del fixture debe resolver exactamente igual en el engine."""

    def setUp(self):
        self.fixture = cargar_fixture()
        seed.sembrar_modulos(Modulo)

        escenario = self.fixture['escenario']
        self.negocio = Negocio.objects.create(
            nombre=escenario['negocio']['nombre'],
            slug=escenario['negocio']['slug'],
        )
        plan_data = escenario['plan']
        self.plan = Plan.objects.create(
            nombre=plan_data['nombre'],
            slug=plan_data['slug'],
            activo=True,
            preset_version=plan_data['preset_version'],
        )
        self.plan.modulos.set(Modulo.objects.filter(key__in=plan_data['modulos']))
        SuscripcionNegocio.objects.create(
            negocio=self.negocio,
            plan=self.plan,
            activa=escenario['suscripcion']['activa'],
        )

        for override in escenario['overrides_negocio']:
            NegocioModulo.objects.create(
                negocio=self.negocio,
                modulo=Modulo.objects.get(key=override['modulo']),
                incluido=override['incluido'],
            )

        self.sucursales = {}
        for sucursal_data in escenario['sucursales']:
            sucursal = Sucursal.objects.create(
                codigo=sucursal_data['codigo'],
                nombre=sucursal_data['nombre'],
                activa=True,
                negocio=self.negocio,
            )
            self.sucursales[sucursal_data['codigo']] = sucursal
            for override in sucursal_data['overrides']:
                SucursalModuloOverride.objects.create(
                    sucursal=sucursal,
                    modulo=Modulo.objects.get(key=override['modulo']),
                    activo=override['activo'],
                )

    def test_plan_sano_deterministico(self):
        self.assertEqual(
            set(self.plan.modulos.values_list('key', flat=True)),
            set(self.fixture['escenario']['plan']['modulos']),
        )

    def test_estado_suscripcion(self):
        self.assertEqual(
            engine.estado_suscripcion(self.negocio),
            self.fixture['esperado']['estado_suscripcion'],
        )

    def test_modulos_negocio_coincide_con_el_contrato(self):
        self.assertEqual(
            engine.modulos_negocio(self.negocio),
            set(self.fixture['esperado']['modulos_negocio']),
        )

    def test_modulos_activos_por_sucursal_coincide_con_el_contrato(self):
        esperado = self.fixture['esperado']['modulos_activos_por_sucursal']
        for codigo, claves in esperado.items():
            with self.subTest(sucursal=codigo):
                self.assertEqual(
                    engine.modulos_activos(self.negocio, self.sucursales[codigo]),
                    set(claves),
                )

    def test_override_sucursal_apaga_vendible_pero_no_core(self):
        activos = engine.modulos_activos(self.negocio, self.sucursales['02'])
        self.assertNotIn('ecf', activos)
        self.assertIn('ventas', activos)
