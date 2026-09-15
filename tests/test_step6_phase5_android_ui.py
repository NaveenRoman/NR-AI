"""
NR-AI Step 6 Phase 5: Android UI / Device Intelligence Foundation Test Suite.

Tests A through Z verifying:
- Test A: Authorized emulator accepted (emulator-5554)
- Test B: Authorized physical device accepted (15930545720012G)
- Test C: Unknown device rejected (DEVICE_NOT_AUTHORIZED)
- Test D: Device state inspection
- Test E: Foreground app detection
- Test F: Screen capture
- Test G: UI extraction (uiautomator hierarchy distillation)
- Test H: Deterministic target IDs (android.target.001)
- Test I: Target TTL enforcement
- Test J: Stale target rejection (STALE_TARGET)
- Test K: Coordinate bounds validation (COORDINATES_OUT_OF_BOUNDS)
- Test L: Screen dimension mismatch rejection (SCREEN_DIMENSION_MISMATCH)
- Test M: Malformed action schema rejection (MALFORMED_ACTION_SCHEMA)
- Test N: Unauthorized action rejection (ACTION_NOT_ALLOWED)
- Test O: Emergency stop immediately halts UI operations (EMERGENCY_STOPPED)
- Test P: Safe tap execution with coordinate resolution
- Test Q: Back action
- Test R: Scroll action
- Test S: UI state verification
- Test T: Model receives bounded UI data only
- Test U: Model cannot access ADB directly
- Test V: High-risk action rejection (HIGH_RISK_ACTION_BLOCKED)
- Test W: Audit log redaction for sensitive UI/tokens
- Test X: Companion routing to ANDROID_STUDIO
- Test Y: Authorized project and device boundaries
- Test Z: Clean workspace and registry cleanup
"""

import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from app.agent.android_safety import (
    ALLOWED_ANDROID_UI_OPERATIONS,
    ALLOWED_KEYCODES,
    AUTHORIZED_DEVICE_SERIALS,
    AUTHORIZED_PACKAGE_NAME,
    AUTHORIZED_PROJECT_PATH,
    DEFAULT_TARGET_TTL_SECONDS,
    AndroidErrorCode,
    AndroidSafetyError,
    AndroidSafetyGate,
    EmergencyStopActiveError,
)
from app.agent.android_studio_agent import (
    AndroidStudioAgent,
    AndroidWorkflowReport,
)
from app.agent.android_tools import SafeAdbClient
from app.agent.android_ui import (
    AndroidTarget,
    AndroidTargetRegistry,
    AndroidUIController,
    AndroidUIDistiller,
    AndroidUISnapshot,
    build_model_ui_prompt,
    parse_model_ui_action,
)
from app.brain.companion import CommandCategory, NRCompanion
from app.memory.audit_logger import AuditLogger


SAMPLE_UI_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.nrai.test" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,0][1080,2400]">
    <node index="0" text="NR-AI Test App" resource-id="com.nrai.test:id/title" class="android.widget.TextView" package="com.nrai.test" content-desc="App Title Header" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[100,150][980,250]" />
    <node index="1" text="Login" resource-id="com.nrai.test:id/login_btn" class="android.widget.Button" package="com.nrai.test" content-desc="Submit Credentials" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[200,600][880,750]" />
    <node index="2" text="Cancel" resource-id="com.nrai.test:id/cancel_btn" class="android.widget.Button" package="com.nrai.test" content-desc="Cancel Login" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[200,800][880,950]" />
    <node index="3" text="" resource-id="com.nrai.test:id/input_user" class="android.widget.EditText" package="com.nrai.test" content-desc="Username Field" checkable="false" checked="false" clickable="true" enabled="true" focusable="true" focused="true" scrollable="false" long-clickable="true" password="false" selected="false" bounds="[200,400][880,520]" />
  </node>
</hierarchy>
"""


class TestStep6Phase5AndroidUI(unittest.TestCase):
    """Acceptance test suite for Android UI & Device Intelligence Foundation."""

    def setUp(self):
        AndroidSafetyGate.deactivate_emergency_stop()
        self.safety = AndroidSafetyGate()
        self.audit = AuditLogger()
        self.mock_adb = MagicMock(spec=SafeAdbClient)
        self.mock_adb.get_device_state.return_value = "device"
        self.mock_adb.get_screen_size.return_value = (1080, 2400)
        self.mock_adb.get_foreground_app.return_value = {"package": "com.nrai.test", "activity": ".MainActivity"}
        self.mock_adb.dump_ui_hierarchy.return_value = SAMPLE_UI_XML
        self.mock_adb.capture_screen.return_value = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x048"
        self.mock_adb.tap.return_value = True
        self.mock_adb.back.return_value = True
        self.mock_adb.home.return_value = True
        self.mock_adb.press_key.return_value = True
        self.mock_adb.swipe.return_value = True
        self.mock_adb.scroll.return_value = True

        self.registry = AndroidTargetRegistry(safety_gate=self.safety)
        self.ui_ctrl = AndroidUIController(
            safety_gate=self.safety,
            adb_client=self.mock_adb,
            target_registry=self.registry,
            audit_logger=self.audit,
        )
        self.agent = AndroidStudioAgent(
            safety_gate=self.safety,
            audit_logger=self.audit,
        )
        self.agent.ui_controller = self.ui_ctrl

    def tearDown(self):
        AndroidSafetyGate.deactivate_emergency_stop()
        self.registry.clear()

    # -------------------------------------------------------------------------
    # Test A: Authorized emulator accepted
    # -------------------------------------------------------------------------
    def test_A_authorized_emulator_accepted(self):
        """Authorized emulator serial 'emulator-5554' is valid and accepted."""
        serial = self.safety.validate_device_serial("emulator-5554")
        self.assertEqual(serial, "emulator-5554")
        state = self.ui_ctrl.get_device_state("emulator-5554")
        self.assertEqual(state, "device")

    # -------------------------------------------------------------------------
    # Test B: Authorized physical device accepted
    # -------------------------------------------------------------------------
    def test_B_authorized_physical_device_accepted(self):
        """Authorized physical device serial '15930545720012G' is valid and accepted."""
        serial = self.safety.validate_device_serial("15930545720012G")
        self.assertEqual(serial, "15930545720012G")
        state = self.ui_ctrl.get_device_state("15930545720012G")
        self.assertEqual(state, "device")

    # -------------------------------------------------------------------------
    # Test C: Unknown device rejected
    # -------------------------------------------------------------------------
    def test_C_unknown_device_rejected(self):
        """Unknown or rogue device serials trigger DEVICE_NOT_AUTHORIZED."""
        for rogue in ("emulator-5556", "unauthorized_phone", "192.168.1.100:5555", "rogue_device"):
            with self.assertRaises(AndroidSafetyError) as ctx:
                self.safety.validate_device_serial(rogue)
            self.assertEqual(ctx.exception.code, AndroidErrorCode.DEVICE_NOT_AUTHORIZED)

            with self.assertRaises(AndroidSafetyError) as ctx2:
                self.ui_ctrl.get_device_state(rogue)
            self.assertEqual(ctx2.exception.code, AndroidErrorCode.DEVICE_NOT_AUTHORIZED)

    # -------------------------------------------------------------------------
    # Test D: Device state inspection
    # -------------------------------------------------------------------------
    def test_D_device_state_inspection(self):
        """Queries authorized device state via SafeAdbClient."""
        state = self.ui_ctrl.get_device_state("emulator-5554")
        self.assertEqual(state, "device")
        self.mock_adb.get_device_state.assert_called_with("emulator-5554")

    # -------------------------------------------------------------------------
    # Test E: Foreground app detection
    # -------------------------------------------------------------------------
    def test_E_foreground_app_detection(self):
        """Accurately identifies current focused package and activity."""
        fg = self.ui_ctrl.get_foreground_app("emulator-5554")
        self.assertEqual(fg.get("package"), "com.nrai.test")
        self.assertEqual(fg.get("activity"), ".MainActivity")

    # -------------------------------------------------------------------------
    # Test F: Screen capture
    # -------------------------------------------------------------------------
    def test_F_screen_capture(self):
        """Captures in-memory screenshot bytes and cleans up properly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "screen.png"
            data = self.ui_ctrl.capture_screen("emulator-5554", dest_path=dest)
            self.assertTrue(data.startswith(b"\x89PNG"))
            self.assertTrue(dest.exists())
            self.assertEqual(dest.read_bytes(), data)

    # -------------------------------------------------------------------------
    # Test G: UI extraction
    # -------------------------------------------------------------------------
    def test_G_ui_extraction(self):
        """Distills XML hierarchy into semantic AndroidTarget objects."""
        snap = AndroidUIDistiller.parse_hierarchy_xml(
            SAMPLE_UI_XML,
            device_serial="emulator-5554",
            screen_width=1080,
            screen_height=2400,
        )
        self.assertGreater(len(snap.targets), 0)
        self.assertIn("NR-AI Test App", snap.visible_text_items)
        self.assertIn("Login", snap.visible_text_items)
        self.assertIn("Cancel", snap.visible_text_items)

    # -------------------------------------------------------------------------
    # Test H: Deterministic target IDs
    # -------------------------------------------------------------------------
    def test_H_deterministic_target_ids(self):
        """Ensures target identifiers follow sequential format android.target.001, 002..."""
        snap = AndroidUIDistiller.parse_hierarchy_xml(
            SAMPLE_UI_XML,
            device_serial="emulator-5554",
            screen_width=1080,
            screen_height=2400,
        )
        for idx, target in enumerate(snap.targets, 1):
            expected_id = f"android.target.{idx:03d}"
            self.assertEqual(target.target_id, expected_id)

    # -------------------------------------------------------------------------
    # Test I: Target TTL enforcement
    # -------------------------------------------------------------------------
    def test_I_target_ttl_enforcement(self):
        """Verifies targets expire strictly after 15 seconds."""
        t_fresh = AndroidTarget(
            target_id="android.target.001",
            semantic_type="button",
            text="Login",
            content_desc="",
            resource_id="",
            class_name="android.widget.Button",
            package_name="com.nrai.test",
            bounds=(10, 10, 100, 100),
            center=(55, 55),
            clickable=True,
            enabled=True,
            selected=False,
            focused=False,
            device_serial="emulator-5554",
            screen_width=1080,
            screen_height=2400,
            timestamp=time.time(),
            ttl_seconds=15.0,
        )
        self.assertFalse(t_fresh.is_stale())

        t_stale = AndroidTarget(
            target_id="android.target.002",
            semantic_type="button",
            text="Old",
            content_desc="",
            resource_id="",
            class_name="android.widget.Button",
            package_name="com.nrai.test",
            bounds=(10, 10, 100, 100),
            center=(55, 55),
            clickable=True,
            enabled=True,
            selected=False,
            focused=False,
            device_serial="emulator-5554",
            screen_width=1080,
            screen_height=2400,
            timestamp=time.time() - 25.0,
            ttl_seconds=15.0,
        )
        self.assertTrue(t_stale.is_stale())

    # -------------------------------------------------------------------------
    # Test J: Stale target rejection
    # -------------------------------------------------------------------------
    def test_J_stale_target_rejection(self):
        """Attempting to resolve or tap an expired target raises STALE_TARGET."""
        snap = AndroidUIDistiller.parse_hierarchy_xml(
            SAMPLE_UI_XML,
            device_serial="emulator-5554",
            screen_width=1080,
            screen_height=2400,
            timestamp=time.time() - 30.0,
        )
        self.registry.store_snapshot(snap)

        with self.assertRaises(AndroidSafetyError) as ctx:
            self.registry.get_target("emulator-5554", "android.target.001")
        self.assertEqual(ctx.exception.code, AndroidErrorCode.STALE_TARGET)

        with self.assertRaises(AndroidSafetyError) as ctx2:
            self.ui_ctrl.tap_target("emulator-5554", "android.target.001")
        self.assertEqual(ctx2.exception.code, AndroidErrorCode.STALE_TARGET)

    # -------------------------------------------------------------------------
    # Test K: Coordinate bounds validation
    # -------------------------------------------------------------------------
    def test_K_coordinate_bounds_validation(self):
        """Coordinates outside screen bounds raise COORDINATES_OUT_OF_BOUNDS."""
        # Valid bounds
        x, y = self.safety.validate_target_coordinates(500, 1200, 1080, 2400)
        self.assertEqual((x, y), (500, 1200))

        # Negative X
        with self.assertRaises(AndroidSafetyError) as ctx:
            self.safety.validate_target_coordinates(-5, 100, 1080, 2400)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.COORDINATES_OUT_OF_BOUNDS)

        # Offscreen Y
        with self.assertRaises(AndroidSafetyError) as ctx:
            self.safety.validate_target_coordinates(500, 2500, 1080, 2400)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.COORDINATES_OUT_OF_BOUNDS)

    # -------------------------------------------------------------------------
    # Test L: Screen dimension mismatch rejection
    # -------------------------------------------------------------------------
    def test_L_screen_dimension_mismatch_rejection(self):
        """Rejects interaction if current device dimensions differ from target snapshot."""
        snap = AndroidUIDistiller.parse_hierarchy_xml(
            SAMPLE_UI_XML,
            device_serial="emulator-5554",
            screen_width=1080,
            screen_height=2400,
        )
        self.registry.store_snapshot(snap)

        # Change screen size report to simulated rotation or resolution change
        self.mock_adb.get_screen_size.return_value = (1440, 3120)

        with self.assertRaises(AndroidSafetyError) as ctx:
            self.ui_ctrl.tap_target("emulator-5554", "android.target.001")
        self.assertEqual(ctx.exception.code, AndroidErrorCode.SCREEN_DIMENSION_MISMATCH)

    # -------------------------------------------------------------------------
    # Test M: Malformed action schema rejection
    # -------------------------------------------------------------------------
    def test_M_malformed_action_schema_rejection(self):
        """Rejects non-JSON, missing action, or invalid target_id."""
        with self.assertRaises(AndroidSafetyError) as ctx:
            parse_model_ui_action("Not a valid json string")
        self.assertEqual(ctx.exception.code, AndroidErrorCode.MALFORMED_ACTION_SCHEMA)

        with self.assertRaises(AndroidSafetyError) as ctx:
            parse_model_ui_action(json.dumps({"reason": "missing action field"}))
        self.assertEqual(ctx.exception.code, AndroidErrorCode.MALFORMED_ACTION_SCHEMA)

        with self.assertRaises(AndroidSafetyError) as ctx:
            parse_model_ui_action(json.dumps({"action": "tap_target", "target_id": "invalid_id"}))
        self.assertEqual(ctx.exception.code, AndroidErrorCode.MALFORMED_ACTION_SCHEMA)

    # -------------------------------------------------------------------------
    # Test N: Unauthorized action rejection
    # -------------------------------------------------------------------------
    def test_N_unauthorized_action_rejection(self):
        """Rejects arbitrary or dangerous actions not in ALLOWED_ANDROID_UI_OPERATIONS."""
        with self.assertRaises(AndroidSafetyError) as ctx:
            self.safety.validate_ui_operation("format_device")
        self.assertEqual(ctx.exception.code, AndroidErrorCode.ACTION_NOT_ALLOWED)

        with self.assertRaises(AndroidSafetyError) as ctx:
            parse_model_ui_action(json.dumps({"action": "exec_shell_command", "target_id": "android.target.001"}))
        self.assertEqual(ctx.exception.code, AndroidErrorCode.ACTION_NOT_ALLOWED)

    # -------------------------------------------------------------------------
    # Test O: Emergency stop
    # -------------------------------------------------------------------------
    def test_O_emergency_stop(self):
        """Active emergency stop halts all Android UI operations immediately."""
        self.safety.trigger_emergency_stop("Test emergency stop")
        self.assertTrue(self.safety.is_emergency_stop_active())

        with self.assertRaises(EmergencyStopActiveError):
            self.ui_ctrl.inspect_ui("emulator-5554")

        with self.assertRaises(EmergencyStopActiveError):
            self.ui_ctrl.tap_target("emulator-5554", "android.target.001")

        with self.assertRaises(EmergencyStopActiveError):
            self.ui_ctrl.back("emulator-5554")

        with self.assertRaises(EmergencyStopActiveError):
            self.ui_ctrl.capture_screen("emulator-5554")

    # -------------------------------------------------------------------------
    # Test P: Safe tap execution
    # -------------------------------------------------------------------------
    def test_P_safe_tap_execution(self):
        """Resolves target center and injects safe tap."""
        snap = self.ui_ctrl.inspect_ui("emulator-5554")
        # Target 2 is Login button (bounds [200,600][880,750] -> center (540, 675))
        login_target = snap.find_targets_by_text("Login")[0]
        res = self.ui_ctrl.tap_target("emulator-5554", login_target.target_id)
        self.assertTrue(res["success"])
        self.assertEqual(res["coordinates"], [540, 675])
        self.mock_adb.tap.assert_called_with("emulator-5554", 540, 675)

    # -------------------------------------------------------------------------
    # Test Q: Back action
    # -------------------------------------------------------------------------
    def test_Q_back_action(self):
        """Injects safe keyevent for Back key (4)."""
        res = self.ui_ctrl.back("emulator-5554")
        self.assertTrue(res["success"])
        self.mock_adb.back.assert_called_with("emulator-5554")

    # -------------------------------------------------------------------------
    # Test R: Scroll action
    # -------------------------------------------------------------------------
    def test_R_scroll_action(self):
        """Executes safe directional scroll using screen proportions."""
        res = self.ui_ctrl.scroll("emulator-5554", direction="down")
        self.assertTrue(res["success"])
        self.mock_adb.scroll.assert_called_with("emulator-5554", direction="down")

    # -------------------------------------------------------------------------
    # Test S: UI state verification
    # -------------------------------------------------------------------------
    def test_S_ui_state_verification(self):
        """Verifies ground-truth device state and foreground app against expectation."""
        res = self.ui_ctrl.verify_ui_state(
            "emulator-5554",
            expected_package="com.nrai.test",
            expected_text="Login",
        )
        self.assertTrue(res["success"])
        self.assertTrue(res["package_verified"])
        self.assertTrue(res["text_verified"])

    # -------------------------------------------------------------------------
    # Test T: Model receives bounded UI data only
    # -------------------------------------------------------------------------
    def test_T_model_receives_bounded_ui_data_only(self):
        """Prompt generator supplies sanitized summary without raw XML or secret fields."""
        snap = self.ui_ctrl.inspect_ui("emulator-5554")
        sys_prompt, user_prompt = build_model_ui_prompt(snap, "Tap the Login button")
        self.assertIn("STRICTLY ADVISORY", sys_prompt.upper())
        self.assertIn("Login", user_prompt)
        self.assertNotIn("<?xml", user_prompt)
        self.assertNotIn("password=", user_prompt)

    # -------------------------------------------------------------------------
    # Test U: Model cannot access ADB directly
    # -------------------------------------------------------------------------
    def test_U_model_cannot_access_adb(self):
        """Advisory models produce only JSON action proposals; zero direct ADB authority."""
        valid_json = json.dumps({
            "action": "tap_target",
            "target_id": "android.target.002",
            "reason": "Tap login button to submit",
        })
        intent = parse_model_ui_action(valid_json)
        self.assertEqual(intent["action"], "tap_target")
        self.assertEqual(intent["target_id"], "android.target.002")

        # The intent contains no executable code or shell wrappers
        self.assertNotIn("adb", intent)
        self.assertNotIn("shell", intent)

    # -------------------------------------------------------------------------
    # Test V: High-risk action rejection
    # -------------------------------------------------------------------------
    def test_V_high_risk_action_rejection(self):
        """Destructive requests (factory reset, wipe data, uninstall) are blocked."""
        for dangerous in ("factory reset", "erase all data", "wipe data", "delete account"):
            with self.assertRaises(AndroidSafetyError) as ctx:
                self.safety.validate_safe_ui_action("tap_target", {"text": dangerous})
            self.assertEqual(ctx.exception.code, AndroidErrorCode.HIGH_RISK_ACTION_BLOCKED)

    # -------------------------------------------------------------------------
    # Test W: Audit log redaction
    # -------------------------------------------------------------------------
    def test_W_audit_redaction(self):
        """Audit logger redacts sensitive credentials and keys from recorded details."""
        self.audit.log_event(
            event_type="android.ui_test",
            status="SUCCESS",
            details={
                "password": "SuperSecretPassword123!",
                "api_key": "AIzaSy123456789012345678901234567890123",
                "normal_field": "visible_text",
            },
        )
        last_entry = self.audit.entries[-1]
        self.assertEqual(last_entry["details"]["password"], "[REDACTED]")
        self.assertNotIn("SuperSecretPassword123!", str(last_entry))

    # -------------------------------------------------------------------------
    # Test X: Companion routing
    # -------------------------------------------------------------------------
    def test_X_companion_routing(self):
        """Android UI queries route cleanly to CommandCategory.ANDROID_STUDIO."""
        companion = NRCompanion()
        for phrase in (
            "inspect the android screen",
            "what is on the android screen",
            "find the login button on android",
            "tap the login button on android",
            "scroll down on android",
            "verify the android screen",
        ):
            cat = companion.classify_command(phrase)
            self.assertEqual(cat, CommandCategory.ANDROID_STUDIO)

    # -------------------------------------------------------------------------
    # Test Y: Authorized project and device boundary
    # -------------------------------------------------------------------------
    def test_Y_authorized_project_device_boundary(self):
        r"""Ensures boundaries remain C:\NR-AI\nr_android_test, com.nrai.test, and authorized serials."""
        self.assertEqual(self.safety.authorized_project, AUTHORIZED_PROJECT_PATH)
        self.assertEqual(self.safety.authorized_package, AUTHORIZED_PACKAGE_NAME)
        self.assertEqual(AUTHORIZED_DEVICE_SERIALS, {"emulator-5554", "15930545720012G"})

    # -------------------------------------------------------------------------
    # Test Z: Clean workspace and registry cleanup
    # -------------------------------------------------------------------------
    def test_Z_cleanup_after_tests(self):
        """Ensures registry clears and workspace remains clean after test cycle."""
        self.registry.clear()
        self.assertIsNone(self.registry.get_snapshot("emulator-5554"))
        self.assertFalse(self.safety.is_emergency_stop_active())


if __name__ == "__main__":
    unittest.main()
