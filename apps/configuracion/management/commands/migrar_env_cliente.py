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

from config.env_check import CRITICAS as _CRITICAS_ENV_CHECK

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

# `%NOMBRE%` -- un identificador ENTRE DOS `%` -- es una expansion de cmd sin
# resolver y no tiene sentido en un .env. Un `%` suelto NO lo es: probar solo
# "'%' in valor" (la version vieja de este comando) descartaba en silencio
# cualquier valor con un `%` literal, incluido un DJANGO_SECRET_KEY real que
# traia dos `%` en su alfabeto aleatorio -- se perdio en produccion en Royal
# Plast el 2026-08-22 y el sistema arranco con el SECRET_KEY default del
# repo, sin ningun error, hasta que alguien lo noto a mano. Este patron exige
# el PAR de `%` con un identificador valido en medio.
_EXPANSION_CMD = re.compile(r'%[A-Za-z_][A-Za-z0-9_]*%')

# Ademas de lo que ya considera critico el chequeo de arranque (hoy solo
# DJANGO_SECRET_KEY), la conexion a la BD es igual de catastrofica si se
# pierde en silencio: una contrasena con un `%` real dejaria la instalacion
# sin poder conectar, con el mismo patron exacto de perdida silenciosa.
_CRITICAS = frozenset(_CRITICAS_ENV_CHECK) | {'DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_HOST'}


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

        variables, ignoradas, valores_originales = self._parsear(origen)
        if not variables:
            raise CommandError(f'No se encontro ninguna variable en {origen}')

        contenido = self._render(variables, origen)

        if opts['dry_run']:
            # CFG-004: escribia el contenido COMPLETO a stdout, y el origen
            # contiene passwords y tokens — el propio docstring del comando
            # lo dice. Un `DB_PASSWORD` de prueba apareció literal en la
            # salida capturada. "dry-run" sugiere que es seguro porque no
            # escribe archivo, no que imprime todo su contenido en una
            # consola que termina en un ticket de soporte o en el log de CI.
            self.stdout.write(self._render_redactado(variables, origen))
            self.stdout.write(self.style.WARNING(
                f'\n(dry-run: no se escribio nada. {len(variables)} '
                f'variables. Los valores sensibles van enmascarados.)'
            ))
            return

        destino.write_text(contenido, encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(
            f'{len(variables)} variables escritas en {destino}'
        ))

        # Las omitidas por ser propias del .bat (POS_DIR, BACKUP_DIR) son
        # ruido esperado. Las omitidas por parecer una expansion de cmd sin
        # resolver son sospechosas SIEMPRE, y si ademas es una variable
        # critica, no basta con avisar de pasada: el archivo ya se escribio
        # (no se pierde lo demas), pero el comando tiene que salir en rojo
        # para que nadie lea "22 variables escritas" y siga de largo.
        omitidas_por_bat = ignoradas & _OMITIR
        omitidas_sospechosas = ignoradas - _OMITIR
        if omitidas_por_bat:
            self.stdout.write(f'  Omitidas (propias del .bat): {", ".join(sorted(omitidas_por_bat))}')

        omitidas_criticas = omitidas_sospechosas & _CRITICAS
        if omitidas_sospechosas:
            self.stdout.write(self.style.WARNING(
                f'  Omitidas por traer una expansion de cmd sin resolver: '
                f'{", ".join(sorted(omitidas_sospechosas))}'
            ))
            for nombre in sorted(omitidas_sospechosas):
                self.stdout.write(f'    {nombre}={valores_originales.get(nombre, "?")}')

        self.stdout.write('')
        self.stdout.write('Siguientes pasos:')
        self.stdout.write('  1. python manage.py verificar_instalacion')
        self.stdout.write('  2. Re-registrar los servicios (ahora solo pasan 2 variables):')
        self.stdout.write('       deploy\\registrar_servicio.bat')
        self.stdout.write('       deploy\\registrar_sync_servicio.bat')

        if omitidas_criticas:
            raise CommandError(
                f'{len(omitidas_criticas)} variable(s) CRITICA(S) quedaron fuera de '
                f'{destino.name}: {", ".join(sorted(omitidas_criticas))}. El resto del '
                f'archivo SI se escribio, pero sin estas la instalacion no arranca '
                f'(o arranca insegura, con el default del repo). Agregarlas a mano al '
                f'.env con su valor real -- esta arriba, "Omitidas por traer una '
                f'expansion..." -- antes de continuar.'
            )

    # ------------------------------------------------------------------

    def _parsear(self, origen):
        """Devuelve (variables, ignoradas, valores_originales). Ultimo `set` gana, como en cmd."""
        variables = {}
        ignoradas = set()
        # Valor crudo de TODO lo que se vio en un `set`, incluido lo omitido.
        # Sirve para mostrarle al operador el valor real de una variable
        # critica que se omitio, sin que tenga que ir a abrir el .bat.
        valores_originales = {}

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
            valores_originales[nombre] = valor
            if nombre in _OMITIR:
                ignoradas.add(nombre)
                continue
            # Expansion de cmd sin resolver (`%NOMBRE%` pareado): no tiene
            # sentido en un .env. Un `%` suelto SI es un valor literal valido
            # -- ver el docstring de _EXPANSION_CMD arriba.
            if _EXPANSION_CMD.search(valor):
                ignoradas.add(nombre)
                continue
            variables[nombre] = valor

        # Aplicar los alias: si el .bat definia PRINTER_TERMICA con valor y no
        # habia THERMAL_PRINTER_NAME, ese era el nombre efectivo.
        for viejo, nuevo in _ALIAS.items():
            valor = variables.pop(viejo, '')
            if valor and not variables.get(nuevo):
                variables[nuevo] = valor

        return variables, ignoradas, valores_originales

    # Nombres cuyo VALOR nunca se imprime. Se decide por el NOMBRE de la
    # variable, no por su contenido: un secreto no se reconoce mirandolo.
    _PATRONES_SENSIBLES = (
        'PASSWORD', 'SECRET', 'TOKEN', 'KEY', 'API', 'PIN', 'CERT',
        'CREDENTIAL', 'AUTH',
    )

    def _es_sensible(self, nombre):
        return any(p in nombre.upper() for p in self._PATRONES_SENSIBLES)

    def _enmascarar(self, valor):
        """
        Muestra lo justo para reconocer el valor, no para reutilizarlo.

        Con menos de 8 caracteres no se muestra ninguno: en un secreto
        corto, dos caracteres de cada punta ya son una fraccion util.
        """
        if not valor:
            return '(vacio)'
        if len(valor) < 8:
            return f'*** ({len(valor)} caracteres)'
        return f'{valor[:2]}***{valor[-2:]} ({len(valor)} caracteres)'

    def _render_redactado(self, variables, origen):
        """El mismo archivo que se escribiria, con los secretos ocultos."""
        lineas = [f'# (dry-run de {origen})']
        for nombre, valor in variables.items():
            if self._es_sensible(nombre):
                lineas.append(f'{nombre}={self._enmascarar(str(valor))}')
            else:
                lineas.append(f'{nombre}={valor}')
        return chr(10).join(lineas)

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
