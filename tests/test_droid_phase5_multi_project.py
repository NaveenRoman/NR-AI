import unittest
import tempfile
import shutil
from pathlib import Path

from app.agent.android_multi_project import (
    AndroidMultiProjectManager,
    ProjectHealthScore,
)
from app.agent.android_project_registry import AndroidProjectRegistry


class TestDroidPhase5MultiProject(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.reg = AndroidProjectRegistry(registry_file=self.temp_dir / "projects.json")
        self.manager = AndroidMultiProjectManager(registry=self.reg)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_active_project_switching(self):
        # Primary is default
        self.assertEqual(self.manager.active_project_id, "nr_android_test")
        active = self.manager.get_active_project()
        self.assertEqual(active.project_id, "nr_android_test")

    def test_generate_workspace_matrix(self):
        matrix = self.manager.generate_workspace_matrix()
        self.assertGreaterEqual(len(matrix), 1)
        self.assertTrue(any(p.project_id == "nr_android_test" for p in matrix))


if __name__ == "__main__":
    unittest.main()
