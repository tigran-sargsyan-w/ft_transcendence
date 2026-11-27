"""Offline tests for fail-closed, single-service WAF upgrade."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bootstrap_nginx as nginx
import upgrade_waf as upgr


def legacy_fixture():
    return {
        'Name': '/' + upgr.NAME,
        'Config': {
            'Image': upgr.OLD_IMAGE,
            'Labels': {
                'com.docker.compose.project': upgr.APP,
                'com.docker.compose.service': 'nginx',
                'com.docker.compose.project.config_files': str(upgr.COMPOSE_FILE),
                'com.docker.compose.project.working_dir': str(upgr.ROOT),
            },
            'Env': ['PATH=/usr/bin'],
        },
        'HostConfig': {
            'Privileged': False,
            'PortBindings': {
                '80/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8080'}],
                '443/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8443'}],
            },
            'GroupAdd': None,
            'RestartPolicy': {'Name': 'unless-stopped'},
        },
        'Mounts': [
            {'Destination': '/etc/nginx/conf.d/default.conf', 'Source': str(upgr.ROOT / 'infra/nginx/default.conf'), 'Type': 'bind', 'RW': False},
            {'Destination': '/etc/nginx/certs', 'Source': str(nginx.CERTS), 'Type': 'bind', 'RW': False},
        ],
        'NetworkSettings': {'Networks': {nginx.DEFAULT_NETWORK: {}}},
        'State': {'Running': True, 'Status': 'running'},
    }


class UpgradeWAFTests(unittest.TestCase):
    def test_https_status_uses_application_ca(self):
        with patch.object(nginx.certificates, "curl_ca_args", return_value=["--cacert", "/test/rootCA.pem"]), \
             patch.object(upgr, "command", return_value=b"200") as command:
            self.assertEqual(upgr.curl_status(), "200")
        args = command.call_args.args[0]
        self.assertEqual(args[:2], ["curl", "-q"])
        self.assertEqual(args[args.index("--cacert") + 1], "/test/rootCA.pem")
        self.assertNotIn("--insecure", args)
        self.assertNotIn("-k", args)

    def test_already_waf_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            lock = Path(root) / 'lock'
            lock.write_bytes(b'')
            with patch.object(upgr.os, 'geteuid', return_value=1000), \
                 patch.object(upgr.pg, 'LOCK', lock), \
                 patch.object(upgr, 'validate_target', return_value=b'services: test'), \
                 patch.object(upgr, 'current_mode', return_value='waf'), \
                 patch.object(upgr.tls, 'main', return_value=0), \
                 patch.object(upgr, 'verify_target') as verify, \
                 patch.object(upgr, 'compose') as compose, \
                 patch.object(upgr, 'snapshot_other_containers') as inventory:
                self.assertEqual(upgr.do_upgrade('apply'), 0)
                verify.assert_called_once()
                compose.assert_not_called()
                inventory.assert_not_called()

    def test_legacy_plan_never_creates_containers(self):
        with tempfile.TemporaryDirectory() as root:
            lock = Path(root) / 'lock'
            lock.write_bytes(b'')
            with patch.object(upgr.os, 'geteuid', return_value=1000), \
                 patch.object(upgr.pg, 'LOCK', lock), \
                 patch.object(upgr, 'validate_target', return_value=b'mock'), \
                 patch.object(upgr, 'current_mode', return_value='legacy'), \
                 patch.object(upgr, 'read_only_checks'), \
                 patch.object(upgr.tls, 'main', return_value=0), \
                 patch.object(upgr, 'legacy_manifest', return_value=b'old'), \
                 patch.object(upgr, 'compose') as compose, \
                 patch.object(upgr, 'snapshot_other_containers') as inventory:
                self.assertEqual(upgr.do_upgrade('plan'), 0)
                compose.assert_not_called()
                inventory.assert_not_called()

    def test_accept_known_legacy(self):
        upgr.verify_legacy(legacy_fixture())

    def test_reject_missing_legacy_container(self):
        with self.assertRaises(upgr.UpgradeError):
            upgr.verify_legacy(None)

    def test_reject_readwrite_tls_mount(self):
        obj = legacy_fixture()
        obj['Mounts'][1]['RW'] = True
        with self.assertRaises(upgr.UpgradeError):
            upgr.verify_legacy(obj)

    def test_reject_wrong_image(self):
        obj = legacy_fixture()
        obj['Config']['Image'] = 'nginx:latest'
        with self.assertRaises(upgr.UpgradeError):
            upgr.verify_legacy(obj)

    def test_reject_wrong_project(self):
        obj = legacy_fixture()
        obj['Config']['Labels']['com.docker.compose.project'] = 'someone-else'
        with self.assertRaises(upgr.UpgradeError):
            upgr.verify_legacy(obj)

    def test_reject_wrong_compose_path(self):
        obj = legacy_fixture()
        obj['Config']['Labels']['com.docker.compose.project.config_files'] = '/tmp/other.yml'
        with self.assertRaises(upgr.UpgradeError):
            upgr.verify_legacy(obj)

    def test_reject_public_ports(self):
        obj = legacy_fixture()
        obj['HostConfig']['PortBindings']['443/tcp'][0]['HostIp'] = '0.0.0.0'
        with self.assertRaises(upgr.UpgradeError):
            upgr.verify_legacy(obj)

    def test_reject_extra_network(self):
        obj = legacy_fixture()
        obj['NetworkSettings']['Networks']['other'] = {}
        with self.assertRaises(upgr.UpgradeError):
            upgr.verify_legacy(obj)

    def test_reject_privileged(self):
        obj = legacy_fixture()
        obj['HostConfig']['Privileged'] = True
        with self.assertRaises(upgr.UpgradeError):
            upgr.verify_legacy(obj)

    def test_reject_extra_mount(self):
        obj = legacy_fixture()
        obj['Mounts'].append({'Destination': '/secret', 'Source': '/home', 'Type': 'bind', 'RW': True})
        with self.assertRaises(upgr.UpgradeError):
            upgr.verify_legacy(obj)

    def test_reject_secret_env(self):
        obj = legacy_fixture()
        obj['Config']['Env'].append('VAULT_TOKEN=not-real')
        with self.assertRaises(upgr.UpgradeError):
            upgr.verify_legacy(obj)

    def test_reject_unexpected_supplementary_group(self):
        obj = legacy_fixture()
        obj['HostConfig']['GroupAdd'] = ['101']
        with self.assertRaises(upgr.UpgradeError):
            upgr.verify_legacy(obj)

    def test_legacy_manifest_changes_only_nginx_section(self):
        original = (
            'services:\n'
            '  backend:\n    image: backend:example\n'
            '  nginx:\n    image: owasp/modsecurity-crs:test\n    group_add:\n      - "43043"\n    environment:\n      MODSEC_RULE_ENGINE: "On"\n'
            '  frontend:\n    image: frontend:example\n'
            'volumes:\n  postgresql:\n'
        ).encode()
        changed = upgr.legacy_manifest(original).decode()
        self.assertIn('    image: nginx:1.28-alpine\n', changed)
        self.assertEqual(changed.count('  nginx:\n'), 1)
        self.assertIn('  backend:\n    image: backend:example\n', changed)
        self.assertIn('  frontend:\n    image: frontend:example\n', changed)
        self.assertTrue(changed.endswith('volumes:\n  postgresql:\n'))

    def test_legacy_manifest_refuses_missing_nginx(self):
        with self.assertRaises(upgr.UpgradeError):
            upgr.legacy_manifest(b'services:\n  backend:\n    image: x\n')

    def test_legacy_manifest_refuses_ambiguous_nginx(self):
        s = b'services:\n  nginx:\n    image: x\n  nginx:\n    image: x\n  backend:\n    image: x\n'
        with self.assertRaises(upgr.UpgradeError):
            upgr.legacy_manifest(s)

    def test_current_mode_legacy_without_recreate(self):
        with patch.object(upgr.nginx, 'container', return_value=legacy_fixture()):
            self.assertEqual(upgr.current_mode(), 'legacy')

    def test_current_mode_refuses_unknown_image(self):
        obj = legacy_fixture()
        obj['Config']['Image'] = 'unknown'
        with patch.object(upgr.nginx, 'container', return_value=obj):
            with self.assertRaises(upgr.UpgradeError):
                upgr.current_mode()

    def test_current_mode_refuses_stopped_legacy(self):
        obj = legacy_fixture()
        obj['State']['Running'] = False
        obj['State']['Status'] = 'exited'
        with patch.object(upgr.nginx, 'container', return_value=obj):
            with self.assertRaises(upgr.UpgradeError):
                upgr.current_mode()

    def test_atomic_write_preserves_contents_and_mode(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / 'compose.yml'
            p.write_text('original')
            upgr.save_atomic(p, b'restored', 0o644)
            self.assertEqual(p.read_bytes(), b'restored')
            self.assertEqual(p.stat().st_mode & 0o777, 0o644)

    def test_target_preflight_refuses_waf_not_blocking(self):
        model = {
            'services': {
                'nginx': {
                    'image': upgr.NEW_IMAGE,
                    'group_add': ['43043'],
                    'environment': {'MODSEC_RULE_ENGINE': 'DetectionOnly'},
                    'ports': [
                        {'target': 8080, 'published': '8080', 'host_ip': '127.0.0.1'},
                        {'target': 8443, 'published': '8443', 'host_ip': '127.0.0.1'},
                    ],
                    'volumes': [
                        {'type': 'bind', 'source': str(nginx.CONFIG), 'target': '/etc/nginx/templates/conf.d/default.conf.template', 'read_only': True},
                        {'type': 'bind', 'source': str(nginx.CERTS), 'target': '/etc/nginx/certs', 'read_only': True},
                    ],
                }
            }
        }
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / 'compose.yml'
            p.write_text('test')
            with patch.object(upgr, 'COMPOSE_FILE', p), patch.object(upgr, 'compose', return_value=json.dumps(model).encode()):
                with self.assertRaises(upgr.UpgradeError):
                    upgr.validate_target()


if __name__ == '__main__':
    unittest.main()
