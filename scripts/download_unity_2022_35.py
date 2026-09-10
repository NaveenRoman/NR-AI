import os
import sys
import time
import urllib.request
from pathlib import Path

tools_dir = Path("C:/NR-AI/tools")
tools_dir.mkdir(parents=True, exist_ok=True)

editor_url = "https://download.unity3d.com/download_unity/011206c7a712/Windows64EditorInstaller/UnitySetup64-2022.3.35f1.exe"
editor_dest = tools_dir / "UnitySetup64-2022.3.35f1.exe"
editor_expected_len = 3449749008

il2cpp_url = "https://download.unity3d.com/download_unity/011206c7a712/TargetSupportInstaller/UnitySetup-Windows-IL2CPP-Support-for-Editor-2022.3.35f1.exe"
il2cpp_dest = tools_dir / "UnitySetup-Windows-IL2CPP-Support-for-Editor-2022.3.35f1.exe"
il2cpp_expected_len = 101021408


def download_file(url: str, dest: Path, expected_size: int, label: str):
    temp_dest = dest.with_suffix(".tmp")
    if temp_dest.exists():
        temp_dest.unlink()

    print(f"[*] Starting download of {label} ({expected_size / (1024*1024):.1f} MB)...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    
    with urllib.request.urlopen(req, timeout=30) as resp, open(str(temp_dest), "wb") as f:
        total = int(resp.headers.get("content-length", expected_size))
        downloaded = 0
        chunk_size = 16 * 1024 * 1024  # 16MB buffer
        t0 = time.time()
        last_print = t0

        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            now = time.time()
            if now - last_print > 4.0:
                pct = (downloaded / total * 100) if total else 0
                mb = downloaded / (1024 * 1024)
                total_mb = total / (1024 * 1024)
                speed = mb / max(now - t0, 0.1)
                print(f"    [{label}] {mb:.1f} MB / {total_mb:.1f} MB ({pct:.1f}%) @ {speed:.2f} MB/s")
                last_print = now

    if temp_dest.stat().st_size != expected_size:
        raise ValueError(f"Download size mismatch for {label}: got {temp_dest.stat().st_size}, expected {expected_size}")

    if dest.exists():
        dest.unlink()
    temp_dest.rename(dest)
    print(f"[+] {label} downloaded and verified successfully! ({dest.stat().st_size:,} bytes)")


if __name__ == "__main__":
    download_file(editor_url, editor_dest, editor_expected_len, "Unity 2022.3.35f1 Editor")
    download_file(il2cpp_url, il2cpp_dest, il2cpp_expected_len, "Windows IL2CPP Support")
