"""Validate the sync service configuration without echoing secrets to cmd."""

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values


def main() -> int:
    if len(sys.argv) != 2:
        print('[ERROR] Indique la ruta de env_cliente.env.')
        return 1
    path = Path(sys.argv[1])
    if not path.is_file():
        print('[ERROR] Falta deploy\\env_cliente.env.')
        return 1
    config = dotenv_values(path)
    if str(config.get('SYNC_ENABLED') or '').strip().lower() != 'true':
        print('[ERROR] SYNC_ENABLED debe ser true en env_cliente.env.')
        return 1
    cloud_url = urlparse(str(config.get('CLOUD_API_URL') or ''))
    if cloud_url.scheme not in ('http', 'https') or not cloud_url.netloc:
        print('[ERROR] CLOUD_API_URL debe ser una URL valida.')
        return 1
    if not re.fullmatch(r'[a-fA-F0-9]{40}', str(config.get('CLOUD_API_TOKEN') or '')):
        print('[ERROR] CLOUD_API_TOKEN debe ser un token DRF valido.')
        return 1
    if not str(config.get('SUCURSAL_CODIGO') or '').strip():
        print('[ERROR] SUCURSAL_CODIGO es obligatorio.')
        return 1
    print('[OK] Configuracion de sync lista para registrar el servicio.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
