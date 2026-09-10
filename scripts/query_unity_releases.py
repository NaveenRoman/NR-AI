import subprocess

hub_exe = r"C:\Program Files\WindowsApps\UnityTechnologies.UnityHub_3.21.0.65535_x64__2vrhnee42bhxm\app\Unity Hub.exe"

print("[*] Querying Unity Hub official releases...")
res = subprocess.run([hub_exe, "--", "--headless", "editors", "--releases"], capture_output=True, text=True, timeout=30)
releases_2022 = [l for l in (res.stdout + res.stderr).splitlines() if "2022.3" in l]
print("2022.3 Releases found:")
for r in releases_2022[:10]:
    print("  ", r)
