"""Prueba de deriva entre CT-04 y las piezas A05 ya implementadas.

El fixture es el insumo canónico para C04/C05.  La lista y las acciones están
reservadas para A06, pero el transporte ``master.mutation.v1`` ya existe y no
puede cambiar de forma silenciosa mientras el portal se construye en paralelo.
"""
import json
from pathlib import Path

from django.test import SimpleTestCase

from apps.api.serializers.sync import MutacionMaestroEntradaSerializer
from apps.permisos.catalogo import CATALOGO
from apps.sync.models import MutacionMaestro


class CT04ContratoTests(SimpleTestCase):
    """El fixture versionado declara solo semántica que A05 puede respaldar."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ruta = (
            Path(__file__).resolve().parents[3]
            / 'docs' / 'handoffs' / 'cierre_prod' / 'fixtures'
            / 'ct04_master_offline_v1.json'
        )
        cls.fixture = json.loads(ruta.read_text(encoding='utf-8'))

    def test_transporte_implementado_usa_el_schema_y_los_enums_reales(self):
        transporte = self.fixture['transporte_implementado']
        self.assertEqual(transporte['schema_version'], 'master.mutation.v1')
        self.assertEqual(
            transporte['respuesta']['schema_version'], 'master.mutation.v1',
        )

        serializer = MutacionMaestroEntradaSerializer(
            data=transporte['solicitud']['mutaciones'][0],
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

        catalogos = self.fixture['catalogos']
        self.assertEqual(
            catalogos['entidades'],
            [valor for valor, _ in MutacionMaestro.Entidad.choices],
        )
        self.assertEqual(
            catalogos['operaciones'],
            [valor for valor, _ in MutacionMaestro.Operacion.choices],
        )
        self.assertEqual(
            catalogos['estados_locales'],
            [valor for valor, _ in MutacionMaestro.Estado.choices],
        )

    def test_listado_reservado_declara_conflicto_rechazo_y_semantica_operativa(self):
        listado = self.fixture['listado_conflictos_reservado_a06']
        self.assertEqual(listado['estado_implementacion'], 'RESERVADA_A06')
        self.assertEqual(
            listado['respuesta']['schema_version'], 'master.conflict-list.v1',
        )
        self.assertEqual(
            {fila['estado'] for fila in listado['respuesta']['items']},
            {
                MutacionMaestro.Estado.CONFLICTO,
                MutacionMaestro.Estado.RECHAZADA,
            },
        )

        semantica = {
            fila['estado']: fila
            for fila in self.fixture['semantica_operativa_local']
        }
        self.assertTrue(
            semantica[MutacionMaestro.Estado.CONFLICTO]
            ['bloquea_nuevas_ventas'],
        )
        self.assertFalse(
            semantica[MutacionMaestro.Estado.PENDIENTE]
            ['bloquea_nuevas_ventas'],
        )
        self.assertFalse(
            semantica[MutacionMaestro.Estado.RECHAZADA]
            ['bloquea_nuevas_ventas'],
        )

    def test_acciones_reservadas_exigen_motivo_y_permisos_existentes(self):
        permisos_catalogo = {codigo for codigo, *_ in CATALOGO}
        permisos_ct04 = self.fixture['permisos_por_entidad']
        for entidad, permisos in permisos_ct04.items():
            with self.subTest(entidad=entidad):
                self.assertIn(permisos['ver'], permisos_catalogo)
                self.assertIn(permisos['resolver'], permisos_catalogo)

        acciones = self.fixture['listado_conflictos_reservado_a06']['acciones']
        self.assertEqual(
            {accion['codigo'] for accion in acciones},
            {'CONSERVAR_CLOUD', 'APLICAR_LOCAL'},
        )
        for accion in acciones:
            with self.subTest(accion=accion['codigo']):
                self.assertEqual(accion['estado_implementacion'], 'RESERVADA_A06')
                self.assertTrue(accion['motivo_obligatorio'])
                self.assertEqual(accion['motivo']['min_length'], 1)
                self.assertEqual(accion['motivo']['max_length'], 500)
