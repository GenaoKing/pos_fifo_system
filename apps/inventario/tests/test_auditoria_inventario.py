"""
apps/inventario/tests/test_auditoria_inventario.py

Regresion de los hallazgos de
`docs/exploracion/AUDITORIA_CODIGO_APPS_INVENTARIO.md`.

La invariante que atraviesa casi todo el modulo, y que varios tests verifican
de forma explicita:

    cantidad_inicial + suma(movimientos del lote) == cantidad_actual

Cuando esa igualdad se rompe, el stock y su historia dejaron de contar lo
mismo — que es exactamente lo que producian el doble movimiento por ajuste, la
reaplicacion al guardar y el borrado destructivo del ledger.
"""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.inventario.models import (
    AjusteInventario,
    Compra,
    DetalleCompra,
    Lote,
    MovimientoLote,
)
from apps.inventario.services import (
    AjusteInvalidoError,
    LoteNoEncontradoError,
    StockInsuficienteLoteError,
    registrar_ajuste_service,
)
from apps.permisos import testing as permisos_testing
from apps.productos.models import Categoria, Producto

User = get_user_model()


class InventarioTestCase(TestCase):
    """Fixture comun: un admin, una cajera sin permisos y una compra con lote."""

    def setUp(self):
        cache.clear()

        self.admin = User.objects.create_user(
            username='admin_inv_aud', email='admin_inv_aud@test.local',
            password='pass', rol='ADMIN', activo=True,
        )
        self.cajera = User.objects.create_user(
            username='cajera_inv_aud', email='cajera_inv_aud@test.local',
            password='pass', rol='CAJERA', activo=True,
        )
        self.categoria = Categoria.objects.create(nombre='Inventario Auditoria')
        self.producto = self._producto('INV-AUD-001')

        self.compra = Compra.objects.create(
            usuario=self.admin, proveedor='Proveedor Test',
            numero_factura='FAC-AUD-001', total=Decimal('0'),
        )
        self.detalle = DetalleCompra.objects.create(
            compra=self.compra, producto=self.producto,
            cantidad=10, costo_unitario=Decimal('4.00'), subtotal=Decimal('40.00'),
        )
        self.compra.total = Decimal('40.00')
        self.compra.save()
        self.lote = self.detalle.lote

    def tearDown(self):
        cache.clear()

    def _producto(self, sku, activo=True):
        return Producto.objects.create(
            sku=sku, codigo_barras=sku, nombre=f'Producto {sku}',
            descripcion='', categoria=self.categoria, precio_venta='100.00',
            stock_minimo=1, activo=activo, estado='nuevo', marca='',
            atributos={},
        )

    def _asertar_invariante(self, lote):
        """cantidad_inicial + suma(movimientos) == cantidad_actual."""
        lote.refresh_from_db()
        suma = sum(m.cantidad for m in lote.movimientos.all())
        # El movimiento inicial de COMPRA ya representa `cantidad_inicial`,
        # asi que la suma de TODOS los movimientos debe dar el saldo actual.
        self.assertEqual(
            suma, lote.cantidad_actual,
            f'ledger={suma} vs lote={lote.cantidad_actual} '
            f'({list(lote.movimientos.values_list("tipo", "cantidad"))})',
        )


class PermisosInventarioTests(InventarioTestCase):
    """INVENTARIO-001: los gates viven en el servidor, no en la plantilla."""

    URLS_GET = (
        ('compras.ver', 'inventario:compras_lista', ()),
        ('compras.registrar', 'inventario:compra_crear', ()),
    )

    def test_cajera_sin_permisos_no_entra_a_compras(self):
        self.client.force_login(self.cajera)

        for permiso, nombre, args in self.URLS_GET:
            with self.subTest(url=nombre):
                resp = self.client.get(reverse(nombre, args=args))
                self.assertEqual(resp.status_code, 302, f'{nombre} no redirige')

    def test_cajera_sin_permisos_no_puede_crear_una_compra(self):
        """
        El escenario demostrado en la auditoria: POST directo a compra_crear
        devolvia 200 y creaba compra + lote.
        """
        self.client.force_login(self.cajera)
        compras_antes = Compra.objects.count()

        resp = self.client.post(
            reverse('inventario:compra_crear'),
            data=json.dumps({
                'proveedor': 'Proveedor Colado',
                'productos': [{
                    'producto_id': self.producto.id,
                    'cantidad': 50,
                    'costo_unitario': 10.00,
                }],
            }),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Compra.objects.count(), compras_antes)

    def test_cajera_sin_permisos_no_ve_lotes_ni_ajusta(self):
        self.client.force_login(self.cajera)

        lotes = self.client.get(
            reverse('inventario:api_lotes_producto', args=[self.producto.id])
        )
        self.assertEqual(lotes.status_code, 403)

        ajuste = self.client.post(
            reverse('inventario:api_ajustar'),
            data=json.dumps({
                'lote_id': self.lote.id, 'tipo': 'MERMA',
                'cantidad': 1, 'motivo': 'intento sin permiso',
            }),
            content_type='application/json',
        )
        self.assertEqual(ajuste.status_code, 403)

    def test_con_el_permiso_correcto_si_entra(self):
        """El gate no puede ser un muro: con el permiso, la operacion pasa."""
        permisos_testing.habilitar_cajero(
            self.cajera, permisos=['compras.ver', 'inventario.ver'],
        )
        self.client.force_login(self.cajera)

        self.assertEqual(
            self.client.get(reverse('inventario:compras_lista')).status_code, 200
        )
        self.assertEqual(
            self.client.get(
                reverse('inventario:api_lotes_producto', args=[self.producto.id])
            ).status_code,
            200,
        )


class ValidacionCompraTests(InventarioTestCase):
    """INVENTARIO-002: crear compras validaba tanto como editar: nada."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def _crear(self, **linea):
        base = {
            'producto_id': self.producto.id,
            'cantidad': 5,
            'costo_unitario': 10.00,
        }
        base.update(linea)
        return self.client.post(
            reverse('inventario:compra_crear'),
            data=json.dumps({'proveedor': 'Proveedor', 'productos': [base]}),
            content_type='application/json',
        )

    def test_cantidad_negativa_es_rechazada(self):
        compras_antes = Compra.objects.count()

        resp = self._crear(cantidad=-3)

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Compra.objects.count(), compras_antes)
        self.assertFalse(Lote.objects.filter(cantidad_inicial__lt=0).exists())

    def test_cantidad_cero_es_rechazada(self):
        self.assertEqual(self._crear(cantidad=0).status_code, 400)

    def test_costo_no_positivo_es_rechazado(self):
        self.assertEqual(self._crear(costo_unitario=0).status_code, 400)

    def test_producto_inactivo_es_rechazado(self):
        inactivo = self._producto('INV-AUD-INACTIVO', activo=False)
        compras_antes = Compra.objects.count()

        resp = self._crear(producto_id=inactivo.id)

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Compra.objects.count(), compras_antes)

    def test_una_compra_valida_sigue_pasando(self):
        resp = self._crear()

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['success'])


class AjusteUnMovimientoTests(InventarioTestCase):
    """INVENTARIO-003: un ajuste = un cambio de stock = UN movimiento."""

    def test_el_endpoint_crea_exactamente_un_movimiento(self):
        self.client.force_login(self.admin)
        movimientos_antes = self.lote.movimientos.count()

        resp = self.client.post(
            reverse('inventario:api_ajustar'),
            data=json.dumps({
                'lote_id': self.lote.id, 'tipo': 'MERMA',
                'cantidad': 2, 'motivo': 'Rotura en almacen',
            }),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200, resp.content)

        nuevos = self.lote.movimientos.exclude(tipo='COMPRA')
        self.assertEqual(nuevos.count(), 1, list(nuevos.values('tipo', 'cantidad')))

        movimiento = nuevos.get()
        # El tipo real, no 'AJUSTE': el modelo escribia AJUSTE y la vista MERMA,
        # asi que quedaban los dos con clasificaciones distintas.
        self.assertEqual(movimiento.tipo, 'MERMA')
        self.assertEqual(movimiento.cantidad, -2)

        self.assertEqual(self.lote.movimientos.count(), movimientos_antes + 1)
        self._asertar_invariante(self.lote)

    def test_devolucion_suma_stock(self):
        registrar_ajuste_service(
            usuario=self.admin, lote_id=self.lote.id, tipo='DEVOLUCION',
            cantidad=3, motivo='Devolucion del cliente',
        )

        self.lote.refresh_from_db()
        self.assertEqual(self.lote.cantidad_actual, 13)
        self._asertar_invariante(self.lote)


class AjusteInmutableTests(InventarioTestCase):
    """INVENTARIO-004: guardar un ajuste ya aplicado no vuelve a mover stock."""

    def test_reguardar_un_ajuste_no_reaplica_la_cantidad(self):
        ajuste = registrar_ajuste_service(
            usuario=self.admin, lote_id=self.lote.id, tipo='MERMA',
            cantidad=2, motivo='Rotura en almacen',
        )
        self.lote.refresh_from_db()
        self.assertEqual(self.lote.cantidad_actual, 8)
        movimientos = self.lote.movimientos.count()

        # Solo cambia el texto. Antes esto descontaba 2 unidades OTRA VEZ y
        # agregaba un segundo movimiento.
        ajuste.motivo = 'Rotura en almacen (corregido)'
        ajuste.save()

        self.lote.refresh_from_db()
        self.assertEqual(self.lote.cantidad_actual, 8)
        self.assertEqual(self.lote.movimientos.count(), movimientos)
        self._asertar_invariante(self.lote)

    def test_crear_un_ajuste_a_mano_ya_no_mueve_inventario(self):
        """
        `AjusteInventario` es un registro, no un aplicador: la unica via que
        mueve stock es el service.
        """
        AjusteInventario.objects.create(
            lote=self.lote, tipo='MERMA', cantidad=-5,
            motivo='Creado directo por ORM', usuario=self.admin,
        )

        self.lote.refresh_from_db()
        self.assertEqual(self.lote.cantidad_actual, 10)
        self._asertar_invariante(self.lote)


class ContratoAjusteTests(InventarioTestCase):
    """INVENTARIO-013 y 014: errores tipados, no 500 ni UnicodeEncodeError."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def _ajustar(self, **over):
        base = {
            'lote_id': self.lote.id, 'tipo': 'MERMA',
            'cantidad': 1, 'motivo': 'Motivo suficientemente largo',
        }
        base.update(over)
        return self.client.post(
            reverse('inventario:api_ajustar'),
            data=json.dumps(base), content_type='application/json',
        )

    def test_lote_inexistente_es_404(self):
        """
        Antes caia en el `except Exception`, cuyo `print` con emoji reventaba
        con UnicodeEncodeError en la consola cp1252 del proyecto: el cliente ni
        siquiera recibia el JSON.
        """
        resp = self._ajustar(lote_id=999999)

        self.assertEqual(resp.status_code, 404)
        self.assertFalse(resp.json()['success'])

    def test_stock_insuficiente_es_400(self):
        resp = self._ajustar(cantidad=999)

        self.assertEqual(resp.status_code, 400)
        self.assertIn('insuficiente', resp.json()['error'].lower())

    def test_tipo_invalido_es_400(self):
        self.assertEqual(self._ajustar(tipo='INVENTADO').status_code, 400)

    def test_motivo_corto_es_400(self):
        self.assertEqual(self._ajustar(motivo='corto').status_code, 400)

    def test_producto_inexistente_en_lotes_es_404(self):
        resp = self.client.get(
            reverse('inventario:api_lotes_producto', args=[999999])
        )
        self.assertEqual(resp.status_code, 404)

    def test_el_service_levanta_excepciones_tipadas(self):
        with self.assertRaises(LoteNoEncontradoError) as ctx:
            registrar_ajuste_service(
                usuario=self.admin, lote_id=999999, tipo='MERMA',
                cantidad=1, motivo='Motivo suficientemente largo',
            )
        self.assertEqual(ctx.exception.status_code, 404)

        with self.assertRaises(StockInsuficienteLoteError):
            registrar_ajuste_service(
                usuario=self.admin, lote_id=self.lote.id, tipo='MERMA',
                cantidad=999, motivo='Motivo suficientemente largo',
            )

        with self.assertRaises(AjusteInvalidoError):
            registrar_ajuste_service(
                usuario=self.admin, lote_id=self.lote.id, tipo='MERMA',
                cantidad=0, motivo='Motivo suficientemente largo',
            )


class NumeracionTests(InventarioTestCase):
    """INVENTARIO-008: la secuencia no se deriva del conteo de filas vivas."""

    def test_un_hueco_en_los_lotes_no_reutiliza_un_numero(self):
        """
        Este escenario NO necesita concurrencia: basta eliminar una linea no
        final el mismo dia, que es justamente lo que hace corregir una compra.
        """
        segundo = DetalleCompra.objects.create(
            compra=self.compra, producto=self.producto,
            cantidad=5, costo_unitario=Decimal('4.00'), subtotal=Decimal('20.00'),
        )
        numero_borrado = self.lote.numero_lote
        self.assertTrue(numero_borrado.endswith('-00001'))
        self.assertTrue(segundo.lote.numero_lote.endswith('-00002'))

        # Se libera el primer numero (hueco no final).
        self.lote.detalle_compra = None
        self.lote.save(update_fields=['detalle_compra'])
        self.detalle.delete()
        self.lote.delete()

        tercero = DetalleCompra.objects.create(
            compra=self.compra, producto=self.producto,
            cantidad=1, costo_unitario=Decimal('4.00'), subtotal=Decimal('4.00'),
        )

        # Con `count() + 1` el conteo restante era 1 y se reproponia -00002,
        # que ya existia: IntegrityError sin concurrencia de por medio.
        self.assertTrue(tercero.lote.numero_lote.endswith('-00003'))
        self.assertNotEqual(tercero.lote.numero_lote, segundo.lote.numero_lote)

    def test_las_compras_tampoco_reutilizan_numero(self):
        primera = Compra.objects.create(
            usuario=self.admin, proveedor='P1', total=Decimal('1.00'),
        )
        segunda = Compra.objects.create(
            usuario=self.admin, proveedor='P2', total=Decimal('1.00'),
        )
        numeros_previos = {self.compra.numero_compra, primera.numero_compra,
                           segunda.numero_compra}
        primera.delete()

        tercera = Compra.objects.create(
            usuario=self.admin, proveedor='P3', total=Decimal('1.00'),
        )

        self.assertNotIn(tercera.numero_compra, numeros_previos)


class ContratoDetalleCompraTests(InventarioTestCase):
    """INVENTARIO-009: la guarda del lote es persistente, no un flag efimero."""

    def test_reguardar_un_detalle_recargado_no_intenta_crear_otro_lote(self):
        """
        El flag `_lote_creado` solo existia en la instancia que habia creado el
        lote; al recargar desde BD se perdia y `save()` moria con
        IntegrityError contra el OneToOne.
        """
        recargado = DetalleCompra.objects.get(pk=self.detalle.pk)

        recargado.save()  # sin cambios

        self.assertEqual(Lote.objects.filter(detalle_compra=recargado).count(), 1)

    def test_actualizar_un_detalle_recargado_funciona(self):
        recargado = DetalleCompra.objects.get(pk=self.detalle.pk)
        recargado.costo_unitario = Decimal('7.00')

        recargado.save()

        recargado.refresh_from_db()
        self.assertEqual(recargado.costo_unitario, Decimal('7.00'))
        self.assertEqual(Lote.objects.filter(detalle_compra=recargado).count(), 1)


class AdminInventarioTests(InventarioTestCase):
    """INVENTARIO-011, 012 y 004: el Admin no puede romperse ni mover stock."""

    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_superuser(
            username='staff_inv_aud', email='staff_inv_aud@test.local',
            password='pass',
        )
        self.staff.rol = 'ADMIN'
        self.staff.is_staff = True
        self.staff.save(update_fields=['rol', 'is_staff'])
        self.client.force_login(self.staff)

        # Los ModelAdmin consultan `request.user` (p.ej. para decidir si
        # muestran el "+" de agregar relacionados), asi que necesitan un
        # request real, no None.
        peticion = RequestFactory().get('/admin/')
        peticion.user = self.staff
        self.peticion = peticion

    def test_el_formulario_de_compra_se_puede_construir(self):
        """
        `fecha_compra` es auto_now_add: estaba en el fieldset sin ser readonly y
        `get_form` levantaba FieldError antes de renderizar.
        """
        from django.contrib import admin as django_admin

        from apps.inventario.admin import CompraAdmin

        compra_admin = CompraAdmin(Compra, django_admin.site)

        # No debe levantar.
        compra_admin.get_form(self.peticion, self.compra)

    def test_la_pantalla_de_compra_responde(self):
        resp = self.client.get(
            reverse('admin:inventario_compra_change', args=[self.compra.id])
        )
        self.assertEqual(resp.status_code, 200)

    def test_el_porcentaje_consumido_del_lote_se_calcula(self):
        """`Lote.get_porcentaje_consumido()` nunca existio: era AttributeError."""
        from django.contrib import admin as django_admin

        from apps.inventario.admin import LoteAdmin

        lote_admin = LoteAdmin(Lote, django_admin.site)

        self.assertEqual(lote_admin.porcentaje_consumido(self.lote), '0.0%')

        registrar_ajuste_service(
            usuario=self.admin, lote_id=self.lote.id, tipo='MERMA',
            cantidad=5, motivo='Consumo para la prueba',
        )
        self.lote.refresh_from_db()
        self.assertEqual(lote_admin.porcentaje_consumido(self.lote), '50.0%')

    def test_la_pantalla_de_lote_responde(self):
        resp = self.client.get(
            reverse('admin:inventario_lote_change', args=[self.lote.id])
        )
        self.assertEqual(resp.status_code, 200)

    def test_los_ajustes_son_inmutables_desde_el_admin(self):
        from django.contrib import admin as django_admin

        from apps.inventario.admin import AjusteInventarioAdmin

        ajuste_admin = AjusteInventarioAdmin(AjusteInventario, django_admin.site)

        self.assertFalse(ajuste_admin.has_add_permission(self.peticion))
        self.assertFalse(ajuste_admin.has_change_permission(self.peticion))
        self.assertFalse(ajuste_admin.has_delete_permission(self.peticion))
