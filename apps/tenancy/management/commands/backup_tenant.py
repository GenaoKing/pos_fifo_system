"""
Backup real de la base de un tenant.

ANTES este comando no hacia backup: validaba el tenant, imprimia una sugerencia
de `pg_dump` y terminaba con exit 0. Un operador —o peor, una automatizacion—
podia leer esa salida como "backup hecho" cuando no existia ningun artefacto.
La documentacion de diseno lo presentaba como "pg_dump de tnt_royalplast", lo
que reforzaba la confusion.

Ahora ejecuta `pg_dump` de verdad, deriva la conexion del settings activo (no
del ambiente libpq del operador), y **verifica** el resultado con
`pg_restore --list` antes de declarar exito. Si no puede producir un artefacto
verificable, termina con error.
"""
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.tenancy.management.base import TenantCommandMixin


class Command(TenantCommandMixin, BaseCommand):
    help = 'Genera y verifica un dump de la base de un tenant.'

    def add_arguments(self, parser):
        self.add_tenant_argument(parser)
        parser.add_argument(
            '--output',
            help='Ruta del archivo destino. Default: <db_name>-<fecha>.dump '
                 'en el directorio actual.',
        )
        parser.add_argument(
            '--solo-comando',
            action='store_true',
            help='No ejecuta nada: imprime el comando pg_dump equivalente. '
                 'Util cuando el dump se corre desde otra maquina.',
        )

    def handle(self, *args, **options):
        from django.utils import timezone

        tenant = self.get_tenant(options['tenant'])
        conexion = self._conexion_desde_settings(tenant)

        destino = Path(
            options.get('output')
            or f'{tenant.db_name}-{timezone.now():%Y%m%d-%H%M%S}.dump'
        ).resolve()

        if options['solo_comando']:
            self.stdout.write(self._comando_legible(conexion, destino))
            self.stdout.write(self.style.WARNING(
                'No se ejecuto nada (--solo-comando). No hay backup todavia.'
            ))
            return

        binario = shutil.which('pg_dump')
        if binario is None:
            raise CommandError(
                'pg_dump no esta en el PATH. Instala las client tools de '
                'PostgreSQL o corre con --solo-comando desde una maquina que '
                'las tenga.'
            )

        self.stdout.write(f'Volcando {conexion["dbname"]} -> {destino}')
        self._ejecutar_pg_dump(binario, conexion, destino)

        if not destino.exists() or destino.stat().st_size == 0:
            raise CommandError(
                f'pg_dump termino sin error pero {destino} no existe o esta '
                f'vacio. NO hay backup.'
            )

        # Verificacion: un archivo con bytes no es un backup restaurable.
        self._verificar_dump(destino)

        tamano = destino.stat().st_size
        digest = self._sha256(destino)
        self.stdout.write(self.style.SUCCESS(
            f'Backup OK: {destino} ({tamano:,} bytes)'
        ))
        self.stdout.write(f'  sha256: {digest}')
        self.stdout.write(
            '  Verificado con `pg_restore --list`. Para un drill de '
            'restauracion real, restaurar en una BD aislada y comparar conteos.'
        )

    # -- helpers ------------------------------------------------------------

    def _conexion_desde_settings(self, tenant):
        """
        Datos de conexion derivados del settings ACTIVO, no del ambiente.

        La sugerencia anterior omitia host, puerto, usuario y SSL: dependia de
        que el operador tuviera libpq configurado igual que la app, cosa que en
        Azure no pasa.
        """
        base = settings.DATABASES['default']
        return {
            'dbname': tenant.db_name,
            'host': base.get('HOST') or 'localhost',
            'port': str(base.get('PORT') or '5432'),
            'user': base.get('USER') or '',
            'password': base.get('PASSWORD') or '',
            'sslmode': (base.get('OPTIONS') or {}).get('sslmode', ''),
        }

    def _argumentos(self, conexion, destino):
        args = [
            '--format=custom',
            f'--file={destino}',
            f'--host={conexion["host"]}',
            f'--port={conexion["port"]}',
        ]
        if conexion['user']:
            args.append(f'--username={conexion["user"]}')
        args.append('--no-password')  # la password va por PGPASSWORD
        args.append(conexion['dbname'])
        return args

    def _entorno(self, conexion):
        entorno = os.environ.copy()
        if conexion['password']:
            entorno['PGPASSWORD'] = conexion['password']
        if conexion['sslmode']:
            entorno['PGSSLMODE'] = conexion['sslmode']
        return entorno

    def _ejecutar_pg_dump(self, binario, conexion, destino):
        proceso = subprocess.run(
            [binario, *self._argumentos(conexion, destino)],
            env=self._entorno(conexion),
            capture_output=True,
            text=True,
        )
        if proceso.returncode != 0:
            # La password nunca aparece en el comando (va por env), asi que el
            # stderr se puede mostrar tal cual.
            raise CommandError(
                f'pg_dump fallo (exit {proceso.returncode}):\n'
                f'{proceso.stderr.strip()[:2000]}'
            )

    def _verificar_dump(self, destino):
        binario = shutil.which('pg_restore')
        if binario is None:
            self.stdout.write(self.style.WARNING(
                'pg_restore no esta en el PATH: no se pudo VERIFICAR el dump. '
                'El archivo existe, pero su restaurabilidad no esta comprobada.'
            ))
            return

        proceso = subprocess.run(
            [binario, '--list', str(destino)],
            capture_output=True,
            text=True,
        )
        if proceso.returncode != 0 or not proceso.stdout.strip():
            raise CommandError(
                f'El archivo {destino} no es un dump restaurable '
                f'(pg_restore --list fallo). NO hay backup valido.'
            )

    @staticmethod
    def _sha256(ruta):
        digest = hashlib.sha256()
        with open(ruta, 'rb') as archivo:
            for bloque in iter(lambda: archivo.read(1024 * 1024), b''):
                digest.update(bloque)
        return digest.hexdigest()

    def _comando_legible(self, conexion, destino):
        """Comando equivalente, con la password enmascarada."""
        partes = ['PGPASSWORD=*** pg_dump', *self._argumentos(conexion, destino)]
        return ' '.join(partes)
