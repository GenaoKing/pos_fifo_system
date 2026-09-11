"""TEN-016: gate opt-in con dos bases PostgreSQL fisicas y aisladas."""

import copy
import hashlib
import os
import re
from unittest import skipUnless
from unittest import mock

from django.conf import settings
from django.db import connections, transaction
from django.test import TestCase, override_settings

from apps.auditoria.models import Auditoria
from apps.auditoria.services import registrar_mutacion
from apps.negocios.models import Negocio
from apps.productos.models import Categoria, Producto
from apps.sync.engine import SyncEngine
from apps.tenancy.context import (
    force_tenancy,
    reset_current_tenant,
    set_current_tenant,
)
from apps.usuarios.models import Usuario


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
        # Este gate prueba routing/aislamiento, no el grafo historico. Usa el
        # schema actual via syncdb; migrate_tenants tiene su bateria separada.
        'MIGRATE': False,
        'TENANT_ISOLATION_GATE': True,
    })
    config['TEST'] = test_config
    settings.DATABASES[alias] = config
    connections.databases[alias] = config


if NAMESPACE:
    RUN_ID = _identidad(NAMESPACE)
    ALIAS_A = f'tnt_t16_{RUN_ID}_a'
    ALIAS_B = f'tnt_t16_{RUN_ID}_b'
    _registrar_alias(ALIAS_A, f'test_t16_{RUN_ID}_a')
    _registrar_alias(ALIAS_B, f'test_t16_{RUN_ID}_b')
else:
    # La clase se salta y no solicita BDs adicionales al test runner.
    ALIAS_A = ALIAS_B = 'default'


class _PullResponse:
    status_code = 200
    text = ''

    def __init__(self, item):
        self._payload = {
            'count': 1,
            'next': None,
            'previous': None,
            'results': [item],
        }

    def json(self):
        return self._payload


@skipUnless(NAMESPACE, 'TEN-016 requiere TENANT_TEST_DB_NAMESPACE aislado.')
@override_settings(
    TENANCY_DB_PER_TENANT_ENABLED=True,
    SUCURSAL_CODIGO='A05-TENANT',
)
class TenantPhysicalDatabaseIsolationTests(TestCase):
    # Algunas invalidaciones legacy registran on_commit() sin ``using`` y por
    # eso consultan default; el gate lo permite, pero no escribe dominio alli.
    databases = {'default', ALIAS_A, ALIAS_B}

    def _seed(self, alias, tenant_key):
        tokens = set_current_tenant(tenant_key, alias)
        try:
            with force_tenancy(True):
                negocio = Negocio.objects.using(alias).create(
                    nombre=f'Negocio {tenant_key}', slug=tenant_key,
                )
                usuario = Usuario.objects.db_manager(alias).create_human_user(
                    username='admin',
                    email=f'admin@{tenant_key.replace("_", "-")}.example',
                    password='A9!clave-larga-segura',
                    rol='ADMIN',
                    negocio=negocio,
                )
                with transaction.atomic(using=alias):
                    evento = registrar_mutacion(
                        accion='tenant.isolation.created',
                        actor=usuario,
                        entidad=negocio,
                        antes=None,
                        despues={'nombre': negocio.nombre},
                        resultado=Auditoria.Resultado.SUCCEEDED,
                        canal=Auditoria.Canal.COMMAND,
                        tenant=tenant_key,
                        using=alias,
                    )
                return negocio, usuario, evento
        finally:
            reset_current_tenant(tokens)

    def test_mismo_pk_no_comparte_filas_ni_referencias_de_auditoria(self):
        negocio_a, usuario_a, evento_a = self._seed(ALIAS_A, 'gate_a')
        negocio_b, usuario_b, evento_b = self._seed(ALIAS_B, 'gate_b')

        self.assertEqual(negocio_a.pk, negocio_b.pk)
        self.assertEqual(usuario_a.pk, usuario_b.pk)
        self.assertNotEqual(evento_a.entity_ref, evento_b.entity_ref)
        self.assertNotEqual(evento_a.actor_ref, evento_b.actor_ref)

        self.assertEqual(
            list(Negocio.objects.using(ALIAS_A).values_list('slug', flat=True)),
            ['gate_a'],
        )
        self.assertEqual(
            list(Negocio.objects.using(ALIAS_B).values_list('slug', flat=True)),
            ['gate_b'],
        )
        self.assertEqual(Auditoria.objects.using(ALIAS_A).count(), 1)
        self.assertEqual(Auditoria.objects.using(ALIAS_B).count(), 1)

    def _adoptar_producto(self, alias, tenant_key, nombre_local, nombre_cloud):
        tokens = set_current_tenant(tenant_key, alias)
        try:
            with force_tenancy(True):
                categoria = Categoria.objects.using(alias).create(
                    nombre='Categoria A05', origen_cloud_id=44,
                )
                producto = Producto.objects.using(alias).create(
                    sku='A05-TENANT-SKU',
                    nombre=nombre_local,
                    categoria=categoria,
                    precio_venta='10.00',
                )
                item = {
                    'id': 700,
                    'sku': producto.sku,
                    'nombre': nombre_cloud,
                    'descripcion': '',
                    'precio_venta': '20.00',
                    'codigo_barras': None,
                    'categoria': 44,
                    'categoria_nombre': categoria.nombre,
                    'activo': True,
                    'estado': 'nuevo',
                    'marca': '',
                    'stock_minimo': 5,
                    'atributos': {},
                    'fecha_modificacion': '2026-09-11T12:00:00+00:00',
                }
                with mock.patch(
                    'apps.sync.engine.requests.get',
                    return_value=_PullResponse(item),
                ):
                    resultado = SyncEngine(
                        cloud_url='https://cloud.a05.test', token='a05-token',
                    )._pull_productos()
                producto.refresh_from_db(using=alias)
                return resultado, producto
        finally:
            reset_current_tenant(tokens)

    def test_producto_adopta_misma_identidad_sin_cruzar_tenants(self):
        resultado_a, producto_a = self._adoptar_producto(
            ALIAS_A, 'gate_a', 'Local A', 'Cloud tenant A',
        )
        resultado_b, producto_b = self._adoptar_producto(
            ALIAS_B, 'gate_b', 'Local B', 'Cloud tenant B',
        )

        self.assertEqual(resultado_a['count'], 1)
        self.assertEqual(resultado_b['count'], 1)
        self.assertEqual(producto_a.pk, producto_b.pk)
        self.assertEqual(producto_a.origen_cloud_id, 700)
        self.assertEqual(producto_b.origen_cloud_id, 700)
        self.assertEqual(
            Producto.objects.using(ALIAS_A).get(pk=producto_a.pk).nombre,
            'Cloud tenant A',
        )
        self.assertEqual(
            Producto.objects.using(ALIAS_B).get(pk=producto_b.pk).nombre,
            'Cloud tenant B',
        )
