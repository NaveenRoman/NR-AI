import os
import subprocess
import sys
from pathlib import Path

print("=== DEEP SYSTEM SCAN FOR DOCKER, UNITY, UNREAL, EPIC ===")

# 1. Running Processes
try:
    p_tasklist = subprocess.run(["tasklist"], capture_output=True, text=True)
    relevant_procs = [l for l in p_tasklist.stdout.splitlines() if any(k in l.lower() for k in ["docker", "unity", "epic", "unreal"])]
    print(f"Running Processes ({len(relevant_procs)} found):")
    for p in relevant_procs:
        print("  ", p)
except Exception as e:
    print(f"Error checking processes: {e}")

# 2. Start Menu Shortcuts
start_menu_paths = [
    Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
]
found_shortcuts = []
for sm in start_menu_paths:
    if sm.exists():
        for item in sm.rglob("*"):
            if any(k in item.name.lower() for k in ["docker", "unity", "unreal", "epic"]):
                found_shortcuts.append(str(item))

print(f"\nStart Menu Entries ({len(found_shortcuts)} found):")
for s in found_shortcuts:
    print("  ", s)

# 3. Common Program Files Folders
search_dirs = [
    Path(r"C:\Program Files"),
    Path(r"C:\Program Files (x86)"),
    Path(r"C:\Users\navee\AppData\Local\Programs"),
]
found_dirs = []
for sd in search_dirs:
    if sd.exists():
        for sub in sd.iterdir():
            if sub.is_dir() and any(k in sub.name.lower() for k in ["docker", "unity", "unreal", "epic", "visual studio"]):
                found_dirs.append(str(sub))

print(f"\nRelevant Program Folders ({len(found_dirs)} found):")
for d in found_dirs:
    print("  ", d)
