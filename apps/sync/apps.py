"""
apps/sync/apps.py

Configuracion de la app Sync.

Responsable de la sincronizacion entre la sucursal (donde corre este POS)
y la nube (API central con datos consolidados de todas las sucursales).

MODO STANDALONE (`SYNC_ENABLED=False`): los decoradores pasan y NO se envia
nada al cloud, pero los eventos **si se generan** y se acumulan en la cola
local. Es deliberado: encender el sync mas tarde recupera el historico en vez
de perderlo. Ver `apps/sync/events.py` y BUG-A en `docs/BUGS.md`.

(Hasta 2026-08 este docstring decia que "los eventos nunca se generan". Esa era
justamente la causa de que las ventas de una sucursal mal configurada
desaparecieran sin dejar rastro.)
"""
import logging

from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger('sync')


class SyncConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.sync'
    verbose_name = 'Sincronizacion con la nube'

    def ready(self):
        self._avisar_configuracion_incoherente()

    @staticmethod
    def _avisar_configuracion_incoherente():
        """
        Grita si la instalacion tiene credenciales de cloud pero el sync apagado.

        Es el sintoma exacto del modo de falla que costo 7 ventas en Royal Plast:
        el servicio se registro sin `SYNC_ENABLED`, nadie lo noto porque no habia
        error visible, y el daemon de sync reportaba "0 pendientes" con total
        naturalidad. Un WARNING al arrancar es barato y lo hace evidente.
        """
        try:
            sync_enabled = bool(getattr(settings, 'SYNC_ENABLED', False))
            url = getattr(settings, 'CLOUD_API_URL', '') or ''
            token = getattr(settings, 'CLOUD_API_TOKEN', '') or ''
        except Exception:  # pragma: no cover - settings a medio cargar
            return

        if (url or token) and not sync_enabled:
            logger.warning(
                'SYNC: hay credenciales de cloud configuradas (CLOUD_API_URL/'
                'CLOUD_API_TOKEN) pero SYNC_ENABLED=False. Los eventos se '
                'encolan pero NUNCA se envian. Revisar las variables de entorno '
                'del servicio. Diagnostico: manage.py verificar_sync'
            )
        elif sync_enabled and not (url and token):
            logger.warning(
                'SYNC: SYNC_ENABLED=True pero falta CLOUD_API_URL o '
                'CLOUD_API_TOKEN. Los eventos se acumularan sin destino.'
            )
