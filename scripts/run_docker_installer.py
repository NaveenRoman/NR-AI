import subprocess
import sys

installer = r"C:\NR-AI\tools\DockerDesktopInstaller.exe"
print(f"[*] Launching Docker Desktop Installer: {installer}")
try:
    res = subprocess.run([installer, "install", "--quiet", "--accept-license", "--backend=wsl-2"], capture_output=True, text=True, timeout=30)
    print(f"[*] Return code: {res.returncode}")
    print(f"[*] Stdout: {res.stdout}")
    print(f"[*] Stderr: {res.stderr}")
except subprocess.TimeoutExpired:
    print("[*] Installer timed out (waiting for UAC / UI prompt).")
