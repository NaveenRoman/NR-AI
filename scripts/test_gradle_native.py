import os
import shutil
import subprocess
from pathlib import Path

test_dir = Path("C:/NR-AI/data/real_benchmarks/gradle_demo").resolve()
if test_dir.exists():
    shutil.rmtree(test_dir)
test_dir.mkdir(parents=True, exist_ok=True)

(test_dir / "build.gradle").write_text("plugins { id 'java' }\n", encoding="utf-8")
src_dir = test_dir / "src" / "main" / "java"
src_dir.mkdir(parents=True, exist_ok=True)
(src_dir / "Hello.java").write_text(
    "public class Hello {\n"
    "    public static void main(String[] args) {\n"
    "        System.out.println(\"Gradle 8.5 Native Build OK\");\n"
    "    }\n"
    "}\n",
    encoding="utf-8"
)

gradle_bin = r"C:\NR-AI\tools\gradle-8.5\bin\gradle.bat"
print("[*] Running Gradle build...")
res = subprocess.run([gradle_bin, "build", "--quiet"], cwd=str(test_dir), capture_output=True, text=True, shell=True)
print("Gradle returncode:", res.returncode)
if res.returncode != 0:
    print("Gradle STDOUT:", res.stdout)
    print("Gradle STDERR:", res.stderr)
else:
    print("[+] Gradle build succeeded!")
    java_exe = r"C:\Program Files\Java\jdk-23\bin\java.exe"
    run_res = subprocess.run([java_exe, "-cp", "build/classes/java/main", "Hello"], cwd=str(test_dir), capture_output=True, text=True)
    print("JVM output:", run_res.stdout.strip())
