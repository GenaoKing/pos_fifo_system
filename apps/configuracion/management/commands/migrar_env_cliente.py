"""
apps/configuracion/management/commands/migrar_env_cliente.py

Convierte un `deploy/env_cliente.bat` (formato antiguo) al `.env` que ahora lee
la aplicacion.

Las instalaciones existentes tienen su configuracion real en el `.bat`, con
contrasenas y tokens. No se les puede pedir que la reescriban a mano, y copiarla
manualmente es justo donde se cometen los errores que esta fase busca eliminar.

Preserva los valores **tal cual estan**, incluidos los que puedan estar
corrompidos: el objetivo es no perder informacion y que se vea que habia. Para
detectar valores sospechosos esta `verificar_instalacion`.

Uso:
    python manage.py migrar_env_cliente
    python manage.py migrar_env_cliente --origen deploy/env_cliente.bat --destino deploy/env_cliente.env
    python manage.py migrar_env_cliente --dry-run
"""
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

# `set NOMBRE=valor` o `set "NOMBRE=valor"`. El valor puede traer cualquier cosa.
_SET = re.compile(r'^\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=(.*?)"?\s*$', re.IGNORECASE)

# Variables del .bat que NO van al .env: son de su propio scripting.
_OMITIR = {'POS_DIR', 'BACKUP_DIR'}

# El .bat traducia estos alias a lo que la app realmente lee. Con una sola
# fuente de verdad el mapeo sobra, pero hay que respetarlo al convertir.
_ALIAS = {
    'PRINTER_TERMICA': 'THERMAL_PRINTER_NAME',
    'PRINTER_ZEBRA': 'ZEBRA_PRINTER_NAME',
}


class Command(BaseCommand):
    help = 'Convierte deploy/env_cliente.bat al formato .env que lee la aplicacion.'

    def add_arguments(self, parser):
        base = Path(settings.BASE_DIR) / 'deploy'
        parser.add_argument('--origen', default=str(base / 'env_cliente.bat'))
        parser.add_argument('--destino', default=str(base / 'env_cliente.env'))
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Muestra el resultado sin escribir el archivo.',
        )
        parser.add_argument(
            '--forzar', action='store_true',
            help='Sobrescribe el .env si ya existe.',
        )

    def handle(self, *args, **opts):
        origen = Path(opts['origen'])
        destino = Path(opts['destino'])

        if not origen.is_file():
            raise CommandError(f'No existe el archivo de origen: {origen}')
        if destino.exists() and not opts['forzar'] and not opts['dry_run']:
            raise CommandError(
                f'{destino} ya existe. Use --forzar para sobrescribirlo '
                f'(conviene respaldarlo antes).'
            )

        variables, ignoradas = self._parsear(origen)
        if not variables:
            raise CommandError(f'No se encontro ninguna variable en {origen}')

        contenido = self._render(variables, origen)

        if opts['dry_run']:
            self.stdout.write(contenido)
            self.stdout.write(self.style.WARNING(
                f'\n(dry-run: no se escribio nada. {len(variables)} variables)'
            ))
            return

        destino.write_text(contenido, encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(
            f'{len(variables)} variables escritas en {destino}'
        ))
        if ignoradas:
            self.stdout.write(f'  Omitidas (propias del .bat): {", ".join(sorted(ignoradas))}')
        self.stdout.write('')
        self.stdout.write('Siguientes pasos:')
        self.stdout.write('  1. python manage.py verificar_instalacion')
        self.stdout.write('  2. Re-registrar los servicios (ahora solo pasan 2 variables):')
        self.stdout.write('       deploy\\registrar_servicio.bat')
        self.stdout.write('       deploy\\registrar_sync_servicio.bat')

    # ------------------------------------------------------------------

    def _parsear(self, origen):
        """Devuelve (variables, ignoradas). Ultimo `set` gana, como en cmd."""
        variables = {}
        ignoradas = set()

        for linea in origen.read_text(encoding='utf-8', errors='replace').splitlines():
            limpia = linea.strip()
            if not limpia or limpia.upper().startswith('REM') or limpia.startswith('::'):
                continue
            # Las lineas `if defined X set Y=%X%` son el mapeo de alias que
            # resolvemos aparte; no aportan valores.
            if limpia.lower().startswith('if '):
                continue

            m = _SET.match(limpia)
            if not m:
                continue

            nombre, valor = m.group(1).upper(), m.group(2)
            if nombre in _OMITIR:
                ignoradas.add(nombre)
                continue
            # Expansiones de cmd sin resolver: no tienen sentido en un .env.
            if '%' in valor:
                ignoradas.add(nombre)
                continue
            variables[nombre] = valor

        # Aplicar los alias: si el .bat definia PRINTER_TERMICA con valor y no
        # habia THERMAL_PRINTER_NAME, ese era el nombre efectivo.
        for viejo, nuevo in _ALIAS.items():
            valor = variables.pop(viejo, '')
            if valor and not variables.get(nuevo):
                variables[nuevo] = valor

        return variables, ignoradas

    def _render(self, variables, origen):
        lineas = [
            '# ============================================================',
            '# POS FIFO System - Configuracion de la instalacion',
            '#',
            f'# Generado por `manage.py migrar_env_cliente` desde {origen.name}.',
            '#',
            '# Los valores se toman TAL CUAL: no hace falta escapar espacios ni',
            '# simbolos. Para cambiar algo: editar aqui y reiniciar el servicio.',
            '#   nssm restart POSFifoSystem',
            '# NO hace falta volver a registrar el servicio.',
            '# ============================================================',
            '',
        ]
        for nombre in sorted(variables):
            lineas.append(f'{nombre}={variables[nombre]}')
        lineas.append('')
        return '\n'.join(lineas)
