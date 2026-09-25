"""Guardas estáticas A08 para la política de proxy de USR-014.

La topología ACA se acredita en un preflight autorizado. Estas pruebas solo
evitan que el código versionado vuelva a tomar el primer X-Forwarded-For o
habilite confianza en proxy de forma silenciosa.
"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class ProxyUsr014PolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.auth_views = (ROOT / "apps" / "api" / "auth_views.py").read_text(encoding="utf-8")
        cls.audit_models = (ROOT / "apps" / "auditoria" / "models.py").read_text(encoding="utf-8")
        cls.cloud_settings = (ROOT / "config" / "settings_cloud.py").read_text(encoding="utf-8")

    def test_impersonation_reuses_the_fail_closed_audit_extractor(self) -> None:
        self.assertIn("from apps.auditoria.models import get_client_ip", self.auth_views)
        self.assertIn("ip_address=get_client_ip(request)", self.auth_views)
        self.assertNotIn("def _ip_cliente", self.auth_views)

    def test_proxy_trust_is_opt_in_and_uses_only_the_rightmost_value(self) -> None:
        self.assertIn(
            "AUDITORIA_CONFIAR_EN_PROXY = _bool_env('AUDITORIA_CONFIAR_EN_PROXY', False)",
            self.cloud_settings,
        )
        self.assertIn("return partes[-1]", self.audit_models)
        self.assertNotIn("return partes[0]", self.audit_models)


if __name__ == "__main__":
    unittest.main()
