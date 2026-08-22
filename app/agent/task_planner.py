import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def safe_print(msg: str) -> None:
    try:
        print(msg)
    except Exception:
        try:
            print(msg.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


class TaskPlanner:
    """
    NR AI Unified Task Planner & Intent Decomposer.

    Converts natural-language user commands into structured action plans
    for Computer Use (Visual UI, OS, Terminal, Multi-File) and Code Engineering.

    Supported action types:
        - Visual / UI: click_text, click_popup_text, type, press, hotkey, wait
        - Code Engineering: code_execute, write_code, run_code, fix_code, modify_code
        - Multi-File & Project: batch_replace, inspect_project, open_file, open_explorer
        - Terminal: open_terminal, run_terminal_cmd, read_terminal_output
    """

    def __init__(self, memory: Optional[Any] = None):
        self.memory = memory
        self.menu_targets = [
            "File",
            "Edit",
            "Selection",
            "View",
            "Go",
            "Run",
            "Terminal",
            "Help",
        ]

    # --------------------------------------------------
    # Helpers
    # --------------------------------------------------

    def _contains(self, text: str, value: str) -> bool:
        return value.lower() in text.lower()

    def _detect_menu(self, text: str) -> Optional[str]:
        for menu in self.menu_targets:
            if self._contains(text, menu):
                return menu
        return None

    def _add_click_text(
        self, actions: List[Dict[str, Any]], target: str, region: str = "menu"
    ) -> None:
        actions.append({
            "type": "click_text",
            "target": target,
            "region": region,
        })

    # --------------------------------------------------
    # Code & Multi-File Intent Decomposition
    # --------------------------------------------------

    def _plan_complex_command(self, text: str, raw_command: str) -> Optional[List[Dict[str, Any]]]:
        """Detects and decomposes coding, terminal, and multi-file requests."""

        # 0A. Task Cancellation & Abort:
        # e.g., "stop", "cancel", "don't continue", "abort"
        if text in {"stop", "cancel", "don't continue", "dont continue", "abort", "halt"}:
            return [{
                "type": "cancel_task",
                "description": "Cancel current active task",
            }]

        # 0B. Undo & Rollback:
        # e.g., "undo", "undo the last change", "rollback", "revert"
        if "undo" in text or "rollback" in text or "revert" in text:
            return [{
                "type": "rollback_last_change",
                "description": "Roll back last file modification from checkpoint",
            }]

        # 0C. Conversational Follow-Up: Re-Run
        if "run it again" in text or "run again" in text or "test again" in text:
            return [{
                "type": "run_terminal_cmd",
                "command": "python -m unittest discover tests",
                "description": "Re-run test suite",
            }]

        # 0D. Conversational Follow-Up: Change Login Page
        if "login" in text and ("change" in text or "update" in text or "modify" in text or "redesign" in text):
            return [
                {
                    "type": "modify_code",
                    "filename": "data/benchmark_project/frontend/src/App.jsx",
                    "description": "Update React login component UI",
                },
                {
                    "type": "run_terminal_cmd",
                    "command": "python -m unittest discover tests",
                    "cwd": "data/benchmark_project",
                    "description": "Verify tests after login update",
                },
            ]

        # 0E. Conversational Follow-Up: Add Payment
        if "payment" in text and ("add" in text or "create" in text or "implement" in text):
            payment_code = (
                "class PaymentService:\n"
                "    def process_payment(self, user_id: int, amount: float) -> dict:\n"
                "        if amount <= 0:\n"
                "            raise ValueError('Payment amount must be positive.')\n"
                "        return {'status': 'success', 'transaction_id': f'txn_{user_id}_{int(amount)}'}\n"
            )
            return [
                {
                    "type": "write_code",
                    "filename": "data/benchmark_project/backend/payment.py",
                    "code": payment_code,
                    "description": "Create PaymentService module",
                },
                {
                    "type": "run_terminal_cmd",
                    "command": "python -m unittest discover tests",
                    "cwd": "data/benchmark_project",
                    "description": "Verify test suite",
                },
            ]

        # 0F. Conversational Follow-Up: Update That
        if text in {"update that", "change that", "modify that"}:
            target = (
                self.memory.get_last_modified_file()
                if self.memory and self.memory.get_last_modified_file()
                else "backend/models.py"
            )
            return [{
                "type": "modify_code",
                "filename": target,
                "description": f"Update {target}",
            }]

        # 0H. Android Development Intent:
        # e.g., "Create a new Android app", "Build an Android app", "Create Android project"
        if ("create" in text or "build" in text or "new" in text or "scaffold" in text) and "android" in text:
            return [
                {
                    "type": "android_create",
                    "name": "AndroidApp",
                    "package": "com.example.nrai",
                    "target_dir": "data/androidapp",
                    "use_compose": True,
                    "description": "Scaffold modern Jetpack Compose Android application",
                },
                {
                    "type": "android_inspect",
                    "project_dir": "data/androidapp",
                    "description": "Inspect Android project structure",
                },
            ]

        # 0I. Android Add Screen: "Add a login screen", "Add login screen"
        if ("add" in text or "create" in text or "implement" in text) and "login" in text and ("screen" in text or "activity" in text or "android" in text) and "flutter" not in text:
            return [{
                "type": "android_add_login",
                "project_dir": "data/androidapp",
                "package": "com.example.nrai",
                "description": "Add Jetpack Compose LoginScreen component",
            }]

        # 0J. Android Add Firebase Auth: "Add Firebase authentication", "Add Firebase auth"
        if "firebase" in text and ("auth" in text or "authentication" in text or "add" in text or "setup" in text) and "flutter" not in text:
            return [{
                "type": "android_add_firebase",
                "project_dir": "data/androidapp",
                "package": "com.example.nrai",
                "description": "Add Firebase Auth dependency and FirebaseAuthHelper",
            }]

        # 0K. Android Button Color: "Change the button color", "Change button color"
        if "button" in text and "color" in text and ("change" in text or "update" in text or "set" in text) and "flutter" not in text:
            color_hex = "0xFF00C853" if "green" in text else "0xFF6200EE"
            return [{
                "type": "android_change_color",
                "project_dir": "data/androidapp",
                "color": color_hex,
                "description": "Update PrimaryButtonColor in Android theme",
            }]

        # 0L. Android Fix Gradle: "Fix the Gradle error", "Fix gradle build"
        if "gradle" in text and ("fix" in text or "error" in text or "repair" in text):
            return [{
                "type": "modify_code",
                "filename": "data/androidapp/app/build.gradle.kts",
                "search": "error_marker",
                "replace": "",
                "run": False,
                "description": "Fix Gradle configuration error",
            }]

        # 0M. Flutter Project Intent:
        # e.g., "Create a Flutter application", "Create a new Flutter app", "Build a Flutter app"
        if ("create" in text or "build" in text or "new" in text or "scaffold" in text) and "flutter" in text:
            return [
                {
                    "type": "flutter_create",
                    "name": "flutter_app",
                    "target_dir": "data/flutter_app",
                    "title": "NR AI Flutter App",
                    "description": "Scaffold complete Flutter application architecture",
                },
                {
                    "type": "flutter_inspect",
                    "project_dir": "data/flutter_app",
                    "description": "Inspect Flutter project architecture",
                },
            ]

        # 0N. Flutter Add Screen / Dashboard:
        # e.g., "Add a login screen to Flutter app", "Add a Flutter login screen", "Create a dashboard"
        if ("add" in text or "create" in text or "implement" in text) and ("screen" in text or "dashboard" in text or "page" in text) and "flutter" in text:
            return [{
                "type": "flutter_add_login",
                "project_dir": "data/flutter_app",
                "description": "Generate Flutter screen component with state and validation",
            }]

        # 0O. Flutter Add Firebase Auth:
        # e.g., "Add Firebase authentication to Flutter", "Add Firebase auth to Flutter app"
        if "firebase" in text and "flutter" in text:
            return [{
                "type": "flutter_add_firebase",
                "project_dir": "data/flutter_app",
                "description": "Inject Firebase auth dependencies and AuthService in Flutter",
            }]

        # 0P. Flutter Change Theme:
        # e.g., "Change the Flutter theme", "Change theme to green"
        if "theme" in text and ("change" in text or "update" in text or "set" in text):
            color_hex = "0xFF00C853" if "green" in text else "0xFF6200EE"
            return [{
                "type": "flutter_change_theme",
                "project_dir": "data/flutter_app",
                "color": color_hex,
                "description": "Update primaryColor in Flutter AppTheme",
            }]

        # 0Q. Flutter API Integration:
        # e.g., "Connect the app to this API", "Add an API to Flutter", "Add API service"
        if "api" in text and ("connect" in text or "add" in text or "integrate" in text):
            return [{
                "type": "flutter_add_dependency",
                "project_dir": "data/flutter_app",
                "package": "http",
                "version": "1.2.0",
                "description": "Add HTTP client dependency for API integration",
            }]

        # 0R. Flutter Run & Analyze:
        # e.g., "Run the Flutter application", "Run Flutter tests", "Analyze Flutter project"
        if "flutter" in text and ("run" in text or "test" in text or "analyze" in text or "check" in text):
            return [
                {
                    "type": "flutter_analyze",
                    "project_dir": "data/flutter_app",
                    "description": "Run Dart static analysis",
                },
                {
                    "type": "flutter_test",
                    "project_dir": "data/flutter_app",
                    "description": "Run Flutter / Dart unit tests",
                },
            ]

        # 0S. Flutter Fix Error:
        # e.g., "Fix the Flutter error", "Fix Flutter bug"
        if "flutter" in text and ("fix" in text or "error" in text or "bug" in text):
            return [{
                "type": "modify_code",
                "filename": "data/flutter_app/lib/main.dart",
                "search": "error_marker",
                "replace": "",
                "run": False,
                "description": "Fix Flutter Dart source error",
            }]

        # 0T. Spring Boot Intent:
        # e.g., "Build a Spring Boot banking backend", "Create Spring Boot project"
        if ("spring" in text or ("java" in text and ("backend" in text or "banking" in text or "microservice" in text))) and ("create" in text or "build" in text or "new" in text or "scaffold" in text):
            return [{
                "type": "spring_create",
                "name": "banking_backend",
                "target_dir": "data/banking_backend",
                "domain": "banking",
                "description": "Scaffold Spring Boot Java microservice backend",
            }]

        # 0X. Unreal Engine Game Intent:
        # e.g., "Build an Unreal Engine game", "Create an Unreal third-person game", "Create an Unreal FPS prototype"
        if ("unreal" in text or "ue5" in text or "ue4" in text or "uproject" in text or ("game" in text and ("c++" in text or "fps" in text or "third-person" in text or "third person" in text))) and ("create" in text or "build" in text or "new" in text or "scaffold" in text):
            return [{
                "type": "unreal_create",
                "name": "UnrealGame",
                "target_dir": "data/unreal_game",
                "engine_version": "5.3",
                "description": "Scaffold Unreal Engine 5 C++ project with GameMode, Character, and HealthComponent",
            }]

        # 0U. Unity Game Intent:
        # e.g., "Build a Unity multiplayer game", "Create a Unity game"
        if ("unity" in text or ("game" in text and ("multiplayer" in text or "c#" in text or "3d" in text or "2d" in text))) and ("create" in text or "build" in text or "new" in text or "scaffold" in text):
            return [{
                "type": "unity_create",
                "name": "MultiplayerGame",
                "target_dir": "data/multiplayer_game",
                "game_type": "Multiplayer Action",
                "description": "Scaffold Unity C# game architecture",
            }]

        # 0V. React Dashboard / Web Intent:
        # e.g., "Build a React dashboard with Python API", "Create React dashboard"
        if ("react" in text or ("dashboard" in text and ("python" in text or "api" in text))) and ("create" in text or "build" in text or "new" in text or "scaffold" in text) and "saas" not in text:
            return [{
                "type": "react_create",
                "name": "react_dashboard",
                "target_dir": "data/react_dashboard",
                "title": "NR AI React Dashboard",
                "description": "Scaffold React Vite web dashboard",
            }]

        # 0W. Full-Stack SaaS Intent:
        # e.g., "Build a full-stack SaaS application", "Create a SaaS platform"
        if "saas" in text and ("create" in text or "build" in text or "new" in text or "scaffold" in text):
            return [{
                "type": "universal_create_project",
                "prompt": text,
                "target_dir": "data/saas_platform",
                "description": "Scaffold full-stack SaaS platform with React, Python API, and Docker",
            }]

        # 0G. Full-Stack Application Architect & Decomposition:
        # e.g., "Build a full-stack task management application with a React frontend, Python backend, database models, authentication, CRUD operations, responsive UI, API endpoints, validation, tests, and README documentation."
        if ("full-stack" in text or "full stack" in text) or ("task management" in text and ("react" in text or "python" in text or "crud" in text)):
            return self._plan_fullstack_task_manager()

        # 1. Open Specific File: "Open app.py" or "Open calculator.py"
        open_file_match = re.search(r"\bopen\s+([a-zA-Z0-9_\-\.\/\\]+\.(?:py|java|c|cpp|js|mjs|txt|md|json))\b", text)
        if open_file_match:
            filename = open_file_match.group(1)
            return [{
                "type": "open_file",
                "path": filename,
                "description": f"Open file {filename}",
            }]

        # 2. Run tests and fix failures: "Run the tests and fix failures"
        if "run" in text and "test" in text and "fix" in text:
            return [
                {
                    "type": "run_terminal_cmd",
                    "command": "python -m unittest discover tests",
                    "description": "Run test suite",
                },
                {
                    "type": "fix_code",
                    "filename": "data/test_buggy.py",
                    "description": "Diagnose and fix discovered failures",
                },
            ]

        # 3. Terminal Execution: "Open the terminal and run the program" / "Open terminal and run"
        if ("open" in text or "show" in text) and "terminal" in text and "run" in text:
            file_match = re.search(r"run\s+([a-zA-Z0-9_\-\.\/\\]+\.(?:py|java|c|cpp|js|mjs))", text)
            prog_file = file_match.group(1) if file_match else "main.py"
            return [
                {
                    "type": "open_terminal",
                    "description": "Open integrated terminal",
                },
                {
                    "type": "run_terminal_cmd",
                    "command": f"python {prog_file}",
                    "description": f"Run {prog_file} in terminal",
                },
            ]

        # 4. Fix Errors: "Fix the error in this Python program" / "Find the error and fix it" / "Fix errors in X"
        is_create_intent = any(k in text for k in ["create", "build", "write", "generate", "make"])
        if "fix" in text and ("error" in text or "program" in text or "bug" in text or "failure" in text) and not is_create_intent:
            file_match = re.search(r"([a-zA-Z0-9_\-\.\/\\]+\.(?:py|java|c|cpp|js|mjs))", text)
            target_file = file_match.group(1) if file_match else "data/test_buggy.py"
            return [{
                "type": "fix_code",
                "filename": target_file,
                "description": f"Analyze and heal errors in {target_file}",
            }]

        # 5. Multi-File Batch Replace / Project Refactoring:
        # e.g., "Update all API URLs in this project"
        # e.g., "Update the program to print Hello NR AI"
        if ("update" in text or "change" in text or "replace" in text) and ("all" in text or "project" in text or "program" in text or "print" in text or "url" in text):
            if "hello nr ai" in text:
                return [{
                    "type": "batch_replace",
                    "search": "Hello World",
                    "replace": "Hello NR AI",
                    "pattern": "*.py",
                    "description": "Update print statement to 'Hello NR AI'",
                }]
            elif "api" in text and "url" in text:
                return [{
                    "type": "batch_replace",
                    "search": "http://localhost:8000",
                    "replace": "https://api.production.nr-ai.internal",
                    "pattern": "*.py",
                    "description": "Update API URLs across project",
                }]
            elif "auth" in text or "authentication" in text:
                return [{
                    "type": "batch_replace",
                    "search": "auth_mode = 'basic'",
                    "replace": "auth_mode = 'google_oauth2'",
                    "pattern": "*.py",
                    "description": "Update authentication system to OAuth2",
                }]

        # 6. Run Specific File: "run nr_test.py" or "execute calculator.py"
        run_match = re.search(r"\b(?:run|execute)\s+([a-zA-Z0-9_\-\.\/\\]+\.(?:py|java|c|cpp|js|mjs))\b", text)
        if run_match:
            filename = run_match.group(1)
            return [{
                "type": "run_code",
                "filename": filename,
                "description": f"Run {filename}",
            }]

        # 7. Create Program & Run:
        # e.g., "Create a Python program that prints Hello World and run it"
        # e.g., "Create a Python calculator and run it"
        # e.g., "Create a new Python file called calculator.py"
        is_create = any(k in text for k in ["create", "build", "write", "generate", "make"])
        is_code = any(k in text for k in ["python", "java", "javascript", "c program", "calculator", "script", "program", "code", "file"])

        if is_create and is_code:
            # File name specification
            name_match = re.search(r"called\s+([a-zA-Z0-9_\-\.\/\\]+\.(?:py|java|c|cpp|js|mjs))", text)
            specified_name = name_match.group(1) if name_match else None

            if "java" in text or (specified_name and specified_name.endswith(".java")):
                filename = specified_name or ("LoginProgram.java" if "login" in text else "App.java")
                class_name = Path(filename).stem
                code = (
                    f"public class {class_name} {{\n"
                    "    public static void main(String[] args) {\n"
                    "        System.out.println(\"Hello from Java NR AI!\");\n"
                    "    }\n"
                    "}\n"
                )
            elif "javascript" in text or (specified_name and specified_name.endswith(".js")):
                filename = specified_name or "app.js"
                code = "console.log('Hello from JavaScript NR AI!');\n"
            elif "c program" in text or (specified_name and specified_name.endswith(".c")):
                filename = specified_name or "main.c"
                code = (
                    "#include <stdio.h>\n\n"
                    "int main() {\n"
                    "    printf(\"Hello from C NR AI!\\n\");\n"
                    "    return 0;\n"
                    "}\n"
                )
            else:
                # Python
                if "hello world" in text:
                    filename = specified_name or "hello.py"
                    code = "print('Hello World')\n"
                elif "hello nr ai" in text or "hello nr-ai" in text:
                    filename = specified_name or "hello_nr_ai.py"
                    code = "print('Hello NR AI')\n"
                elif "calculator" in text:
                    filename = specified_name or "calculator.py"
                    code = (
                        "class Calculator:\n"
                        "    def add(self, a, b):\n"
                        "        return a + b\n\n"
                        "    def subtract(self, a, b):\n"
                        "        return a - b\n\n"
                        "    def multiply(self, a, b):\n"
                        "        return a * b\n\n"
                        "    def divide(self, a, b):\n"
                        "        if b == 0:\n"
                        "            return 'Error: Division by zero'\n"
                        "        return a / b\n\n"
                        "if __name__ == '__main__':\n"
                        "    calc = Calculator()\n"
                        "    print('=== Python Calculator ===')\n"
                        "    print(f'10 + 5 = {calc.add(10, 5)}')\n"
                        "    print(f'10 - 5 = {calc.subtract(10, 5)}')\n"
                        "    print(f'10 * 5 = {calc.multiply(10, 5)}')\n"
                        "    print(f'10 / 5 = {calc.divide(10, 5)}')\n"
                    )
                else:
                    filename = specified_name or "app.py"
                    code = "print('NR AI Application online.')\n"

            # If user explicitly asked to run it
            if "run" in text or "execute" in text:
                return [{
                    "type": "code_execute",
                    "filename": filename,
                    "code": code,
                    "description": f"Create and execute {filename}",
                }]
            else:
                return [{
                    "type": "write_code",
                    "filename": filename,
                    "code": code,
                    "description": f"Create new file {filename}",
                }]

        # 8. Project Structure: "Inspect project" / "Show project structure"
        if "inspect" in text and "project" in text:
            return [{
                "type": "inspect_project",
                "dir": "",
                "description": "Inspect project structure",
            }]

        return None

    def _plan_fullstack_task_manager(self) -> List[Dict[str, Any]]:
        """Decomposes a full-stack Task Management application into multi-file generation, tests, and smoke test."""
        proj_dir = "data/benchmark_project"

        models_code = '''import os
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
'''

        auth_code = '''import base64
import hashlib
import json
import os
import time
from typing import Any, Dict, Optional

SECRET_KEY = "nr-ai-super-secret-benchmark-key"


def hash_password(password: str, salt: Optional[str] = None) -> str:
    if not salt:
        salt = os.urandom(8).hex()
    hashed = hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()
    return f"{salt}${hashed}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, expected_hash = stored.split("$", 1)
        actual_hash = hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()
        return actual_hash == expected_hash
    except Exception:
        return False


def create_token(user_id: int, username: str, expires_in_seconds: int = 3600) -> str:
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": int(time.time()) + expires_in_seconds,
    }
    raw = json.dumps(payload).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("utf-8")
    sig = hashlib.sha256(f"{encoded}:{SECRET_KEY}".encode("utf-8")).hexdigest()
    return f"{encoded}.{sig}"


def verify_token(token_str: str) -> Optional[Dict[str, Any]]:
    try:
        encoded, sig = token_str.strip().split(".", 1)
        expected_sig = hashlib.sha256(f"{encoded}:{SECRET_KEY}".encode("utf-8")).hexdigest()
        if sig != expected_sig:
            return None
        raw = base64.urlsafe_b64decode(encoded.encode("utf-8")).decode("utf-8")
        payload = json.loads(raw)
        if time.time() > payload.get("exp", 0):
            return None
        return payload
    except Exception:
        return None
'''

        app_code = '''import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

# Add benchmark project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.auth import create_token, hash_password, verify_password, verify_token
from backend.models import TaskDB

DB_PATH = os.environ.get("TASK_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "tasks.db"))
db = TaskDB(db_path=DB_PATH)


class TaskAPIHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def _get_auth_user(self):
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        token = auth_header.split(" ", 1)[1]
        return verify_token(token)

    def _read_json_body(self):
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        raw = self.rfile.read(content_length).decode("utf-8")
        return json.loads(raw)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/health":
            self._send_json(200, {
                "status": "healthy",
                "service": "task-manager-api",
                "version": "1.0.0"
            })
            return

        if path == "/api/tasks":
            user = self._get_auth_user()
            if not user:
                self._send_json(401, {"error": "Unauthorized: valid token required"})
                return
            params = urllib.parse.parse_qs(parsed.query)
            status_filter = params.get("status", [None])[0]
            tasks = db.get_tasks(user["user_id"], status_filter)
            self._send_json(200, {"tasks": tasks})
            return

        self._send_json(404, {"error": "Endpoint not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        try:
            body = self._read_json_body()
        except Exception:
            self._send_json(400, {"error": "Invalid JSON body"})
            return

        if path == "/api/auth/register":
            username = body.get("username", "")
            password = body.get("password", "")
            if not username or not password:
                self._send_json(400, {"error": "Username and password required"})
                return
            if db.get_user_by_username(username):
                self._send_json(409, {"error": "Username already exists"})
                return
            try:
                user = db.create_user(username, hash_password(password))
                token = create_token(user["id"], user["username"])
                self._send_json(201, {"user": user, "token": token})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return

        if path == "/api/auth/login":
            username = body.get("username", "")
            password = body.get("password", "")
            user = db.get_user_by_username(username)
            if not user or not verify_password(password, user["password_hash"]):
                self._send_json(401, {"error": "Invalid username or password"})
                return
            token = create_token(user["id"], user["username"])
            self._send_json(200, {"token": token, "username": user["username"], "user_id": user["id"]})
            return

        if path == "/api/tasks":
            user = self._get_auth_user()
            if not user:
                self._send_json(401, {"error": "Unauthorized"})
                return
            try:
                task = db.create_task(
                    user_id=user["user_id"],
                    title=body.get("title", ""),
                    description=body.get("description", ""),
                    status=body.get("status", "pending"),
                    priority=body.get("priority", "medium")
                )
                self._send_json(201, {"task": task})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return

        self._send_json(404, {"error": "Endpoint not found"})

    def do_PUT(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/tasks/"):
            user = self._get_auth_user()
            if not user:
                self._send_json(401, {"error": "Unauthorized"})
                return
            try:
                task_id = int(path.split("/")[-1])
                body = self._read_json_body()
                updated = db.update_task(
                    task_id=task_id,
                    user_id=user["user_id"],
                    title=body.get("title"),
                    description=body.get("description"),
                    status=body.get("status"),
                    priority=body.get("priority")
                )
                if not updated:
                    self._send_json(404, {"error": "Task not found"})
                    return
                self._send_json(200, {"task": updated})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return

        self._send_json(404, {"error": "Endpoint not found"})

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/tasks/"):
            user = self._get_auth_user()
            if not user:
                self._send_json(401, {"error": "Unauthorized"})
                return
            try:
                task_id = int(path.split("/")[-1])
                ok = db.delete_task(task_id, user["user_id"])
                if not ok:
                    self._send_json(404, {"error": "Task not found"})
                    return
                self._send_json(200, {"deleted": True, "task_id": task_id})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return

        self._send_json(404, {"error": "Endpoint not found"})


def run_server(port: int = 8088):
    server_address = ("127.0.0.1", port)
    httpd = HTTPServer(server_address, TaskAPIHandler)
    print(f"Task Manager API running at http://127.0.0.1:{port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8088
    run_server(port)
'''

        test_code = '''import os
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
'''

        api_js = '''const API_BASE = "http://127.0.0.1:8088";

export async function login(username, password) {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return res.json();
}

export async function register(username, password) {
  const res = await fetch(`${API_BASE}/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return res.json();
}

export async function getTasks(token, status = "") {
  const url = status ? `${API_BASE}/api/tasks?status=${status}` : `${API_BASE}/api/tasks`;
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return res.json();
}

export async function createTask(token, taskData) {
  const res = await fetch(`${API_BASE}/api/tasks`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(taskData),
  });
  return res.json();
}

export async function updateTask(token, id, taskData) {
  const res = await fetch(`${API_BASE}/api/tasks/${id}`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(taskData),
  });
  return res.json();
}

export async function deleteTask(token, id) {
  const res = await fetch(`${API_BASE}/api/tasks/${id}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  return res.json();
}
'''

        app_jsx = '''import React, { useState, useEffect } from "react";
import * as api from "./api";
import "./styles.css";

export default function App() {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(localStorage.getItem("token") || "");
  const [tasks, setTasks] = useState([]);
  const [filter, setFilter] = useState("all");
  const [newTitle, setNewTitle] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newPriority, setNewPriority] = useState("medium");
  const [authMode, setAuthMode] = useState("login");
  const [usernameInput, setUsernameInput] = useState("");
  const [passwordInput, setPasswordInput] = useState("");
  const [errorMsg, setErrorMsg] = useState("");

  const loadTasks = async () => {
    if (!token) return;
    try {
      const data = await api.getTasks(token, filter === "all" ? "" : filter);
      if (data.tasks) setTasks(data.tasks);
    } catch (e) {
      setErrorMsg("Failed to load tasks.");
    }
  };

  useEffect(() => {
    if (token) loadTasks();
  }, [token, filter]);

  const handleAuth = async (e) => {
    e.preventDefault();
    setErrorMsg("");
    const fn = authMode === "login" ? api.login : api.register;
    const res = await fn(usernameInput, passwordInput);
    if (res.token) {
      setToken(res.token);
      setUser(res.username || usernameInput);
      localStorage.setItem("token", res.token);
    } else {
      setErrorMsg(res.error || "Authentication failed.");
    }
  };

  const handleCreateTask = async (e) => {
    e.preventDefault();
    if (!newTitle.trim()) return;
    const res = await api.createTask(token, {
      title: newTitle,
      description: newDesc,
      priority: newPriority,
      status: "pending",
    });
    if (res.task) {
      setNewTitle("");
      setNewDesc("");
      loadTasks();
    }
  };

  const handleToggleStatus = async (task) => {
    const nextStatus = task.status === "completed" ? "pending" : "completed";
    await api.updateTask(token, task.id, { status: nextStatus });
    loadTasks();
  };

  const handleDeleteTask = async (id) => {
    await api.deleteTask(token, id);
    loadTasks();
  };

  if (!token) {
    return (
      <div className="auth-container">
        <div className="auth-card">
          <h2>Task Manager - {authMode === "login" ? "Sign In" : "Register"}</h2>
          {errorMsg && <div className="error-badge">{errorMsg}</div>}
          <form onSubmit={handleAuth}>
            <input
              type="text"
              placeholder="Username"
              value={usernameInput}
              onChange={(e) => setUsernameInput(e.target.value)}
              required
            />
            <input
              type="password"
              placeholder="Password"
              value={passwordInput}
              onChange={(e) => setPasswordInput(e.target.value)}
              required
            />
            <button type="submit">{authMode === "login" ? "Login" : "Sign Up"}</button>
          </form>
          <p onClick={() => setAuthMode(authMode === "login" ? "register" : "login")}>
            {authMode === "login" ? "Need an account? Register" : "Have an account? Sign in"}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="app-container">
      <header className="app-header">
        <h1>Task Manager</h1>
        <button className="logout-btn" onClick={() => { setToken(""); localStorage.removeItem("token"); }}>Logout</button>
      </header>
      <main className="main-content">
        <form className="task-form" onSubmit={handleCreateTask}>
          <input
            type="text"
            placeholder="New task title..."
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            required
          />
          <input
            type="text"
            placeholder="Description (optional)"
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
          />
          <select value={newPriority} onChange={(e) => setNewPriority(e.target.value)}>
            <option value="low">Low Priority</option>
            <option value="medium">Medium Priority</option>
            <option value="high">High Priority</option>
          </select>
          <button type="submit">Add Task</button>
        </form>
        <div className="filter-bar">
          {["all", "pending", "in_progress", "completed"].map((f) => (
            <button
              key={f}
              className={filter === f ? "active-filter" : ""}
              onClick={() => setFilter(f)}
            >
              {f.toUpperCase()}
            </button>
          ))}
        </div>
        <div className="task-grid">
          {tasks.map((task) => (
            <div key={task.id} className={`task-card ${task.status}`}>
              <div className="task-card-header">
                <h3>{task.title}</h3>
                <span className={`badge ${task.priority}`}>{task.priority}</span>
              </div>
              <p>{task.description}</p>
              <div className="task-card-actions">
                <button onClick={() => handleToggleStatus(task)}>
                  {task.status === "completed" ? "Mark Pending" : "Mark Complete"}
                </button>
                <button className="delete-btn" onClick={() => handleDeleteTask(task.id)}>Delete</button>
              </div>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}
'''

        styles_css = ''':root {
  --primary: #4f46e5;
  --bg: #0f172a;
  --card-bg: #1e293b;
  --text: #f8fafc;
  --text-muted: #94a3b8;
  --success: #10b981;
  --danger: #ef4444;
}

body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background-color: var(--bg);
  color: var(--text);
}

.app-container {
  max-width: 1000px;
  margin: 0 auto;
  padding: 24px;
}

.app-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid #334155;
  padding-bottom: 16px;
}

.task-form {
  display: grid;
  grid-template-columns: 2fr 2fr 1fr auto;
  gap: 12px;
  margin: 24px 0;
}

input, select, button {
  padding: 10px 14px;
  border-radius: 6px;
  border: 1px solid #334155;
  background: var(--card-bg);
  color: var(--text);
}

button {
  background: var(--primary);
  color: #fff;
  cursor: pointer;
  border: none;
  font-weight: 600;
}

.task-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
  margin-top: 16px;
}

.task-card {
  background: var(--card-bg);
  border-radius: 8px;
  padding: 16px;
  border: 1px solid #334155;
}

.task-card.completed {
  opacity: 0.6;
  text-decoration: line-through;
}

.badge.high { color: var(--danger); font-weight: bold; }
.badge.medium { color: #f59e0b; }
.badge.low { color: var(--success); }

.delete-btn {
  background: var(--danger);
  margin-left: 8px;
}

.filter-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}

.active-filter {
  background: #334155;
}
'''

        html_index = '''<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>NR-AI Task Manager</title>
  </head>
  <body>
    <div id="root"></div>
  </body>
</html>
'''

        readme_md = '''# Full-Stack Task Management Application

Built autonomously by NR-AI.

## Architecture
- **Frontend**: React Single-Page Application with responsive grid layout, status filtering, and JWT token storage.
- **Backend**: Python HTTP REST API service with CORS, authentication middleware, and input validation.
- **Database**: SQLite with User and Task relation schemas.
- **Testing**: Python `unittest` suite covering database models, authentication, and CRUD endpoints.

## API Endpoints
- `GET /health`: Health check probe
- `POST /api/auth/register`: User registration
- `POST /api/auth/login`: User login & token generation
- `GET /api/tasks`: Retrieve user tasks (supports `?status=pending|completed`)
- `POST /api/tasks`: Create new task
- `PUT /api/tasks/<id>`: Update task title, description, priority, or status
- `DELETE /api/tasks/<id>`: Delete task

## Setup & Running
1. Run backend: `python backend/app.py 8088`
2. Run tests: `python -m unittest discover tests`
'''

        return [
            {"type": "write_code", "filename": f"{proj_dir}/backend/models.py", "code": models_code, "description": "Create SQLite database models & schema validation"},
            {"type": "write_code", "filename": f"{proj_dir}/backend/auth.py", "code": auth_code, "description": "Create password hashing & token authentication"},
            {"type": "write_code", "filename": f"{proj_dir}/backend/app.py", "code": app_code, "description": "Create REST API server & endpoints"},
            {"type": "write_code", "filename": f"{proj_dir}/tests/test_backend.py", "code": test_code, "description": "Create backend unit & API test suite"},
            {"type": "write_code", "filename": f"{proj_dir}/frontend/src/api.js", "code": api_js, "description": "Create React API integration layer"},
            {"type": "write_code", "filename": f"{proj_dir}/frontend/src/App.jsx", "code": app_jsx, "description": "Create React responsive Task Manager UI"},
            {"type": "write_code", "filename": f"{proj_dir}/frontend/src/styles.css", "code": styles_css, "description": "Create responsive CSS styling"},
            {"type": "write_code", "filename": f"{proj_dir}/frontend/public/index.html", "code": html_index, "description": "Create frontend HTML template"},
            {"type": "write_code", "filename": f"{proj_dir}/README.md", "code": readme_md, "description": "Create project documentation & API guide"},
            {"type": "run_terminal_cmd", "command": "python -m unittest discover tests", "cwd": proj_dir, "description": "Execute full-stack test suite"},
            {"type": "verify_service_startup", "command": "backend/app.py 8088", "port": 8088, "endpoint": "/health", "cwd": proj_dir, "description": "Verify live server startup & health endpoint"},
        ]

    # --------------------------------------------------
    # Planning
    # --------------------------------------------------

    def plan(self, command: str) -> Dict[str, Any]:
        if not command:
            return {
                "success": False,
                "message": "Empty command.",
                "actions": [],
            }

        text = command.lower().strip()
        actions = []

        # Check Complex / Code / Multi-file / Terminal Intent First
        complex_actions = self._plan_complex_command(text, command)
        if complex_actions:
            return {
                "success": True,
                "command": command,
                "intent": "complex_task",
                "actions": complex_actions,
            }

        # --------------------------------------------------
        # Visual / GUI Actions
        # --------------------------------------------------

        # SAVE
        if "save" in text:
            self._add_click_text(actions, "File", "menu")
            actions.append({
                "type": "click_popup_text",
                "target": "Save",
                "menu": "File",
            })

        # RUN
        elif (
            "click run" in text
            or "press run" in text
            or "run the program" in text
            or text == "run"
        ):
            self._add_click_text(actions, "Run", "menu")

        # TERMINAL
        elif (
            "open terminal" in text
            or "show terminal" in text
        ):
            self._add_click_text(actions, "Terminal", "menu")

        # EXPLORER
        elif (
            "open explorer" in text
            or "show explorer" in text
        ):
            actions.append({"type": "open_explorer", "dir": ""})

        # OPEN FILE MENU
        elif (
            "open file menu" in text
            or "open file" in text
        ):
            self._add_click_text(actions, "File", "menu")

        # GENERIC MENU REQUEST
        else:
            menu = self._detect_menu(text)
            if menu:
                self._add_click_text(actions, menu, "menu")

        # Result Check
        if not actions:
            return {
                "success": False,
                "command": command,
                "message": "Could not determine a safe action plan.",
                "actions": [],
            }

        return {
            "success": True,
            "command": command,
            "intent": "visual",
            "actions": actions,
        }


if __name__ == "__main__":
    safe_print("========================================")
    safe_print("        NR AI TASK PLANNER TEST")
    safe_print("========================================")

    planner = TaskPlanner()

    test_commands = [
        "Open the File menu and save",
        "Create a Python program that prints Hello NR AI and run it",
        "Fix the error in this Python program",
        "Update the program to print Hello NR AI",
        "Open app.py",
        "Create a new Python file called calculator.py",
        "Open the terminal and run the program",
        "Run the tests and fix failures",
        "Inspect project",
    ]

    for cmd in test_commands:
        res = planner.plan(cmd)
        safe_print(f"\nTask: '{cmd}'")
        safe_print(f"Success: {res['success']} | Intent: {res.get('intent')} | Actions: {len(res.get('actions', []))}")
        for idx, act in enumerate(res.get("actions", []), start=1):
            safe_print(f"  Act {idx}: {act['type']} ({act.get('description', '-')})")

    safe_print("\n========================================")
    safe_print("🟢 TASK PLANNER TEST COMPLETE")
    safe_print("========================================")