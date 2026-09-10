"""
deploy/preflight_actualizar.py

Puente entre `deploy\\actualizar.bat` y `deploy\\env_cliente.env`, para los
pasos que corren ANTES de que el paquete nuevo este copiado (el backup de la
FASE 2) o que necesitan un valor puntual del .env sin arriesgar que `cmd` lo
reinterprete.

Por que existe: `actualizar.bat` leia el .env con un `for /f ... delims==` y
volcaba cada linea a una variable de BAT con `set "%%A=%%B"`. Ese patron hace
que `cmd` vuelva a parsear el valor ya sustituido buscando pares `%NOMBRE%`
para expandir -- exactamente el defecto que perdio en produccion (Royal
Plast, 2026-08-22) un DJANGO_SECRET_KEY con dos `%` en su alfabeto aleatorio
(ver `_EXPANSION_CMD` en `apps/configuracion/management/commands/
migrar_env_cliente.py`). Aplicado a DB_PASSWORD -- que este mismo bloque
alimentaba a `PGPASSWORD` para el `pg_dump` de la FASE 2, la red de seguridad
de toda la actualizacion -- el mismo patron puede corromper la contrasena de
la BD en silencio, justo en el paso que existe para poder revertir.

Este script reemplaza ese parser: usa `python-dotenv` (ya presente en el venv
VIEJO de cualquier cliente que ya tenga `env_cliente.env`, porque la propia
app no arranca sin el) para leer el archivo, y para datos sensibles actua
directamente -- corre `pg_dump` con la contrasena puesta en el entorno del
subproceso -- en vez de devolver el valor para que `cmd` lo guarde en una
variable. Los valores no sensibles se imprimen para que el `.bat` los lea con
`set /p VAR=<archivo`, que es una lectura literal (no vuelve a interpretar
`%`, `&`, `!` ni comillas).

Se ejecuta con el Python del venv VIEJO de la instalacion (todavia no se
corrio `pip install` del paquete nuevo): por eso no depende de nada del
proyecto Django, solo de la libreria estandar y de `python-dotenv`.
"""
import argparse
import os
import re
import subprocess
import sys

_TOKEN_PLACEHOLDER = 'PEGAR-TOKEN-DE-vincular_sucursal_token'

# Comillas, `!` (dispara la expansion retrasada de cmd si esta activa) o un
# `%NOMBRE%` pareado no sobreviven intactos un viaje como argumento
# entrecomillado de linea de comandos (`--nombre "%X%"`): cmd no soporta
# comillas anidadas -- las de adentro y las de afuera se confunden -- y un
# `!` suelto puede desaparecer en silencio. Esto es orthogonal al problema
# que resuelve `campo`/`backup`: incluso un valor leido perfectamente puede
# corromperse en el punto de USO si despues se pasa asi. No hay arreglo
# entrecomillado equivalente a `set /p` para argv; los llamadores deben leer
# el .env directamente en vez de recibir el valor por linea de comandos.
_RIESGOSO_COMO_ARGUMENTO = re.compile(r'["!]|%[A-Za-z_][A-Za-z0-9_]*%')


def _leer_valores(archivo_env):
    try:
        from dotenv import dotenv_values
    except ImportError as exc:
        raise SystemExit(
            f'[ERROR] python-dotenv no esta disponible en {sys.executable}. '
            f'Es requisito para leer {archivo_env} sin pasar por el parser de '
            f'cmd. Instalelo en el venv de la instalacion viva antes de '
            f'reintentar (pip install python-dotenv).'
        ) from exc
    return dotenv_values(archivo_env, encoding='utf-8')


def cmd_campo(args):
    """Imprime el valor crudo de una variable (o nada si no existe)."""
    valores = _leer_valores(args.archivo_env)
    valor = valores.get(args.nombre)
    if valor is None:
        return 1
    sys.stdout.write(valor)
    return 0


def cmd_backup(args):
    """Corre `pg_dump` con los datos de conexion del .env, sin exponer la
    contrasena a `cmd`: viaja en el entorno del subproceso, no en la linea de
    comando ni en una variable de BAT."""
    valores = _leer_valores(args.archivo_env)
    requeridas = ('DB_NAME', 'DB_USER', 'DB_HOST', 'DB_PORT')
    faltantes = [nombre for nombre in requeridas if not valores.get(nombre)]
    if faltantes:
        print(
            f'[ERROR] Faltan variables de conexion en {args.archivo_env}: '
            f'{", ".join(faltantes)}',
            file=sys.stderr,
        )
        return 1

    entorno = dict(os.environ)
    entorno['PGPASSWORD'] = valores.get('DB_PASSWORD') or ''
    comando = [
        'pg_dump',
        '-U', valores['DB_USER'],
        '-h', valores['DB_HOST'],
        '-p', valores['DB_PORT'],
        '-F', 'c', '-b',
        '-f', args.destino,
        valores['DB_NAME'],
    ]
    try:
        resultado = subprocess.run(comando, env=entorno)
    except FileNotFoundError:
        print('[ERROR] pg_dump no esta en el PATH.', file=sys.stderr)
        return 1
    return resultado.returncode


def cmd_riesgoso_como_argumento(args):
    """Exit 0 si el valor tiene comillas, `!` o un `%NOMBRE%` pareado -- no
    sobrevive intacto un viaje como argumento entrecomillado de linea de
    comandos. No imprime el valor: solo dice si conviene avisar."""
    valores = _leer_valores(args.archivo_env)
    valor = valores.get(args.nombre) or ''
    return 0 if _RIESGOSO_COMO_ARGUMENTO.search(valor) else 1


def cmd_sync_configurado(args):
    """Exit 0 si el sync esta habilitado y con token real; nunca imprime el
    token (ni siquiera en un log de esta corrida)."""
    valores = _leer_valores(args.archivo_env)
    habilitado = (valores.get('SYNC_ENABLED') or '').strip().lower() == 'true'
    token = (valores.get('CLOUD_API_TOKEN') or '').strip()
    configurado = habilitado and bool(token) and token != _TOKEN_PLACEHOLDER
    return 0 if configurado else 1


def _construir_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='accion', required=True)

    p_campo = sub.add_parser('campo', help='Imprime el valor crudo de una variable del .env')
    p_campo.add_argument('nombre')
    p_campo.add_argument('archivo_env')
    p_campo.set_defaults(func=cmd_campo)

    p_backup = sub.add_parser('backup', help='Corre pg_dump con los datos del .env')
    p_backup.add_argument('archivo_env')
    p_backup.add_argument('destino')
    p_backup.set_defaults(func=cmd_backup)

    p_sync = sub.add_parser('sync-configurado', help='Exit 0 si SYNC_ENABLED=true y hay token real')
    p_sync.add_argument('archivo_env')
    p_sync.set_defaults(func=cmd_sync_configurado)

    p_riesgo = sub.add_parser(
        'riesgoso-como-argumento',
        help='Exit 0 si el valor no sobrevive intacto como argumento de linea de comandos',
    )
    p_riesgo.add_argument('nombre')
    p_riesgo.add_argument('archivo_env')
    p_riesgo.set_defaults(func=cmd_riesgoso_como_argumento)

    return parser


def main(argv=None):
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except AttributeError:  # pragma: no cover - Python sin reconfigure
            pass
    args = _construir_parser().parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
