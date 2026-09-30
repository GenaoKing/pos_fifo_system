"""Exercise release failures with no Azure account, Django, or database."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock


SPEC = importlib.util.spec_from_file_location(
    'production_gate', Path(__file__).resolve().parents[1] / 'production_gate.py',
)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
SHA = 'a' * 40
IMAGE = 'registry.example/backend@sha256:' + 'b' * 64


def state():
    return {
        'app': 'api', 'ingress_disabled': True, 'active_revisions': [],
        'latest_revision': 'api--new', 'image': IMAGE, 'runtime_sha': SHA,
        'min_replicas': 1,
    }


class ProductionGateTests(unittest.TestCase):
    def test_internal_ingress_and_any_active_revision_block_migration(self):
        app = {'properties': {'configuration': {'ingress': {'external': False}}}}
        with self.assertRaisesRegex(gate.GateError, 'ingress must be absent'):
            gate.require_frozen(gate.snapshot(app, []))
        app['properties']['configuration']['ingress'] = None
        revision = {'name': 'old', 'properties': {'active': True}}
        with self.assertRaisesRegex(gate.GateError, 'deactivated'):
            gate.require_frozen(gate.snapshot(app, [revision]))
        revision['properties']['active'] = False
        gate.require_frozen(gate.snapshot(app, [revision]))

    def test_unknown_freeze_metadata_fails_closed(self):
        for app, revisions in (({}, []), ({'properties': {'configuration': {}}}, [{}])):
            with self.subTest(app=app), self.assertRaises(gate.GateError):
                gate.snapshot(app, revisions)

    def test_evidence_excludes_environment_and_secret_values(self):
        app = {'properties': {'configuration': {'ingress': None, 'secrets': ['sensitive']},
                             'template': {'containers': [{'image': IMAGE, 'env': [
                                 {'name': 'DB_PASSWORD', 'value': 'do-not-output'},
                                 {'name': 'GIT_COMMIT_SHA', 'value': SHA},
                             ]}]}}}
        evidence = json.dumps(gate.snapshot(app, []))
        self.assertNotIn('sensitive', evidence)
        self.assertNotIn('do-not-output', evidence)
        self.assertIn(SHA, evidence)

    def test_new_revision_requires_exact_identity_and_no_old_writers(self):
        good = state()
        good['active_revisions'] = ['api--new']
        gate.require_new_revision(good, SHA, IMAGE)
        for changes in ({'active_revisions': ['api--old', 'api--new']},
                        {'active_revisions': []}, {'ingress_disabled': False},
                        {'image': 'old-image'}, {'runtime_sha': 'stale'}, {'min_replicas': 0}):
            with self.subTest(changes=changes), self.assertRaises(gate.GateError):
                gate.require_new_revision({**good, **changes}, SHA, IMAGE)

    def test_migration_budget_includes_retry_and_startup_grace(self):
        self.assertEqual(4200, gate.migration_budget({'replicaTimeout': 1800, 'replicaRetryLimit': 1}))
        for configuration in ({}, {'replicaTimeout': 1800, 'replicaRetryLimit': -1},
                              {'replicaTimeout': True, 'replicaRetryLimit': 1},
                              {'replicaTimeout': 7200, 'replicaRetryLimit': 2}):
            with self.subTest(configuration=configuration), self.assertRaises(gate.GateError):
                gate.migration_budget(configuration)

    def test_actual_revision_must_match_desired_app_template(self):
        revision = {'properties': {'active': True, 'template': {'containers': [
            {'image': IMAGE, 'env': [{'name': 'GIT_COMMIT_SHA', 'value': SHA}]},
        ]}}}
        gate.require_revision_template(revision, SHA, IMAGE)
        revision['properties']['template']['containers'][0]['image'] = 'old-image'
        with self.assertRaisesRegex(gate.GateError, 'actual API revision'):
            gate.require_revision_template(revision, SHA, IMAGE)

    @patch.object(gate.time, 'sleep')
    def test_migration_failure_preserves_terminal_evidence(self, sleep):
        job = {'properties': {'configuration': {'replicaTimeout': 1800, 'replicaRetryLimit': 1}}}
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / 'migration.json'
            with patch.object(gate, 'azure_json', side_effect=[job, {'properties': {'status': 'Failed'}}]):
                with self.assertRaisesRegex(gate.GateError, 'fix-forward'):
                    gate.wait_migration('rg', 'job', 'run-1', evidence)
            record = json.loads(evidence.read_text())
            self.assertEqual(record['budget_seconds'], 4200)
            self.assertEqual(record['states'][-1]['status'], 'Failed')
            sleep.assert_not_called()

    @patch.object(gate.time, 'sleep')
    def test_transient_status_read_failure_does_not_claim_success(self, sleep):
        job = {'properties': {'configuration': {'replicaTimeout': 1800, 'replicaRetryLimit': 1}}}
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / 'migration.json'
            with patch.object(gate, 'azure_json', side_effect=[job, gate.GateError('read failed'),
                                                               {'properties': {'status': 'Succeeded'}}]):
                gate.wait_migration('rg', 'job', 'run-1', evidence)
            states = json.loads(evidence.read_text())['states']
            self.assertEqual([row['status'] for row in states], ['Unknown', 'Succeeded'])

    def test_exec_requires_matching_marker_not_exit_code_or_echo(self):
        success = {'ok': True, 'nonce': 'unique', 'sha': SHA}
        result = gate.parse_health_marker('noise\r\n' + gate.MARKER + json.dumps(success) + '\r\nclosed', 'unique', SHA)
        self.assertEqual(result, success)
        for output in ('connected successfully', gate.MARKER + '[]',
                       gate.MARKER + json.dumps({**success, 'nonce': 'previous-run'}),
                       gate.MARKER + json.dumps({**success, 'sha': 'stale'}),
                       gate.MARKER + json.dumps({**success, 'ok': False})):
            with self.subTest(output=output), self.assertRaises(gate.GateError):
                gate.parse_health_marker(output, 'unique', SHA)

    def test_local_http_health_uses_loopback_and_checks_both_endpoints(self):
        healthy = {'status': 'ok', 'db': 'ok', 'environment': 'prod', 'commit': SHA}
        opener = Mock()
        def response(request, timeout):
            self.assertTrue(request.full_url.startswith('http://127.0.0.1:8000/api/v1/health/'))
            self.assertEqual(request.get_header('Host'), 'prod.example')
            self.assertEqual(request.get_header('X-forwarded-proto'), 'https')
            result = io.StringIO(json.dumps(healthy))
            result.status = 200
            return result
        opener.open.side_effect = response
        output = io.StringIO()
        with patch.object(gate, 'build_opener', return_value=opener), contextlib.redirect_stdout(output):
            gate.local_health(SHA, 'prod.example', 'unique')
        marker = gate.parse_health_marker(output.getvalue(), 'unique', SHA)
        self.assertEqual(set(marker['health']), {'/api/v1/health/', '/api/v1/health/live/'})
        self.assertEqual(opener.open.call_count, 2)

    def test_local_http_health_rejects_stale_sha_without_success_marker(self):
        response = io.StringIO(json.dumps({'status': 'ok', 'environment': 'prod', 'commit': 'old'}))
        response.status = 200
        opener = Mock()
        opener.open.return_value = response
        output = io.StringIO()
        with patch.object(gate, 'build_opener', return_value=opener), contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(gate.GateError, 'runtime identity'):
                gate.local_health(SHA, 'prod.example', 'unique')
        self.assertNotIn(gate.MARKER, output.getvalue())

    def test_internal_gate_targets_one_verified_revision_and_replica(self):
        deployed = state()
        deployed['active_revisions'] = ['api--new']
        revision = {'properties': {'active': True, 'template': {'containers': [
            {'image': IMAGE, 'env': [{'name': 'GIT_COMMIT_SHA', 'value': SHA}]},
        ]}}}
        replicas = [{'name': 'replica-1', 'properties': {'containers': [{'name': 'api'}]}}]
        marker = gate.MARKER + json.dumps({'ok': True, 'nonce': 'unique', 'sha': SHA})
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / 'health.json'
            with patch.object(gate, 'inspect_app', return_value=deployed), \
                 patch.object(gate, 'azure_json', side_effect=[revision, replicas]), \
                 patch.object(gate.uuid, 'uuid4', return_value=Mock(hex='unique')), \
                 patch.object(gate, 'terminal_run', return_value=marker) as terminal:
                gate.internal_health('rg', 'api', SHA, IMAGE, 'https://prod.example', evidence)
            command = terminal.call_args.args[0]
            self.assertEqual(command[command.index('--revision') + 1], 'api--new')
            self.assertEqual(command[command.index('--replica') + 1], 'replica-1')
            self.assertIn('--host prod.example', command[command.index('--command') + 1])
            record = json.loads(evidence.read_text())
            self.assertTrue(record['state']['ingress_disabled'])
            self.assertEqual(record['replica'], 'replica-1')

    @unittest.skipIf(os.name == 'nt', 'PTY runner is Linux-only, like GitHub Actions')
    def test_terminal_keeps_input_open_for_remote_command(self):
        output = gate.terminal_run([sys.executable, '-c',
            "import os,time; assert os.isatty(0); time.sleep(.1); print('remote-complete',flush=True)"], timeout=5)
        self.assertIn('remote-complete', output)


if __name__ == '__main__':
    unittest.main()
