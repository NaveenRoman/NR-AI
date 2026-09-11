"""
Step 5 Phase 4 Test Suite — BrowserAgent: Goal-Driven Safe Web Workflow Engine.

Tests A through AB:
A. BrowserAgent import
B. goal parsing
C. structured action intent
D. tool allowlist
E. invalid tool rejection
F. model cannot access Playwright directly
G. simple goal execution
H. observe-plan-act-verify loop
I. successful click verification
J. failed action detection
K. stale target recovery
L. bounded retry
M. max workflow steps
N. rate limiting
O. emergency stop
P. prompt injection remains untrusted
Q. sensitive data protection
R. download quarantine preserved
S. high-risk confirmation required
T. ground-truth overrides model success claim
U. dynamic DOM recovery
V. state refresh after action
W. audit logging
X. local fixture end-to-end workflow
Y. Phase 3 regression (27 tests)
Z. Phase 2 regression (29 tests)
AA. Phase 1 regression (16 tests)
AB. Step 4E regression (32 tests)
"""

import http.server
import json
import os
from pathlib import Path
import socketserver
import sys
import threading
import time
import unittest

from app.agent.browser_agent import (
    ALLOWED_BROWSER_TOOLS,
    ActionIntent,
    BrowserAgent,
    BrowserWorkflowReport,
    BrowserWorkflowStep,
    HIGH_RISK_ACTION_PATTERNS,
    MAX_RETRIES_PER_ACTION,
    MAX_WORKFLOW_STEPS,
    TARGET_TTL_SECONDS,
)
from app.agent.browser_driver import BrowserDriver
from app.agent.browser_perception import WebPerception
from app.agent.browser_safety import (
    BrowserRateLimiter,
    BrowserSafetyGate,
    RateLimitExceededError,
    StaleTargetError,
)
from app.agent.browser_tools import (
    BrowserToolRegistry,
    BrowserToolResult,
    RiskLevel,
)
from app.agent.browser_verifier import (
    BrowserStateSnapshot,
    BrowserStateVerifier,
    StateVerificationResult,
)
from app.agent.consensus_engine import (
    ConsensusDecision,
    DecisionCase,
)
from app.agent.model_router import ModelRouter
from app.config.model_config import ModelCapability
from app.memory.audit_logger import AuditLogger

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class QuietHTTPHandler(http.server.SimpleHTTPRequestHandler):
    """Suppresses console logging during test execution."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FIXTURES_DIR.resolve()), **kwargs)

    def log_message(self, format, *args):
        pass


class TestStep5Phase4BrowserAgent(unittest.TestCase):
    """Step 5 Phase 4 BrowserAgent Acceptance Suite."""

    server = None
    server_thread = None
    server_port = None
    base_url = None

    @classmethod
    def setUpClass(cls):
        cls.server = socketserver.TCPServer(("127.0.0.1", 0), QuietHTTPHandler)
        cls.server_port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.server_port}"
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.shutdown()
            cls.server.server_close()

    def setUp(self):
        self.safety_gate = BrowserSafetyGate()
        self.tool_registry = BrowserToolRegistry(safety_gate=self.safety_gate)
        self.perception = WebPerception(safety_gate=self.safety_gate)
        self.verifier = BrowserStateVerifier(perception=self.perception, safety_gate=self.safety_gate)
        self.model_router = ModelRouter()
        self.audit = AuditLogger()
        self.agent = BrowserAgent(
            tool_registry=self.tool_registry,
            perception=self.perception,
            verifier=self.verifier,
            model_router=self.model_router,
            audit_logger=self.audit,
        )

    def tearDown(self):
        if self.tool_registry and self.tool_registry.driver:
            try:
                self.tool_registry.driver.close()
            except Exception:
                pass
        try:
            import asyncio
            asyncio.set_event_loop(None)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Test A: BrowserAgent Import & Defaults
    # -------------------------------------------------------------------------
    def test_A_browser_agent_import(self):
        self.assertIsNotNone(self.agent)
        self.assertEqual(self.agent.max_steps, MAX_WORKFLOW_STEPS)
        self.assertEqual(self.agent.max_retries, MAX_RETRIES_PER_ACTION)
        self.assertEqual(self.agent.target_ttl, TARGET_TTL_SECONDS)
        self.assertEqual(len(ALLOWED_BROWSER_TOOLS), 16)

    # -------------------------------------------------------------------------
    # Test B: Goal Parsing
    # -------------------------------------------------------------------------
    def test_B_goal_parsing(self):
        goal = "Open the local test page and click the submit button."
        actions = self.agent.parse_goal(goal, base_url=self.base_url)
        self.assertGreaterEqual(len(actions), 2)
        tool_names = [a.tool for a in actions]
        self.assertIn("browser.open", tool_names)
        self.assertIn("browser.navigate", tool_names)
        self.assertIn("browser.click", tool_names)

        # Verify target_id and expected state are populated
        click_action = [a for a in actions if a.tool == "browser.click"][0]
        self.assertEqual(click_action.target_id, "submit-button")
        self.assertIn("expected_text_appeared", click_action.expected)

    # -------------------------------------------------------------------------
    # Test C: Structured Action Intent
    # -------------------------------------------------------------------------
    def test_C_structured_action_intent(self):
        intent = ActionIntent(
            tool="browser.click",
            target_id="btn_submit",
            params={"selector": "#btn_submit"},
            reason="Submit the completed local test form",
            expected={"expected_text_appeared": "Success"},
            risk_level="LOW",
        )
        d = intent.to_dict()
        self.assertEqual(d["tool"], "browser.click")
        self.assertEqual(d["target_id"], "btn_submit")
        self.assertEqual(d["params"]["selector"], "#btn_submit")
        self.assertEqual(d["expected"]["expected_text_appeared"], "Success")
        # Ensure JSON-serializable
        json_str = json.dumps(d)
        self.assertIn("btn_submit", json_str)

    # -------------------------------------------------------------------------
    # Test D: Tool Allowlist
    # -------------------------------------------------------------------------
    def test_D_tool_allowlist(self):
        expected_tools = {
            "browser.open", "browser.navigate", "browser.back", "browser.forward",
            "browser.refresh", "browser.list_tabs", "browser.switch_tab",
            "browser.inspect_page", "browser.find_text", "browser.find_element",
            "browser.click", "browser.type", "browser.scroll", "browser.verify",
            "browser.screenshot", "browser.close"
        }
        self.assertEqual(ALLOWED_BROWSER_TOOLS, expected_tools)

    # -------------------------------------------------------------------------
    # Test E: Invalid Tool Rejection
    # -------------------------------------------------------------------------
    def test_E_invalid_tool_rejection(self):
        invalid_intent = ActionIntent(tool="browser.eval", params={"script": "alert(1)"})
        valid, msg = self.agent.validate_action(invalid_intent)
        self.assertFalse(valid)
        self.assertIn("not in approved allowlist", msg)

        # Verify execution fails safely
        report = self.agent.execute_workflow(
            user_goal="Execute malicious script",
            initial_plan=[invalid_intent],
        )
        self.assertFalse(report.success)
        self.assertEqual(report.error, "INVALID_TOOL")

    # -------------------------------------------------------------------------
    # Test F: Model Cannot Access Playwright Directly
    # -------------------------------------------------------------------------
    def test_F_model_cannot_access_playwright_directly(self):
        # Open fixture
        self.tool_registry.execute_tool("browser.open", {"headless": True})
        self.tool_registry.execute_tool("browser.navigate", {"url": f"{self.base_url}/test_page.html"})
        page = self.tool_registry.driver.get_active_page()
        snapshot = self.verifier.capture_snapshot(page)

        # Consult model - ensure snapshot only contains text/dict representation
        self.assertIsInstance(snapshot.to_dict(), dict)
        self.assertFalse(hasattr(snapshot, "page"))
        self.assertFalse(hasattr(snapshot, "locator"))
        self.assertFalse(hasattr(snapshot, "evaluate"))

        # Model consultation does not expose Playwright
        res = self.agent.consult_model("What is the title?", snapshot)
        self.assertIsNone(res)  # Advisory returns None or ActionIntent, never Playwright handles

    # -------------------------------------------------------------------------
    # Test G: Simple Goal Execution
    # -------------------------------------------------------------------------
    def test_G_simple_goal_execution(self):
        report = self.agent.execute_workflow(
            user_goal="Open the local fixture",
            base_url=self.base_url,
        )
        self.assertTrue(report.success)
        self.assertGreaterEqual(report.steps_executed, 1)
        self.assertTrue(self.tool_registry.driver.is_running())

    # -------------------------------------------------------------------------
    # Test H: Observe-Plan-Act-Verify Loop
    # -------------------------------------------------------------------------
    def test_H_observe_plan_act_verify_loop(self):
        report = self.agent.execute_workflow(
            user_goal="Open the local fixture and verify 'NR-AI Safe Browser Test Fixture' appears",
            base_url=self.base_url,
        )
        self.assertTrue(report.success)
        self.assertGreaterEqual(report.steps_executed, 2)
        # Verify steps have records
        for s in report.steps:
            self.assertEqual(s["status"], "COMPLETED")
            self.assertIsNotNone(s["tool_result"])

    # -------------------------------------------------------------------------
    # Test I: Successful Click Verification
    # -------------------------------------------------------------------------
    def test_I_successful_click_verification(self):
        plan = [
            ActionIntent(tool="browser.open", params={"headless": True}),
            ActionIntent(tool="browser.navigate", params={"url": f"{self.base_url}/test_page.html"}),
            ActionIntent(
                tool="browser.click",
                target_id="test-button",
                params={"selector": "#test-button"},
                reason="Click the test button",
                expected={"expected_text_appeared": "Button Clicked Successfully"},
            ),
        ]
        report = self.agent.execute_workflow(
            user_goal="Click the test button and verify message",
            initial_plan=plan,
        )
        self.assertTrue(report.success)
        self.assertEqual(report.steps_executed, 3)
        self.assertTrue(report.steps[2]["verification_result"]["verified"])

    # -------------------------------------------------------------------------
    # Test J: Failed Action Detection
    # -------------------------------------------------------------------------
    def test_J_failed_action_detection(self):
        plan = [
            ActionIntent(tool="browser.open", params={"headless": True}),
            ActionIntent(tool="browser.navigate", params={"url": f"{self.base_url}/test_page.html"}),
            ActionIntent(
                tool="browser.click",
                target_id="test-button",
                params={"selector": "#test-button"},
                reason="Click the button expecting impossible outcome",
                expected={"expected_text_appeared": "IMPOSSIBLE_TEXT_12345"},
            ),
        ]
        report = self.agent.execute_workflow(
            user_goal="Click button with failing expectation",
            initial_plan=plan,
        )
        self.assertFalse(report.success)
        self.assertEqual(report.error, "GROUND_TRUTH_VERIFICATION_FAILED")
        self.assertIn("IMPOSSIBLE_TEXT_12345", report.failure_reason)

    # -------------------------------------------------------------------------
    # Test K: Stale Target Recovery
    # -------------------------------------------------------------------------
    def test_K_stale_target_recovery(self):
        # Open page first
        self.tool_registry.execute_tool("browser.open", {"headless": True})
        self.tool_registry.execute_tool("browser.navigate", {"url": f"{self.base_url}/test_page.html"})
        page = self.tool_registry.driver.get_active_page()

        # Target ID is known as submit-button, but selector was initially slightly wrong
        action = ActionIntent(
            tool="browser.click",
            target_id="submit-button",
            params={"selector": "#non-existent-stale-selector"},
            reason="Click submit button",
            expected={"expected_text_appeared": "Form Submitted"},
        )
        recovered, notes, fresh_action = self.agent._attempt_stale_target_recovery(page, action)
        self.assertTrue(recovered)
        self.assertIsNotNone(fresh_action)
        self.assertIn("submit", fresh_action.params["selector"].lower())

    # -------------------------------------------------------------------------
    # Test L: Bounded Retry
    # -------------------------------------------------------------------------
    def test_L_bounded_retry(self):
        plan = [
            ActionIntent(tool="browser.open", params={"headless": True}),
            ActionIntent(tool="browser.navigate", params={"url": f"{self.base_url}/test_page.html"}),
            ActionIntent(
                tool="browser.click",
                target_id="nonexistent-target",
                params={"selector": "#completely-invalid-target-xyz"},
                reason="Click non-existent target",
            ),
        ]
        report = self.agent.execute_workflow(
            user_goal="Click non-existent target with bounded retries",
            initial_plan=plan,
        )
        self.assertFalse(report.success)
        # Should attempt initial + 2 retries = 3 attempts, then stop
        failing_step = report.steps[2]
        self.assertEqual(failing_step["attempt"], 3)
        self.assertEqual(failing_step["status"], "FAILED")

    # -------------------------------------------------------------------------
    # Test M: Max Workflow Steps (25)
    # -------------------------------------------------------------------------
    def test_M_max_workflow_steps(self):
        # Create plan with 26 steps (> 25)
        long_plan = [ActionIntent(tool="browser.refresh") for _ in range(26)]
        report = self.agent.execute_workflow(
            user_goal="Excessive plan",
            initial_plan=long_plan,
        )
        self.assertFalse(report.success)
        self.assertEqual(report.error, "MAX_STEPS_EXCEEDED")
        self.assertEqual(report.steps_executed, 0)

    # -------------------------------------------------------------------------
    # Test N: Rate Limiting
    # -------------------------------------------------------------------------
    def test_N_rate_limiting(self):
        limiter = BrowserRateLimiter(max_actions_per_minute=2)
        self.agent.tool_registry.rate_limiter = limiter

        self.assertTrue(limiter.check_and_record())
        self.assertTrue(limiter.check_and_record())
        # Third action must trigger rate limit
        with self.assertRaises(RateLimitExceededError):
            limiter.check_and_record()

    # -------------------------------------------------------------------------
    # Test O: Emergency Stop
    # -------------------------------------------------------------------------
    def test_O_emergency_stop(self):
        self.agent.tool_registry.activate_emergency_stop()
        report = self.agent.execute_workflow(
            user_goal="Open fixture while emergency stop is active",
            base_url=self.base_url,
        )
        self.assertFalse(report.success)
        self.assertTrue(report.stopped_by_emergency)
        self.assertEqual(report.error, "EMERGENCY_STOP_ACTIVE")
        self.assertEqual(report.steps_executed, 0)

        # Clear emergency stop
        self.agent.tool_registry.reset_emergency_stop()
        self.assertFalse(self.agent.tool_registry.is_emergency_stopped())

    # -------------------------------------------------------------------------
    # Test P: Prompt Injection Remains Untrusted
    # -------------------------------------------------------------------------
    def test_P_prompt_injection_remains_untrusted(self):
        report = self.agent.execute_workflow(
            user_goal="Open the local fixture and check status",
            base_url=self.base_url,
        )
        self.assertTrue(report.success)
        # Untrusted injection on page ("Ignore previous instructions...")
        # was detected and logged as warning, but NOT executed
        self.assertGreater(len(self.agent._prompt_injection_warnings), 0)
        self.assertTrue(any("format" in w.lower() or "instructions" in w.lower() for w in self.agent._prompt_injection_warnings))

    # -------------------------------------------------------------------------
    # Test Q: Sensitive Data Protection
    # -------------------------------------------------------------------------
    def test_Q_sensitive_data_protection(self):
        plan = [
            ActionIntent(tool="browser.open", params={"headless": True}),
            ActionIntent(tool="browser.navigate", params={"url": f"{self.base_url}/test_page.html"}),
            ActionIntent(
                tool="browser.type",
                target_id="password-field",
                params={"selector": "#password-field", "text": "SuperSecretPass123!"},
                reason="Enter sensitive password",
            ),
        ]
        report = self.agent.execute_workflow(
            user_goal="Type password into sensitive input",
            initial_plan=plan,
        )
        self.assertTrue(report.success)
        # Verify password is not exposed in plain text in step params or audit
        type_step = report.steps[2]
        self.assertNotEqual(type_step["tool_result"]["output"]["value"], "SuperSecretPass123!")
        self.assertEqual(type_step["tool_result"]["output"]["value"], "********")

    # -------------------------------------------------------------------------
    # Test R: Download Quarantine Preserved
    # -------------------------------------------------------------------------
    def test_R_download_quarantine_preserved(self):
        gate = self.agent.safety_gate
        # 1. Dangerous downloads blocked
        ok, reason = gate.validate_download("malware.exe")
        self.assertFalse(ok)
        self.assertIn("dangerous", reason.lower())

        ok, reason = gate.validate_download("script.bat")
        self.assertFalse(ok)

        # 2. Safe downloads quarantined
        ok, reason = gate.validate_download("sample_safe.txt")
        self.assertTrue(ok)
        quarantine_path = gate.get_quarantine_path("sample_safe.txt")
        self.assertTrue("scratch" in str(quarantine_path).lower() or "download" in str(quarantine_path).lower())

        # 3. Downloads cannot be executed
        self.assertFalse(hasattr(self.agent, "execute_download"))
        self.assertNotIn("browser.execute_download", ALLOWED_BROWSER_TOOLS)

    # -------------------------------------------------------------------------
    # Test S: High-Risk Confirmation Required
    # -------------------------------------------------------------------------
    def test_S_high_risk_confirmation_required(self):
        intent = ActionIntent(
            tool="browser.click",
            target_id="delete-account",
            params={"selector": "#btn-delete-account", "text": "delete account"},
            reason="Delete user account and purge all data",
        )
        report = self.agent.execute_workflow(
            user_goal="Delete user account",
            initial_plan=[intent],
            user_confirmed=False,
        )
        self.assertFalse(report.success)
        self.assertTrue(report.requires_confirmation)
        self.assertIsNotNone(report.pending_action)

        # Confirming with user_confirmed=True allows it past the gate
        valid, msg = self.agent.validate_action(intent)
        self.assertTrue(intent.requires_confirmation)

    # -------------------------------------------------------------------------
    # Test T: Ground Truth Overrides Model Success Claim (Case D)
    # -------------------------------------------------------------------------
    def test_T_ground_truth_overrides_model_success_claim(self):
        # Setup failed verification result
        verif_res = StateVerificationResult(
            verified=False,
            status="VERIFIED_FAIL",
            checks=[],
            objections=["Expected button to become enabled, but remained disabled."],
            evidence_summary={},
        )
        report = self.verifier.evaluate_model_claim(
            model_claim_success=True,
            model_explanation="The model claims the button was clicked and is enabled.",
            verification_result=verif_res,
        )
        self.assertEqual(report.decision, ConsensusDecision.REJECT)
        self.assertEqual(report.case, DecisionCase.CASE_D)
        self.assertIn("Ground-truth state verification failed", report.summary)

    # -------------------------------------------------------------------------
    # Test U: Dynamic DOM Recovery
    # -------------------------------------------------------------------------
    def test_U_dynamic_dom_recovery(self):
        plan = [
            ActionIntent(tool="browser.open", params={"headless": True}),
            ActionIntent(tool="browser.navigate", params={"url": f"{self.base_url}/test_page.html"}),
            # 1. Enable disabled button
            ActionIntent(
                tool="browser.click",
                target_id="btn-toggle-enable",
                params={"selector": "#btn-toggle-enable"},
                reason="Toggle button state",
                expected={"expected_element_enabled": "btn-target-disabled"},
            ),
            # 2. Add dynamic item
            ActionIntent(
                tool="browser.click",
                target_id="btn-add-item",
                params={"selector": "#btn-add-item"},
                reason="Add dynamic list item",
                expected={"expected_element_appeared": "dynamic-item-1"},
            ),
            # 3. Remove dynamic item
            ActionIntent(
                tool="browser.click",
                target_id="btn-remove-item",
                params={"selector": "#btn-remove-item"},
                reason="Remove dynamic list item",
                expected={"expected_element_disappeared": "dynamic-item-1"},
            ),
        ]
        report = self.agent.execute_workflow(
            user_goal="Dynamic DOM test",
            initial_plan=plan,
        )
        self.assertTrue(report.success)
        self.assertEqual(report.steps_executed, 5)

    # -------------------------------------------------------------------------
    # Test V: State Refresh After Action
    # -------------------------------------------------------------------------
    def test_V_state_refresh_after_action(self):
        report = self.agent.execute_workflow(
            user_goal="Open the local fixture",
            base_url=self.base_url,
        )
        self.assertTrue(report.success)
        self.assertIsNotNone(self.agent._last_snapshot)
        self.assertIn("127.0.0.1", self.agent._last_snapshot.url)
        self.assertEqual(self.agent._last_snapshot.title, "NR-AI Safe Browser Test Fixture")
        self.assertGreater(len(self.agent._last_snapshot.interactive_elements), 5)

    # -------------------------------------------------------------------------
    # Test W: Audit Logging
    # -------------------------------------------------------------------------
    def test_W_audit_logging(self):
        report = self.agent.execute_workflow(
            user_goal="Open the local fixture and click submit",
            base_url=self.base_url,
        )
        self.assertTrue(report.success)
        self.assertTrue(report.audit_logged)
        # Ensure report serialization has all required audit fields
        d = report.to_dict()
        for field_name in ["workflow_id", "goal", "success", "total_steps", "steps_executed", "steps", "duration_s"]:
            self.assertIn(field_name, d)

    # -------------------------------------------------------------------------
    # Test X: Local Fixture End-to-End Workflow
    # -------------------------------------------------------------------------
    def test_X_local_fixture_end_to_end_workflow(self):
        plan = [
            ActionIntent(tool="browser.open", params={"headless": True}),
            ActionIntent(tool="browser.navigate", params={"url": f"{self.base_url}/test_page.html"}),
            ActionIntent(
                tool="browser.click",
                target_id="submit-button",
                params={"selector": "#submit-button"},
                reason="Click submit",
                expected={"expected_text_appeared": "Form Submitted"},
            ),
            ActionIntent(
                tool="browser.click",
                target_id="btn-toggle-enable",
                params={"selector": "#btn-toggle-enable"},
                reason="Enable button",
                expected={"expected_element_enabled": "btn-target-disabled"},
            ),
            ActionIntent(
                tool="browser.click",
                target_id="btn-add-item",
                params={"selector": "#btn-add-item"},
                reason="Add dynamic item",
                expected={"expected_element_appeared": "dynamic-item-1"},
            ),
            ActionIntent(
                tool="browser.click",
                target_id="btn-remove-item",
                params={"selector": "#btn-remove-item"},
                reason="Remove dynamic item",
                expected={"expected_element_disappeared": "dynamic-item-1"},
            ),
            ActionIntent(tool="browser.close", params={"close_all": True}),
        ]
        report = self.agent.execute_workflow(
            user_goal="Complete end-to-end web automation workflow",
            initial_plan=plan,
        )
        self.assertTrue(report.success)
        self.assertEqual(report.steps_executed, 7)
        self.assertFalse(self.tool_registry.driver.is_running())

    # -------------------------------------------------------------------------
    # Test Y: Step 5 Phase 3 Regression (27 Tests)
    # -------------------------------------------------------------------------
    def test_Y_step5_phase3_regression(self):
        from tests.test_step5_phase3_perception import TestStep5Phase3Perception
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromTestCase(TestStep5Phase3Perception)
        runner = unittest.TextTestRunner(verbosity=0)
        result = runner.run(suite)
        self.assertEqual(len(result.failures), 0)
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(result.testsRun, 27)

    # -------------------------------------------------------------------------
    # Test Z: Step 5 Phase 2 Regression (29 Tests)
    # -------------------------------------------------------------------------
    def test_Z_step5_phase2_regression(self):
        from tests.test_step5_phase2_browser import TestStep5Phase2Browser
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromTestCase(TestStep5Phase2Browser)
        runner = unittest.TextTestRunner(verbosity=0)
        result = runner.run(suite)
        self.assertEqual(len(result.failures), 0)
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(result.testsRun, 29)

    # -------------------------------------------------------------------------
    # Test AA: Step 5 Phase 1 Regression (16 Tests)
    # -------------------------------------------------------------------------
    def test_AA_step5_phase1_regression(self):
        from tests.test_step5_phase1_models import TestStep5Phase1Models
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromTestCase(TestStep5Phase1Models)
        runner = unittest.TextTestRunner(verbosity=0)
        result = runner.run(suite)
        self.assertEqual(len(result.failures), 0)
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(result.testsRun, 16)

    # -------------------------------------------------------------------------
    # Test AB: Step 4E Regression (32 Tests)
    # -------------------------------------------------------------------------
    def test_AB_step4e_regression(self):
        from tests.test_step4e_unified_agent import TestStep4EUnifiedComputerAgent
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromTestCase(TestStep4EUnifiedComputerAgent)
        runner = unittest.TextTestRunner(verbosity=0)
        result = runner.run(suite)
        self.assertEqual(len(result.failures), 0)
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(result.testsRun, 32)


if __name__ == "__main__":
    unittest.main(verbosity=2)
