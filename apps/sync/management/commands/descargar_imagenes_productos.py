"""
apps/sync/management/commands/descargar_imagenes_productos.py

Backfill / reparacion de fotos de producto (BUG-G, docs/BUGS.md).

El pull normal (`apps/sync/engine.py::SyncEngine._descargar_imagen_producto`)
baja la foto de cada producto que cambia, pero es best-effort a proposito: si
la descarga falla, no se reintenta hasta que el registro vuelva a cambiar en
el cloud. Este comando es la reparacion manual -- y el backfill inicial la
primera vez que una sucursal actualiza a esta version, cuando la mayoria de
las fotos ya subidas nunca se van a "modificar" para disparar una descarga
sola.

Corre EN LA SUCURSAL, contra el cloud configurado (CLOUD_API_URL/TOKEN).
Recorre TODO el catalogo del cloud (sin ?desde=) -- a proposito NO toca el
cursor de sync (VersionMaestro): es una lectura aparte, pensada para
correrse a mano, no parte del ciclo del daemon.

Uso:
    python manage.py descargar_imagenes_productos              (dry-run)
    python manage.py descargar_imagenes_productos --ejecutar
    python manage.py descargar_imagenes_productos --ejecutar --limite=20
"""
import logging

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger('sync')


def _iterar_productos_cloud(engine):
    """
    Generador: recorre TODOS los productos del cloud, paginando por `next`.

    No usa `_pull_generic` a proposito: ese avanza `VersionMaestro`, y esta
    es una lectura manual aparte del ciclo normal del daemon.
    """
    import requests

    url = engine._url('/api/v1/maestros/productos/')
    params = {}
    while url:
        resp = requests.get(url, params=params, headers=engine.headers, timeout=engine.timeout)
        if resp.status_code >= 400:
            raise CommandError(f'Cloud respondio HTTP {resp.status_code}: {resp.text[:300]}')
        data = resp.json()
        items = data['results'] if isinstance(data, dict) and 'results' in data else data
        yield from items

        siguiente = data.get('next') if isinstance(data, dict) else None
        if not siguiente:
            return
        url, params = siguiente, None


class Command(BaseCommand):
    help = 'Descarga las fotos de producto que no bajaron durante el pull normal.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--ejecutar', action='store_true',
            help='Descarga de verdad. Sin esto, solo reporta que haria (dry-run).',
        )
        parser.add_argument(
            '--limite', type=int,
            help='Procesa como maximo N productos. Util para probar antes de '
                 'comprometerse con el catalogo entero.',
        )

    def handle(self, *args, **options):
        from apps.productos.models import Producto
        from apps.sync.engine import SyncConfigError, SyncEngine

        ejecutar = options['ejecutar']
        limite = options.get('limite')

        engine = SyncEngine()
        try:
            engine._require_config()
        except SyncConfigError as exc:
            raise CommandError(str(exc))

        self.stdout.write(f'Cloud: {engine.cloud_url}')
        self.stdout.write('Buscando fotos pendientes de descargar...')

        candidatos = []
        for item in _iterar_productos_cloud(engine):
            imagen_url = item.get('imagen_url')
            if not imagen_url:
                continue
            producto = Producto.objects.filter(sku=item.get('sku')).first()
            if producto is None:
                # No sincronizado localmente todavia (o pendiente_revision, y
                # el cloud ya lo excluyo del listado para este token). El
                # pull normal de productos lo va a traer solo cuando toque.
                continue
            if imagen_url == producto.imagen_origen_url:
                continue
            candidatos.append((producto, imagen_url))
            if limite and len(candidatos) >= limite:
                break

        self.stdout.write('')
        self.stdout.write(f'{len(candidatos)} producto(s) con foto pendiente.')

        if not ejecutar:
            for producto, _url in candidatos:
                self.stdout.write(f'  DRY-RUN {producto.sku}')
            if candidatos:
                self.stdout.write(self.style.WARNING(
                    '(dry-run: agregar --ejecutar para aplicar.)'
                ))
            return

        descargadas = fallidas = 0
        for producto, imagen_url in candidatos:
            antes = producto.imagen_origen_url
            engine._descargar_imagen_producto(producto, imagen_url)
            producto.refresh_from_db(fields=['imagen_origen_url'])
            if producto.imagen_origen_url != antes:
                descargadas += 1
                self.stdout.write(f'  OK {producto.sku}')
            else:
                fallidas += 1
                self.stdout.write(self.style.WARNING(
                    f'  FALLO {producto.sku} (ver logger "sync" para el detalle)'
                ))

        self.stdout.write('')
        self.stdout.write(f'descargadas: {descargadas}')
        estilo = self.style.WARNING if fallidas else self.style.SUCCESS
        self.stdout.write(estilo(f'fallidas:    {fallidas}'))
