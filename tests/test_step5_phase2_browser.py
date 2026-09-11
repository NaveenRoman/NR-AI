"""
Step 5 Phase 2 Test Suite — Browser Driver + Deterministic Browser Safety Foundation.

Covers tests A through AC:
A. BrowserDriver imports
B. Playwright availability
C. Chromium launch
D. Context creation
E. Page creation
F. Local navigation
G. Tab listing
H. Tab switching
I. Back navigation
J. Forward navigation
K. Refresh
L. Screenshot
M. Safe URL validation
N. javascript: blocked
O. data: blocked
P. file: blocked
Q. external HTTP blocked for Phase 2 local mode
R. SSRF/private destination rejection
S. Download quarantine
T. dangerous download rejection
U. sensitive-field redaction
V. webpage prompt-injection text treated as untrusted data
W. stale-target rejection
X. rate limiting
Y. emergency stop
Z. audit logging
AA. browser close/cleanup
AB. Step 5 Phase 1 regression
AC. Step 4E regression
"""

import http.server
import os
from pathlib import Path
import socketserver
import sys
import threading
import time
import unittest

from app.agent.browser_driver import (
    BrowserDriver,
    BrowserTabInfo,
    DownloadRecord,
)
from app.agent.browser_safety import (
    BrowserRateLimiter,
    BrowserSafetyError,
    BrowserSafetyGate,
    BrowserWorkflowBounds,
    DangerousDownloadError,
    EmergencyStopActiveError,
    RateLimitExceededError,
    StaleTargetError,
    UntrustedWebData,
)
from app.agent.browser_tools import (
    BrowserToolRegistry,
    BrowserToolResult,
    RiskLevel,
)
from app.memory.audit_logger import AuditLogger

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class QuietHTTPHandler(http.server.SimpleHTTPRequestHandler):
    """Suppresses console logging during test execution."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FIXTURES_DIR.resolve()), **kwargs)

    def log_message(self, format, *args):
        pass


class TestStep5Phase2Browser(unittest.TestCase):
    """Step 5 Phase 2 Browser Driver & Safety Foundation Acceptance Suite."""

    server = None
    server_thread = None
    server_port = None
    base_url = None

    @classmethod
    def setUpClass(cls):
        # Start a local HTTP server serving fixtures on loopback
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
        self.safety_gate = BrowserSafetyGate(allow_external_http=False)
        self.driver = BrowserDriver(headless=True, safety_gate=self.safety_gate)
        self.registry = BrowserToolRegistry(driver=self.driver, safety_gate=self.safety_gate)

    def tearDown(self):
        if self.driver.is_running():
            self.driver.close()

    # -------------------------------------------------------------------------
    # Test A: BrowserDriver imports
    # -------------------------------------------------------------------------
    def test_A_browser_driver_imports(self):
        self.assertTrue(callable(BrowserDriver))
        self.assertTrue(callable(BrowserTabInfo))
        self.assertTrue(callable(DownloadRecord))

    # -------------------------------------------------------------------------
    # Test B: Playwright availability
    # -------------------------------------------------------------------------
    def test_B_playwright_availability(self):
        import importlib.metadata
        import playwright.sync_api
        version = importlib.metadata.version("playwright")
        self.assertTrue(bool(version))
        self.assertTrue(hasattr(playwright.sync_api, "sync_playwright"))

    # -------------------------------------------------------------------------
    # Test C: Chromium launch
    # -------------------------------------------------------------------------
    def test_C_chromium_launch(self):
        self.driver.launch()
        self.assertTrue(self.driver.is_running())
        self.assertIsNotNone(self.driver.session_id)

    # -------------------------------------------------------------------------
    # Test D: Context creation
    # -------------------------------------------------------------------------
    def test_D_context_creation(self):
        self.driver.launch()
        ctx = self.driver._context
        self.assertIsNotNone(ctx)

    # -------------------------------------------------------------------------
    # Test E: Page creation
    # -------------------------------------------------------------------------
    def test_E_page_creation(self):
        self.driver.launch()
        page = self.driver.new_page()
        self.assertIsNotNone(page)
        self.assertFalse(page.is_closed())

    # -------------------------------------------------------------------------
    # Test F: Local navigation
    # -------------------------------------------------------------------------
    def test_F_local_navigation(self):
        url = f"{self.base_url}/test_page.html"
        res = self.driver.navigate(url)
        self.assertEqual(res["status"], "NAVIGATED")
        self.assertIn("test_page.html", res["url"])
        self.assertEqual(res["title"], "NR-AI Safe Browser Test Fixture")

    # -------------------------------------------------------------------------
    # Test G: Tab listing
    # -------------------------------------------------------------------------
    def test_G_tab_listing(self):
        url = f"{self.base_url}/test_page.html"
        self.driver.navigate(url)
        tabs = self.driver.list_tabs()
        self.assertGreaterEqual(len(tabs), 1)
        active_tabs = [t for t in tabs if t.active]
        self.assertEqual(len(active_tabs), 1)
        self.assertEqual(active_tabs[0].title, "NR-AI Safe Browser Test Fixture")

    # -------------------------------------------------------------------------
    # Test H: Tab switching
    # -------------------------------------------------------------------------
    def test_H_tab_switching(self):
        self.driver.navigate(f"{self.base_url}/test_page.html")
        tab1_id = self.driver._active_tab_id

        # Open second tab
        page2 = self.driver.new_page()
        page2.goto(f"{self.base_url}/sample_safe.txt")
        tab2_id = self.driver._active_tab_id
        self.assertNotEqual(tab1_id, tab2_id)

        # Switch back to tab 1
        switch_res = self.driver.switch_tab(tab1_id)
        self.assertEqual(switch_res["active_tab_id"], tab1_id)

    # -------------------------------------------------------------------------
    # Test I: Back navigation
    # -------------------------------------------------------------------------
    def test_I_back_navigation(self):
        self.driver.navigate(f"{self.base_url}/test_page.html")
        page = self.driver.get_active_page()
        page.locator("#test-link").click()
        self.assertIn("#section2", page.url)

        res = self.driver.back()
        self.assertEqual(res["status"], "NAVIGATED_BACK")

    # -------------------------------------------------------------------------
    # Test J: Forward navigation
    # -------------------------------------------------------------------------
    def test_J_forward_navigation(self):
        self.driver.navigate(f"{self.base_url}/test_page.html")
        page = self.driver.get_active_page()
        page.locator("#test-link").click()
        self.driver.back()

        res = self.driver.forward()
        self.assertEqual(res["status"], "NAVIGATED_FORWARD")

    # -------------------------------------------------------------------------
    # Test K: Refresh
    # -------------------------------------------------------------------------
    def test_K_refresh(self):
        self.driver.navigate(f"{self.base_url}/test_page.html")
        res = self.driver.refresh()
        self.assertEqual(res["status"], "REFRESHED")
        self.assertEqual(res["title"], "NR-AI Safe Browser Test Fixture")

    # -------------------------------------------------------------------------
    # Test L: Screenshot
    # -------------------------------------------------------------------------
    def test_L_screenshot(self):
        self.driver.navigate(f"{self.base_url}/test_page.html")
        shot_path = self.driver.screenshot()
        self.assertTrue(os.path.exists(shot_path))
        self.assertGreater(os.path.getsize(shot_path), 0)

    # -------------------------------------------------------------------------
    # Test M: Safe URL validation
    # -------------------------------------------------------------------------
    def test_M_safe_url_validation(self):
        gate = BrowserSafetyGate(allow_external_http=False)
        ok, _ = gate.validate_url("https://www.google.com")
        self.assertTrue(ok)
        ok, _ = gate.validate_url("http://127.0.0.1:8080/test")
        self.assertTrue(ok)
        ok, _ = gate.validate_url("http://localhost:3000/app")
        self.assertTrue(ok)

    # -------------------------------------------------------------------------
    # Test N: javascript: blocked
    # -------------------------------------------------------------------------
    def test_N_javascript_blocked(self):
        gate = BrowserSafetyGate()
        ok, reason = gate.validate_url("javascript:alert(document.cookie)")
        self.assertFalse(ok)
        self.assertIn("javascript", reason.lower())

        with self.assertRaises(BrowserSafetyError):
            self.driver.navigate("javascript:alert(1)")

    # -------------------------------------------------------------------------
    # Test O: data: blocked
    # -------------------------------------------------------------------------
    def test_O_data_blocked(self):
        gate = BrowserSafetyGate()
        ok, reason = gate.validate_url("data:text/html,<h1>Exploit</h1>")
        self.assertFalse(ok)
        self.assertIn("data", reason.lower())

        with self.assertRaises(BrowserSafetyError):
            self.driver.navigate("data:text/html,<h1>Exploit</h1>")

    # -------------------------------------------------------------------------
    # Test P: file: blocked
    # -------------------------------------------------------------------------
    def test_P_file_blocked(self):
        gate = BrowserSafetyGate()
        ok, reason = gate.validate_url("file:///C:/Windows/System32/cmd.exe")
        self.assertFalse(ok)
        self.assertIn("file", reason.lower())

        with self.assertRaises(BrowserSafetyError):
            self.driver.navigate("file:///C:/Windows/System32/cmd.exe")

    # -------------------------------------------------------------------------
    # Test Q: external HTTP blocked for Phase 2 local mode
    # -------------------------------------------------------------------------
    def test_Q_external_http_blocked(self):
        gate = BrowserSafetyGate(allow_external_http=False)
        ok, reason = gate.validate_url("http://example.com")
        self.assertFalse(ok)
        self.assertIn("insecure http", reason.lower())

        with self.assertRaises(BrowserSafetyError):
            self.driver.navigate("http://example.com")

    # -------------------------------------------------------------------------
    # Test R: SSRF/private destination rejection
    # -------------------------------------------------------------------------
    def test_R_ssrf_rejection(self):
        gate = BrowserSafetyGate(allow_external_http=True)
        # Cloud metadata IP
        ok, reason = gate.validate_url("http://169.254.169.254/latest/meta-data/")
        self.assertFalse(ok)
        self.assertIn("metadata", reason.lower())

        # Cloud metadata domain
        ok, reason = gate.validate_url("http://metadata.google.internal/computeMetadata/v1/")
        self.assertFalse(ok)

        # Private subnets
        ok, reason = gate.validate_url("http://10.0.0.1/admin")
        self.assertFalse(ok)

        ok, reason = gate.validate_url("http://192.168.1.1/router")
        self.assertFalse(ok)

        # 0.0.0.0 bypass
        ok, reason = gate.validate_url("http://0.0.0.0:8000/")
        self.assertFalse(ok)

    # -------------------------------------------------------------------------
    # Test S: Download quarantine
    # -------------------------------------------------------------------------
    def test_S_download_quarantine(self):
        self.driver.navigate(f"{self.base_url}/test_page.html")
        page = self.driver.get_active_page()

        # Click safe download link
        with page.expect_download() as download_info:
            page.locator("#download-safe-link").click()
        download = download_info.value

        # Handled by driver listener
        self.assertGreaterEqual(len(self.driver.download_records), 1)
        record = self.driver.download_records[-1]
        self.assertTrue(record.safe)
        self.assertIn("scratch", record.quarantine_path.lower())
        self.assertTrue(os.path.exists(record.quarantine_path))

    # -------------------------------------------------------------------------
    # Test T: dangerous download rejection
    # -------------------------------------------------------------------------
    def test_T_dangerous_download_rejection(self):
        gate = BrowserSafetyGate()
        ok, reason = gate.validate_download("payload.exe")
        self.assertFalse(ok)
        self.assertIn("dangerous", reason.lower())

        ok, reason = gate.validate_download("script.bat")
        self.assertFalse(ok)

        ok, reason = gate.validate_download("powershell.ps1")
        self.assertFalse(ok)

        ok, reason = gate.validate_download("safe_document.pdf")
        self.assertTrue(ok)

    # -------------------------------------------------------------------------
    # Test U: sensitive-field redaction
    # -------------------------------------------------------------------------
    def test_U_sensitive_field_redaction(self):
        self.registry.execute_tool("browser.open")
        self.registry.execute_tool("browser.navigate", {"url": f"{self.base_url}/test_page.html"})

        # Type password into password field
        res = self.registry.execute_tool(
            "browser.type",
            {"selector": "#password-field", "text": "SuperSecretP@ss123"},
        )
        self.assertTrue(res.success)
        # Verify returned value in payload is redacted
        self.assertEqual(res.output["value"], "********")

    # -------------------------------------------------------------------------
    # Test V: webpage prompt-injection text treated as untrusted data
    # -------------------------------------------------------------------------
    def test_V_untrusted_webpage_data(self):
        self.registry.execute_tool("browser.open")
        self.registry.execute_tool("browser.navigate", {"url": f"{self.base_url}/test_page.html"})

        inspect_res = self.registry.execute_tool("browser.inspect_page")
        self.assertTrue(inspect_res.success)
        output = inspect_res.output
        self.assertTrue(output.get("injection_detected"))
        untrusted_ctx = output.get("untrusted_context", "")
        self.assertIn("[BEGIN UNTRUSTED WEBPAGE DATA", untrusted_ctx)
        self.assertIn("[END UNTRUSTED WEBPAGE DATA]", untrusted_ctx)

    # -------------------------------------------------------------------------
    # Test W: stale-target rejection
    # -------------------------------------------------------------------------
    def test_W_stale_target_rejection(self):
        self.registry.execute_tool("browser.open")
        self.registry.execute_tool("browser.navigate", {"url": f"{self.base_url}/test_page.html"})

        # Manually cache an element with an expired timestamp (20s ago)
        self.registry._target_cache["999"] = {
            "ref": 999,
            "tag": "button",
            "text": "Old",
            "timestamp": time.time() - 25.0,  # Expired (> 15s)
        }

        res = self.registry.execute_tool("browser.click", {"selector": "#test-button", "target_ref": "999"})
        self.assertFalse(res.success)
        self.assertIn("stale", res.error.lower())

    # -------------------------------------------------------------------------
    # Test X: rate limiting
    # -------------------------------------------------------------------------
    def test_X_rate_limiting(self):
        limiter = BrowserRateLimiter(max_actions_per_minute=5)
        for _ in range(5):
            self.assertTrue(limiter.check_and_record())

        with self.assertRaises(RateLimitExceededError):
            limiter.check_and_record()

    # -------------------------------------------------------------------------
    # Test Y: emergency stop
    # -------------------------------------------------------------------------
    def test_Y_emergency_stop(self):
        self.registry.activate_emergency_stop()
        self.assertTrue(self.registry.is_emergency_stopped())

        res = self.registry.execute_tool("browser.open")
        self.assertFalse(res.success)
        self.assertIn("Emergency Stop is currently ACTIVE", res.error)

        self.registry.reset_emergency_stop()
        self.assertFalse(self.registry.is_emergency_stopped())

    # -------------------------------------------------------------------------
    # Test Z: audit logging
    # -------------------------------------------------------------------------
    def test_Z_audit_logging(self):
        self.registry.execute_tool("browser.open")
        res = self.registry.execute_tool("browser.navigate", {"url": f"{self.base_url}/test_page.html"})
        self.assertTrue(res.audit_logged)

    # -------------------------------------------------------------------------
    # Test AA: browser close/cleanup
    # -------------------------------------------------------------------------
    def test_AA_browser_close_cleanup(self):
        self.driver.launch()
        self.assertTrue(self.driver.is_running())
        self.driver.close()
        self.assertFalse(self.driver.is_running())
        self.assertIsNone(self.driver._browser)

    # -------------------------------------------------------------------------
    # Test AB: Step 5 Phase 1 regression
    # -------------------------------------------------------------------------
    def test_AB_step5_phase1_regression(self):
        from app.agent.model_router import ModelRouter
        from app.config.model_config import ModelCapability
        router = ModelRouter()
        coding_model = router.route(required_capabilities=[ModelCapability.CODING])
        self.assertTrue(coding_model)

    # -------------------------------------------------------------------------
    # Test AC: Step 4E regression
    # -------------------------------------------------------------------------
    def test_AC_step4e_regression(self):
        from app.agent.computer_tools import ComputerToolRegistry
        reg = ComputerToolRegistry()
        tools = reg.list_tools()
        self.assertEqual(len(tools), 12)


if __name__ == "__main__":
    unittest.main()
