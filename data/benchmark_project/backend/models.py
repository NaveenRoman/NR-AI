import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional


class TaskDB:
    def __init__(self, db_path: str = "tasks.db"):
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                priority TEXT NOT NULL DEFAULT 'medium',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            """)
            conn.commit()

    def create_user(self, username: str, password_hash: str) -> Dict[str, Any]:
        if not username or len(username.strip()) < 3:
            raise ValueError("Username must be at least 3 characters.")
        created_at = datetime.utcnow().isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username.strip(), password_hash, created_at)
            )
            user_id = cursor.lastrowid
            conn.commit()
            return {"id": user_id, "username": username.strip(), "created_at": created_at}

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE username = ?", (username.strip(),))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def create_task(self, user_id: int, title: str, description: str = "", status: str = "pending", priority: str = "medium") -> Dict[str, Any]:
        self.validate_task(title, status, priority)
        now = datetime.utcnow().isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO tasks (user_id, title, description, status, priority, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, title.strip(), description.strip(), status.lower(), priority.lower(), now, now)
            )
            task_id = cursor.lastrowid
            conn.commit()
            return {
                "id": task_id, "user_id": user_id, "title": title.strip(),
                "description": description.strip(), "status": status.lower(),
                "priority": priority.lower(), "created_at": now, "updated_at": now
            }

    def get_tasks(self, user_id: int, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if status_filter:
                cursor.execute(
                    "SELECT * FROM tasks WHERE user_id = ? AND status = ? ORDER BY id DESC",
                    (user_id, status_filter.lower())
                )
            else:
                cursor.execute(
                    "SELECT * FROM tasks WHERE user_id = ? ORDER BY id DESC",
                    (user_id,)
                )
            return [dict(row) for row in cursor.fetchall()]

    def get_task_by_id(self, task_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_task(self, task_id: int, user_id: int, title: Optional[str] = None, description: Optional[str] = None, status: Optional[str] = None, priority: Optional[str] = None) -> Optional[Dict[str, Any]]:
        existing = self.get_task_by_id(task_id, user_id)
        if not existing:
            return None
        new_title = title if title is not None else existing["title"]
        new_desc = description if description is not None else existing["description"]
        new_status = status if status is not None else existing["status"]
        new_priority = priority if priority is not None else existing["priority"]
        self.validate_task(new_title, new_status, new_priority)
        now = datetime.utcnow().isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE tasks SET title = ?, description = ?, status = ?, priority = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (new_title.strip(), new_desc.strip(), new_status.lower(), new_priority.lower(), now, task_id, user_id)
            )
            conn.commit()
            return self.get_task_by_id(task_id, user_id)

    def delete_task(self, task_id: int, user_id: int) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def validate_task(title: str, status: str, priority: str):
        if not title or len(title.strip()) < 1:
            raise ValueError("Task title cannot be empty.")
        if status.lower() not in {"pending", "in_progress", "completed"}:
            raise ValueError(f"Invalid status '{status}'. Must be pending, in_progress, or completed.")
        if priority.lower() not in {"low", "medium", "high"}:
            raise ValueError(f"Invalid priority '{priority}'. Must be low, medium, or high.")
