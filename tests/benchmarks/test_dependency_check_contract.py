"""Offline contract for the nested, read-only Phase 6 nginx health chain.

This intentionally validates the *calls* between existing check entry points;
existing stage-specific security unit tests validate the internals of each
stage. It does not import bootstrap modules or require Docker/Vault.
"""

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
DEV = ROOT / "scripts" / "dev"

# Exact imported-module aliases and their expected read-only dependencies.
# A changed edge requires an explicit review of the dependency graph.
DEPENDENCIES = {
    "nginx": {"frontend": "frontend"},
    "frontend": {"support": "support-services"},
    "support-services": {"backend": "backend"},
    "backend": {"agent": "backend-agent"},
    "backend-agent": {"migrations": "migrations"},
    "migrations": {"roles": "postgres-roles"},
    "postgres-roles": {"pg": "postgres", "secret_bootstrap": "vault-secrets"},
    "vault-secrets": {"config": "vault-config"},
    "vault-config": {"vault": "vault"},
    "postgres": {},
    "vault": {},
}

FILES = {
    "nginx": "bootstrap_nginx.py",
    "frontend": "bootstrap_frontend.py",
    "support-services": "bootstrap_support_services.py",
    "backend": "bootstrap_backend.py",
    "backend-agent": "bootstrap_backend_agent.py",
    "migrations": "bootstrap_migrations.py",
    "postgres-roles": "bootstrap_postgres_roles.py",
    "postgres": "bootstrap_postgres.py",
    "vault-secrets": "bootstrap_vault_secrets.py",
    "vault-config": "bootstrap_vault_config.py",
    "vault": "bootstrap_vault.py",
}


def main_check_calls(source):
    """Return module aliases used in module.main(['check']) calls."""
    tree = ast.parse(source)
    names = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func
        if (attr.attr != "main" or not isinstance(attr.value, ast.Name)
                or len(node.args) != 1 or node.keywords):
            continue
        arg = node.args[0]
        if (isinstance(arg, ast.List) and len(arg.elts) == 1
                and isinstance(arg.elts[0], ast.Constant)
                and arg.elts[0].value == "check"):
            names.append(attr.value.id)
    return names


def imported_modules(source):
    """Map direct imported Python module aliases to source module names."""
    tree = ast.parse(source)
    aliases = {}
    for node in tree.body:
        if not isinstance(node, ast.Import):
            continue
        for entry in node.names:
            if entry.name.startswith("bootstrap_"):
                aliases[entry.asname or entry.name] = entry.name
    return aliases


def reachable(start, graph):
    """Cycle-safe traversal: fail rather than infinitely recurse."""
    seen = set()
    visiting = set()

    def visit(name):
        if name in visiting:
            raise ValueError("Read-only dependency cycle")
        if name in seen:
            return
        if name not in graph:
            raise ValueError("Unknown read-only dependency")
        visiting.add(name)
        for child in graph[name]:
            visit(child)
        visiting.remove(name)
        seen.add(name)

    visit(start)
    return seen


class DependencyCheckContract(unittest.TestCase):
    def test_nginx_includes_all_essential_independent_checks(self):
        actual = {node: set(mapping.values()) for node, mapping in DEPENDENCIES.items()}
        self.assertEqual(reachable("nginx", actual), set(DEPENDENCIES))
        self.assertEqual(len(reachable("nginx", actual)), 11)

    def test_each_module_retains_expected_read_only_check_edges(self):
        for name, mapping in DEPENDENCIES.items():
            with self.subTest(stage=name):
                source = (DEV / FILES[name]).read_text(encoding="utf-8")
                calls = main_check_calls(source)
                self.assertCountEqual(calls, list(mapping),
                                      f"Unexpected dependency changes in {FILES[name]}")
                imports = imported_modules(source)
                for alias, target in mapping.items():
                    self.assertEqual(imports.get(alias), "bootstrap_" + target.replace("-", "_")
                                     if target != "support-services" else "bootstrap_support_services")

    def test_exact_call_extraction_rejects_other_modes(self):
        source = '''import bootstrap_a as dependency

def check():
    dependency.main(["check"])
    dependency.main(["apply"])
    dependency.main(["plan"])
    another.main(["check", "apply"])
'''
        self.assertEqual(main_check_calls(source), ["dependency"])

    def test_cycle_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "cycle"):
            reachable("a", {"a": {"b"}, "b": {"a"}})

    def test_unknown_dependency_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown"):
            reachable("a", {"a": {"missing"}})

    def test_unified_health_invokes_host_resources_and_nginx(self):
        source = (DEV / "bootstrap_unified.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        self.assertIn("health_report", functions)
        self.assertIn("check", functions)
        for name in ("health_report", "check"):
            stage_names = [a.value for a in ast.walk(functions[name])
                           if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            self.assertIn("nginx", stage_names)
            self.assertIn("resources", stage_names)
            self.assertIn("host", stage_names)


if __name__ == "__main__":
    unittest.main()
