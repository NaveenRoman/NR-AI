import subprocess
from pathlib import Path

adb_exe = r"C:\Users\navee\AppData\Local\Android\Sdk\platform-tools\adb.exe"
out_png = Path(r"C:\NR-AI\data\real_benchmarks\android_emulator_screen.png")
out_png.parent.mkdir(parents=True, exist_ok=True)

with open(str(out_png), "wb") as f:
    subprocess.run([adb_exe, "exec-out", "screencap", "-p"], stdout=f, check=True)

print(f"[+] Screen captured successfully! File size: {out_png.stat().st_size} bytes")
