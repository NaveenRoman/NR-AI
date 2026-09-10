import os
import subprocess
import sys
from pathlib import Path

hub_candidates = [
    r"C:\Program Files\Unity Hub\Unity Hub.exe",
    r"C:\Program Files\Unity\Hub\Unity Hub.exe",
    r"C:\Users\navee\AppData\Local\Programs\Unity Hub\Unity Hub.exe",
    r"C:\Program Files (x86)\Unity Hub\Unity Hub.exe",
    r"C:\Program Files\Unity Hub\resources\app.asar",
]

found_hub = None
for cand in hub_candidates:
    if os.path.exists(cand):
        found_hub = cand
        print(f"[+] Found Unity Hub candidate: {cand}")

# Also check running processes
p_task = subprocess.run(["tasklist"], capture_output=True, text=True)
running_unity = [l for l in p_task.stdout.splitlines() if "unity" in l.lower()]
print(f"Running Unity Processes: {running_unity}")

# Search for any Unity.exe on all drives
print("[*] Checking for existing Unity.exe...")
editor_candidates = [
    r"C:\Program Files\Unity\Hub\Editor",
    r"C:\Program Files\Unity\Editor",
    r"C:\Program Files (x86)\Unity\Editor",
    r"C:\Program Files\Unity",
]
for ed in editor_candidates:
    p_ed = Path(ed)
    if p_ed.exists():
        for item in p_ed.rglob("Unity.exe"):
            print(f"[+] Found Unity Editor: {item}")
