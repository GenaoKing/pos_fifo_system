"""
apps/configuracion/management/commands/rotar_secret_key.py

Rota `DJANGO_SECRET_KEY` sobre el `.env` CANONICO de la instalacion
(`deploy/env_cliente.env` por defecto), nunca sobre un `env_cliente.bat` que
ya no consume la aplicacion (C01, punto 5 del encargo).

Reemplaza SOLO la linea de `DJANGO_SECRET_KEY`, preservando el resto del
archivo tal cual (comentarios, orden, otras variables) -- no lo regenera
desde cero como hace `migrar_env_cliente`, que parte de un origen distinto
(el `.bat` legado) y sí necesita reconstruir el formato completo.

Por que hace falta un comando dedicado en vez de editar el archivo a mano:
la instalacion nueva pide generar la key con
`python -c "...get_random_secret_key()..."` y pegarla en el `.env` (ver
`docs/runbooks/INSTALACION_CLIENTE_NUEVO.md`); copiar y pegar a mano es
exactamente donde se cometio el error original (bug #9) y donde se pierde el
respaldo de la key anterior si algo sale mal.

Por que rotar el `.env` alcanza (precedencia CT-03, acordada con A01):
`config/settings.py` carga con `load_dotenv(POS_ENV_FILE, override=False)`.
`override=False` significa que una variable de PROCESO real le gana al
archivo -- pero en operacion normal `DJANGO_SECRET_KEY` no se fija como
variable de proceso en ningun lado (el servicio NSSM solo trae
`DJANGO_SETTINGS_MODULE` y `POS_ENV_FILE`, ver `registrar_servicio.bat`), asi
que la del `.env` es la que efectivamente se usa. Si alguien la exporto ademas
como variable de entorno del sistema/servicio, esa gana y hay que retirarla
ahi, no alcanza con rotar el archivo.

Uso:
    python manage.py rotar_secret_key
    python manage.py rotar_secret_key --archivo deploy/env_cliente.env
    python manage.py rotar_secret_key --forzar   # sin confirmacion interactiva
"""
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.management.utils import get_random_secret_key

from config.env_check import MIN_SECRET_KEY

_VARIABLE = 'DJANGO_SECRET_KEY'


class Command(BaseCommand):
    help = (
        'Rota DJANGO_SECRET_KEY sobre el .env canonico de la instalacion. '
        'Invalida las sesiones abiertas.'
    )

    def add_arguments(self, parser):
        base = Path(settings.BASE_DIR) / 'deploy'
        parser.add_argument('--archivo', default=str(base / 'env_cliente.env'))
        parser.add_argument(
            '--forzar', action='store_true',
            help='No pedir confirmacion interactiva antes de rotar.',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Muestra que haria, sin escribir el archivo.',
        )

    def handle(self, *args, **opts):
        archivo = Path(opts['archivo'])

        if not archivo.is_file():
            bat = archivo.with_suffix('.bat')
            sugerencia = (
                f' Se encontro "{bat}": conviertalo primero con '
                f'"python manage.py migrar_env_cliente".'
            ) if bat.is_file() else ''
            raise CommandError(
                f'No existe {archivo}. La rotacion opera SOLO sobre el .env '
                f'canonico -- nunca sobre un .bat, que ya no lee la '
                f'aplicacion.{sugerencia}'
            )

        contenido = archivo.read_text(encoding='utf-8')
        lineas = contenido.splitlines(keepends=True)
        indices_activos = self._indices_de(lineas)

        nueva_key = get_random_secret_key()
        if len(nueva_key) < MIN_SECRET_KEY:  # pragma: no cover - defensivo
            raise CommandError(
                'La key generada no supera la longitud minima esperada '
                f'({MIN_SECRET_KEY}). No se escribio nada; reintente.'
            )
        nueva_linea = f'{_VARIABLE}={nueva_key}\n'

        if opts['dry_run']:
            accion = 'reemplazaria' if indices_activos else 'agregaria'
            self.stdout.write(
                f'(dry-run) {accion} la linea de {_VARIABLE} en {archivo}. '
                f'No se imprime el valor ni se escribe nada.'
            )
            return

        if not opts['forzar']:
            respuesta = input(
                f'Esto va a rotar {_VARIABLE} en {archivo} e invalidar TODAS '
                f'las sesiones abiertas. Escriba SI para continuar: '
            )
            if respuesta.strip().upper() != 'SI':
                self.stdout.write('Cancelado. No se modifico nada.')
                return

        respaldo = archivo.with_name(
            f'{archivo.name}.bak_{datetime.now():%Y%m%d_%H%M%S}'
        )
        respaldo.write_text(contenido, encoding='utf-8')

        if indices_activos:
            # Si habia duplicados (no deberia, pero un .env se edita a mano),
            # la efectiva es la ULTIMA -- coincide con como la resuelve
            # `dotenv_values`/`load_dotenv`. Rotar solo esa y no dejar las
            # anteriores con la key vieja tirada en el archivo.
            ultimo = indices_activos[-1]
            lineas[ultimo] = nueva_linea
            for i in reversed(indices_activos[:-1]):
                del lineas[i]
        else:
            if lineas and not lineas[-1].endswith('\n'):
                lineas[-1] += '\n'
            lineas.append(nueva_linea)

        archivo.write_text(''.join(lineas), encoding='utf-8')

        self.stdout.write(self.style.SUCCESS(
            f'{_VARIABLE} rotada en {archivo}.'
        ))
        self.stdout.write(f'Respaldo de la version anterior: {respaldo}')
        self.stdout.write('')
        self.stdout.write('Siguiente paso:')
        self.stdout.write('  nssm restart POSFifoSystem')
        self.stdout.write(
            '  (NO hace falta re-registrar el servicio: solo reiniciarlo. '
            'Esto invalida las sesiones abiertas.)'
        )

    def _indices_de(self, lineas):
        """Indices de todas las lineas `DJANGO_SECRET_KEY=...` activas (no
        comentadas), en orden. Vacio si no existe ninguna."""
        prefijo = f'{_VARIABLE}='
        return [
            i for i, linea in enumerate(lineas)
            if not linea.strip().startswith('#') and linea.strip().startswith(prefijo)
        ]
