"""
Tests for Droid Phase 4 Component 8: UI Behavior Debugger.
"""
import unittest
from app.agent.android_ui_debugger import AndroidUIDebugger, UIDefectKind, UIDiagnosisStatus

class TestDroidPhase4UIDebug(unittest.TestCase):
    def setUp(self):
        self.debugger = AndroidUIDebugger()

    def test_diagnose_unmutated_state(self):
        pre = ["Counter: 0", "Increment Button"]
        post = ["Counter: 0", "Increment Button"]
        diag = self.debugger.diagnose_interaction(
            pre_elements=pre,
            post_elements=post,
            action="tap",
            target_text="Increment Button",
        )
        self.assertEqual(diag.defect_kind, UIDefectKind.UNMUTATED_STATE)
        self.assertEqual(diag.status, UIDiagnosisStatus.POSSIBLE)

    def test_diagnose_anr_blocked_thread(self):
        pre = ["Submit"]
        post = ["Submit"]
        diag = self.debugger.diagnose_interaction(
            pre_elements=pre,
            post_elements=post,
            action="tap",
            target_text="Submit",
            logcat_snippet="ANR in com.nrai.test (Application Not Responding)",
        )
        self.assertEqual(diag.defect_kind, UIDefectKind.BLOCKED_MAIN_THREAD)
        self.assertEqual(diag.status, UIDiagnosisStatus.OBSERVED)
