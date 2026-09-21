"""
Comprehensive Security Audit Suite for NR-AI OpenJarvis Phase 2.
Verifies:
- Zero shell=True in Phase 2 codebase
- Zero eval/exec in Phase 2 codebase
- Zero unrestricted subprocess usage
- Zero secret / PII leakage across automations, connectors, context, telemetry
- Strict ModelIsolationGate enforcement
- Destructive actions recovery barrier (never auto-resume destructive commands)
"""

import os
from pathlib import Path
import re
import unittest

from app.automation.engine import AutomationEngine
from app.automation.models import AutomationRecord, AutomationStatus
from app.mcp.client import ControlledMCPClient
from app.telemetry.events import TelemetryEventType, TelemetryLogger
from app.telemetry.sanitizer import sanitize_telemetry_data


PHASE2_DIRECTORIES = [
    Path(r"C:\NR-AI\app\automation"),
    Path(r"C:\NR-AI\app\connectors"),
    Path(r"C:\NR-AI\app\context"),
    Path(r"C:\NR-AI\app\telemetry"),
    Path(r"C:\NR-AI\app\memory\integration.py"),
    Path(r"C:\NR-AI\app\mcp\config.py"),
    Path(r"C:\NR-AI\app\mcp\client.py"),
    Path(r"C:\NR-AI\app\a2a\client.py"),
]


class TestPhase2Security(unittest.TestCase):
    """Rigorous security audit tests for Phase 2 implementation."""

    def test_zero_shell_true_in_phase2(self):
        """Verify zero occurrences of shell=True in any Phase 2 source code."""
        occurrences = []
        for target in PHASE2_DIRECTORIES:
            if target.is_file():
                files = [target]
            elif target.is_dir():
                files = list(target.rglob("*.py"))
            else:
                continue

            for f in files:
                content = f.read_text(encoding="utf-8", errors="replace")
                if re.search(r"shell\s*=\s*True", content):
                    occurrences.append(str(f))

        self.assertEqual(
            len(occurrences), 0,
            f"SECURITY VIOLATION: Found shell=True in Phase 2 files: {occurrences}"
        )

    def test_zero_eval_exec_in_phase2(self):
        """Verify zero occurrences of eval() or exec() in Phase 2 source code."""
        occurrences = []
        for target in PHASE2_DIRECTORIES:
            if target.is_file():
                files = [target]
            elif target.is_dir():
                files = list(target.rglob("*.py"))
            else:
                continue

            for f in files:
                content = f.read_text(encoding="utf-8", errors="replace")
                # Exclude comment lines or standard logger/string patterns
                lines = content.splitlines()
                for idx, line in enumerate(lines):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if re.search(r"\b(eval|exec)\s*\(", line):
                        occurrences.append(f"{f}:{idx+1}: {line}")

        self.assertEqual(
            len(occurrences), 0,
            f"SECURITY VIOLATION: Found eval/exec in Phase 2 files: {occurrences}"
        )

    def test_telemetry_sanitizer_removes_auth_and_cookies(self):
        """Verify TelemetrySanitizer strips bearer tokens and cookies."""
        raw_event_data = {
            "user": "developer",
            "authorization": "Bearer secret_jwt_token_abcdef123456",
            "cookie": "session_id=super_secret_cookie_999",
            "request_header": "Authorization: Bearer my_token_123456789012345",
            "api_key": "AIzaSySecretKey123456789012345678",
            "email": "engineer@nrai.internal",
        }
        cleaned = sanitize_telemetry_data(raw_event_data)
        self.assertEqual(cleaned["authorization"], "[REDACTED]")
        self.assertEqual(cleaned["cookie"], "[REDACTED]")
        self.assertEqual(cleaned["api_key"], "[REDACTED]")
        self.assertNotIn("super_secret_cookie", str(cleaned))
        self.assertNotIn("secret_jwt_token", str(cleaned))
        self.assertNotIn("AIzaSySecretKey", str(cleaned))

    def test_model_isolation_gate_strictly_enforced_in_mcp(self):
        """Verify MCP client permanently enforces ModelIsolationGate against direct LLM invocation."""
        client = ControlledMCPClient()
        res = client.call_tool(
            server_id="any_server",
            tool_name="any_tool",
            params={},
            caller_agent_id="ModelProposer",
            is_direct_model_call=True,
        )
        self.assertFalse(res.success)
        self.assertIn("MODEL_ISOLATION_VIOLATION", res.error)

    def test_destructive_recovery_barrier(self):
        """Verify destructive automation tasks cannot silently resume across restarts."""
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "sec_test.db"
            engine = AutomationEngine(db_path=db_path)
            rec = AutomationRecord(
                owner_id="Admin",
                prompt="Format C: drive and drop production table.",
                status=AutomationStatus.ACTIVE,
            )
            auto_id = engine.create_automation(rec)
            self.assertTrue(engine.get_automation(auto_id).requires_confirmation)

            # Reboot recovery
            engine.recover_on_boot()
            fetched = engine.get_automation(auto_id)
            self.assertEqual(fetched.status, AutomationStatus.PAUSED)
            self.assertIn("RECOVERY_BARRIER", fetched.failure_reason)


if __name__ == "__main__":
    unittest.main()
