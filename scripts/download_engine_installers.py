import os
import shutil
import urllib.request
from pathlib import Path

TOOLS_DIR = Path("C:/NR-AI/tools").resolve()
TOOLS_DIR.mkdir(parents=True, exist_ok=True)


def download_file(url: str, dest: Path, name: str):
    if dest.exists() and dest.stat().st_size > 1000000:
        print(f"[+] {name} installer already downloaded ({dest.stat().st_size} bytes)")
        return
    print(f"[*] Downloading {name} installer from {url}...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp, open(str(dest), "wb") as out_f:
        shutil.copyfileobj(resp, out_f)
    print(f"[+] {name} installer downloaded to {dest} ({dest.stat().st_size} bytes)")


if __name__ == "__main__":
    print("=== DOWNLOADING NATIVE ENGINE INSTALLERS ===")
    unity_installer = TOOLS_DIR / "UnityHubSetup.exe"
    try:
        download_file("https://public-cdn.cloud.unity3d.com/hub/prod/UnityHubSetup.exe", unity_installer, "Unity Hub")
    except Exception as e:
        print(f"[-] Unity Hub download error: {e}")

    docker_installer = TOOLS_DIR / "DockerDesktopInstaller.exe"
    try:
        download_file("https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe", docker_installer, "Docker Desktop")
    except Exception as e:
        print(f"[-] Docker installer download error: {e}")
