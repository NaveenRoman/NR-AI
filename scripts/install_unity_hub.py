import subprocess
import sys
import urllib.request
from pathlib import Path

tools_dir = Path("C:/NR-AI/tools")
tools_dir.mkdir(parents=True, exist_ok=True)
hub_exe = tools_dir / "UnityHubSetup.exe"

if not hub_exe.exists():
    url = "https://public-cdn.cloud.unity3d.com/hub/prod/UnityHubSetup.exe"
    print(f"[*] Downloading Unity Hub from {url}...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp, open(str(hub_exe), "wb") as f:
        f.write(resp.read())
    print(f"[+] Unity Hub downloaded: {hub_exe.stat().st_size} bytes")

print("[*] Attempting silent installation of Unity Hub: /S ...")
res = subprocess.run([str(hub_exe), "/S"], capture_output=True, text=True, timeout=30)
print(f"[*] Return code: {res.returncode}")
print(f"[*] Stdout: {res.stdout}")
print(f"[*] Stderr: {res.stderr}")
