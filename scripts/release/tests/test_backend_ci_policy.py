"""Guardas estaticas del camino A08.2 de promocion backend.

No intenta emular GitHub Actions ni Azure. Protege las invariantes que deben
seguir expresadas en el workflow versionado: prod no reconstruye, usa digest y
no actualiza API antes de un migrate job exitoso.
"""

from __future__ import annotations

import unittest
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "backend-ci.yml"


class BackendCiPromotionPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_prod_requires_digest_and_migration_gate_before_api(self) -> None:
        self.assertIn("approved_image_digest:", self.workflow)
        self.assertIn("Production deployment requires run_migrations=true", self.workflow)
        self.assertIn("Production deployment requires approved_image_digest=sha256:", self.workflow)
        self.assertIn("migrations_required=true", self.workflow)
        self.assertIn(
            "steps.target.outputs.deploy == 'true' && steps.target.outputs.migrations_required == 'true'",
            self.workflow,
        )
        self.assertIn("steps.migrations.outcome == 'success'", self.workflow)

    def test_prod_promotion_uses_existing_digest_without_build_or_push(self) -> None:
        self.assertEqual(
            2,
            self.workflow.count(
                "if: steps.target.outputs.deploy == 'true' && matrix.environment != 'prod'"
            ),
        )
        self.assertIn('reference="$AZURE_ACR_LOGIN_SERVER/$IMAGE_REPOSITORY@$digest"', self.workflow)
        self.assertIn('docker pull "$reference" >/dev/null', self.workflow)
        self.assertIn("Approved image revision ($artifact_source_sha)", self.workflow)

    def test_all_runtime_components_receive_the_immutable_reference(self) -> None:
        immutable_image = '--image "${{ steps.release_image.outputs.reference }}"'
        self.assertEqual(3, self.workflow.count(immutable_image))
        self.assertIn("Generate promotable backend manifest", self.workflow)
        self.assertIn("--require-promotable", self.workflow)


if __name__ == "__main__":
    unittest.main()
