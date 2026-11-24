"""Offline unit tests for Nginx WAF TLS host ACL provisioning."""

import contextlib
import grp
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, call

import nginx_tls_access as tls


class TLSAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "infra/nginx/certs").mkdir(parents=True)
        (root / "infra/nginx/certs/key.pem").write_bytes(b"test-only-key-placeholder")
        (root / "infra/nginx/certs/cert.pem").write_bytes(b"test-only-cert-placeholder")
        (root / "infra/nginx/certs/key.pem").chmod(0o600)
        (root / "infra/nginx/certs").chmod(0o700)
        for prop, val in (
            ("PROJECT_ROOT", root),
            ("CERT_DIR", root / "infra/nginx/certs"),
            ("CERT", root / "infra/nginx/certs/cert.pem"),
            ("KEY", root / "infra/nginx/certs/key.pem"),
        ):
            p = patch.object(tls, prop, val)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(tls, "DESIRED", {
            tls.CERT_DIR: "--x", tls.CERT: "r--", tls.KEY: "r--",
        })
        p.start()
        self.addCleanup(p.stop)
        self.acls = {
            tls.CERT_DIR: ["user::rwx", "group::---", "other::---"],
            tls.CERT: ["user::rw-", "group::r--", "other::r--"],
            tls.KEY: ["user::rw-", "group::---", "other::---"],
        }
        self.cmds = []

        def fake_cmd(argv):
            self.cmds.append(argv)
            path = Path(argv[-1])
            if argv[0] == "getfacl":
                return "\n".join(self.acls[path])
            if argv[0] == "setfacl":
                self.acls[path].append("group:" + argv[2][2:])
                self.acls[path].append("mask::" + argv[2].split(":", 2)[2])
                return ""
            raise AssertionError(argv)

        p = patch.object(tls, "checked_command", side_effect=fake_cmd)
        p.start()
        self.addCleanup(p.stop)
        grp_obj = type("Group", (), {"gr_name": tls.GROUP_NAME, "gr_gid": tls.GROUP_GID, "gr_mem": []})()
        self.group = grp_obj
        p = patch.object(tls.grp, "getgrnam", return_value=grp_obj)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(tls.grp, "getgrgid", return_value=grp_obj)
        p.start()
        self.addCleanup(p.stop)

    def ready(self):
        for path, mode in tls.DESIRED.items():
            self.acls[path].append(f"group:{tls.GROUP_GID}:{mode}")
            self.acls[path].append(f"mask::{mode}")

    def test_check_rejects_unconfigured(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(tls.main(["check"]), 1)

    def test_ready_check_does_not_modify(self):
        self.ready()
        self.assertEqual(tls.main(["check"]), 0)
        self.assertFalse(any(x[0] == "setfacl" for x in self.cmds))

    def test_plan_is_readonly(self):
        self.assertEqual(tls.main(["plan"]), 0)
        self.assertFalse(any(x[0] == "setfacl" for x in self.cmds))

    def test_apply_idempotent(self):
        with patch.object(tls.os, "geteuid", return_value=1000):
            self.assertEqual(tls.main(["apply"]), 0)
            self.assertEqual(len([x for x in self.cmds if x[0] == "setfacl"]), 3)
            self.assertEqual(tls.main(["apply"]), 0)
            self.assertEqual(len([x for x in self.cmds if x[0] == "setfacl"]), 3)

    def test_rejects_legacy_user_acl(self):
        self.acls[tls.KEY].append("user:101:r--")
        with self.assertRaises(tls.TLSAccessError):
            tls.acl_ready()

    def test_rejects_unexpected_named_group(self):
        self.acls[tls.KEY].append("group:9999:r--")
        with self.assertRaises(tls.TLSAccessError):
            tls.acl_ready()

    def test_rejects_wrong_permission(self):
        self.acls[tls.KEY].append(f"group:{tls.GROUP_GID}:rw-")
        with self.assertRaises(tls.TLSAccessError):
            tls.acl_ready()

    def test_rejects_world_readable_key(self):
        tls.KEY.chmod(0o604)
        with self.assertRaises(tls.TLSAccessError):
            tls.check_files()

    def test_rejects_symlink_key(self):
        tls.KEY.unlink()
        tls.KEY.symlink_to(tls.CERT)
        with self.assertRaises(tls.TLSAccessError):
            tls.check_files()

    def test_rejects_mask_denying_group(self):
        self.ready()
        self.acls[tls.KEY].remove("mask::r--")
        self.acls[tls.KEY].append("mask::---")
        with self.assertRaises(tls.TLSAccessError):
            tls.acl_ready()

    def test_rejects_group_gid_collision(self):
        with patch.object(tls.grp, "getgrgid", return_value=type(
            "Group", (), {"gr_name": "wrong-name", "gr_gid": tls.GROUP_GID}
        )()):
            with self.assertRaises(tls.TLSAccessError):
                tls.group_state()

    def test_rejects_group_name_collision(self):
        with patch.object(tls.grp, "getgrnam", return_value=type(
            "Group", (), {"gr_name": tls.GROUP_NAME, "gr_gid": 9999}
        )()):
            with self.assertRaises(tls.TLSAccessError):
                tls.group_state()

    def test_check_refuses_unexpected_public_acl(self):
        self.ready()
        self.acls[tls.KEY].remove("other::---")
        self.acls[tls.KEY].append("other::r--")
        with self.assertRaises(tls.TLSAccessError):
            tls.acl_ready()


if __name__ == "__main__":
    unittest.main()
