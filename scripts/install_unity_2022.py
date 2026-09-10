import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

tools_dir = Path("C:/NR-AI/tools")
tools_dir.mkdir(parents=True, exist_ok=True)
installer = tools_dir / "UnitySetup64-2022.3.35f1.exe"
il2cpp_installer = tools_dir / "UnitySetup-Windows-IL2CPP-Support-for-Editor-2022.3.35f1.exe"

editor_url = "https://download.unity3d.com/download_unity/011206c7a712/Windows64EditorInstaller/UnitySetup64-2022.3.35f1.exe"
il2cpp_url = "https://download.unity3d.com/download_unity/011206c7a712/TargetSupportInstaller/UnitySetup-Windows-IL2CPP-Support-for-Editor-2022.3.35f1.exe"


def download_with_progress(url: str, target: Path, label: str):
    if target.exists() and target.stat().st_size > 10_000_000:
        print(f"[+] {label} already downloaded ({target.stat().st_size / (1024*1024):.1f} MB). Skipping download.")
        return

    print(f"[*] Downloading {label} from {url}...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp, open(str(target), "wb") as f:
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        chunk_size = 8 * 1024 * 1024  # 8MB
        t0 = time.time()
        last_log = t0
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            now = time.time()
            if now - last_log > 3.0:
                mb = downloaded / (1024 * 1024)
                pct = (downloaded / total * 100) if total else 0
                speed = mb / max(now - t0, 0.1)
                print(f"   [{label}] {mb:.1f} MB / {total / (1024*1024):.1f} MB ({pct:.1f}%) @ {speed:.2f} MB/s")
                last_log = now

    print(f"[+] {label} download complete: {target.stat().st_size / (1024*1024):.1f} MB")


def install_unity():
    # 1. Download components
    download_with_progress(editor_url, installer, "Unity Editor 2022.3.35f1")
    download_with_progress(il2cpp_url, il2cpp_installer, "Windows IL2CPP Support")

    # 2. Silent install
    target_dir = r"C:\Program Files\Unity\Hub\Editor\2022.3.35f1\Editor"
    print(f"[*] Installing Unity Editor silently to {target_dir}...")
    res = subprocess.run([str(installer), "/S", f"/D={target_dir}"], capture_output=True, text=True)
    print(f"[+] Editor Install Exit Code: {res.returncode}")

    print(f"[*] Installing Windows IL2CPP Support silently to {target_dir}...")
    res_il2cpp = subprocess.run([str(il2cpp_installer), "/S", f"/D={target_dir}"], capture_output=True, text=True)
    print(f"[+] IL2CPP Install Exit Code: {res_il2cpp.returncode}")

    # 3. Verify Unity.exe
    unity_exe = Path(target_dir) / "Unity.exe"
    if unity_exe.exists():
        r_ver = subprocess.run([str(unity_exe), "-version"], capture_output=True, text=True)
        print(f"[+] Unity.exe Version: {r_ver.stdout.strip()}")
    else:
        print(f"[-] Unity.exe not found at {unity_exe}")


if __name__ == "__main__":
    install_unity()
