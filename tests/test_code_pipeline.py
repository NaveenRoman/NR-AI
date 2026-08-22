import os
import shutil
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.code_writer import CodeWriter
from app.agent.code_runner import CodeRunner
from app.agent.error_analyzer import ErrorAnalyzer
from app.agent.recovery_engine import RecoveryEngine
from app.agent.code_agent import CodeAgent
from app.agent.task_planner import TaskPlanner
from app.agent.action_dispatcher import ActionDispatcher
from app.brain.brain import NRBrain


class TestCodeWriter(unittest.TestCase):
    def setUp(self):
        self.test_dir = PROJECT_ROOT / "data" / "test_scratch"
        self.checkpoint_dir = self.test_dir / "checkpoints"
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.writer = CodeWriter(
            workspace=str(self.test_dir),
            checkpoint_dir=str(self.checkpoint_dir),
        )

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_write_and_read(self):
        content = "print('Test Write Read')\n"
        w_res = self.writer.write_file("sample.py", content)
        self.assertTrue(w_res["success"])
        self.assertTrue(Path(w_res["path"]).exists())

        r_res = self.writer.read_file("sample.py")
        self.assertTrue(r_res["success"])
        self.assertEqual(r_res["code"], content)

    def test_modify_and_backup_restore(self):
        initial = "def main():\n    print('v1')\n"
        self.writer.write_file("mod_test.py", initial)

        # Modify
        m_res = self.writer.modify_file(
            "mod_test.py", "print('v1')", "print('v2')"
        )
        self.assertTrue(m_res["success"])

        # Check backup created
        backups = self.writer.list_backups("mod_test.py")
        self.assertGreaterEqual(len(backups), 1)

        # Restore
        rest_res = self.writer.restore_backup("mod_test.py", backups[0]["path"])
        self.assertTrue(rest_res["success"])
        r_after = self.writer.read_file("mod_test.py")
        self.assertEqual(r_after["code"], initial)

    def test_replace_lines(self):
        code = "line1\nline2\nline3\nline4\n"
        self.writer.write_file("lines.txt", code)
        self.writer.replace_lines("lines.txt", 2, 3, "new_line_2_and_3")
        r_res = self.writer.read_file("lines.txt")
        self.assertIn("new_line_2_and_3", r_res["code"])
        self.assertNotIn("line2", r_res["code"])


class TestCodeRunner(unittest.TestCase):
    def setUp(self):
        self.test_dir = PROJECT_ROOT / "data" / "test_scratch_runner"
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.runner = CodeRunner(workspace=str(self.test_dir))

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_run_python(self):
        py_file = self.test_dir / "test.py"
        py_file.write_text("print('Python Execution OK')", encoding="utf-8")
        res = self.runner.run_file(str(py_file))
        self.assertTrue(res["success"])
        self.assertEqual(res["returncode"], 0)
        self.assertIn("Python Execution OK", res["stdout"])

    def test_run_javascript(self):
        js_file = self.test_dir / "test.js"
        js_file.write_text("console.log('JS Execution OK');", encoding="utf-8")
        res = self.runner.run_file(str(js_file))
        self.assertTrue(res["success"])
        self.assertEqual(res["returncode"], 0)
        self.assertIn("JS Execution OK", res["stdout"])

    def test_run_java(self):
        java_file = self.test_dir / "JavaRunTest.java"
        java_file.write_text(
            "public class JavaRunTest {\n"
            "    public static void main(String[] args) {\n"
            "        System.out.println(\"Java Execution OK\");\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        res = self.runner.run_file(str(java_file))
        self.assertTrue(res["success"])
        self.assertEqual(res["returncode"], 0)
        self.assertIn("Java Execution OK", res["stdout"])

    def test_timeout_protection(self):
        timeout_py = self.test_dir / "timeout.py"
        timeout_py.write_text("import time; time.sleep(5)", encoding="utf-8")
        res = self.runner.run_file(str(timeout_py), timeout=0.5)
        self.assertFalse(res["success"])
        self.assertTrue(res["timed_out"])


class TestErrorAnalyzer(unittest.TestCase):
    def setUp(self):
        self.analyzer = ErrorAnalyzer()

    def test_python_syntax_error(self):
        code = "def foo()\n    return 42"
        stderr = (
            '  File "test.py", line 1\n'
            '    def foo()\n'
            '             ^\n'
            "SyntaxError: expected ':'"
        )
        res = self.analyzer.analyze(stderr=stderr, source_code=code, returncode=1)
        self.assertTrue(res["has_error"])
        self.assertEqual(res["language"], "python")
        self.assertEqual(res["type"], "SyntaxError")
        self.assertEqual(res["line"], 1)

    def test_java_compilation_error(self):
        code = "public class A { int x = 5 }"
        stderr = "A.java:1: error: ';' expected\npublic class A { int x = 5 }"
        res = self.analyzer.analyze(stderr=stderr, source_code=code, returncode=1)
        self.assertTrue(res["has_error"])
        self.assertEqual(res["language"], "java")
        self.assertEqual(res["line"], 1)


class TestRecoveryEngine(unittest.TestCase):
    def setUp(self):
        self.recovery = RecoveryEngine(max_retries=3)

    def test_python_missing_colon_recovery(self):
        buggy = "def calculate(a, b)\n    return a * b\n"
        error_info = {
            "language": "python",
            "type": "SyntaxError",
            "line": 1,
            "message": "SyntaxError: expected ':'",
        }
        fix = self.recovery.generate_fix(error_info, buggy)
        self.assertTrue(fix["success"])
        self.assertIn("def calculate(a, b):", fix["fixed_code"])

    def test_java_semicolon_recovery(self):
        buggy = "public class Hello {\n    public static void main(String[] args) {\n        System.out.println(\"Hi\")\n    }\n}\n"
        error_info = {
            "language": "java",
            "type": "JavaCompilationError",
            "line": 3,
            "message": "Hello.java:3: error: ';' expected",
        }
        fix = self.recovery.generate_fix(error_info, buggy, filename="Hello.java")
        self.assertTrue(fix["success"])
        self.assertIn('System.out.println("Hi");', fix["fixed_code"])


class TestCodeAgentEndToEnd(unittest.TestCase):
    def setUp(self):
        self.test_dir = PROJECT_ROOT / "data" / "test_scratch_agent"
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.agent = CodeAgent(workspace=str(self.test_dir), max_retries=3)

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_self_healing_execution(self):
        # Code has missing colon on line 1
        buggy_code = (
            "def add(x, y)\n"
            "    return x + y\n\n"
            "print(f'Total: {add(20, 30)}')\n"
        )
        res = self.agent.execute("self_heal.py", buggy_code)
        self.assertTrue(res["success"])
        self.assertEqual(res["attempts"], 2)
        self.assertIn("Total: 50", res["output"])

    def test_clean_execution(self):
        clean_code = "print('Clean Run OK')\n"
        res = self.agent.execute("clean.py", clean_code)
        self.assertTrue(res["success"])
        self.assertEqual(res["attempts"], 1)
        self.assertIn("Clean Run OK", res["output"])


class TestIntegrationWithPlannerAndBrain(unittest.TestCase):
    def setUp(self):
        self.planner = TaskPlanner()
        self.brain = NRBrain()

    def test_planner_code_and_visual(self):
        # Code planning
        code_plan = self.planner.plan("Create a Python calculator and run it")
        self.assertTrue(code_plan["success"])
        self.assertIn(code_plan["intent"], ["code", "complex_task"])
        self.assertEqual(code_plan["actions"][0]["type"], "code_execute")

        # Visual planning
        visual_plan = self.planner.plan("Open File menu and save")
        self.assertTrue(visual_plan["success"])
        self.assertEqual(visual_plan["intent"], "visual")
        self.assertEqual(len(visual_plan["actions"]), 2)

    def test_brain_command_routing(self):
        resp_time = self.brain.think("What time is it?")
        self.assertIn("The current time is", resp_time)

        resp_hello = self.brain.think("Hello NR AI")
        self.assertIn("Hello. I am NR AI", resp_hello)


if __name__ == "__main__":
    unittest.main()
