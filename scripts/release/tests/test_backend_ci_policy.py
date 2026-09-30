"""Guardas estaticas del camino A08.2 de promocion backend.

No intenta emular GitHub Actions ni Azure. Protege las invariantes que deben
seguir expresadas en el workflow versionado: prod no reconstruye, usa digest y
no actualiza API antes de un migrate job exitoso.
"""

from __future__ import annotations

import unittest
import re
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "backend-ci.yml"
REPRODUCIBILITY_WORKFLOW = (
    Path(__file__).resolve().parents[3]
    / ".github"
    / "workflows"
    / "release-reproducibility.yml"
)


class BackendCiPromotionPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.reproducibility_workflow = REPRODUCIBILITY_WORKFLOW.read_text(encoding="utf-8")

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
        for name in ('Build image', 'Push image'):
            self.assertIn("matrix.environment != 'prod'", self.step(name))
        self.assertIn('reference="$AZURE_ACR_LOGIN_SERVER/$IMAGE_REPOSITORY@$digest"', self.workflow)
        self.assertIn('docker pull "$reference" >/dev/null', self.workflow)
        self.assertIn("Approved image revision ($artifact_source_sha)", self.workflow)

    def test_all_runtime_components_receive_the_immutable_reference(self) -> None:
        immutable_image = '--image "${{ steps.release_image.outputs.reference }}"'
        self.assertEqual(3, self.workflow.count(immutable_image))
        self.assertIn("Generate promotable backend manifest", self.workflow)
        self.assertIn("--require-promotable", self.workflow)

    def test_ci_runs_an_explicit_non_skippable_physical_tenant_gate(self) -> None:
        self.assertIn("Run physical DB-per-tenant isolation gate", self.workflow)
        self.assertIn('test -n "$TENANT_TEST_DB_NAMESPACE"', self.workflow)
        self.assertIn("apps.tenancy.tests.test_multidb_isolation", self.workflow)
        self.assertIn(
            "apps.api.tests.test_administracion_portal.AdministracionPortalTenantFisicoTests",
            self.workflow,
        )

    def test_release_evidence_tracks_ci_policy_and_has_extended_retention(self) -> None:
        self.assertEqual(
            2,
            self.reproducibility_workflow.count('".github/workflows/backend-ci.yml"'),
        )
        self.assertIn("retention-days: 180", self.reproducibility_workflow)
        self.assertIn("retention-days: 180", self.workflow)

    @classmethod
    def step(cls, name):
        sections = re.split(r'^      - name: ', cls.workflow, flags=re.MULTILINE)
        return next(section for section in sections if section.startswith(name + '\n'))

    def test_production_is_frozen_before_any_schema_change(self):
        freeze = self.workflow.index('name: Verify production maintenance before migrations')
        self.assertLess(freeze, self.workflow.index('name: Update migrate job image'))
        step = self.step('Verify production maintenance before migrations')
        self.assertIn("matrix.environment == 'prod'", step)
        self.assertIn('production_gate.py frozen', step)
        self.assertIn('test "$trigger" = "Manual"', step)
        self.assertIn('test -z "$running"', step)
        self.assertIn('production_gate.py frozen', self.step('Run migrations and wait for completion'))
        self.assertIn('production_gate.py frozen', self.step('Update API image'))

    def test_production_does_not_rollback_old_writers_or_run_notifications(self):
        for name in ('Rollback and verify API image on failure',
                     'Run and wait for one notifications cycle',
                     'Rollback and verify notifications job on failure'):
            self.assertIn("matrix.environment != 'prod'", self.step(name))
        self.assertNotIn('ingress enable', self.workflow)
        self.assertNotIn('--ingress external', self.workflow)

    def test_runtime_health_pins_identity_and_keeps_production_closed(self):
        update = self.step('Update API image')
        self.assertIn('--set-env-vars "GIT_COMMIT_SHA=$GITHUB_SHA"', update)
        self.assertIn('--min-replicas 1', update)
        self.assertIn('--revision-suffix', update)
        health = self.step('Smoke test health')
        self.assertIn('production_gate.py internal-health', health)
        self.assertIn('--expected-sha "$GITHUB_SHA"', health)
        self.assertIn('--expected-image "${{ steps.release_image.outputs.reference }}"', health)
        self.assertIn('exit 0\n          fi\n          for attempt', health)

    def test_migrations_have_configured_budget_evidence_and_no_cancellation(self):
        self.assertIn("matrix.environment == 'prod' && 'serial' || github.run_id", self.workflow)
        self.assertIn('cancel-in-progress: false', self.workflow)
        self.assertIn('timeout-minutes: 120', self.workflow)
        self.assertIn('production_gate.py wait-migration', self.step('Run migrations and wait for completion'))
        evidence = self.step('Upload deployment gate evidence')
        self.assertIn('if: always()', evidence)
        self.assertIn('deploy-evidence-', evidence)


if __name__ == "__main__":
    unittest.main()
