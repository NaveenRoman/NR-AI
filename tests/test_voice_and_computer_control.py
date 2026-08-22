import os
import shutil
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config.voice_config import VoiceConfig
from app.voice.listener import (
    ListeningState,
    MockSpeechRecognizer,
    VoiceListener,
)
from app.voice.speaker import MemoryTTS, SilentTTS, VoiceSpeaker
from app.agent.computer_control import ComputerControl
from app.agent.project_inspector import ProjectInspector
from app.agent.terminal_manager import TerminalManager
from app.agent.task_planner import TaskPlanner
from app.agent.action_dispatcher import ActionDispatcher
from app.brain.brain import NRBrain


class TestVoicePipeline(unittest.TestCase):
    def setUp(self):
        self.config = VoiceConfig(silent_mode=True, tts_enabled=False)
        self.mock_backend = MockSpeechRecognizer()
        self.listener = VoiceListener(
            config=self.config, recognizer_backend=self.mock_backend
        )
        self.memory_tts = MemoryTTS()
        self.speaker = VoiceSpeaker(
            config=VoiceConfig(tts_enabled=True), tts_backend=self.memory_tts
        )

    def test_voice_listener_normalization(self):
        phrases = {
            "Please open the File menu and save.": "open the File menu and save",
            "Can you create a Python calculator and run it?": "create a Python calculator and run it",
            "Hey NR AI fix the error in this Python program!": "fix the error in this Python program",
            "Could you update the program to print Hello NR AI": "update the program to print Hello NR AI",
        }
        for raw, expected in phrases.items():
            normalized = self.listener.normalize_command(raw)
            self.assertEqual(normalized, expected)

    def test_voice_listener_start_stop(self):
        self.listener.stop()
        self.assertEqual(self.listener.state, ListeningState.STOPPED)
        res = self.listener.listen()
        self.assertIsNone(res)

        self.listener.start()
        self.assertEqual(self.listener.state, ListeningState.IDLE)

    def test_mock_speech_to_brain_to_tts(self):
        # Inject simulated voice transcript
        self.mock_backend.queue_text("What time is it?")
        spoken_input = self.listener.listen()
        self.assertEqual(spoken_input, "What time is it")

        brain = NRBrain()
        response = brain.think(spoken_input)
        self.assertIn("The current time is", response)

        # Verify TTS synthesis
        self.speaker.speak(response)
        self.assertTrue(len(self.memory_tts.spoken_history) > 0)
        self.assertIn("The current time is", self.memory_tts.spoken_history[-1])


class TestPhase1RealCommands(unittest.TestCase):
    """
    Test the four explicit commands specified in Phase 1:
    1. "Open the File menu and save"
    2. "Create a Python program that prints Hello World and run it"
    3. "Fix the error in this Python program"
    4. "Update the program to print Hello NR AI"
    """

    def setUp(self):
        self.test_dir = PROJECT_ROOT / "data" / "test_phase1"
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.brain = NRBrain()
        self.memory_tts = MemoryTTS()
        self.speaker = VoiceSpeaker(
            config=VoiceConfig(tts_enabled=True), tts_backend=self.memory_tts
        )

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_command_1_menu_save(self):
        cmd = "Open the File menu and save"
        plan = self.brain.planner.plan(cmd)
        self.assertTrue(plan["success"])
        self.assertEqual(plan["intent"], "visual")
        self.assertEqual(len(plan["actions"]), 2)
        self.assertEqual(plan["actions"][0]["type"], "click_text")
        self.assertEqual(plan["actions"][1]["type"], "click_popup_text")

    def test_command_2_create_and_run_hello_world(self):
        cmd = "Create a Python program that prints Hello World and run it"
        resp = self.brain.think(cmd)
        self.assertIn("Task completed successfully", resp)
        self.assertIn("Hello World", resp)

    def test_command_3_fix_error_in_python_program(self):
        # Create a buggy file
        buggy_file = PROJECT_ROOT / "data" / "test_buggy.py"
        buggy_file.parent.mkdir(parents=True, exist_ok=True)
        buggy_file.write_text("def test_fix(a, b)\n    return a + b\n\nprint(test_fix(1, 2))\n", encoding="utf-8")

        cmd = "Fix the error in this Python program"
        resp = self.brain.think(cmd)
        self.assertIn("Task completed successfully", resp)

    def test_command_4_update_program_to_print_hello_nr_ai(self):
        # Create a file with Hello World in data/
        target_file = PROJECT_ROOT / "data" / "test_hello_sample.py"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text("print('Hello World')\n", encoding="utf-8")

        cmd = "Update the program to print Hello NR AI"
        resp = self.brain.think(cmd)
        self.assertIn("Task completed successfully", resp)

        # Verify file content
        content = target_file.read_text(encoding="utf-8")
        self.assertIn("Hello NR AI", content)


class TestPhase2BroaderComputerControl(unittest.TestCase):
    """
    Test Phase 2 Computer Control Capabilities:
    - Terminal command execution
    - Project inspection
    - Multi-file batch replace
    - File open & state verification
    """

    def setUp(self):
        self.test_dir = PROJECT_ROOT / "data" / "test_phase2"
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.cc = ComputerControl(workspace=str(self.test_dir))
        self.terminal = TerminalManager(workspace=str(self.test_dir))
        self.inspector = ProjectInspector(workspace=str(self.test_dir))

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_terminal_command_execution(self):
        res = self.terminal.run_command("python -c \"print('Terminal Control OK')\"")
        self.assertTrue(res["success"])
        self.assertEqual(res["returncode"], 0)
        self.assertIn("Terminal Control OK", res["stdout"])

    def test_terminal_dangerous_command_guardrail(self):
        res = self.terminal.run_command("rmdir /s /q C:\\Windows")
        self.assertFalse(res["success"])
        self.assertIn("blocked", res["message"].lower())

    def test_multi_file_project_inspection(self):
        # Create small test directory structure
        (self.test_dir / "src").mkdir(parents=True, exist_ok=True)
        (self.test_dir / "src" / "mod1.py").write_text("API_URL = 'http://localhost:8000'", encoding="utf-8")
        (self.test_dir / "src" / "mod2.py").write_text("API_URL = 'http://localhost:8000'", encoding="utf-8")

        struct = self.inspector.inspect_structure()
        self.assertTrue(struct["success"])
        self.assertEqual(struct["total_files"], 2)

    def test_batch_replace_across_project(self):
        (self.test_dir / "src").mkdir(parents=True, exist_ok=True)
        f1 = self.test_dir / "src" / "client1.py"
        f2 = self.test_dir / "src" / "client2.py"
        f1.write_text("ENDPOINT = 'http://localhost:8000/v1'", encoding="utf-8")
        f2.write_text("ENDPOINT = 'http://localhost:8000/v1'", encoding="utf-8")

        rep_res = self.inspector.batch_replace(
            search_text="http://localhost:8000",
            replace_text="https://api.nr-ai.com",
            file_pattern="*.py",
        )
        self.assertTrue(rep_res["success"])
        self.assertEqual(rep_res["files_modified_count"], 2)
        self.assertEqual(rep_res["total_replacements"], 2)

        self.assertIn("https://api.nr-ai.com/v1", f1.read_text(encoding="utf-8"))
        self.assertIn("https://api.nr-ai.com/v1", f2.read_text(encoding="utf-8"))

    def test_computer_control_file_verification(self):
        test_file = self.test_dir / "sample.py"
        test_file.write_text("def run(): pass\n", encoding="utf-8")
        v_res = self.cc.verify_file_state(str(test_file), expected_snippet="def run():")
        self.assertTrue(v_res["success"])


class TestPhase2EndToEndPlanner(unittest.TestCase):
    def setUp(self):
        self.planner = TaskPlanner()
        self.dispatcher = ActionDispatcher()

    def test_open_terminal_and_run(self):
        cmd = "Open the terminal and run the program"
        plan = self.planner.plan(cmd)
        self.assertTrue(plan["success"])
        self.assertEqual(len(plan["actions"]), 2)
        self.assertEqual(plan["actions"][0]["type"], "open_terminal")
        self.assertEqual(plan["actions"][1]["type"], "run_terminal_cmd")

    def test_create_file_called_calculator(self):
        cmd = "Create a new Python file called calculator.py"
        plan = self.planner.plan(cmd)
        self.assertTrue(plan["success"])
        self.assertEqual(plan["actions"][0]["type"], "write_code")
        self.assertEqual(plan["actions"][0]["filename"], "calculator.py")


if __name__ == "__main__":
    unittest.main()
