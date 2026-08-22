import json
import os
import shutil
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.action_dispatcher import ActionDispatcher
from app.agent.autonomous_loop import AutonomousDevLoop
from app.agent.code_writer import CodeWriter
from app.agent.service_supervisor import ServiceSupervisor
from app.agent.task_planner import TaskPlanner
from app.brain.brain import NRBrain
from app.memory.audit_logger import AuditLogger
from app.memory.context_memory import ProjectContextMemory


class TestProductizationPhaseV11(unittest.TestCase):
    """
    Productization Phase v1.1 Test Suite.

    Validates:
    1. Multi-turn project context memory & follow-up resolution
    2. Task cancellation ("stop", "cancel")
    3. Checkpoint undo & rollback
    4. Service supervisor (start, probe, crash detection, restart, stop)
    5. Persistent audit logging
    6. Autonomous development loop
    """

    def setUp(self):
        self.test_dir = PROJECT_ROOT / "data" / "test_product_v11"
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.memory = ProjectContextMemory(workspace=str(self.test_dir))
        self.audit = AuditLogger(log_dir=str(self.test_dir / "audit"))
        self.planner = TaskPlanner(memory=self.memory)
        self.dispatcher = ActionDispatcher(memory=self.memory)
        self.supervisor = ServiceSupervisor(workspace=str(self.test_dir))
        self.brain = NRBrain(workspace=str(self.test_dir))

    def tearDown(self):
        self.supervisor.stop_all()
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_01_context_memory_tracking(self):
        """Verify context memory tracks project path, files, plans, and errors."""
        self.memory.set_active_project("data/benchmark_project")
        self.assertEqual(self.memory.get_active_project(), "data/benchmark_project")

        self.memory.record_file_modified("data/benchmark_project/backend/models.py")
        self.assertEqual(
            self.memory.get_last_modified_file(),
            "data/benchmark_project/backend/models.py",
        )

        summary = self.memory.get_summary()
        self.assertEqual(summary["recent_files_count"], 1)

    def test_02_follow_up_update_that(self):
        """Verify 'update that' targets the most recently modified file."""
        self.memory.record_file_modified("data/benchmark_project/backend/models.py")
        plan = self.planner.plan("update that")
        self.assertTrue(plan["success"])
        self.assertEqual(plan["actions"][0]["type"], "modify_code")
        self.assertEqual(plan["actions"][0]["filename"], "data/benchmark_project/backend/models.py")

    def test_03_follow_up_change_login_page(self):
        """Verify 'change the login page' targets App.jsx and runs test suite."""
        plan = self.planner.plan("change the login page")
        self.assertTrue(plan["success"])
        self.assertEqual(plan["actions"][0]["type"], "modify_code")
        self.assertIn("App.jsx", plan["actions"][0]["filename"])
        self.assertEqual(plan["actions"][1]["type"], "run_terminal_cmd")

    def test_04_follow_up_add_payment(self):
        """Verify 'add payment' creates payment module and runs tests."""
        plan = self.planner.plan("add payment")
        self.assertTrue(plan["success"])
        self.assertEqual(plan["actions"][0]["type"], "write_code")
        self.assertIn("payment.py", plan["actions"][0]["filename"])
        self.assertEqual(plan["actions"][1]["type"], "run_terminal_cmd")

    def test_05_follow_up_run_it_again(self):
        """Verify 'run it again' re-runs test suite."""
        plan = self.planner.plan("run it again")
        self.assertTrue(plan["success"])
        self.assertEqual(plan["actions"][0]["type"], "run_terminal_cmd")

    def test_06_task_cancellation(self):
        """Verify 'stop' and 'cancel' trigger task cancellation."""
        plan = self.planner.plan("cancel")
        self.assertTrue(plan["success"])
        self.assertEqual(plan["actions"][0]["type"], "cancel_task")

        resp = self.brain.think("stop")
        self.assertIn("cancelled", resp.lower())

    def test_07_undo_and_rollback(self):
        """Verify checkpoint rollback restores original file content."""
        writer = CodeWriter(workspace=str(self.test_dir))
        sample_file = self.test_dir / "rollback_test.py"

        # Step 1: Initial write
        writer.write_file("rollback_test.py", "VERSION = 1\n")
        self.assertIn("VERSION = 1", sample_file.read_text(encoding="utf-8"))

        # Step 2: Modify with backup
        mod_res = writer.modify_file("rollback_test.py", "VERSION = 1", "VERSION = 2")
        self.assertTrue(mod_res["success"])
        self.assertIn("VERSION = 2", sample_file.read_text(encoding="utf-8"))

        self.memory.record_file_modified("rollback_test.py", backup_path=mod_res["backup_path"])

        # Step 3: Trigger rollback
        rb_res = self.dispatcher.execute({"type": "rollback_last_change"})
        self.assertTrue(rb_res["success"])
        self.assertIn("VERSION = 1", sample_file.read_text(encoding="utf-8"))

    def test_08_service_supervisor_start_probe_stop(self):
        """Verify ServiceSupervisor tracks background server, probes health, and stops."""
        # Create a simple mock HTTP server file
        server_file = self.test_dir / "mock_srv.py"
        server_file.write_text(
            "import sys\n"
            "from http.server import HTTPServer, BaseHTTPRequestHandler\n"
            "class H(BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            "        self.send_response(200)\n"
            "        self.send_header('Content-Type', 'application/json')\n"
            "        self.end_headers()\n"
            "        self.wfile.write(b'{\"status\": \"healthy\"}')\n"
            "httpd = HTTPServer(('127.0.0.1', 8095), H)\n"
            "httpd.serve_forever()\n",
            encoding="utf-8",
        )

        start_res = self.supervisor.start_service("mock_srv", "mock_srv.py", port=8095, cwd=str(self.test_dir))
        self.assertTrue(start_res["success"], f"Failed to start mock service: {start_res.get('message')}")

        # Probe health
        probe_res = self.supervisor.probe_health(8095, endpoint="/health", timeout=5.0)
        self.assertTrue(probe_res["success"], f"Health probe failed: {probe_res.get('message')}")
        self.assertEqual(probe_res["response"].get("status"), "healthy")

        # Stop service
        stop_res = self.supervisor.stop_service("mock_srv")
        self.assertTrue(stop_res["success"])

    def test_09_service_supervisor_crash_detection(self):
        """Verify ServiceSupervisor detects immediate process crashes and captures exit code."""
        crash_file = self.test_dir / "crash_srv.py"
        crash_file.write_text("import sys\nsys.exit(42)\n", encoding="utf-8")

        start_res = self.supervisor.start_service("crash_srv", "crash_srv.py", cwd=str(self.test_dir))
        self.assertFalse(start_res["success"])
        self.assertIn("crashed on startup with exit code 42", start_res["message"])

    def test_10_audit_logger_persistence(self):
        """Verify AuditLogger writes structured JSON events."""
        self.audit.log_user_request("Build a Python calculator")
        self.audit.log_file_modification("calculator.py", "created")

        entries = self.audit.get_entries()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["event_type"], "user_request")
        self.assertEqual(entries[1]["event_type"], "file_modification")

        # Verify disk file
        log_file = self.audit.log_file
        self.assertTrue(log_file.exists())
        data = json.loads(log_file.read_text(encoding="utf-8"))
        self.assertEqual(data["total_events"], 2)

    def test_11_autonomous_dev_loop(self):
        """Verify AutonomousDevLoop executes planning, implementation, and verification."""
        loop = AutonomousDevLoop(
            planner=self.planner,
            dispatcher=self.dispatcher,
            memory=self.memory,
            audit_logger=self.audit,
        )
        res = loop.run_lifecycle("Create a Python program that prints Hello World and run it")
        self.assertTrue(res["success"])
        self.assertEqual(res["stage"], "complete")


if __name__ == "__main__":
    unittest.main()
