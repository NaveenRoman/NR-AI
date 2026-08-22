import os
import shutil
import sys
import tempfile
import unittest

# Ensure parent directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.auth import create_token, hash_password, verify_password, verify_token
from backend.models import TaskDB


class TestBackendTaskManagement(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_tasks.db")
        self.db = TaskDB(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_user_creation_and_retrieval(self):
        pwd_hash = hash_password("secret123")
        user = self.db.create_user("alice", pwd_hash)
        self.assertEqual(user["username"], "alice")
        self.assertIsNotNone(user["id"])

        fetched = self.db.get_user_by_username("alice")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["username"], "alice")

    def test_auth_token_lifecycle(self):
        token = create_token(user_id=1, username="bob", expires_in_seconds=60)
        self.assertIsInstance(token, str)
        payload = verify_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["username"], "bob")
        self.assertEqual(payload["user_id"], 1)

    def test_task_crud_operations(self):
        pwd_hash = hash_password("secret123")
        user = self.db.create_user("charlie", pwd_hash)

        # Create
        task = self.db.create_task(
            user_id=user["id"],
            title="Implement Authentication",
            description="Add JWT tokens",
            status="pending",
            priority="high"
        )
        self.assertEqual(task["title"], "Implement Authentication")
        self.assertEqual(task["status"], "pending")

        # Read
        tasks = self.db.get_tasks(user_id=user["id"])
        self.assertEqual(len(tasks), 1)

        # Update
        updated = self.db.update_task(
            task_id=task["id"],
            user_id=user["id"],
            status="completed"
        )
        self.assertEqual(updated["status"], "completed")

        # Delete
        deleted = self.db.delete_task(task_id=task["id"], user_id=user["id"])
        self.assertTrue(deleted)
        self.assertEqual(len(self.db.get_tasks(user_id=user["id"])), 0)

    def test_validation_errors(self):
        with self.assertRaises(ValueError):
            self.db.create_task(user_id=1, title="", status="pending")

        with self.assertRaises(ValueError):
            self.db.create_task(user_id=1, title="Test", status="invalid_status")


if __name__ == "__main__":
    unittest.main()
