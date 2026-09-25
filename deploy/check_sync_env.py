"""Validate the sync service configuration without echoing secrets to cmd."""

import re
import sys
from pathlib import Path
from urllib.parse import urlparse


def read_required_values(path: Path) -> dict[str, str]:
    """Read only the four scalar keys needed before service registration."""
    wanted = {'SYNC_ENABLED', 'CLOUD_API_URL', 'CLOUD_API_TOKEN', 'SUCURSAL_CODIGO'}
    values = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:]
        key, separator, value = line.partition('=')
        key = key.strip()
        if separator and key in wanted:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            values[key] = value
    return values


def main() -> int:
    if len(sys.argv) != 2:
        print('[ERROR] Indique la ruta de env_cliente.env.')
        return 1
    path = Path(sys.argv[1])
    if not path.is_file():
        print('[ERROR] Falta deploy\\env_cliente.env.')
        return 1
    config = read_required_values(path)
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
