"""Offline tests for issue #106 read-only readiness checks."""

import importlib.util
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('check-readiness.py')
spec = importlib.util.spec_from_file_location('check_readiness', SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def result(out='', rc=0):
    return subprocess.CompletedProcess([], rc, out, '')


class ReadinessTests(unittest.TestCase):
    def invoke(self, responses, *args):
        calls = []
        def fake_execute(cmd, timeout=12):
            calls.append(cmd)
            cmd_str = ' '.join(cmd)
            for needle, response in responses:
                if needle in cmd_str:
                    return response
            raise AssertionError('Unexpected command: ' + cmd_str)
        with patch.object(check, 'execute', side_effect=fake_execute), patch.object(check.Report, 'tls') as tls:
            tls.side_effect = lambda self_tls: None
            buf = io.StringIO()
            with redirect_stdout(buf):
                status = check.main(args)
            return status, buf.getvalue(), calls

    def test_stopped_server_does_not_attempt_exec_or_start(self):
        responses = [
            ('service=vault', result('v123\n')),
            ('docker inspect', result('exited|none\n')),
            ('service=vault-backend-agent', result('a123\n')),
        ]
        # Test prestart mode: no app Docker commands should be made.
        status, output, calls = self.invoke(responses[:2], '--mode', 'vault')
        self.assertEqual(status, 1)
        self.assertIn('Vault Server: container is exited', output)
        self.assertIn('RESULT: NOT READY', output)
        self.assertFalse(any(c[:2] == ['docker', 'exec'] for c in calls))
        self.assertFalse(any('docker compose' in ' '.join(c) or c[:2] == ['docker', 'start'] for c in calls))

    def test_unsealed_server_passes_vault_prestart(self):
        responses = [
            ('service=vault', result('v123\n')),
            ('docker inspect', result('running|none\n')),
            ('docker exec', result('{"initialized":true,"sealed":false}\n')),
        ]
        status, output, calls = self.invoke(responses, '--mode', 'vault')
        self.assertEqual(status, 0)
        self.assertIn('Vault API: initialized and unsealed', output)
        self.assertIn('RESULT: READY', output)
        self.assertFalse(any('service=postgres' in ' '.join(c) for c in calls))

    def test_sealed_server_blocks_prestart(self):
        responses = [
            ('service=vault', result('v123\n')),
            ('docker inspect', result('running|none\n')),
            ('docker exec', result('{"initialized":true,"sealed":true}\n', 2)),
        ]
        status, output, _ = self.invoke(responses, '--mode', 'vault')
        self.assertEqual(status, 1)
        self.assertIn('manual unseal required', output)

    def test_uninitialized_server_is_not_bootstrapped(self):
        responses = [
            ('service=vault', result('v123\n')),
            ('docker inspect', result('running|none\n')),
            ('docker exec', result('{"initialized":false,"sealed":true}\n', 2)),
        ]
        status, output, calls = self.invoke(responses, '--mode', 'vault')
        self.assertEqual(status, 1)
        self.assertIn('operator bootstrap required', output)
        self.assertFalse(any('init' in ' '.join(c) for c in calls))

    def test_docker_unavailable_fails_closed(self):
        status, output, _ = self.invoke([('service=vault', result('', 1))], '--mode', 'vault')
        self.assertEqual(status, 1)
        self.assertIn('unable to query Docker', output)

    def test_full_stack_requires_healthy_containers(self):
        responses = [
            ('service=vault-backend-agent', result('a123\n')),
            ('service=postgres', result('p123\n')),
            ('service=backend', result('b123\n')),
            ('service=vault', result('v123\n')),
            ('docker inspect', result('running|healthy\n')),
            ('docker exec', result('{"initialized":true,"sealed":false}\n')),
        ]
        status, output, _ = self.invoke(responses, '--mode', 'stack')
        self.assertEqual(status, 0)
        self.assertIn('Backend: running, health=healthy', output)
        self.assertIn('RESULT: READY', output)

    def test_unhealthy_agent_fails_full_stack(self):
        responses = [
            ('service=vault-backend-agent', result('a123\n')),
            ('service=postgres', result('p123\n')),
            ('service=backend', result('b123\n')),
            ('service=vault', result('v123\n')),
            ('a123', result('running|unhealthy\n')),
            ('docker inspect', result('running|healthy\n')),
            ('docker exec', result('{"initialized":true,"sealed":false}\n')),
        ]
        status, output, _ = self.invoke(responses, '--mode', 'stack')
        self.assertEqual(status, 1)
        self.assertIn('Runtime Agent: container running, health=unhealthy', output)


    def test_missing_tls_files_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            report = check.Report()
            with redirect_stdout(io.StringIO()) as stdout:
                report.tls(Path(directory))
            self.assertTrue(report.failed)
            self.assertIn('missing or invalid vault.crt', stdout.getvalue())

    def test_expiring_tls_certificate_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'ca.crt').write_text('dummy ca')
            (path / 'vault.crt').write_text('dummy certificate')
            responses = [
                ('openssl verify', result('OK')),
                ('-enddate', result('notAfter=Jan  1 00:00:00 2027 GMT\n')),
                ('-checkend', result('', 1)),
            ]
            def fake_execute(command, timeout=12):
                joined = ' '.join(command)
                for needle, response in responses:
                    if needle in joined:
                        return response
                raise AssertionError(joined)
            report = check.Report()
            with patch.object(check, 'execute', side_effect=fake_execute):
                with redirect_stdout(io.StringIO()) as stdout:
                    report.tls(path)
            self.assertTrue(report.failed)
            self.assertIn('expires within 7 days', stdout.getvalue())


if __name__ == '__main__':
    unittest.main()
