"""
Tests del contrato keyset del lado CLOUD (Fase 2, BUG-B).

Dos garantias que se protegen aqui:

1. Con `?desde=` los endpoints ordenan por `(fecha_modificacion, id)` y aceptan
   `?desde_id=` para desempatar. Sin eso, la paginacion es inestable y los
   registros con timestamp identico se pierden en el borde del cursor.
2. **Sin** `?desde=` el orden alfabetico del portal queda INTACTO. Este es el
   riesgo mas facil de romper sin darse cuenta al tocar el mixin.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.productos.models import Categoria
from apps.sucursales.models import Sucursal
from urllib.parse import quote

User = get_user_model()


class KeysetTestsBase(TestCase):
    categorias_url = '/api/v1/maestros/categorias/'

    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin_keyset', email='admin_ks@example.com',
            password='pass', rol='ADMIN', activo=True,
        )
        self.sucursal_user = User.objects.create_user(
            username='svc_keyset', email='svc_ks@example.com',
            password='pass', rol='CAJERA', activo=True,
        )
        self.sucursal = Sucursal.objects.create(
            codigo='SD-KS', nombre='Sucursal Keyset', activa=True,
            usuario_servicio=self.sucursal_user,
        )
        self.token = Token.objects.create(user=self.sucursal_user)

    def api(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        return client

    @staticmethod
    def _con_fecha(categoria, fecha):
        """`fecha_modificacion` es auto_now: hay que forzarla con update()."""
        Categoria.objects.filter(pk=categoria.pk).update(fecha_modificacion=fecha)
        categoria.refresh_from_db()
        return categoria


class OrdenSegunModoTests(KeysetTestsBase):
    def setUp(self):
        super().setUp()
        base = timezone.now() - timedelta(hours=1)
        # Nombre y fecha en orden INVERSO: si el endpoint ordena por el criterio
        # equivocado, el test lo detecta.
        self.zeta = self._con_fecha(
            Categoria.objects.create(nombre='Zeta', activa=True), base)
        self.alfa = self._con_fecha(
            Categoria.objects.create(nombre='Alfa', activa=True),
            base + timedelta(minutes=10))

    def test_sin_desde_el_orden_sigue_siendo_alfabetico(self):
        """Protege al portal: su listado no debe cambiar por esta fase."""
        resp = self.api().get(self.categorias_url)

        nombres = [c['nombre'] for c in resp.data['results']]
        self.assertEqual(nombres, ['Alfa', 'Zeta'])

    def test_con_desde_el_orden_es_por_fecha_e_id(self):
        """En modo sync el orden debe coincidir con el criterio del cursor."""
        desde = (timezone.now() - timedelta(days=1)).isoformat()

        resp = self.api().get(f'{self.categorias_url}?desde={quote(desde)}')

        nombres = [c['nombre'] for c in resp.data['results']]
        self.assertEqual(nombres, ['Zeta', 'Alfa'],
                         'Con ?desde= debe ordenar por fecha, no por nombre')


class DesempateDeTimestampTests(KeysetTestsBase):
    def setUp(self):
        super().setUp()
        self.momento = timezone.now() - timedelta(hours=1)
        self.primera = self._con_fecha(
            Categoria.objects.create(nombre='Primera', activa=True), self.momento)
        self.segunda = self._con_fecha(
            Categoria.objects.create(nombre='Segunda', activa=True), self.momento)

    def test_sin_desde_id_el_empate_excluye_a_ambas(self):
        """
        Comportamiento historico: `fecha__gt` estricto. Se documenta porque es
        exactamente la perdida que motiva el keyset.
        """
        resp = self.api().get(
            f'{self.categorias_url}?desde={quote(self.momento.isoformat())}'
        )

        self.assertEqual(resp.data['count'], 0)

    def test_con_desde_id_recupera_la_que_falta(self):
        """
        El cursor quedo en (momento, id_primera). La segunda comparte fecha pero
        tiene id mayor, asi que debe llegar en vez de perderse.
        """
        resp = self.api().get(
            f'{self.categorias_url}?desde={quote(self.momento.isoformat())}'
            f'&desde_id={self.primera.id}'
        )

        nombres = [c['nombre'] for c in resp.data['results']]
        self.assertEqual(nombres, ['Segunda'])

    def test_desde_id_invalido_cae_al_comportamiento_anterior(self):
        resp = self.api().get(
            f'{self.categorias_url}?desde={quote(self.momento.isoformat())}'
            f'&desde_id=no-es-numero'
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 0)

    def test_desde_id_sin_desde_no_filtra_nada(self):
        """`desde_id` solo tiene sentido acompanando a `desde`."""
        resp = self.api().get(f'{self.categorias_url}?desde_id=1')

        self.assertEqual(resp.data['count'], 2)


class PaginacionKeysetTests(KeysetTestsBase):
    def test_recorrido_completo_por_clave_no_repite_ni_salta(self):
        """
        Simula lo que hace el cliente: pedir de a bloques usando la clave del
        ultimo item recibido. Con nombres deliberadamente desordenados respecto
        a la fecha, para que un orden equivocado se note.
        """
        base = timezone.now() - timedelta(hours=2)
        total = 25
        for i in range(total):
            cat = Categoria.objects.create(nombre=f'Cat {(total - i):03d}', activa=True)
            self._con_fecha(cat, base + timedelta(seconds=i))

        vistos = []
        desde = (base - timedelta(seconds=1)).isoformat()
        desde_id = 0

        for _ in range(10):  # tope de seguridad
            resp = self.api().get(
                f'{self.categorias_url}?desde={quote(desde)}&desde_id={desde_id}'
                f'&page_size=10'
            )
            items = resp.data['results']
            if not items:
                break
            vistos.extend(c['id'] for c in items)
            desde = items[-1]['fecha_modificacion']
            desde_id = items[-1]['id']

        self.assertEqual(len(vistos), total, 'Se saltaron o repitieron registros')
        self.assertEqual(len(set(vistos)), total, 'Hubo ids duplicados entre paginas')


class RecorridoRealDelClienteTests(KeysetTestsBase):
    """
    Integracion: recorre el endpoint REAL con la misma logica que usa
    `SyncEngine._pull_generic`, sin mocks.

    Existe por un bug que los tests con mocks no podian ver: el cliente no
    mandaba `desde` en el primer request (cursor vacio), el servidor ordenaba
    por `nombre` en vez de por el cursor, y la clave del ultimo item de la
    pagina no servia como frontera. Resultado real: un pull inicial aplico
    416 items sobre un catalogo de 273.
    """

    def test_primer_pull_sin_cursor_no_duplica_ni_pierde(self):
        base = timezone.now() - timedelta(hours=3)
        total = 25
        for i in range(total):
            # Nombre inverso a la fecha: si el orden es alfabetico, la frontera
            # del keyset queda mal y aparecen solapamientos.
            cat = Categoria.objects.create(nombre=f'Cat {(total - i):03d}', activa=True)
            self._con_fecha(cat, base + timedelta(seconds=i))

        vistos = []
        # Igual que el engine: primer request con el epoch, no sin parametro.
        desde = '1970-01-01T00:00:00+00:00'
        desde_id = 0

        for _ in range(10):
            resp = self.api().get(
                f'{self.categorias_url}?desde={quote(desde)}&desde_id={desde_id}'
                f'&page_size=10'
            )
            items = resp.data['results']
            if not items:
                break
            vistos.extend(c['id'] for c in items)
            desde = items[-1]['fecha_modificacion']
            desde_id = items[-1]['id']

        self.assertEqual(len(vistos), total, 'Se perdieron o repitieron registros')
        self.assertEqual(len(set(vistos)), total, f'Hubo duplicados: {len(vistos)} vistos')
