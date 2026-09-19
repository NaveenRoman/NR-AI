"""
Tests for Droid Phase 2: Runtime Compose Semantics Correlation Subsystem.
"""

import time
import unittest
from app.agent.android_ui import AndroidTarget, AndroidUISnapshot
from app.agent.android_runtime_semantics import (
    RuntimeComposeSemanticsCorrelator,
    CorrelationEvidence,
)


COMPOSE_SOURCE = """
package com.nrai.test

import androidx.compose.runtime.Composable
import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.foundation.layout.Column

@Composable
fun MainScreen() {
    Column {
        Text("NR-AI Android Agent", modifier = Modifier.testTag("app_title"))
        Button(onClick = { }, modifier = Modifier.testTag("login_button")) {
            Text("Login")
        }
        Text("Copyright 2026")
    }
}
"""


class TestDroidPhase2RuntimeSemantics(unittest.TestCase):

    def test_multi_signal_semantics_correlation(self):
        """Verify testTag and exact text matching produce strong CORRELATED evidence."""
        t1 = AndroidTarget(
            target_id="android.target.001",
            semantic_type="text",
            text="NR-AI Android Agent",
            content_desc="",
            resource_id="com.nrai.test:id/app_title",
            class_name="android.widget.TextView",
            package_name="com.nrai.test",
            bounds=(100, 100, 500, 180),
            center=(300, 140),
            clickable=False,
            enabled=True,
            selected=False,
            focused=False,
            device_serial="emulator-5554",
            screen_width=1080,
            screen_height=2400,
        )
        t2 = AndroidTarget(
            target_id="android.target.002",
            semantic_type="button",
            text="Login",
            content_desc="",
            resource_id="com.nrai.test:id/login_button",
            class_name="android.widget.Button",
            package_name="com.nrai.test",
            bounds=(100, 200, 500, 300),
            center=(300, 250),
            clickable=True,
            enabled=True,
            selected=False,
            focused=False,
            device_serial="emulator-5554",
            screen_width=1080,
            screen_height=2400,
        )
        t3 = AndroidTarget(
            target_id="android.target.003",
            semantic_type="text",
            text="Copyright 2026",
            content_desc="",
            resource_id="",
            class_name="android.widget.TextView",
            package_name="com.nrai.test",
            bounds=(100, 2200, 500, 2250),
            center=(300, 2225),
            clickable=False,
            enabled=True,
            selected=False,
            focused=False,
            device_serial="emulator-5554",
            screen_width=1080,
            screen_height=2400,
        )

        snapshot = AndroidUISnapshot(
            snapshot_id="snap_001",
            device_serial="emulator-5554",
            foreground_app={"package": "com.nrai.test", "activity": "com.nrai.test.MainActivity"},
            screen_dimensions=(1080, 2400),
            timestamp=time.time(),
            targets=[t1, t2, t3],
        )

        correlator = RuntimeComposeSemanticsCorrelator()
        report = correlator.correlate_from_source(COMPOSE_SOURCE, snapshot)

        self.assertEqual(report.total_runtime_targets, 3)
        self.assertGreaterEqual(report.total_source_components, 3)
        self.assertGreaterEqual(report.correlated_count, 2)

        # Check button correlation
        btn_nodes = [n for n in report.nodes if n.component_type == "Button"]
        self.assertTrue(len(btn_nodes) > 0)
        btn_node = btn_nodes[0]
        self.assertEqual(btn_node.evidence, CorrelationEvidence.CORRELATED)
        self.assertEqual(btn_node.target_id, "android.target.002")
        self.assertTrue(btn_node.clickable)
        self.assertEqual(btn_node.center, (300, 250))

    def test_unmatched_semantics_identification(self):
        """Verify elements missing from runtime hierarchy are classified as UNMATCHED."""
        snapshot = AndroidUISnapshot(
            snapshot_id="snap_empty",
            device_serial="emulator-5554",
            foreground_app={"package": "com.nrai.test", "activity": "MainActivity"},
            screen_dimensions=(1080, 2400),
            timestamp=time.time(),
            targets=[],
        )

        correlator = RuntimeComposeSemanticsCorrelator()
        report = correlator.correlate_from_source(COMPOSE_SOURCE, snapshot)

        self.assertEqual(report.correlated_count, 0)
        self.assertGreaterEqual(report.unmatched_count, 3)
        self.assertTrue(all(n.evidence == CorrelationEvidence.UNMATCHED for n in report.nodes))


if __name__ == "__main__":
    unittest.main()
