import unittest
import tempfile
import shutil
from pathlib import Path

from app.agent.android_manifest_merge import (
    AndroidManifestMergeEngine,
    ManifestSeverity,
    CompatibilityResult,
)


class TestDroidPhase5ManifestMerge(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.engine = AndroidManifestMergeEngine()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_missing_exported_attribute_audit(self):
        manifest = self.temp_dir / "AndroidManifest.xml"
        manifest.write_text("""<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.test">
    <application>
        <activity android:name=".MainActivity">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
            </intent-filter>
        </activity>
    </application>
</manifest>""", encoding="utf-8")

        issues = self.engine.audit_manifest(manifest, target_sdk=34)
        critical = [i for i in issues if i.severity == ManifestSeverity.CRITICAL]
        self.assertTrue(any(i.code == "MISSING_EXPORTED_ATTRIBUTE" for i in critical))

    def test_cleartext_traffic_warning(self):
        manifest = self.temp_dir / "AndroidManifest.xml"
        manifest.write_text("""<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.test">
    <application android:usesCleartextTraffic="true">
        <activity android:name=".MainActivity" android:exported="true" />
    </application>
</manifest>""", encoding="utf-8")

        issues = self.engine.audit_manifest(manifest, target_sdk=34)
        self.assertTrue(any(i.code == "INSECURE_CLEARTEXT_TRAFFIC" for i in issues))

    def test_evaluate_toolchain_compatibility(self):
        # Compatible pair
        res_ok = self.engine.evaluate_compatibility("8.7.0", "8.10.2", "1.9.24")
        self.assertTrue(res_ok.compatible)

        # Incompatible pair (AGP 8.7 requires Gradle 8.9+, giving it 8.2)
        res_bad = self.engine.evaluate_compatibility("8.7.0", "8.2.0", "1.9.24")
        self.assertFalse(res_bad.compatible)
        self.assertIn("requires Gradle >=8.9", res_bad.details)


if __name__ == "__main__":
    unittest.main()
