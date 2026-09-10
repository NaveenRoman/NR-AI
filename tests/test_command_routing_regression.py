"""
Focused automated regression test suite for NR-AI direct local command execution.

Verifies:
1. Explicit local inspection commands run and return real system output:
   - "Execute direct local inspection commands only. Run:\nwhere java\njava -version"
   - "Run:\nwhere adb\nadb version"
   - "where java"
   - "java -version"
   - "where adb"
   - "adb version"
   - "Get-ChildItem" (PowerShell cmdlet)
2. Commands report:
   - command requested
   - command actually executed
   - exit code
   - stdout
   - stderr
   - execution status
3. Informational Android questions return dynamic toolchain status, never static stubs.
4. Security controls block destructive commands and reject unauthorized binaries.
5. General conversation routes to CONVERSATION.
"""

import os
import unittest
from pathlib import Path

from app.brain.companion import CommandCategory, NRCompanion
from app.config.voice_config import VoiceConfig


class TestCommandRoutingRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = VoiceConfig()
        cfg.silent_mode = True
        cfg.tts_enabled = False
        cfg.wake_word_enabled = False
        cls.companion = NRCompanion(voice_config=cfg)

    def test_classify_header_wrapped_commands(self):
        cmd1 = "Execute direct local inspection commands only. Run:\nwhere java\njava -version"
        self.assertEqual(self.companion.classify_command(cmd1), CommandCategory.COMMAND_EXECUTION)

        cmd2 = "Run:\nwhere adb\nadb version"
        self.assertEqual(self.companion.classify_command(cmd2), CommandCategory.COMMAND_EXECUTION)

    def test_classify_cli_diagnostic_commands(self):
        self.assertEqual(self.companion.classify_command("where java"), CommandCategory.COMMAND_EXECUTION)
        self.assertEqual(self.companion.classify_command("java -version"), CommandCategory.COMMAND_EXECUTION)
        self.assertEqual(self.companion.classify_command("where adb"), CommandCategory.COMMAND_EXECUTION)
        self.assertEqual(self.companion.classify_command("adb version"), CommandCategory.COMMAND_EXECUTION)
        self.assertEqual(self.companion.classify_command("Get-ChildItem"), CommandCategory.COMMAND_EXECUTION)

    def test_classify_android_informational_query(self):
        cmd = "What is the status of the Android development environment?"
        self.assertEqual(self.companion.classify_command(cmd), CommandCategory.ANDROID)

    def test_classify_general_conversation(self):
        cmd = "Hello NR, how are you today?"
        self.assertEqual(self.companion.classify_command(cmd), CommandCategory.CONVERSATION)

    def test_execute_header_wrapped_java(self):
        cmd = "Execute direct local inspection commands only. Run:\nwhere java\njava -version"
        resp = self.companion.interact(cmd, speak_output=False)
        self.assertEqual(resp.category, CommandCategory.COMMAND_EXECUTION)
        self.assertEqual(resp.routed_to, "SafeTerminalRunner")
        self.assertNotIn("Android development environment status:", resp.text)
        self.assertIn("COMMAND REQUESTED: where java", resp.text)
        self.assertIn("COMMAND REQUESTED: java -version", resp.text)
        self.assertIn("EXIT CODE:         0", resp.text)
        self.assertIn("STATUS:            SUCCESS", resp.text)
        self.assertTrue("java.exe" in resp.text.lower())
        self.assertTrue("version" in resp.text.lower())

    def test_execute_header_wrapped_adb(self):
        cmd = "Run:\nwhere adb\nadb version"
        resp = self.companion.interact(cmd, speak_output=False)
        self.assertEqual(resp.category, CommandCategory.COMMAND_EXECUTION)
        self.assertEqual(resp.routed_to, "SafeTerminalRunner")
        self.assertNotIn("Android development environment status:", resp.text)
        self.assertIn("COMMAND REQUESTED: where adb", resp.text)
        self.assertIn("COMMAND REQUESTED: adb version", resp.text)
        self.assertIn("EXIT CODE:         0", resp.text)
        self.assertIn("STATUS:            SUCCESS", resp.text)
        self.assertTrue("adb.exe" in resp.text.lower())
        self.assertTrue("android debug bridge" in resp.text.lower())

    def test_execute_single_where_java(self):
        resp = self.companion.interact("where java", speak_output=False)
        self.assertEqual(resp.category, CommandCategory.COMMAND_EXECUTION)
        self.assertEqual(resp.routed_to, "SafeTerminalRunner")
        self.assertIn("COMMAND REQUESTED: where java", resp.text)
        self.assertIn("EXIT CODE:         0", resp.text)
        self.assertTrue("java.exe" in resp.text.lower())

    def test_execute_single_java_version(self):
        resp = self.companion.interact("java -version", speak_output=False)
        self.assertEqual(resp.category, CommandCategory.COMMAND_EXECUTION)
        self.assertEqual(resp.routed_to, "SafeTerminalRunner")
        self.assertIn("COMMAND REQUESTED: java -version", resp.text)
        self.assertIn("EXIT CODE:         0", resp.text)
        self.assertTrue("version" in resp.text.lower())

    def test_execute_single_where_adb(self):
        resp = self.companion.interact("where adb", speak_output=False)
        self.assertEqual(resp.category, CommandCategory.COMMAND_EXECUTION)
        self.assertEqual(resp.routed_to, "SafeTerminalRunner")
        self.assertIn("COMMAND REQUESTED: where adb", resp.text)
        self.assertIn("EXIT CODE:         0", resp.text)
        self.assertTrue("adb.exe" in resp.text.lower())

    def test_execute_single_adb_version(self):
        resp = self.companion.interact("adb version", speak_output=False)
        self.assertEqual(resp.category, CommandCategory.COMMAND_EXECUTION)
        self.assertEqual(resp.routed_to, "SafeTerminalRunner")
        self.assertIn("COMMAND REQUESTED: adb version", resp.text)
        self.assertIn("EXIT CODE:         0", resp.text)
        self.assertTrue("android debug bridge" in resp.text.lower())

    def test_execute_powershell_get_childitem(self):
        resp = self.companion.interact("Get-ChildItem", speak_output=False)
        self.assertEqual(resp.category, CommandCategory.COMMAND_EXECUTION)
        self.assertEqual(resp.routed_to, "SafeTerminalRunner")
        self.assertIn("COMMAND REQUESTED: Get-ChildItem", resp.text)
        self.assertIn("powershell", resp.text.lower())
        self.assertIn("EXIT CODE:         0", resp.text)
        self.assertIn("STATUS:            SUCCESS", resp.text)

    def test_safety_blocked_destructive_command(self):
        cmd = "Run: del /s /q C:\\"
        resp = self.companion.interact(cmd, speak_output=False)
        self.assertEqual(resp.category, CommandCategory.COMMAND_EXECUTION)
        self.assertIn("[SECURITY BLOCKED]", resp.text)

    def test_safety_rejected_unauthorized_command(self):
        cmd = "Run: vssadmin delete shadows"
        resp = self.companion.interact(cmd, speak_output=False)
        self.assertEqual(resp.category, CommandCategory.COMMAND_EXECUTION)
        self.assertIn("[SECURITY REJECTED]", resp.text)

    def test_android_informational_response(self):
        cmd = "What is the status of the Android development environment?"
        resp = self.companion.interact(cmd, speak_output=False)
        self.assertEqual(resp.category, CommandCategory.ANDROID)
        self.assertIn("Android Development Environment Status:", resp.text)
        self.assertIn("Java JDK:", resp.text)
        self.assertIn("Android SDK:", resp.text)
        self.assertIn("Android Debug Bridge (adb):", resp.text)


if __name__ == "__main__":
    unittest.main()
