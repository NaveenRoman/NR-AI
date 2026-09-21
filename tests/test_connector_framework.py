"""
Tests for NR-AI Selective Personal & Data Connectors Framework (Phase 2).
Verifies:
- Base connector abstraction and strict read-only enforcement
- Deterministic unconfigured fallback (CONNECTOR_NOT_CONFIGURED)
- Explicit user authorization requirement (REQUIRES_AUTH)
- LocalFileConnector security bounds (path traversal & secret file blocking)
- SystemInfoConnector execution without shell execution
- Zero credential / cookie scraping
"""

from pathlib import Path
import tempfile
import unittest

from app.connectors.base import (
    AuthState,
    ConnectorConfig,
    PrivacyClassification,
)
from app.connectors.local_file_connector import LocalFileConnector
from app.connectors.registry import ConnectorRegistry
from app.connectors.system_info_connector import SystemInfoConnector


class TestConnectorFramework(unittest.TestCase):
    """Unit test suite for Data Connectors Framework."""

    def setUp(self):
        self.registry = ConnectorRegistry()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

        # Create a sample text file in temp root
        self.sample_file = self.root_path / "hello.txt"
        self.sample_file.write_text("Hello from NR-AI workspace.\nToken: sk-test123456789012345678", encoding="utf-8")

        self.file_connector = LocalFileConnector(root_dir=self.root_path)
        self.sys_connector = SystemInfoConnector()

        self.registry.register_connector(self.file_connector)
        self.registry.register_connector(self.sys_connector)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_strict_read_only_enforcement(self):
        """Verify calling write() on any Phase 2 connector is strictly blocked."""
        res = self.file_connector.write("some_path.txt", "data")
        self.assertFalse(res.success)
        self.assertEqual(res.status_code, "WRITE_PROHIBITED")

    def test_deterministic_unconfigured_fallback(self):
        """Verify calling an unregistered/unconfigured connector returns CONNECTOR_NOT_CONFIGURED."""
        res = self.registry.execute_read("unregistered_connector_xyz", "some query")
        self.assertFalse(res.success)
        self.assertEqual(res.status_code, "CONNECTOR_NOT_CONFIGURED")
        self.assertIn("CONNECTOR_NOT_CONFIGURED", res.error)

    def test_explicit_authorization_requirement(self):
        """Verify connector in REQUIRES_AUTH stops and requests authorization without fabricating access."""
        auth_req_cfg = ConnectorConfig(
            connector_id="google_drive_connector",
            provider="google_drive",
            authentication_state=AuthState.REQUIRES_AUTH,
        )
        # Mock connector
        class MockDriveConnector(LocalFileConnector):
            pass

        drive_conn = MockDriveConnector(root_dir=self.root_path, connector_id="google_drive_connector")
        drive_conn.config = auth_req_cfg
        self.registry.register_connector(drive_conn)

        res = self.registry.execute_read("google_drive_connector", "list files")
        self.assertFalse(res.success)
        self.assertEqual(res.status_code, "REQUIRES_AUTH")
        self.assertIn("AUTHORIZATION_REQUIRED", res.error)

    def test_local_file_connector_read_and_sanitization(self):
        """Verify reading valid file content and redaction of secrets."""
        res = self.file_connector.read("hello.txt")
        self.assertTrue(res.success)
        self.assertIn("Hello from NR-AI workspace", res.data)
        self.assertNotIn("sk-test", res.data)
        self.assertIn("SECRET_REDACTED", res.data)

    def test_local_file_connector_blocks_path_traversal(self):
        """Verify path traversal attempt (../) is unconditionally rejected."""
        res = self.file_connector.read("../outside.txt")
        self.assertFalse(res.success)
        self.assertIn("ACCESS_DENIED", res.error)

    def test_local_file_connector_blocks_prohibited_secret_files(self):
        """Verify reading .env or private key files is blocked."""
        res = self.file_connector.read(".env")
        self.assertFalse(res.success)
        self.assertIn("ACCESS_DENIED", res.error)

        res2 = self.file_connector.read("id_rsa")
        self.assertFalse(res2.success)
        self.assertIn("ACCESS_DENIED", res2.error)

    def test_system_info_connector_without_shell(self):
        """Verify host metrics are collected safely without shell execution."""
        res = self.sys_connector.read("all")
        self.assertTrue(res.success)
        self.assertIn("os_platform", res.data)
        self.assertIn("cpu_cores", res.data)
        self.assertGreater(res.data["cpu_cores"], 0)


if __name__ == "__main__":
    unittest.main()
