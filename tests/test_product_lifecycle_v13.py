import shutil
import unittest
from pathlib import Path

from app.agent.dependency_graph import DependencyGraph
from app.agent.git_safety import GitSafety
from app.agent.project_health import ProjectHealth
from app.agent.toolchain_registry import ToolchainRegistry


class TestProductLifecycleV13(unittest.TestCase):

    def setUp(self):
        self.test_dir = Path("data/test_product_lifecycle").resolve()
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_01_toolchain_registry_probes(self):
        """Verifies ToolchainRegistry probes tools without faking availability."""
        registry = ToolchainRegistry()
        tools = registry.probe_all()

        self.assertIn("python", tools)
        self.assertIn("node", tools)
        self.assertIn("dart", tools)
        self.assertIn("unreal_editor", tools)
        self.assertIn("unity", tools)
        self.assertIn("docker", tools)

        # Python must be available
        self.assertTrue(registry.is_available("python"))
        py_tool = registry.get_tool("python")
        self.assertEqual(py_tool["status"], "AVAILABLE")
        self.assertIn("runtime", py_tool["capabilities"])

        # Unreal editor status must be transparent
        unreal_tool = registry.get_tool("unreal_editor")
        self.assertIn(unreal_tool["status"], ["AVAILABLE", "UNAVAILABLE"])

    def test_02_dependency_graph_impact_analysis(self):
        """Verifies DependencyGraph accurately tracks downstream dependents."""
        # Create a sample modular Python project
        (self.test_dir / "models.py").write_text("class User: pass\n", encoding="utf-8")
        (self.test_dir / "auth.py").write_text("from models import User\nclass Auth: pass\n", encoding="utf-8")
        (self.test_dir / "api.py").write_text("from auth import Auth\nclass Api: pass\n", encoding="utf-8")
        (self.test_dir / "test_api.py").write_text("from api import Api\nimport unittest\n", encoding="utf-8")
        (self.test_dir / "unrelated.py").write_text("class Unrelated: pass\n", encoding="utf-8")

        dep_graph = DependencyGraph(workspace=str(self.test_dir))
        stats = dep_graph.build_graph(self.test_dir)
        self.assertGreaterEqual(stats["total_files_scanned"], 4)

        # Modifying models.py must impact auth.py, api.py, test_api.py
        impacted = dep_graph.get_impacted_files("models.py")
        self.assertIn("models.py", impacted)
        self.assertIn("auth.py", impacted)
        self.assertIn("api.py", impacted)
        self.assertIn("test_api.py", impacted)
        self.assertNotIn("unrelated.py", impacted)

        # Check impacted tests
        impacted_tests = dep_graph.get_impacted_tests("models.py")
        self.assertIn("test_api.py", impacted_tests)

    def test_03_project_health_evaluation(self):
        """Verifies ProjectHealth aggregates metrics and produces markdown report."""
        health_engine = ProjectHealth(workspace=str(self.test_dir))
        health = health_engine.evaluate_health(
            project_dir=self.test_dir,
            build_status="PASS",
            test_results={"passed": 5, "total": 5},
            runtime_status="ONLINE",
            ui_status="VERIFIED",
        )

        self.assertEqual(health["overall_health"], "HEALTHY")
        self.assertEqual(health["tests"]["pass_rate_pct"], 100.0)
        self.assertIn("python", health["toolchains"]["available"])

        md_report = health_engine.generate_markdown_report(health)
        self.assertIn("# Project Health Report", md_report)
        self.assertIn("HEALTHY", md_report)

    def test_04_git_safety_and_secret_redaction(self):
        """Verifies GitSafety scans secrets and sanitizes logs."""
        safety = GitSafety(workspace=str(self.test_dir))

        leaked_log = "Error connecting with api_key='sk_test_1234567890abcdef' to database."
        findings = safety.scan_for_secrets(leaked_log)
        self.assertGreaterEqual(len(findings), 1)

        sanitized = safety.sanitize_log(leaked_log)
        self.assertNotIn("sk_test_1234567890abcdef", sanitized)
        self.assertIn("[REDACTED_SECRET]", sanitized)


if __name__ == "__main__":
    unittest.main()
