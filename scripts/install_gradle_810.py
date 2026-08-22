import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

TOOLS_DIR = Path("C:/NR-AI/tools").resolve()
TOOLS_DIR.mkdir(parents=True, exist_ok=True)


def download_file(url: str, dest: Path):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp, open(str(dest), "wb") as out_file:
        shutil.copyfileobj(resp, out_file)


def install_gradle_8_10():
    gradle_dir = TOOLS_DIR / "gradle-8.10.2"
    if (gradle_dir / "bin" / "gradle.bat").exists():
        print(f"[+] Gradle 8.10.2 already installed at {gradle_dir}")
        return gradle_dir / "bin" / "gradle.bat"

    zip_path = TOOLS_DIR / "gradle-8.10.2-bin.zip"
    url = "https://services.gradle.org/distributions/gradle-8.10.2-bin.zip"
    print(f"[*] Downloading Gradle 8.10.2 from {url}...")
    download_file(url, zip_path)
    print(f"[*] Extracting Gradle 8.10.2 to {TOOLS_DIR}...")
    with zipfile.ZipFile(str(zip_path), "r") as zf:
        zf.extractall(str(TOOLS_DIR))
    if zip_path.exists():
        zip_path.unlink()
    print(f"[+] Gradle 8.10.2 successfully extracted!")
    return gradle_dir / "bin" / "gradle.bat"


if __name__ == "__main__":
    g_bin = install_gradle_8_10()
    if g_bin and g_bin.exists():
        res = subprocess.run([str(g_bin), "-v"], capture_output=True, text=True, shell=True)
        print(res.stdout)
