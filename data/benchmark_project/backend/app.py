import json
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
