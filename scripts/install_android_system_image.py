import subprocess
import sys
from pathlib import Path

sdk_mgr = r"C:\Users\navee\AppData\Local\Android\Sdk\cmdline-tools\latest\bin\sdkmanager.bat"

print("[*] Installing Android 34 Google APIs system image...")
p = subprocess.Popen(
    [sdk_mgr, "system-images;android-34;google_apis;x86_64"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    shell=True,
)
out, _ = p.communicate(input="y\ny\ny\ny\n")
print(out)
print("[*] Return code:", p.returncode)
