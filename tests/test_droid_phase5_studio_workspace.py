import unittest
import tempfile
import shutil
from pathlib import Path
import xml.etree.ElementTree as ET

from app.agent.android_studio_workspace import (
    AndroidStudioWorkspaceEngine,
    StudioWorkspaceSnapshot,
    ProGuardRule,
)


class TestDroidPhase5StudioWorkspace(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.engine = AndroidStudioWorkspaceEngine()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_parse_gradle_xml(self):
        idea_dir = self.temp_dir / ".idea"
        idea_dir.mkdir(parents=True)
        gradle_xml = idea_dir / "gradle.xml"
        gradle_xml.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<project version="4">
  <component name="GradleSettings">
    <option name="linkedExternalProjectsSettings">
      <GradleProjectSettings>
        <option name="externalProjectPath" value="$PROJECT_DIR$/app" />
        <option name="gradleJvm" value="jbr-21" />
        <option name="gradleHome" value="C:/gradle-8.10" />
      </GradleProjectSettings>
    </option>
  </component>
</project>
""", encoding="utf-8")

        jdk, home, modules = self.engine.parse_gradle_xml(idea_dir)
        self.assertEqual(jdk, "jbr-21")
        self.assertEqual(home, "C:/gradle-8.10")
        self.assertIn("app", modules)

    def test_parse_run_configurations(self):
        idea_dir = self.temp_dir / ".idea"
        rc_dir = idea_dir / "runConfigurations"
        rc_dir.mkdir(parents=True)
        (rc_dir / "app.xml").write_text("""<component name="ProjectRunConfigurationManager">
  <configuration default="false" name="app" type="AndroidRunConfigurationType" factoryName="Android App">
    <module name="nr_android_test.app.main" />
  </configuration>
</component>""", encoding="utf-8")

        configs = self.engine.parse_run_configurations(idea_dir)
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["name"], "app")
        self.assertEqual(configs[0]["type"], "AndroidRunConfigurationType")

    def test_analyze_proguard_rules(self):
        pg_file = self.temp_dir / "proguard-rules.pro"
        pg_file.write_text("""# Sample ProGuard rules
-keep class com.nrai.test.models.** { *; }
-keepclassmembers class * {
    @com.google.gson.annotations.SerializedName <fields>;
}
-dontwarn okhttp3.**
""", encoding="utf-8")

        rules, warnings, pg_files = self.engine.analyze_proguard_rules(self.temp_dir)
        self.assertEqual(len(rules), 3)
        self.assertTrue(any(r.directive == "keep" and "models" in r.target_class for r in rules))
        self.assertTrue(any(r.directive == "dontwarn" for r in rules))


if __name__ == "__main__":
    unittest.main()
