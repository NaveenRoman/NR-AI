import os
import shutil
import subprocess
import sys
import winreg
from pathlib import Path

print("=== REAL ENVIRONMENT PROBE FOR DOCKER, UNITY, UNREAL, MSVC ===")

# 1. DOCKER
docker_exe = shutil.which("docker") or shutil.which("docker.exe")
docker_paths = [
    r"C:\Program Files\Docker\Docker\resources\bin\docker.exe",
    r"C:\Program Files\Docker\Docker\DockerCli.exe",
]
found_docker = docker_exe or next((p for p in docker_paths if os.path.exists(p)), None)
print(f"Docker Binary: {found_docker}")
if found_docker:
    try:
        r_ver = subprocess.run([found_docker, "version"], capture_output=True, text=True, timeout=5)
        print(f"Docker Version Output:\n{r_ver.stdout}")
    except Exception as e:
        print(f"Docker Execution Error: {e}")

# 2. UNITY
unity_candidates = [
    r"C:\Program Files\Unity\Hub\Editor\2022.3.35f1\Editor\Unity.exe",
    r"C:\Program Files\Unity\Hub\Editor\2022.3.38f1\Editor\Unity.exe",
    r"C:\Program Files\Unity\Hub\Editor\2022.3.0f1\Editor\Unity.exe",
    r"C:\Program Files\Unity\Editor\Unity.exe",
]
# Search Unity Hub folders
hub_dir = Path(r"C:\Program Files\Unity\Hub\Editor")
if hub_dir.exists():
    for sub in hub_dir.iterdir():
        cand = sub / "Editor" / "Unity.exe"
        if cand.exists():
            unity_candidates.insert(0, str(cand))

found_unity = next((p for p in unity_candidates if os.path.exists(p)), None)
print(f"Unity Binary: {found_unity}")
if found_unity:
    try:
        r_u = subprocess.run([found_unity, "-version"], capture_output=True, text=True, timeout=5)
        print(f"Unity Version Output:\n{r_u.stdout}")
    except Exception as e:
        print(f"Unity Execution Error: {e}")

# 3. UNREAL ENGINE & UBT
ue_candidates = [
    r"C:\Program Files\Epic Games\UE_5.3\Engine\Binaries\Win64\UnrealEditor.exe",
    r"C:\Program Files\Epic Games\UE_5.4\Engine\Binaries\Win64\UnrealEditor.exe",
    r"C:\Program Files\Epic Games\UE_5.2\Engine\Binaries\Win64\UnrealEditor.exe",
]
found_ue = next((p for p in ue_candidates if os.path.exists(p)), None)
print(f"Unreal Editor Binary: {found_ue}")

ubt_candidates = [
    r"C:\Program Files\Epic Games\UE_5.3\Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.exe",
    r"C:\Program Files\Epic Games\UE_5.4\Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.exe",
]
found_ubt = next((p for p in ubt_candidates if os.path.exists(p)), None)
print(f"UnrealBuildTool Binary: {found_ubt}")

# 4. MSVC cl.exe
cl_exe = shutil.which("cl") or shutil.which("cl.exe")
vs_candidates = [
    r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC",
    r"C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Tools\MSVC",
    r"C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Tools\MSVC",
    r"C:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC",
    r"C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools\VC\Tools\MSVC",
]
found_cl = cl_exe
if not found_cl:
    for vs in vs_candidates:
        p_vs = Path(vs)
        if p_vs.exists():
            for msvc_ver in p_vs.iterdir():
                cand_cl = msvc_ver / "bin" / "Hostx64" / "x64" / "cl.exe"
                if cand_cl.exists():
                    found_cl = str(cand_cl)
                    break
print(f"MSVC cl.exe Binary: {found_cl}")
if found_cl:
    try:
        r_cl = subprocess.run([found_cl], capture_output=True, text=True, timeout=5)
        print(f"MSVC cl Output:\n{r_cl.stderr or r_cl.stdout}")
    except Exception as e:
        print(f"MSVC Execution Error: {e}")
