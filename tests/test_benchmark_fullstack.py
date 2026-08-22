import os
import shutil
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.action_dispatcher import ActionDispatcher
from app.agent.code_agent import CodeAgent
from app.agent.computer_control import ComputerControl
from app.agent.project_inspector import ProjectInspector
from app.agent.task_planner import TaskPlanner
from app.brain.brain import NRBrain


class TestProductLevelBenchmarkFullStack(unittest.TestCase):
    """
    Product-Level End-to-End Build Validation Test Suite.

    Validates that NR-AI autonomously transforms complex full-stack requirements into
    working software through its integrated cognitive & execution pipeline:
    INTENT -> TASK DECOMPOSITION -> MULTI-FILE SCENARIOS -> TESTS -> ERROR INJECTION
    -> ERROR ANALYSIS -> AUTOMATIC RECOVERY -> SERVICE SMOKE TEST -> FINAL VERIFICATION.
    """

    def setUp(self):
        self.proj_dir = PROJECT_ROOT / "data" / "benchmark_project"
        self.brain = NRBrain()
        self.cc = ComputerControl()
        self.code_agent = CodeAgent()
        self.inspector = ProjectInspector()

    def tearDown(self):
        # Keep artifacts clean, but ensure checkpoints directory exists
        pass

    def test_01_fullstack_benchmark_execution(self):
        """
        Benchmark Request:
        'Build a full-stack task management application with a React frontend, Python backend,
        database models, authentication, CRUD operations, responsive UI, API endpoints,
        validation, tests, and README documentation.'
        """
        benchmark_cmd = (
            "Build a full-stack task management application with a React frontend, "
            "Python backend, database models, authentication, CRUD operations, "
            "responsive UI, API endpoints, validation, tests, and README documentation."
        )

        # 1. Pipeline Execution via NRBrain (Planner -> ActionDispatcher -> CodeWriter -> Terminal -> SmokeTest)
        response = self.brain.think(benchmark_cmd)
        self.assertIn("Task completed successfully", response)

        # 2. Verify Generated Multi-File Project Architecture
        expected_files = [
            self.proj_dir / "backend" / "models.py",
            self.proj_dir / "backend" / "auth.py",
            self.proj_dir / "backend" / "app.py",
            self.proj_dir / "tests" / "test_backend.py",
            self.proj_dir / "frontend" / "src" / "api.js",
            self.proj_dir / "frontend" / "src" / "App.jsx",
            self.proj_dir / "frontend" / "src" / "styles.css",
            self.proj_dir / "frontend" / "public" / "index.html",
            self.proj_dir / "README.md",
        ]

        for ef in expected_files:
            self.assertTrue(ef.exists(), f"Missing expected file: {ef}")
            self.assertGreater(ef.stat().st_size, 20, f"File appears empty: {ef}")

        # 3. Inspect Project Structure
        struct = self.inspector.inspect_structure("data/benchmark_project", max_depth=4)
        self.assertTrue(struct["success"])
        self.assertGreaterEqual(struct["total_files"], 9)

    def test_02_controlled_error_injection_and_self_healing(self):
        """
        Controlled Error Injection & Autonomous Recovery Validation:
        1. Injects a controlled syntax error (missing colon) into models.py.
        2. Executes CodeAgent to detect error, analyze traceback, and apply patch.
        3. Verifies automated checkpoint creation and 100% test recovery.
        """
        models_file = self.proj_dir / "backend" / "models.py"
        self.assertTrue(models_file.exists(), "models.py must exist from build step")

        original_code = models_file.read_text(encoding="utf-8")

        # Inject controlled bug: remove colon on get_connection
        buggy_code = original_code.replace("def get_connection(self):", "def get_connection(self)")
        self.assertIn("def get_connection(self)\n", buggy_code)
        models_file.write_text(buggy_code, encoding="utf-8")

        # Execute healing through CodeAgent
        rel_path = "data/benchmark_project/backend/models.py"
        heal_res = self.code_agent.fix_existing_file(rel_path)

        # Verify healing succeeded
        self.assertTrue(heal_res["success"], f"Self-healing failed: {heal_res.get('message')}")
        self.assertEqual(heal_res["stage"], "complete")

        # Verify fixed code contains the colon
        healed_code = models_file.read_text(encoding="utf-8")
        self.assertIn("def get_connection(self):", healed_code)

        # Run backend tests to verify 100% test pass rate after self-healing
        test_res = self.cc.run_terminal_command(
            "python -m unittest discover tests",
            cwd=str(self.proj_dir),
            timeout=15.0,
        )
        self.assertTrue(test_res["success"])
        self.assertEqual(test_res["returncode"], 0)
        self.assertIn("OK", test_res["stderr"] + test_res["stdout"])

    def test_03_live_server_smoke_test(self):
        """
        Service Startup & Live Endpoint Probe:
        Spawns backend server on a test port, probes /health, confirms 200 OK, and terminates.
        """
        smoke_res = self.cc.smoke_test_server(
            command="backend/app.py 8089",
            port=8089,
            endpoint="/health",
            timeout=10.0,
            cwd=str(self.proj_dir),
        )
        self.assertTrue(smoke_res["success"], f"Smoke test failed: {smoke_res.get('message')}")
        self.assertEqual(smoke_res["status_code"], 200)
        self.assertEqual(smoke_res["response"].get("status"), "healthy")

    def test_04_e2e_hello_nr_ai_task(self):
        """
        User Required Task 8:
        'Create a Python program that prints Hello NR AI,
        run it, detect any error, fix it if necessary,
        and verify the final output.'
        """
        cmd = "Create a Python program that prints Hello NR AI and run it"
        resp = self.brain.think(cmd)
        self.assertIn("Task completed successfully", resp)
        self.assertIn("Hello NR AI", resp)

    def test_05_visual_file_menu_save_task(self):
        """
        User Required Task 9:
        'Open the File menu and save'
        """
        cmd = "Open the File menu and save"
        plan = self.brain.planner.plan(cmd)
        self.assertTrue(plan["success"])
        self.assertEqual(plan["intent"], "visual")
        self.assertEqual(len(plan["actions"]), 2)
        self.assertEqual(plan["actions"][0]["type"], "click_text")
        self.assertEqual(plan["actions"][1]["type"], "click_popup_text")
        self.assertEqual(plan["actions"][1]["target"], "Save")


if __name__ == "__main__":
    unittest.main()
