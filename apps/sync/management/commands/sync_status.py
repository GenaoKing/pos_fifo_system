"""
apps/sync/management/commands/sync_status.py

Muestra un resumen rapido del estado de sincronizacion.

Uso:
    python manage.py sync_status

Salida tipica:
    Cloud URL:       https://pos-cloud.../
    Conectividad:    OK
    Eventos pendientes:   3
    Eventos en error:     1
    Eventos confirmados:  142
    Ultimo log:     EXITOSO hace 45s
    Cursor productos:  2026-04-20 18:30:12
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Muestra un resumen del estado del sync engine.'

    def handle(self, *args, **opts):
        from apps.sync.engine import SyncEngine
        from apps.sync.models import EventoSync, LogSync, VersionMaestro

        self.stdout.write(self.style.MIGRATE_HEADING('=== Estado del Sync Engine ==='))

        sync_enabled = getattr(settings, 'SYNC_ENABLED', False)
        cloud_url = getattr(settings, 'CLOUD_API_URL', '(no configurado)')
        sucursal = getattr(settings, 'SUCURSAL_CODIGO', '(no configurada)')

        self.stdout.write(f'SYNC_ENABLED:    {sync_enabled}')
        self.stdout.write(f'Cloud URL:       {cloud_url}')
        self.stdout.write(f'Sucursal:        {sucursal}')

        if not sync_enabled:
            self.stdout.write(self.style.WARNING('\nSync deshabilitado. No hay nada mas que reportar.'))
            return

        # Conectividad
        try:
            engine = SyncEngine()
            online = engine.check_connection()
        except Exception as exc:
            online = False
            self.stderr.write(f'Error iniciando engine: {exc}')

        estado_con = self.style.SUCCESS('OK') if online else self.style.ERROR('SIN CONEXION')
        self.stdout.write(f'Conectividad:    {estado_con}')

        # Eventos
        self.stdout.write('')
        pendientes = EventoSync.objects.filter(estado='PENDIENTE').count()
        errores = EventoSync.objects.filter(estado='ERROR').count()
        confirmados = EventoSync.objects.filter(estado='CONFIRMADO').count()
        descartados = EventoSync.objects.filter(estado='DESCARTADO').count()

        self.stdout.write('Eventos:')
        self.stdout.write(f'  Pendientes:   {pendientes}')
        if errores:
            self.stdout.write(self.style.WARNING(f'  Error:        {errores}'))
        else:
            self.stdout.write(f'  Error:        {errores}')
        self.stdout.write(f'  Confirmados:  {confirmados}')
        if descartados:
            self.stdout.write(self.style.ERROR(f'  Descartados:  {descartados}'))

        # Ultimo log
        self.stdout.write('')
        ultimo = LogSync.objects.order_by('-inicio').first()
        if ultimo:
            hace = timezone.now() - ultimo.inicio
            self.stdout.write(f'Ultimo log:      {ultimo.tipo} {ultimo.resultado} '
                              f'hace {self._fmt_delta(hace)}')
            if ultimo.mensaje:
                self.stdout.write(f'  Mensaje: {ultimo.mensaje[:200]}')
        else:
            self.stdout.write('Ultimo log:      (nunca)')

        # Cursores de maestros
        self.stdout.write('')
        self.stdout.write('Cursores de datos maestros:')
        for cursor in VersionMaestro.objects.all():
            ver = cursor.ultima_version.strftime('%Y-%m-%d %H:%M') if cursor.ultima_version else 'nunca'
            self.stdout.write(f'  {cursor.tabla:<15} {ver}   ({cursor.registros_ultima_sync} ult)')

        if not VersionMaestro.objects.exists():
            self.stdout.write('  (ninguno aun)')

    @staticmethod
    def _fmt_delta(delta):
        secs = int(delta.total_seconds())
        if secs < 60:
            return f'{secs}s'
        if secs < 3600:
            return f'{secs // 60}m {secs % 60}s'
        return f'{secs // 3600}h {(secs % 3600) // 60}m'