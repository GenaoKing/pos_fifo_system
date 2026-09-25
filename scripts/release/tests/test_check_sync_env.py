"""Regression coverage for sync service setup with dotenv credentials."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CHECKER = ROOT / 'deploy' / 'check_sync_env.py'
BAT = ROOT / 'deploy' / 'registrar_sync_servicio.bat'


class CheckSyncEnvTests(unittest.TestCase):
    def _run(self, *lines):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / 'env_cliente.env'
            env_file.write_text('\n'.join(lines) + '\n', encoding='utf-8')
            return subprocess.run(
                [sys.executable, str(CHECKER), str(env_file)],
                capture_output=True, text=True, check=False,
            )

    def test_valid_dotenv_with_shell_metacharacters_is_accepted_without_echo(self):
        secret = 'pass&word%(123)'
        result = self._run(
            'SYNC_ENABLED=true',
            'CLOUD_API_URL=https://staging.example.test',
            'CLOUD_API_TOKEN=' + 'a' * 40,
            'SUCURSAL_CODIGO=QA-PC-01',
            'INITIAL_SYSADMIN_PASSWORD=' + secret,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(secret, result.stdout + result.stderr)

    def test_disabled_sync_or_placeholder_token_blocks_registration(self):
        common = [
            'CLOUD_API_URL=https://staging.example.test',
            'SUCURSAL_CODIGO=QA-PC-01',
        ]
        for enabled, token in (
            ('false', 'a' * 40),
            ('true', 'PEGAR-TOKEN-DE-vincular_sucursal_token'),
        ):
            with self.subTest(enabled=enabled, token_valid=len(token) == 40):
                result = self._run(
                    'SYNC_ENABLED=' + enabled,
                    'CLOUD_API_TOKEN=' + token,
                    *common,
                )
                self.assertNotEqual(result.returncode, 0)

    def test_batch_uses_checker_without_expanding_secret_values(self):
        source = BAT.read_text(encoding='utf-8')
        self.assertIn('check_sync_env.py', source)
        self.assertNotIn('%SYNC_ENABLED%', source)
        self.assertNotIn('%CLOUD_API_TOKEN%', source)


if __name__ == '__main__':
    unittest.main()
