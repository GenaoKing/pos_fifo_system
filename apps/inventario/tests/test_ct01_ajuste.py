"""
apps/inventario/tests/test_ct01_ajuste.py

C05 punto 6 — el ajuste de inventario usa el contrato CT-01
(`registrar_mutacion`), no el adaptador legacy
`Auditoria.registrar_ajuste_inventario`.
"""
from decimal import Decimal

from apps.auditoria.models import Auditoria
from apps.inventario.models import AjusteInventario
from apps.inventario.services import registrar_ajuste_service

from .test_auditoria_inventario import InventarioTestCase


class ProductorCT01AjusteTests(InventarioTestCase):
    def test_ajuste_emite_evento_ct01(self):
        ajuste = registrar_ajuste_service(
            usuario=self.admin, lote_id=self.lote.id, tipo='MERMA',
            cantidad=2, motivo='Merma por rotura CT-01',
        )
        evento = Auditoria.objects.get(
            accion='inventario.ajuste.creado',
            object_id=ajuste.id,
        )
        self.assertEqual(evento.schema_version, 'audit.event.v1')
        self.assertEqual(evento.canal, Auditoria.Canal.POS_LOCAL)
        self.assertEqual(evento.resultado, Auditoria.Resultado.SUCCEEDED)
        self.assertEqual(evento.entity_type, AjusteInventario._meta.label)
        self.assertEqual(evento.datos_nuevos['tipo'], 'MERMA')
        self.lote.refresh_from_db()
        self.assertEqual(
            evento.datos_nuevos['cantidad_actual'], str(self.lote.cantidad_actual),
        )

    def test_no_queda_productor_legacy_de_ajuste(self):
        registrar_ajuste_service(
            usuario=self.admin, lote_id=self.lote.id, tipo='MERMA',
            cantidad=1, motivo='Merma legacy check CT-01',
        )
        # El adaptador legacy usaba TipoAccion.AJUSTE_INVENTARIO.
        self.assertFalse(
            Auditoria.objects.filter(
                accion=Auditoria.TipoAccion.AJUSTE_INVENTARIO,
            ).exists()
        )
