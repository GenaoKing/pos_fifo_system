"""
`Categoria.get_sin_clasificar()` — molde de `Cliente.get_cliente_contado()`.

Usada por el stub de productos (apps/api/views/sync.py). Tiene que ser una
fila real y activa: el pull de categorias la baja antes que los productos en
el mismo ciclo, o el stub quedaria diferido esperando una categoria que no
existe local.
"""
from django.test import TestCase

from apps.productos.models import Categoria


class CategoriaSinClasificarTests(TestCase):
    def test_crea_la_categoria_si_no_existe(self):
        categoria = Categoria.get_sin_clasificar()
        self.assertEqual(categoria.nombre, 'Sin clasificar')
        self.assertTrue(categoria.activa)

    def test_es_idempotente(self):
        primera = Categoria.get_sin_clasificar()
        segunda = Categoria.get_sin_clasificar()
        self.assertEqual(primera.pk, segunda.pk)
        self.assertEqual(Categoria.objects.filter(nombre='Sin clasificar').count(), 1)

    def test_no_pisa_una_ya_existente(self):
        existente = Categoria.objects.create(
            nombre='Sin clasificar', activa=False, descripcion='personalizada',
        )
        resultado = Categoria.get_sin_clasificar()
        self.assertEqual(resultado.pk, existente.pk)
        self.assertFalse(resultado.activa, 'no debe sobreescribir una categoria ya existente')
