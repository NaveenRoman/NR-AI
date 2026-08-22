import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

TOOLS_DIR = Path("C:/NR-AI/tools").resolve()
TOOLS_DIR.mkdir(parents=True, exist_ok=True)


def download_and_extract(url: str, target_dir: Path, name: str) -> bool:
    zip_path = TOOLS_DIR / f"{name}.zip"
    print(f"[*] Downloading {name} from {url}...")
    try:
        urllib.request.urlretrieve(url, str(zip_path))
        print(f"[*] Extracting {name} to {target_dir}...")
        with zipfile.ZipFile(str(zip_path), "r") as zip_ref:
            zip_ref.extractall(str(TOOLS_DIR))
        if zip_path.exists():
            zip_path.unlink()
        print(f"[+] {name} successfully installed at {target_dir}!")
        return True
    except Exception as e:
        print(f"[-] Failed to install {name}: {e}")
        return False


def install_maven():
    mvn_dir = list(TOOLS_DIR.glob("apache-maven-*"))
    if mvn_dir and (mvn_dir[0] / "bin" / "mvn.cmd").exists():
        print(f"[+] Maven already present at {mvn_dir[0]}")
        return mvn_dir[0] / "bin" / "mvn.cmd"
    url = "https://archive.apache.org/dist/maven/maven-3/3.9.6/binaries/apache-maven-3.9.6-bin.zip"
    if download_and_extract(url, TOOLS_DIR, "maven"):
        mvn_dir = list(TOOLS_DIR.glob("apache-maven-*"))
        if mvn_dir:
            return mvn_dir[0] / "bin" / "mvn.cmd"
    return None


def install_gradle():
    gradle_dir = list(TOOLS_DIR.glob("gradle-*"))
    if gradle_dir and (gradle_dir[0] / "bin" / "gradle.bat").exists():
        print(f"[+] Gradle already present at {gradle_dir[0]}")
        return gradle_dir[0] / "bin" / "gradle.bat"
    url = "https://services.gradle.org/distributions/gradle-8.5-bin.zip"
    if download_and_extract(url, TOOLS_DIR, "gradle"):
        gradle_dir = list(TOOLS_DIR.glob("gradle-*"))
        if gradle_dir:
            return gradle_dir[0] / "bin" / "gradle.bat"
    return None


if __name__ == "__main__":
    print("=== NR-AI AUTOMATED TOOLCHAIN INSTALLER ===")
    mvn_bin = install_maven()
    if mvn_bin:
        print(f"Maven Executable: {mvn_bin}")
        res = subprocess.run([str(mvn_bin), "-v"], capture_output=True, text=True, shell=True)
        print(res.stdout)

    gradle_bin = install_gradle()
    if gradle_bin:
        print(f"Gradle Executable: {gradle_bin}")
        res = subprocess.run([str(gradle_bin), "-v"], capture_output=True, text=True, shell=True)
        print(res.stdout)
