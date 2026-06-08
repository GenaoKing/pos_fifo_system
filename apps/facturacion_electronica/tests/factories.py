"""
apps/facturacion_electronica/tests/factories.py

Factories factory_boy para crear objetos de test relacionados con la
emisión de e-CF.

Diseño:
- Cada factory crea un objeto mínimamente válido con valores razonables;
  los tests sobrescriben SOLO lo que necesitan para su caso.
- DetalleVenta autocalcula subtotal/descuento_porcentaje/total_linea en
  su save(), por eso la factory solo setea los campos primarios
  (cantidad, precio_unitario, descuento_monto).
- Venta autosetea fecha_venta en save() si no se provee.
- ClienteContadoFactory usa `django_get_or_create` para no duplicar el
  CONTADO genérico (que es único en el sistema).

Helper:
- `crear_venta_con_detalles()`: construye una Venta con N DetalleVenta
  de una sola llamada, lo que la mayoría de tests del mapper necesita.
"""
from decimal import Decimal

import factory
from django.contrib.auth import get_user_model
from factory.django import DjangoModelFactory

from apps.clientes.models import Cliente
from apps.facturacion_electronica.models import Emisor
from apps.productos.models import Categoria, Producto
from apps.ventas.models import DetalleVenta, Venta


# =============================================================================
# Usuario (custom user model)
# =============================================================================

class UsuarioFactory(DjangoModelFactory):
    """
    Crea un Usuario usando create_user() para que la contraseña se hashee
    correctamente. No nos importa el password en tests del mapper, pero
    si se reutiliza esta factory para tests de vistas, vendrá útil.
    """
    class Meta:
        model = get_user_model()
        django_get_or_create = ('username',)

    username = factory.Sequence(lambda n: f'usuario_test_{n}')
    email = factory.Sequence(lambda n: f'usuario_test_{n}@test.local')
    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        manager = cls._get_manager(model_class)
        email = kwargs.pop('email', None)
        return manager.create_user(*args, password='test1234', **kwargs, email=email)


# =============================================================================
# Productos y categorías
# =============================================================================

class CategoriaFactory(DjangoModelFactory):
    class Meta:
        model = Categoria
        django_get_or_create = ('nombre',)

    nombre = factory.Sequence(lambda n: f'Categoria Test {n}')
    activa = True
    tipo_negocio = 'general'


class ProductoFactory(DjangoModelFactory):
    class Meta:
        model = Producto

    sku = factory.Sequence(lambda n: f'PROD-TEST-{n:04d}')
    nombre = factory.Sequence(lambda n: f'Producto Test {n}')
    precio_venta = Decimal('100.00')
    stock_minimo = 5
    activo = True
    categoria = factory.SubFactory(CategoriaFactory)


# =============================================================================
# Clientes (tres variantes según el caso fiscal)
# =============================================================================

class ClienteContadoFactory(DjangoModelFactory):
    """
    Cliente CONTADO genérico. Es el placeholder del sistema para ventas
    sin cliente real; fiscalmente equivale a ausencia de comprador.

    `django_get_or_create` para que múltiples tests no creen duplicados
    (el CONTADO real es uno solo en el sistema).
    """
    class Meta:
        model = Cliente
        django_get_or_create = ('tipo', 'nombre')

    tipo = 'CONTADO'
    nombre = 'CLIENTE CONTADO'
    activo = True


class ClienteConRNCFactory(DjangoModelFactory):
    """
    Cliente real con RNC. Usado para tests de tipo 31 (crédito fiscal)
    que requiere comprador identificado.

    El RNC se genera con sequence para evitar colisión con el unique
    constraint de cedula_rnc.
    """
    class Meta:
        model = Cliente

    tipo = 'CORPORATIVO'
    nombre = factory.Sequence(lambda n: f'Empresa Test {n}')
    cedula_rnc = factory.Sequence(lambda n: f'13{n:07d}')  # 9 dígitos
    direccion = 'Calle Test 123, Santo Domingo'
    activo = True


class ClientePersonalSinRNCFactory(DjangoModelFactory):
    """
    Cliente personal SIN cédula/RNC. Usado para tests donde tipo 31 debe
    fallar por falta de identificación fiscal y donde tipo 32 debe
    aceptar pero retornar `rnc_o_cedula=None`.
    """
    class Meta:
        model = Cliente

    tipo = 'PERSONAL'
    nombre = factory.Sequence(lambda n: f'Persona Test {n}')
    cedula_rnc = None
    activo = True


# =============================================================================
# Ventas y detalles
# =============================================================================

class VentaFactory(DjangoModelFactory):
    """
    Crea una Venta SIN detalles. Para tests que necesitan detalles, usar
    el helper `crear_venta_con_detalles()` o agregar DetalleVentaFactory
    manualmente.

    Default: cliente=None (equivale a CONTADO fiscal), total=118 (un solo
    item de $100 + 18 ITBIS al 18%).
    """
    class Meta:
        model = Venta

    numero_venta = factory.Sequence(lambda n: f'V-TEST-{n:06d}')
    usuario = factory.SubFactory(UsuarioFactory)
    cliente = None
    subtotal = Decimal('118.00')
    descuento_total = Decimal('0.00')
    total = Decimal('118.00')
    estado = 'COMPLETADA'


class DetalleVentaFactory(DjangoModelFactory):
    """
    Crea un DetalleVenta. Subtotal, descuento_porcentaje y total_linea se
    autocalculan en el save() del modelo, NO los seteamos acá.

    Default: 1 unidad a $118 (equivale a $100 base + 18 ITBIS en modo
    incluido).
    """
    class Meta:
        model = DetalleVenta

    venta = factory.SubFactory(VentaFactory)
    producto = factory.SubFactory(ProductoFactory)
    cantidad = 1
    precio_unitario = Decimal('118.00')
    descuento_monto = Decimal('0.00')


# =============================================================================
# Emisor e-CF
# =============================================================================

class EmisorFactory(DjangoModelFactory):
    """
    Crea un Emisor configurado para MSeller en ambiente TesteCF.

    config_proveedor referencia variables de entorno por nombre (no
    valores reales) siguiendo la convención del modelo.
    """
    class Meta:
        model = Emisor

    rnc = factory.Sequence(lambda n: f'13{n:07d}')
    razon_social = factory.Sequence(lambda n: f'Emisor Test {n}')
    nombre_comercial = 'Test'
    direccion = 'Calle Test 456, Santo Domingo'
    proveedor_actual = 'mseller'
    config_proveedor = factory.LazyFunction(lambda: {
        'email_env': 'TEST_MSELLER_EMAIL',
        'password_env': 'TEST_MSELLER_PASSWORD',
        'api_key_env': 'TEST_MSELLER_API_KEY',
        'entorno': 'TesteCF',
        'fecha_vencimiento_secuencia': '31-12-2028',
        'indicador_envio_diferido': 1,
        'tipo_ingresos': '01',
        'tipo_pago': 1,
    })
    activo = True


# =============================================================================
# Helper: crear venta con detalles de una llamada
# =============================================================================

def crear_venta_con_detalles(*, cliente=None, items=None, **venta_kwargs):
    """
    Helper de conveniencia: crea una Venta y sus DetalleVenta asociados.

    La mayoría de tests del mapper necesitan una Venta con al menos un
    DetalleVenta. Sin este helper habría que hacer:
        venta = VentaFactory(cliente=...)
        DetalleVentaFactory(venta=venta, ...)
        DetalleVentaFactory(venta=venta, ...)
    Con el helper queda:
        venta = crear_venta_con_detalles(cliente=..., items=[...])

    Args:
        cliente: instancia Cliente a asignar. None = sin cliente (ausencia
                 fiscal, equivale a CONTADO).
        items: lista de dicts con 'precio_unitario', 'cantidad' y
               opcionalmente 'descuento_monto'. Si None, crea UN item
               default ($118 x 1, sin descuento).
        **venta_kwargs: campos extra para la Venta (numero_venta, total,
                        etc.). Se pasan tal cual a VentaFactory.

    Returns:
        Venta con sus DetalleVenta creados. Note que `total` de la Venta
        NO se recalcula automáticamente; si el test depende de él, pasarlo
        en venta_kwargs.
    """
    venta = VentaFactory(cliente=cliente, **venta_kwargs)

    if items is None:
        items = [{'precio_unitario': Decimal('118.00'), 'cantidad': 1}]

    for item in items:
        DetalleVentaFactory(
            venta=venta,
            cantidad=item.get('cantidad', 1),
            precio_unitario=Decimal(str(item['precio_unitario'])),
            descuento_monto=Decimal(str(item.get('descuento_monto', '0'))),
        )

    return venta