"""Carga determinista del archivo de entorno de una instalacion POS."""

from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def cargar_env_file(*, base_dir, environ):
    """Carga el archivo efectivo y devuelve su ruta absoluta, o ``None``.

    ``POS_ENV_FILE`` es un contrato de instalacion: cuando se declara debe ser
    una ruta absoluta a un archivo legible. Si no se declara, el archivo
    historico ``deploy/env_cliente.env`` es opcional. Las variables ya presentes
    en el proceso siempre prevalecen sobre el contenido del archivo.
    """
    valor_explicito = environ.get('POS_ENV_FILE')
    if valor_explicito is not None:
        valor_explicito = valor_explicito.strip().strip('"').strip("'")
        if not valor_explicito:
            raise ImproperlyConfigured(
                'POS_ENV_FILE esta definida pero vacia; indique una ruta absoluta.'
            )
        ruta = Path(valor_explicito)
        if not ruta.is_absolute():
            raise ImproperlyConfigured(
                f'POS_ENV_FILE debe ser una ruta absoluta: {valor_explicito}'
            )
        requerida = True
    else:
        ruta = Path(base_dir) / 'deploy' / 'env_cliente.env'
        requerida = False

    ruta = ruta.resolve()
    if not ruta.is_file():
        if requerida:
            raise ImproperlyConfigured(
                f'POS_ENV_FILE no existe o no es un archivo: {ruta}'
            )
        return None

    try:
        # Abrir primero permite distinguir un archivo ilegible de uno ausente y
        # produce un error de configuracion con la ruta exacta.
        with ruta.open('r', encoding='utf-8') as archivo:
            archivo.read(1)
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=ruta, override=False, encoding='utf-8')
    except ImportError as exc:  # pragma: no cover - cubierto por el lock
        raise ImproperlyConfigured(
            'python-dotenv es obligatorio para cargar POS_ENV_FILE.'
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise ImproperlyConfigured(
            f'No se pudo leer POS_ENV_FILE como UTF-8 ({ruta}): {exc}'
        ) from exc

    return ruta
