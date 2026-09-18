"""
Tests for Droid Phase 1: Dynamic Android Project Registry

Validates:
  1. Successful project registration with metadata and module discovery
  2. Duplicate registration idempotency
  3. Canonical path resolution & traversal prevention (../ rejection)
  4. Invalid project rejection (missing build.gradle / AndroidManifest.xml)
  5. Protected system directory rejection (Windows, Program Files, system root)
  6. Project revocation and suspension lifecycle states
  7. Multi-project isolation
  8. Integration with AndroidSafetyGate
"""

import tempfile
import unittest
from pathlib import Path

from app.agent.android_project_registry import (
    AndroidProjectRecord,
    AndroidProjectRegistry,
    ProjectStatus,
    ProjectType,
)
from app.agent.android_safety import (
    AndroidErrorCode,
    AndroidSafetyError,
    AndroidSafetyGate,
)


class TestDroidPhase1ProjectRegistry(unittest.TestCase):
    """Unit and safety tests for AndroidProjectRegistry."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name).resolve()
        self.registry_path = self.tmp_path / "projects.json"
        self.registry = AndroidProjectRegistry(self.registry_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _create_mock_android_project(self, name: str) -> Path:
        """Helper to create a realistic valid Android project fixture."""
        p = self.tmp_path / name
        p.mkdir(parents=True, exist_ok=True)
        (p / "build.gradle").write_text("// Mock build.gradle", encoding="utf-8")
        (p / "settings.gradle").write_text(f"rootProject.name = '{name}'", encoding="utf-8")
        app_dir = p / "app"
        app_dir.mkdir(parents=True, exist_ok=True)
        (app_dir / "build.gradle").write_text("plugins { id 'com.android.application' }", encoding="utf-8")
        src_dir = app_dir / "src" / "main"
        src_dir.mkdir(parents=True, exist_ok=True)
        manifest_content = f"""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.{name}">
    <application android:label="{name}" />
</manifest>"""
        (src_dir / "AndroidManifest.xml").write_text(manifest_content, encoding="utf-8")
        return p

    def test_01_registration_successful(self):
        """Verify explicit user registration of an Android project."""
        p_path = self._create_mock_android_project("SampleApp")
        record = self.registry.register_project(
            project_path=p_path,
            project_name="Sample App",
            authorized_by_user=True,
        )

        self.assertIsNotNone(record.project_id)
        self.assertEqual(record.project_name, "Sample App")
        self.assertEqual(Path(record.canonical_path).resolve(), p_path.resolve())
        self.assertEqual(record.status, ProjectStatus.ACTIVE.value)
        self.assertTrue(record.authorized_by_user)
        self.assertEqual(record.package_id, "com.example.SampleApp")
        self.assertIn("app", record.modules)

        # Verify disk persistence
        reloaded = AndroidProjectRegistry(self.registry_path)
        fetched = reloaded.get_project(record.project_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.project_name, "Sample App")

    def test_02_duplicate_registration_idempotent(self):
        """Verify re-registering an existing project returns the existing record without duplicating."""
        p_path = self._create_mock_android_project("DupeApp")
        r1 = self.registry.register_project(p_path, project_name="App One")
        r2 = self.registry.register_project(p_path, project_name="App Two")

        self.assertEqual(r1.project_id, r2.project_id)
        all_projs = [p for p in self.registry.list_projects() if p.canonical_path == str(p_path.resolve())]
        self.assertEqual(len(all_projs), 1)

    def test_03_canonical_path_handling(self):
        """Verify non-canonical and relative paths are normalized."""
        p_path = self._create_mock_android_project("NormApp")
        unnormalized = p_path / "app" / ".."
        record = self.registry.register_project(unnormalized)
        self.assertEqual(record.canonical_path, str(p_path.resolve()))

    def test_04_invalid_project_rejection(self):
        """Verify non-Android directory is rejected."""
        empty_dir = self.tmp_path / "NotAnAndroidProject"
        empty_dir.mkdir()

        with self.assertRaises(ValueError) as ctx:
            self.registry.register_project(empty_dir)
        self.assertIn("does not appear to be an android project", str(ctx.exception).lower())

    def test_05_protected_directory_rejection(self):
        """CRITICAL SAFETY: Verify protected system directories cannot be registered."""
        system_dirs = [r"C:\Windows", r"C:\Program Files", r"C:\\"]
        for sys_dir in system_dirs:
            p = Path(sys_dir)
            if p.exists():
                with self.assertRaises(ValueError) as ctx:
                    self.registry.register_project(p)
                self.assertIn("cannot register root or system directory", str(ctx.exception).lower())

    def test_06_revoke_and_suspend(self):
        """Verify projects can be suspended and revoked, blocking file authorization."""
        p_path = self._create_mock_android_project("LifecycleApp")
        rec = self.registry.register_project(p_path)

        target_file = p_path / "app" / "build.gradle"
        auth, _, _ = self.registry.is_path_authorized(target_file)
        self.assertTrue(auth)

        # Suspend
        self.registry.suspend_project(rec.project_id, reason="Maintenance")
        auth, reason, _ = self.registry.is_path_authorized(target_file)
        self.assertFalse(auth)
        self.assertIn("SUSPENDED", reason)

        # Revoke
        self.registry.revoke_project(rec.project_id, reason="Security audit")
        auth, reason, _ = self.registry.is_path_authorized(target_file)
        self.assertFalse(auth)
        self.assertIn("REVOKED", reason)

    def test_07_multi_project_isolation(self):
        """Verify files from Project A cannot be authorized under Project B."""
        p_a = self._create_mock_android_project("ProjectA")
        p_b = self._create_mock_android_project("ProjectB")

        rec_a = self.registry.register_project(p_a)
        rec_b = self.registry.register_project(p_b)

        file_a = p_a / "app" / "build.gradle"
        file_b = p_b / "app" / "build.gradle"

        auth_a, _, matched_a = self.registry.is_path_authorized(file_a)
        auth_b, _, matched_b = self.registry.is_path_authorized(file_b)

        self.assertTrue(auth_a)
        self.assertTrue(auth_b)
        self.assertEqual(matched_a.project_id, rec_a.project_id)
        self.assertEqual(matched_b.project_id, rec_b.project_id)
        self.assertNotEqual(matched_a.project_id, matched_b.project_id)

    def test_08_path_traversal_prevention(self):
        """Verify directory traversal attempts are rejected."""
        p_a = self._create_mock_android_project("ProjectTraversal")
        self.registry.register_project(p_a)

        traversal_file = p_a / ".." / "outside_file.kt"
        auth, _, _ = self.registry.is_path_authorized(traversal_file)
        self.assertFalse(auth)

    def test_09_safety_gate_integration(self):
        """Verify AndroidSafetyGate honors dynamically registered projects."""
        p_path = self._create_mock_android_project("GateApp")
        rec = self.registry.register_project(p_path)

        gate = AndroidSafetyGate(project_registry=self.registry)

        # Validate project path succeeds
        validated = gate.validate_project_path(p_path)
        self.assertEqual(validated, p_path.resolve())

        # Validate editable file succeeds
        file_path = p_path / "app" / "build.gradle"
        val_file = gate.validate_editable_file(file_path)
        self.assertEqual(val_file, file_path.resolve())

        # Revoking in registry causes safety gate to reject immediately
        self.registry.revoke_project(rec.project_id)
        with self.assertRaises(AndroidSafetyError) as ctx:
            gate.validate_project_path(p_path)
        self.assertEqual(ctx.exception.code, AndroidErrorCode.PROJECT_NOT_AUTHORIZED)


if __name__ == "__main__":
    unittest.main()
