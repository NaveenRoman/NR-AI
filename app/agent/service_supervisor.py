import collections
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


class ServiceSupervisor:
    """
    NR AI Service Supervisor & Long-Running Process Manager.

    Features:
    - Tracks active background services (servers, daemons, workers).
    - Captures stdout/stderr into ring buffers in real-time.
    - Detects premature exit codes and crashes.
    - Automatic restart capability on service failure.
    - HTTP endpoint health probe polling.
    - Graceful process termination and resource cleanup.
    """

    def __init__(self, workspace: Optional[str] = None):
        self.workspace = Path(workspace or os.getcwd()).resolve()
        self.services: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()

    def start_service(
        self,
        name: str,
        command: Any,
        port: Optional[int] = None,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Starts a background service and tracks its lifecycle."""
        with self.lock:
            if name in self.services and self.services[name]["proc"].poll() is None:
                return {
                    "success": True,
                    "message": f"Service '{name}' is already running.",
                    "pid": self.services[name]["proc"].pid,
                }

            work_dir = Path(cwd or self.workspace).resolve()

            if isinstance(command, str):
                cmd_args = command.split()
            else:
                cmd_args = list(command)

            if cmd_args and cmd_args[0].endswith(".py"):
                cmd_args = [sys.executable, *cmd_args]

            proc_env = os.environ.copy()
            if env:
                proc_env.update(env)

            try:
                proc = subprocess.Popen(
                    cmd_args,
                    cwd=str(work_dir),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=proc_env,
                )

                log_buffer: collections.deque = collections.deque(maxlen=200)

                def stream_reader(pipe, prefix):
                    try:
                        for line in iter(pipe.readline, ""):
                            log_buffer.append(f"{prefix}: {line.strip()}")
                    except Exception:
                        pass

                t_out = threading.Thread(target=stream_reader, args=(proc.stdout, "OUT"), daemon=True)
                t_err = threading.Thread(target=stream_reader, args=(proc.stderr, "ERR"), daemon=True)
                t_out.start()
                t_err.start()

                self.services[name] = {
                    "name": name,
                    "command": command,
                    "cmd_args": cmd_args,
                    "port": port,
                    "cwd": str(work_dir),
                    "env": env,
                    "proc": proc,
                    "logs": log_buffer,
                    "start_time": time.time(),
                    "restart_count": 0,
                }

                # Brief wait to catch immediate startup crashes
                time.sleep(0.3)
                if proc.poll() is not None:
                    return {
                        "success": False,
                        "message": f"Service '{name}' crashed on startup with exit code {proc.returncode}.",
                        "logs": list(log_buffer),
                    }

                return {
                    "success": True,
                    "message": f"Service '{name}' started successfully.",
                    "pid": proc.pid,
                    "port": port,
                }

            except Exception as e:
                return {
                    "success": False,
                    "message": f"Failed to start service '{name}': {e}",
                }

    def probe_health(
        self,
        port: int,
        endpoint: str = "/health",
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """Probes a service HTTP health endpoint."""
        url = f"http://127.0.0.1:{port}{endpoint}"
        start = time.time()

        while time.time() - start < timeout:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "NR-AI-Supervisor"})
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    if resp.getcode() == 200:
                        body = resp.read().decode("utf-8")
                        try:
                            data = json.loads(body)
                        except Exception:
                            data = body
                        return {
                            "success": True,
                            "status_code": 200,
                            "response": data,
                            "endpoint": url,
                        }
            except Exception:
                time.sleep(0.3)

        return {
            "success": False,
            "message": f"Health probe to {url} timed out after {timeout}s.",
            "endpoint": url,
        }

    def restart_service(self, name: str) -> Dict[str, Any]:
        """Restarts a tracked service."""
        with self.lock:
            if name not in self.services:
                return {"success": False, "message": f"Service '{name}' not found."}
            svc = self.services[name]

        self.stop_service(name)
        time.sleep(0.2)
        res = self.start_service(
            name=svc["name"],
            command=svc["command"],
            port=svc.get("port"),
            cwd=svc.get("cwd"),
            env=svc.get("env"),
        )
        if res["success"]:
            with self.lock:
                if name in self.services:
                    self.services[name]["restart_count"] = svc.get("restart_count", 0) + 1
        return res

    def stop_service(self, name: str) -> Dict[str, Any]:
        """Gracefully stops a service."""
        with self.lock:
            if name not in self.services:
                return {"success": False, "message": f"Service '{name}' not found."}
            proc = self.services[name]["proc"]

        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2.0)
            except Exception:
                proc.kill()

        try:
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()
        except Exception:
            pass

        return {"success": True, "message": f"Service '{name}' stopped."}

    def stop_all(self) -> None:
        """Stops all tracked services."""
        for name in list(self.services.keys()):
            self.stop_service(name)

    def get_service_status(self, name: str) -> Dict[str, Any]:
        """Returns the status and recent logs of a service."""
        with self.lock:
            if name not in self.services:
                return {"success": False, "message": f"Service '{name}' not found."}
            svc = self.services[name]
            is_running = svc["proc"].poll() is None
            return {
                "success": True,
                "name": name,
                "running": is_running,
                "pid": svc["proc"].pid if is_running else None,
                "exit_code": svc["proc"].returncode,
                "restart_count": svc.get("restart_count", 0),
                "recent_logs": list(svc["logs"])[-20:],
            }
