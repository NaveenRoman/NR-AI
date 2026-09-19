import unittest
import tempfile
import shutil
from pathlib import Path

from app.agent.android_accessibility_audit import (
    AndroidAccessibilityAuditEngine,
    QualitySeverity,
)


class TestDroidPhase5AccessibilityAudit(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.engine = AndroidAccessibilityAuditEngine()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_missing_content_description_in_compose(self):
        kt_file = self.temp_dir / "MyScreen.kt"
        kt_file.write_text("""package com.example
import androidx.compose.runtime.Composable

@Composable
fun Profile() {
    Image(
        painter = painterResource(id = 123)
    )
}
""", encoding="utf-8")

        issues = self.engine.audit_compose_file(kt_file)
        self.assertTrue(any(i.rule_id == "COMPOSE_A11Y_MISSING_CONTENT_DESC" for i in issues))

    def test_hardcoded_string_literal(self):
        kt_file = self.temp_dir / "MyScreen.kt"
        kt_file.write_text("""package com.example
import androidx.compose.runtime.Composable

@Composable
fun Greeting() {
    Text(text = "Hello World")
}
""", encoding="utf-8")

        issues = self.engine.audit_compose_file(kt_file)
        self.assertTrue(any(i.rule_id == "COMPOSE_UI_HARDCODED_STRING" for i in issues))

    def test_touch_target_size_violation(self):
        kt_file = self.temp_dir / "MyScreen.kt"
        kt_file.write_text("""package com.example
import androidx.compose.runtime.Composable

@Composable
fun SmallButton() {
    Box(modifier = Modifier.size(32.dp).clickable { })
}
""", encoding="utf-8")

        issues = self.engine.audit_compose_file(kt_file)
        self.assertTrue(any(i.rule_id == "COMPOSE_A11Y_TOUCH_TARGET_SIZE" for i in issues))


if __name__ == "__main__":
    unittest.main()
