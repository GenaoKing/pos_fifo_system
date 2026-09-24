"""Aceptación PostgreSQL de carreras en servicios y bootstrap RBAC."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase

from apps.auditoria.models import Auditoria
from apps.negocios.models import Negocio
from apps.permisos import testing
from apps.permisos.catalogo import sembrar_catalogo
from apps.permisos.models import AsignacionRol, Permiso, Rol
from apps.permisos.seed import bootstrap
from apps.permisos.services import crear_o_reactivar_asignacion, crear_rol
from apps.sucursales.models import Sucursal


Usuario = get_user_model()


class ConcurrenciaRBACTests(TransactionTestCase):
    def setUp(self):
        sembrar_catalogo(Permiso)
        self.negocio = testing.crear_negocio('Concurrente')
        self.actor = Usuario.objects.create_user(
            username='actor_concurrente',
            email='actor_concurrente@test.local',
            password='pass',
            rol='ADMIN',
            activo=True,
            negocio=self.negocio,
        )

    @staticmethod
    def _correr_dos(funcion):
        barrera = Barrier(2)

        def worker():
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                return funcion()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futuros = [executor.submit(worker) for _ in range(2)]
            return [futuro.result(timeout=30) for futuro in futuros]

    def test_dos_altas_de_rol_convergen_sin_500_ni_slug_duplicado(self):
        def crear():
            negocio = Negocio.objects.get(pk=self.negocio.pk)
            actor = Usuario.objects.get(pk=self.actor.pk)
            permiso = Permiso.objects.get(codigo='clientes.ver')
            return crear_rol(
                actor=actor,
                negocio=negocio,
                nombre='Supervisor',
                permisos=[permiso],
                using='default',
            ).pk

        ids = self._correr_dos(crear)

        roles = Rol.objects.filter(negocio=self.negocio, nombre='Supervisor')
        self.assertEqual(roles.count(), 2)
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(
            set(roles.values_list('slug', flat=True)),
            {'supervisor', 'supervisor-2'},
        )
        self.assertEqual(
            Auditoria.objects.filter(accion='permisos.rol.creado').count(),
            2,
        )

    def test_dos_altas_de_la_misma_asignacion_convergen_en_una_fila(self):
        rol = testing.crear_rol(
            self.negocio, 'Operador', ['clientes.ver'],
        )
        usuario = Usuario.objects.create_user(
            username='operador_concurrente',
            email='operador_concurrente@test.local',
            password='pass',
            rol='CAJERA',
            activo=True,
            negocio=self.negocio,
        )

        def asignar():
            actor = Usuario.objects.get(pk=self.actor.pk)
            usuario_local = Usuario.objects.get(pk=usuario.pk)
            rol_local = Rol.objects.get(pk=rol.pk)
            asignacion, creada = crear_o_reactivar_asignacion(
                actor=actor,
                usuario=usuario_local,
                rol=rol_local,
                using='default',
            )
            return asignacion.pk, creada

        resultados = self._correr_dos(asignar)

        self.assertEqual(len({pk for pk, _ in resultados}), 1)
        self.assertEqual(sorted(creada for _, creada in resultados), [False, True])
        self.assertEqual(
            AsignacionRol.objects.filter(usuario=usuario, rol=rol).count(),
            1,
        )
        self.assertEqual(
            Auditoria.objects.filter(
                accion='permisos.asignacion.creada',
            ).count(),
            1,
        )


class ConcurrenciaBootstrapTests(TransactionTestCase):
    def test_dos_bootstrap_vacios_convergen_en_un_negocio_y_dos_roles(self):
        barrera = Barrier(2)

        def ejecutar():
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                negocio = bootstrap(
                    NegocioModel=Negocio,
                    SucursalModel=Sucursal,
                    UsuarioModel=Usuario,
                    RolModel=Rol,
                    PermisoModel=Permiso,
                    AsignacionRolModel=AsignacionRol,
                    nombre='Bootstrap concurrente',
                    using='default',
                )
                return negocio.pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futuros = [executor.submit(ejecutar) for _ in range(2)]
            ids = [futuro.result(timeout=30) for futuro in futuros]

        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(Negocio.objects.count(), 1)
        self.assertEqual(
            set(Rol.objects.values_list('slug', flat=True)),
            {'administrador', 'cajero'},
        )
