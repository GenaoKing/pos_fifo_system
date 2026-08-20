"""
Tests del cursor keyset y la marca de agua contigua (Fase 2, BUG-B).

Lo que se garantiza aqui:

1. Un registro editado llega aunque caiga en la ultima pagina (paginacion real).
2. Dos registros con `fecha_modificacion` identica no se pierden en el borde.
3. Un item que falla al aplicarse NO se salta: congela la marca de agua y el
   siguiente ciclo lo reintenta.
4. Un corte de red a media paginacion retoma donde iba, no desde cero.
5. Un cursor congelado queda visible.
"""
from datetime import datetime, timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.productos.models import Categoria
from apps.sync.engine import SyncEngine
from apps.sync.models import VersionMaestro


class _Resp:
    """Respuesta HTTP falsa con el shape que devuelve DRF."""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


def _pagina(items, hay_mas=False):
    return {'count': len(items), 'next': 'http://x/next' if hay_mas else None,
            'results': items}


def _item(nombre, fecha, cursor_id, activa=True):
    return {
        'nombre': nombre,
        'descripcion': '',
        'tipo_negocio': '',
        'atributos_configurados': {},
        'activa': activa,
        'fecha_modificacion': fecha.isoformat(),
        'id': cursor_id,
    }


@override_settings(SYNC_ENABLED=True, CLOUD_API_URL='https://cloud.test',
                   CLOUD_API_TOKEN='t')
class PullKeysetTests(TestCase):
    def setUp(self):
        self.engine = SyncEngine()
        self.base = timezone.now() - timedelta(days=1)

    def _cursor(self):
        return VersionMaestro.objects.get(tabla='categorias')

    # ------------------------------------------------------------------

    @mock.patch('apps.sync.engine.requests.get')
    def test_pagina_siguiente_se_pide_por_clave_no_por_offset(self, mock_get):
        """
        El cliente deja de seguir el `next` de DRF (offset) y pide cada pagina
        con la clave del ultimo item. Eso es lo que hace la paginacion estable.
        """
        p1 = [_item(f'Cat {i:03d}', self.base + timedelta(seconds=i), i)
              for i in range(1, 4)]
        p2 = [_item(f'Cat {i:03d}', self.base + timedelta(seconds=i), i)
              for i in range(4, 6)]
        mock_get.side_effect = [_Resp(_pagina(p1, hay_mas=True)),
                                _Resp(_pagina(p2)), _Resp(_pagina([]))]

        n = self.engine._pull_categorias()

        self.assertEqual(n, 5)
        self.assertEqual(Categoria.objects.count(), 5)

        # La segunda llamada debe ir con la clave del ultimo item de la p1.
        segunda = mock_get.call_args_list[1]
        params = segunda.kwargs['params']
        self.assertEqual(params['desde_id'], 3)
        self.assertIn('desde', params)

    @mock.patch('apps.sync.engine.requests.get')
    def test_cursor_guarda_fecha_e_id(self, mock_get):
        items = [_item('Cat A', self.base, 7), _item('Cat B', self.base, 9)]
        mock_get.side_effect = [_Resp(_pagina(items)), _Resp(_pagina([]))]

        self.engine._pull_categorias()

        cursor = self._cursor()
        self.assertEqual(cursor.ultimo_id, 9)
        self.assertIsNotNone(cursor.ultima_version)

    @mock.patch('apps.sync.engine.requests.get')
    def test_empate_de_timestamp_no_pierde_registros(self, mock_get):
        """
        Dos registros guardados en el mismo instante. Con el filtro viejo
        (`fecha__gt` estricto) el segundo caia fuera del rango para siempre.
        """
        misma_fecha = self.base
        items = [_item('Cat A', misma_fecha, 10), _item('Cat B', misma_fecha, 11)]
        mock_get.side_effect = [_Resp(_pagina(items)), _Resp(_pagina([]))]

        self.engine._pull_categorias()

        self.assertEqual(Categoria.objects.count(), 2)
        cursor = self._cursor()
        # El cursor queda EN esa fecha pero con el id del ultimo: el keyset
        # distingue "mismo instante, id mayor" de "instante posterior".
        self.assertEqual(cursor.ultimo_id, 11)

    # ------------------------------------------------------------------
    # Marca de agua contigua
    # ------------------------------------------------------------------

    @mock.patch('apps.sync.engine.requests.get')
    def test_item_que_falla_congela_la_marca_de_agua(self, mock_get):
        """
        El corazon de BUG-B: antes el cursor saltaba al maximo visto y el item
        fallido no volvia a entrar en ningun pull. Ahora la marca de agua se
        detiene ANTES del fallo.
        """
        items = [
            _item('Cat OK 1', self.base + timedelta(seconds=1), 1),
            _item('Cat OK 2', self.base + timedelta(seconds=2), 2),
            _item('Cat MALA', self.base + timedelta(seconds=3), 3),
            _item('Cat OK 4', self.base + timedelta(seconds=4), 4),
        ]
        mock_get.side_effect = [_Resp(_pagina(items)), _Resp(_pagina([]))]

        real = Categoria.objects.update_or_create

        def falla_en_la_mala(*args, **kwargs):
            if kwargs.get('nombre') == 'Cat MALA':
                raise ValueError('no se puede aplicar')
            return real(*args, **kwargs)

        with mock.patch.object(Categoria.objects, 'update_or_create',
                               side_effect=falla_en_la_mala):
            self.engine._pull_categorias()

        cursor = self._cursor()
        # Se detuvo en el item 2, justo antes del que falla.
        self.assertEqual(cursor.ultimo_id, 2)
        # Y quedo marcado como bloqueado, con la referencia del culpable.
        self.assertIsNotNone(cursor.bloqueado_desde)
        self.assertIn('Cat MALA', cursor.bloqueado_detalle)

    @mock.patch('apps.sync.engine.requests.get')
    def test_los_items_posteriores_al_fallo_si_se_aplican(self, mock_get):
        """
        Congelar la marca de agua no debe dejar a la sucursal con datos viejos:
        los registros siguientes se aplican igual (son idempotentes).
        """
        items = [
            _item('Cat MALA', self.base + timedelta(seconds=1), 1),
            _item('Cat POSTERIOR', self.base + timedelta(seconds=2), 2),
        ]
        mock_get.side_effect = [_Resp(_pagina(items)), _Resp(_pagina([]))]

        real = Categoria.objects.update_or_create

        def falla_en_la_mala(*args, **kwargs):
            if kwargs.get('nombre') == 'Cat MALA':
                raise ValueError('no se puede aplicar')
            return real(*args, **kwargs)

        with mock.patch.object(Categoria.objects, 'update_or_create',
                               side_effect=falla_en_la_mala):
            self.engine._pull_categorias()

        self.assertTrue(Categoria.objects.filter(nombre='Cat POSTERIOR').exists())
        self.assertFalse(Categoria.objects.filter(nombre='Cat MALA').exists())

    @mock.patch('apps.sync.engine.requests.get')
    def test_el_siguiente_ciclo_reintenta_el_item_fallido(self, mock_get):
        """Cuando el item deja de fallar, el pull lo recupera y desbloquea."""
        items = [
            _item('Cat OK', self.base + timedelta(seconds=1), 1),
            _item('Cat MALA', self.base + timedelta(seconds=2), 2),
        ]
        mock_get.side_effect = [_Resp(_pagina(items)), _Resp(_pagina([]))]

        real = Categoria.objects.update_or_create

        def falla(*args, **kwargs):
            if kwargs.get('nombre') == 'Cat MALA':
                raise ValueError('boom')
            return real(*args, **kwargs)

        with mock.patch.object(Categoria.objects, 'update_or_create', side_effect=falla):
            self.engine._pull_categorias()

        self.assertEqual(self._cursor().ultimo_id, 1)

        # Segundo ciclo: el cloud vuelve a mandar el item (el cursor no lo paso)
        # y esta vez aplica bien.
        mock_get.side_effect = [_Resp(_pagina([items[1]])), _Resp(_pagina([]))]
        self.engine._pull_categorias()

        cursor = self._cursor()
        self.assertEqual(cursor.ultimo_id, 2)
        self.assertTrue(Categoria.objects.filter(nombre='Cat MALA').exists())
        self.assertIsNone(cursor.bloqueado_desde, 'El bloqueo debio limpiarse')

    # ------------------------------------------------------------------
    # Resiliencia de red
    # ------------------------------------------------------------------

    @mock.patch('apps.sync.engine.requests.get')
    def test_corte_de_red_a_media_paginacion_conserva_lo_aplicado(self, mock_get):
        """
        Antes, un corte de red retornaba sin guardar cursor y el ciclo siguiente
        empezaba de cero. Ahora la primera pagina queda confirmada.
        """
        import requests as rq

        p1 = [_item(f'Cat {i}', self.base + timedelta(seconds=i), i) for i in (1, 2)]
        mock_get.side_effect = [
            _Resp(_pagina(p1, hay_mas=True)),
            rq.RequestException('se cayo la red'),
        ]

        n = self.engine._pull_categorias()

        self.assertEqual(n, 2)
        cursor = self._cursor()
        self.assertEqual(cursor.ultimo_id, 2, 'No conservo el avance de la 1a pagina')

    @mock.patch('apps.sync.engine.requests.get')
    def test_http_error_no_avanza_el_cursor(self, mock_get):
        mock_get.side_effect = [_Resp({'detail': 'boom'}, status_code=500)]

        n = self.engine._pull_categorias()

        self.assertEqual(n, 0)
        self.assertEqual(self._cursor().ultimo_id, 0)


@override_settings(SYNC_ENABLED=True, CLOUD_API_URL='https://cloud.test',
                   CLOUD_API_TOKEN='t')
class PullSinIdTests(TestCase):
    """La configuracion es un singleton sin id: el cursor cae a fecha sola."""

    @mock.patch('apps.sync.engine.requests.get')
    def test_item_sin_id_no_rompe_el_cursor(self, mock_get):
        ahora = timezone.now()
        item = {'nombre': 'Cat sin id', 'descripcion': '', 'tipo_negocio': '',
                'atributos_configurados': {}, 'activa': True,
                'fecha_modificacion': ahora.isoformat()}
        mock_get.side_effect = [_Resp(_pagina([item])), _Resp(_pagina([]))]

        SyncEngine()._pull_categorias()

        cursor = VersionMaestro.objects.get(tabla='categorias')
        self.assertEqual(cursor.ultimo_id, 0)
        self.assertIsNotNone(cursor.ultima_version)


class ClaveCursorTests(TestCase):
    def test_prefiere_cursor_id_sobre_id(self):
        """
        Los endpoints de sync mandan `cursor_id` (token de paginacion); los de
        maestros mandan `id`. El cliente acepta ambos.
        """
        ahora = timezone.now()
        clave = SyncEngine._clave_cursor(
            {'fecha_modificacion': ahora.isoformat(), 'cursor_id': 42, 'id': 7}
        )
        self.assertEqual(clave[1], 42)

    def test_item_sin_fecha_no_produce_clave(self):
        self.assertIsNone(SyncEngine._clave_cursor({'id': 1}))

    def test_fecha_invalida_no_produce_clave(self):
        self.assertIsNone(
            SyncEngine._clave_cursor({'fecha_modificacion': 'no-es-fecha', 'id': 1})
        )

    def test_referencia_legible_para_el_mensaje_de_bloqueo(self):
        self.assertEqual(SyncEngine._ref_item({'sku': 'ABC'}), 'sku=ABC')
        self.assertEqual(SyncEngine._ref_item({'nombre': 'Vaso'}), 'nombre=Vaso')
        self.assertEqual(SyncEngine._ref_item({}), '(sin referencia)')
