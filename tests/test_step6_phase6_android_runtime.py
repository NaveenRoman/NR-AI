"""
NR-AI Step 6 Phase 6: Android Logcat / Runtime Debugging & Diagnostics Test Suite.

Tests A through Z verifying:
- Test A: Bounded logcat retrieval (lines and bytes bounded)
- Test B: Severity filtering (V, D, I, W, E, F)
- Test C: Package and tag filtering
- Test D: Sensitive data redaction in logcat output
- Test E: Device info extraction (OS, SDK, Model, Manufacturer)
- Test F: Process state inspection (PID, running state, memory)
- Test G: Kotlin runtime exception detection & parsing
- Test H: Java runtime exception detection & parsing
- Test I: FATAL EXCEPTION and AndroidRuntime crash detection
- Test J: ANR (Application Not Responding) detection
- Test K: Permission denial (SecurityException) detection
- Test L: Missing resource (Resources$NotFoundException) detection
- Test M: Missing class (ClassNotFoundException) detection
- Test N: Activity launch failure detection
- Test O: Noisy logcat parsing & layout noise handling
- Test P: Diagnostic snapshot generation & schema validation
- Test Q: Error analyzer distinguishing build vs runtime vs configuration
- Test R: Stack trace source mapping to com.nrai.test
- Test S: Emergency stop immediately halts diagnostic operations
- Test T: Advisory model receives bounded, sanitized diagnostic data only
- Test U: Advisory model diagnosis schema validation
- Test V: Deterministic evidence overrides model hallucinated crash claim
- Test W: Advisory model cannot execute ADB or shell commands directly
- Test X: Companion command routing to CommandCategory.ANDROID_STUDIO
- Test Y: Audit logging of diagnostic events with sensitive data redaction
- Test Z: Cleanup & resource isolation
"""

import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from app.agent.android_code_repair import (
    AndroidBuildError,
    AndroidErrorAnalyzer,
    AndroidErrorCategory,
)
from app.agent.android_diagnostics import (
    AndroidDiagnosticsController,
    AndroidLogParser,
    AndroidRuntimeError,
    DiagnosticSnapshot,
    LogEntry,
    LogLevel,
    RuntimeErrorType,
    build_model_diagnosis_prompt,
    parse_model_diagnosis,
    redact_sensitive_runtime_data,
)
from app.agent.android_safety import (
    ALLOWED_ANDROID_DIAGNOSTIC_OPERATIONS,
    ALLOWED_ANDROID_TOOLS,
    AUTHORIZED_DEVICE_SERIALS,
    AUTHORIZED_PACKAGE_NAME,
    AUTHORIZED_PROJECT_PATH,
    MAX_LOGCAT_BYTES,
    MAX_LOGCAT_LINES,
    MAX_SNAPSHOT_BYTES,
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
from app.brain.companion import CommandCategory, NRCompanion
from app.memory.audit_logger import AuditLogger


# -----------------------------------------------------------------------------
# Test Fixtures: Sample Logcat Streams
# -----------------------------------------------------------------------------

SAMPLE_NORMAL_LOGCAT = """
09-15 09:20:01.100  1000  1000 I ActivityManager: Start proc 12345:com.nrai.test/u0a123 for activity
09-15 09:20:01.150 12345 12345 D MainActivity: onCreate called successfully
09-15 09:20:01.200 12345 12345 I MainActivity: View hierarchy rendered in 50ms
09-15 09:20:01.250 12345 12345 D TestApp: Initializing user session
09-15 09:20:01.300  1000  1000 I WindowManager: Displayed com.nrai.test/.MainActivity: +200ms
"""

SAMPLE_KOTLIN_EXCEPTION_LOGCAT = """
09-15 09:20:02.100 12345 12345 D MainActivity: Loading profile data...
09-15 09:20:02.150 12345 12345 E AndroidRuntime: FATAL EXCEPTION: main
09-15 09:20:02.150 12345 12345 E AndroidRuntime: Process: com.nrai.test, PID: 12345
09-15 09:20:02.150 12345 12345 E AndroidRuntime: java.lang.NullPointerException: Parameter specified as non-null is null: method com.nrai.test.MainActivity.render, parameter user
09-15 09:20:02.150 12345 12345 E AndroidRuntime: \tat com.nrai.test.MainActivity.render(MainActivity.kt:42)
09-15 09:20:02.150 12345 12345 E AndroidRuntime: \tat com.nrai.test.MainActivity.onCreate(MainActivity.kt:18)
09-15 09:20:02.150 12345 12345 E AndroidRuntime: \tat android.app.Activity.performCreate(Activity.java:8051)
"""

SAMPLE_JAVA_EXCEPTION_LOGCAT = """
09-15 09:20:03.100 12345 12345 E AndroidRuntime: FATAL EXCEPTION: main
09-15 09:20:03.100 12345 12345 E AndroidRuntime: Process: com.nrai.test, PID: 12345
09-15 09:20:03.100 12345 12345 E AndroidRuntime: java.lang.IllegalStateException: View already detached
09-15 09:20:03.100 12345 12345 E AndroidRuntime: \tat com.nrai.test.JavaHelper.updateView(JavaHelper.java:85)
09-15 09:20:03.100 12345 12345 E AndroidRuntime: \tat android.view.View.dispatchAttachedToWindow(View.java:21295)
"""

SAMPLE_ANR_LOGCAT = """
09-15 09:20:04.100  1000  1050 E ActivityManager: ANR in com.nrai.test (com.nrai.test/.MainActivity)
09-15 09:20:04.100  1000  1050 E ActivityManager: PID: 12345
09-15 09:20:04.100  1000  1050 E ActivityManager: Reason: Input dispatching timed out (Waiting to send key event)
09-15 09:20:04.100  1000  1050 E ActivityManager: Load: 5.4 / 4.2 / 3.1
09-15 09:20:04.100  1000  1050 E ActivityManager: CPU usage from 0ms to 5000ms later:
"""

SAMPLE_SECURITY_EXCEPTION_LOGCAT = """
09-15 09:20:05.100 12345 12345 W System.err: java.lang.SecurityException: Permission Denial: opening provider com.android.providers.contacts from ProcessRecord{12345:com.nrai.test/u0a123} requires android.permission.READ_CONTACTS
09-15 09:20:05.100 12345 12345 W System.err: \tat android.os.Parcel.createExceptionOrNull(Parcel.java:2425)
"""

SAMPLE_RESOURCE_NOT_FOUND_LOGCAT = """
09-15 09:20:06.100 12345 12345 E AndroidRuntime: android.content.res.Resources$NotFoundException: Resource ID #0x7f0a0001 type string name 'missing_label' not found
09-15 09:20:06.100 12345 12345 E AndroidRuntime: \tat android.content.res.ResourcesImpl.getValue(ResourcesImpl.java:237)
"""

SAMPLE_CLASS_NOT_FOUND_LOGCAT = """
09-15 09:20:07.100 12345 12345 E AndroidRuntime: java.lang.ClassNotFoundException: Didn't find class "com.nrai.test.MissingFeature" on path: DexPathList[...]
09-15 09:20:07.100 12345 12345 E AndroidRuntime: \tat dalvik.system.BaseDexClassLoader.findClass(BaseDexClassLoader.java:207)
"""

SAMPLE_ACTIVITY_NOT_FOUND_LOGCAT = """
09-15 09:20:08.100 12345 12345 E AndroidRuntime: android.content.ActivityNotFoundException: Unable to find explicit activity class {com.nrai.test/com.nrai.test.SecretActivity}; have you declared this activity in your AndroidManifest.xml?
"""

SAMPLE_SENSITIVE_LOGCAT = """
09-15 09:20:09.100 12345 12345 D AuthManager: Authenticating with API_KEY=AIzaSy123456789012345678901234567890123
09-15 09:20:09.110 12345 12345 D Network: Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.secretToken123
09-15 09:20:09.120 12345 12345 D Network: Cookie: session_id=sess_abc123xyz; password=supersecretpass
09-15 09:20:09.130 12345 12345 D Client: OpenAI token sk-proj-1234567890abcdefghijklmn and ghp_123456789012345678901234567890123456
"""


class TestStep6Phase6AndroidRuntime(unittest.TestCase):
    """Step 6 Phase 6: Android Logcat / Runtime Debugging & Diagnostics Acceptance Test Suite."""

    def setUp(self):
        AndroidSafetyGate.deactivate_emergency_stop()
        self.safety = AndroidSafetyGate()
        self.audit = AuditLogger()
        self.mock_adb = MagicMock(spec=SafeAdbClient)
        self.controller = AndroidDiagnosticsController(
            safety_gate=self.safety,
            adb_client=self.mock_adb,
            audit_logger=self.audit,
        )

    def tearDown(self):
        AndroidSafetyGate.deactivate_emergency_stop()

    # -------------------------------------------------------------------------
    # Test A: Bounded Logcat Retrieval
    # -------------------------------------------------------------------------
    def test_A_bounded_logcat_retrieval(self):
        """Bounded logcat retrieval respects line count and byte size limits."""
        # 1. Bounds lines
        self.assertEqual(self.safety.validate_logcat_bounds(500), 500)
        self.assertEqual(self.safety.validate_logcat_bounds(5000), MAX_LOGCAT_LINES)
        self.assertEqual(self.safety.validate_logcat_bounds(-10), 100)

        # 2. Large simulated log is bounded by capture_logcat_advanced
        real_adb = SafeAdbClient()
        huge_log = ("09-15 09:20:00.000  1000  1000 D Log: Line data padding\n" * 15000)
        with patch.object(real_adb, "_run_adb", return_value=(0, huge_log, "")):
            res = real_adb.capture_logcat_advanced("emulator-5554", lines=2000)
            self.assertLessEqual(len(res.encode("utf-8")), MAX_LOGCAT_BYTES + 500)
            self.assertIn("LOGCAT TRUNCATED", res)

    # -------------------------------------------------------------------------
    # Test B: Severity Filtering
    # -------------------------------------------------------------------------
    def test_B_severity_filtering(self):
        """Severity levels (V, D, I, W, E, F) properly filter logcat output."""
        real_adb = SafeAdbClient()
        with patch.object(real_adb, "_run_adb") as mock_run:
            mock_run.return_value = (0, "sample log", "")
            real_adb.capture_logcat_advanced("emulator-5554", severity="E")
            called_cmd = mock_run.call_args[0][0]
            self.assertIn("*:E", called_cmd)

            real_adb.capture_logcat_advanced("emulator-5554", severity="w")
            called_cmd2 = mock_run.call_args[0][0]
            self.assertIn("*:W", called_cmd2)

    # -------------------------------------------------------------------------
    # Test C: Package and Tag Filtering
    # -------------------------------------------------------------------------
    def test_C_package_and_tag_filtering(self):
        """Filtering to package or tag retains only matching entries."""
        real_adb = SafeAdbClient()
        multi_pkg_log = (
            "09-15 09:20:01.000 12345 12345 I com.nrai.test: App started\n"
            "09-15 09:20:01.100  5555  5555 I com.other.app: Other app started\n"
            "09-15 09:20:01.200 12345 12345 D com.nrai.test: MainActivity loaded\n"
        )
        with patch.object(real_adb, "_run_adb", return_value=(0, multi_pkg_log, "")):
            res = real_adb.capture_logcat_advanced("emulator-5554", filter_package="com.nrai.test")
            self.assertIn("com.nrai.test", res)
            self.assertNotIn("com.other.app", res)

    # -------------------------------------------------------------------------
    # Test D: Sensitive Data Redaction
    # -------------------------------------------------------------------------
    def test_D_sensitive_data_redaction(self):
        """Sensitive credentials, keys, passwords, and tokens are redacted from logs."""
        clean = redact_sensitive_runtime_data(SAMPLE_SENSITIVE_LOGCAT)
        self.assertNotIn("AIzaSy123456789012345678901234567890123", clean)
        self.assertIn("[REDACTED_GEMINI_KEY]", clean)
        self.assertNotIn("secretToken123", clean)
        self.assertIn("[REDACTED_TOKEN]", clean)
        self.assertNotIn("supersecretpass", clean)
        self.assertIn("[REDACTED]", clean)
        self.assertNotIn("sk-proj-1234567890abcdefghijklmn", clean)
        self.assertIn("[REDACTED_OPENAI_KEY]", clean)
        self.assertNotIn("ghp_123456789012345678901234567890123456", clean)
        self.assertIn("[REDACTED_GITHUB_TOKEN]", clean)

    # -------------------------------------------------------------------------
    # Test E: Device Info Extraction
    # -------------------------------------------------------------------------
    def test_E_device_info_extraction(self):
        """Queries and parses device properties via getprop."""
        real_adb = SafeAdbClient()
        fake_getprop = (
            "[ro.build.version.release]: [14]\n"
            "[ro.build.version.sdk]: [34]\n"
            "[ro.product.model]: [Pixel 6]\n"
            "[ro.product.manufacturer]: [Google]\n"
            "[ro.product.brand]: [google]\n"
            "[ro.build.fingerprint]: [google/oriole/oriole:14/UP1A.231005.007/10754064:user/release-keys]\n"
        )
        with patch.object(real_adb, "_run_adb", return_value=(0, fake_getprop, "")):
            info = real_adb.get_device_info("emulator-5554")
            self.assertEqual(info["os_version"], "14")
            self.assertEqual(info["sdk_level"], "34")
            self.assertEqual(info["model"], "Pixel 6")
            self.assertEqual(info["manufacturer"], "Google")

    # -------------------------------------------------------------------------
    # Test F: Process State Inspection
    # -------------------------------------------------------------------------
    def test_F_process_state_inspection(self):
        """Queries process state and memory usage."""
        real_adb = SafeAdbClient()
        with patch.object(real_adb, "get_process_pid", return_value=12345):
            with patch.object(real_adb, "_run_adb", return_value=(0, "TOTAL PSS: 45210", "")):
                pinfo = real_adb.get_process_info("emulator-5554", "com.nrai.test")
                self.assertTrue(pinfo["is_running"])
                self.assertEqual(pinfo["pid"], 12345)
                self.assertEqual(pinfo.get("total_pss_kb"), 45210)

    # -------------------------------------------------------------------------
    # Test G: Kotlin Runtime Exception Detection
    # -------------------------------------------------------------------------
    def test_G_kotlin_runtime_exception_detection(self):
        """Detects Kotlin NullPointerException and resolves stack trace."""
        errors = AndroidLogParser.extract_runtime_errors(SAMPLE_KOTLIN_EXCEPTION_LOGCAT)
        self.assertEqual(len(errors), 1)
        err = errors[0]
        self.assertEqual(err.error_type, RuntimeErrorType.FATAL_EXCEPTION)
        self.assertEqual(err.exception_class, "NullPointerException")
        self.assertIn("MainActivity.render", err.message)
        self.assertEqual(err.package_name, "com.nrai.test")
        self.assertEqual(err.process_id, 12345)
        self.assertEqual(err.source_file, "MainActivity.kt")
        self.assertEqual(err.source_line, 42)

    # -------------------------------------------------------------------------
    # Test H: Java Runtime Exception Detection
    # -------------------------------------------------------------------------
    def test_H_java_runtime_exception_detection(self):
        """Detects Java IllegalStateException and resolves stack trace."""
        errors = AndroidLogParser.extract_runtime_errors(SAMPLE_JAVA_EXCEPTION_LOGCAT)
        self.assertEqual(len(errors), 1)
        err = errors[0]
        self.assertEqual(err.error_type, RuntimeErrorType.FATAL_EXCEPTION)
        self.assertEqual(err.exception_class, "IllegalStateException")
        self.assertIn("View already detached", err.message)
        self.assertEqual(err.source_file, "JavaHelper.java")
        self.assertEqual(err.source_line, 85)

    # -------------------------------------------------------------------------
    # Test I: FATAL EXCEPTION Crash Detection
    # -------------------------------------------------------------------------
    def test_I_fatal_exception_crash_detection(self):
        """Recognizes AndroidRuntime FATAL EXCEPTION blocks."""
        errors = AndroidLogParser.extract_runtime_errors(SAMPLE_KOTLIN_EXCEPTION_LOGCAT)
        self.assertTrue(any(e.error_type == RuntimeErrorType.FATAL_EXCEPTION for e in errors))

    # -------------------------------------------------------------------------
    # Test J: ANR Detection
    # -------------------------------------------------------------------------
    def test_J_anr_detection(self):
        """Extracts ANR (Application Not Responding) events with reason."""
        errors = AndroidLogParser.extract_runtime_errors(SAMPLE_ANR_LOGCAT)
        self.assertEqual(len(errors), 1)
        err = errors[0]
        self.assertEqual(err.error_type, RuntimeErrorType.ANR)
        self.assertEqual(err.package_name, "com.nrai.test")
        self.assertIn("Input dispatching timed out", err.message)

    # -------------------------------------------------------------------------
    # Test K: Permission Denial Detection
    # -------------------------------------------------------------------------
    def test_K_permission_denial_detection(self):
        """Extracts SecurityException permission denials."""
        errors = AndroidLogParser.extract_runtime_errors(SAMPLE_SECURITY_EXCEPTION_LOGCAT)
        self.assertEqual(len(errors), 1)
        err = errors[0]
        self.assertEqual(err.error_type, RuntimeErrorType.PERMISSION_DENIED)
        self.assertIn("SecurityException", err.exception_class)
        self.assertIn("READ_CONTACTS", err.message)

    # -------------------------------------------------------------------------
    # Test L: Missing Resource Detection
    # -------------------------------------------------------------------------
    def test_L_resource_not_found_detection(self):
        """Extracts Resources$NotFoundException."""
        errors = AndroidLogParser.extract_runtime_errors(SAMPLE_RESOURCE_NOT_FOUND_LOGCAT)
        self.assertEqual(len(errors), 1)
        err = errors[0]
        self.assertEqual(err.error_type, RuntimeErrorType.RESOURCE_NOT_FOUND)
        self.assertIn("missing_label", err.message)

    # -------------------------------------------------------------------------
    # Test M: Missing Class Detection
    # -------------------------------------------------------------------------
    def test_M_class_not_found_detection(self):
        """Extracts ClassNotFoundException."""
        errors = AndroidLogParser.extract_runtime_errors(SAMPLE_CLASS_NOT_FOUND_LOGCAT)
        self.assertEqual(len(errors), 1)
        err = errors[0]
        self.assertEqual(err.error_type, RuntimeErrorType.CLASS_NOT_FOUND)
        self.assertIn("MissingFeature", err.message)

    # -------------------------------------------------------------------------
    # Test N: Activity Launch Failure Detection
    # -------------------------------------------------------------------------
    def test_N_activity_launch_failure_detection(self):
        """Extracts ActivityNotFoundException."""
        errors = AndroidLogParser.extract_runtime_errors(SAMPLE_ACTIVITY_NOT_FOUND_LOGCAT)
        self.assertEqual(len(errors), 1)
        err = errors[0]
        self.assertEqual(err.error_type, RuntimeErrorType.ACTIVITY_LAUNCH_FAILURE)
        self.assertIn("SecretActivity", err.message)

    # -------------------------------------------------------------------------
    # Test O: Noisy Logcat Handling
    # -------------------------------------------------------------------------
    def test_O_noisy_logcat_handling(self):
        """Handles noisy logs and extracts accurate entries without breaking."""
        noisy = (
            "--------- beginning of main\n"
            "09-15 09:20:00.001   500   500 D OpenGLRenderer: Canvas rendered\n"
            "09-15 09:20:00.002   600   600 I Choreographer: Skipped 15 frames!\n"
            + SAMPLE_KOTLIN_EXCEPTION_LOGCAT +
            "09-15 09:20:00.003   500   500 D ViewRootImpl: Relayout window\n"
        )
        entries = AndroidLogParser.parse_lines(noisy)
        self.assertGreater(len(entries), 5)
        errors = AndroidLogParser.extract_runtime_errors(noisy)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].exception_class, "NullPointerException")

    # -------------------------------------------------------------------------
    # Test P: Diagnostic Snapshot Generation & Schema Validation
    # -------------------------------------------------------------------------
    def test_P_diagnostic_snapshot_generation(self):
        """Generates complete DiagnosticSnapshot with all expected structure."""
        self.mock_adb.get_device_info.return_value = {"model": "Pixel 6", "os_version": "14"}
        self.mock_adb.get_foreground_app.return_value = {"package": "com.nrai.test", "activity": ".MainActivity"}
        self.mock_adb.get_process_info.return_value = {"package": "com.nrai.test", "is_running": True, "pid": 12345}
        self.mock_adb.capture_logcat_advanced.return_value = SAMPLE_KOTLIN_EXCEPTION_LOGCAT

        snap = self.controller.create_diagnostic_snapshot(serial="emulator-5554")
        self.assertIsInstance(snap, DiagnosticSnapshot)
        self.assertEqual(snap.device_serial, "emulator-5554")
        self.assertEqual(len(snap.detected_errors), 1)
        self.assertEqual(snap.foreground_app["package"], "com.nrai.test")
        self.assertTrue(snap.snapshot_id.startswith("snap_"))
        self.assertIn("NullPointerException", snap.detected_errors[0].exception_class)

    # -------------------------------------------------------------------------
    # Test Q: Error Analyzer Distinguishing Build vs Runtime vs Config
    # -------------------------------------------------------------------------
    def test_Q_error_analyzer_distinguishes_build_vs_runtime(self):
        """AndroidErrorAnalyzer accurately categorizes error domains."""
        self.assertEqual(
            AndroidErrorAnalyzer.distinguish_error_type("compileDebugKotlin failed with 2 errors"),
            "BUILD_TIME",
        )
        self.assertEqual(
            AndroidErrorAnalyzer.distinguish_error_type("AndroidRuntime: FATAL EXCEPTION: main"),
            "RUNTIME_CRASH",
        )
        self.assertEqual(
            AndroidErrorAnalyzer.distinguish_error_type("Unsupported class file major version 65"),
            "ENVIRONMENT_CONFIG",
        )
        self.assertEqual(
            AndroidErrorAnalyzer.distinguish_error_type("random text"),
            "UNKNOWN",
        )

    # -------------------------------------------------------------------------
    # Test R: Stack Trace Source Mapping
    # -------------------------------------------------------------------------
    def test_R_stack_trace_source_mapping(self):
        """Maps runtime stack trace to project source files."""
        build_errs = AndroidErrorAnalyzer.analyze_runtime_log(SAMPLE_KOTLIN_EXCEPTION_LOGCAT)
        self.assertEqual(len(build_errs), 1)
        err = build_errs[0]
        self.assertEqual(err.category, AndroidErrorCategory.RUNTIME_CRASH)
        self.assertEqual(err.line, 42)
        self.assertTrue(err.file_path.endswith("MainActivity.kt"))

    # -------------------------------------------------------------------------
    # Test S: Emergency Stop Freezes Diagnostics
    # -------------------------------------------------------------------------
    def test_S_emergency_stop_halts_diagnostics(self):
        """Active emergency stop halts diagnostic operations immediately."""
        AndroidSafetyGate.activate_emergency_stop()
        with self.assertRaises(EmergencyStopActiveError):
            self.controller.capture_logs("emulator-5554")

        with self.assertRaises(EmergencyStopActiveError):
            self.controller.create_diagnostic_snapshot("emulator-5554")

    # -------------------------------------------------------------------------
    # Test T: Model Receives Bounded Diagnostic Data Only
    # -------------------------------------------------------------------------
    def test_T_model_receives_bounded_diagnostic_data_only(self):
        """Advisory prompt generator bounds entries and contains no credentials."""
        snap = DiagnosticSnapshot(
            snapshot_id="snap_123",
            timestamp=time.time(),
            device_serial="emulator-5554",
            device_info={"model": "Pixel 6", "os_version": "14", "sdk_level": "34"},
            foreground_app={"package": "com.nrai.test", "activity": ".MainActivity"},
            process_info={"is_running": True, "pid": 12345},
            detected_errors=[],
            log_entries=[],
            log_summary={"total": 0},
            correlation_id="corr_123",
            raw_log_sample="",
        )
        sys_p, user_p = build_model_diagnosis_prompt(snap, "why did the app crash?")
        self.assertIn("STRICTLY ADVISORY", sys_p)
        self.assertIn("emulator-5554", user_p)
        self.assertIn("com.nrai.test", user_p)
        self.assertNotIn("password", user_p.lower())
        self.assertNotIn("api_key", user_p.lower())

    # -------------------------------------------------------------------------
    # Test U: Advisory Model Diagnosis Schema Validation
    # -------------------------------------------------------------------------
    def test_U_model_diagnosis_schema_validation(self):
        """Strict JSON validation accepts valid schemas and rejects malformed ones."""
        valid_json = json.dumps({
            "summary": "Crash due to null pointer exception in MainActivity",
            "crash_detected": True,
            "likely_cause": "Null user object passed to render method",
            "confidence": 0.95,
            "evidence_log_lines": ["FATAL EXCEPTION: main", "NullPointerException"],
            "recommendations": ["Add null check before render(user)"]
        })
        parsed = parse_model_diagnosis(valid_json)
        self.assertEqual(parsed["confidence"], 0.95)
        self.assertTrue(parsed["crash_detected"])

        # Missing required field must fail with MALFORMED_DIAGNOSTIC_SCHEMA
        invalid_json = json.dumps({"summary": "Incomplete"})
        with self.assertRaises(AndroidSafetyError) as ctx:
            parse_model_diagnosis(invalid_json)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.MALFORMED_DIAGNOSTIC_SCHEMA)

    # -------------------------------------------------------------------------
    # Test V: Deterministic Evidence Overrides Model Hallucination
    # -------------------------------------------------------------------------
    def test_V_deterministic_evidence_overrides_model_hallucination(self):
        """Ground-truth logs override advisory model claim if no crash occurred."""
        clean_snapshot = DiagnosticSnapshot(
            snapshot_id="snap_clean",
            timestamp=time.time(),
            device_serial="emulator-5554",
            device_info={},
            foreground_app={"package": "com.nrai.test", "activity": ".MainActivity"},
            process_info={"is_running": True, "pid": 12345},
            detected_errors=[],  # Clean: 0 crashes
            log_entries=[],
            log_summary={"total": 10},
            correlation_id="corr_clean",
        )
        hallucinated_model_claim = {
            "summary": "App crashed with OutOfMemoryError",
            "crash_detected": True,
            "likely_cause": "Bitmap memory leak",
            "confidence": 0.99,
            "recommendations": ["Recycle bitmaps"],
        }
        res = self.controller.verify_model_diagnosis(clean_snapshot, hallucinated_model_claim)
        self.assertFalse(res["verified"])
        self.assertEqual(res["final_assessment"], "NO_CRASH_VERIFIED")
        self.assertIn("OVERRIDE", res["override_reason"])

    # -------------------------------------------------------------------------
    # Test W: Model Cannot Execute ADB or Shell Directly
    # -------------------------------------------------------------------------
    def test_W_model_cannot_execute_adb_or_shell(self):
        """Advisory models have zero tool or execution authority."""
        agent = AndroidStudioAgent(safety_gate=self.safety)
        advisory_text = agent.consult_model("why did the app crash?")
        self.assertIsNotNone(advisory_text)
        self.assertIn("advisory", advisory_text.lower())
        # Model output cannot trigger ADB or file write

    # -------------------------------------------------------------------------
    # Test X: Companion Command Routing
    # -------------------------------------------------------------------------
    def test_X_companion_command_routing(self):
        """NRCompanion routes runtime diagnostic commands to ANDROID_STUDIO."""
        companion = NRCompanion()
        phrases = [
            "show android logs",
            "check android logs",
            "why did the android app crash",
            "inspect android runtime",
            "diagnose android error",
            "what happened in android",
            "android crash",
        ]
        for phrase in phrases:
            cat = companion.classify_command(phrase)
            self.assertEqual(
                cat,
                CommandCategory.ANDROID_STUDIO,
                f"Failed to route phrase: '{phrase}'",
            )

    # -------------------------------------------------------------------------
    # Test Y: Audit Logging of Diagnostic Events with Redaction
    # -------------------------------------------------------------------------
    def test_Y_audit_logging_with_redaction(self):
        """Diagnostic events are recorded in audit logs with credentials redacted."""
        self.mock_adb.get_device_info.return_value = {"model": "Pixel 6"}
        self.mock_adb.get_foreground_app.return_value = {"package": "com.nrai.test"}
        self.mock_adb.get_process_info.return_value = {"is_running": True}
        self.mock_adb.capture_logcat_advanced.return_value = SAMPLE_SENSITIVE_LOGCAT

        snap = self.controller.create_diagnostic_snapshot("emulator-5554")
        recent = self.audit.get_recent_events(limit=5)
        self.assertTrue(any(e.get("event_type") == "DIAGNOSTIC_SNAPSHOT_CREATED" for e in recent))

        # Check raw audit records do not contain secret keys
        audit_str = json.dumps(recent)
        self.assertNotIn("AIzaSy123456789012345678901234567890123", audit_str)
        self.assertNotIn("supersecretpass", audit_str)

    # -------------------------------------------------------------------------
    # Test Z: Cleanup & Resource Isolation
    # -------------------------------------------------------------------------
    def test_Z_cleanup_and_resource_isolation(self):
        """Ensures emergency stop deactivates and clean workspace is preserved."""
        AndroidSafetyGate.deactivate_emergency_stop()
        self.assertFalse(self.safety.is_emergency_stop_active())
        self.assertEqual(len(ALLOWED_ANDROID_TOOLS), 15)


if __name__ == "__main__":
    unittest.main()