import subprocess
import sys
from pathlib import Path

WORKSPACE = Path("C:/NR-AI").resolve()
sys.path.insert(0, str(WORKSPACE))

from app.agent.error_analyzer import ErrorAnalyzer
from app.agent.recovery_engine import RecoveryEngine


def test_recovery():
    src_file = WORKSPACE / "data" / "real_benchmarks" / "spring_boot_app" / "src" / "main" / "java" / "com" / "nrai" / "demo" / "TaskService.java"
    # 1. Inject error (missing semicolon)
    bad_code = (
        "package com.nrai.demo;\n"
        "public class TaskService {\n"
        "    public static String getStatus() {\n"
        "        return \"READY\"\n"
        "    }\n"
        "}\n"
    )
    src_file.write_text(bad_code, encoding="utf-8")

    mvn_bin = r"C:\NR-AI\tools\apache-maven-3.9.6\bin\mvn.cmd"
    pom_file = WORKSPACE / "data" / "real_benchmarks" / "spring_boot_app" / "pom.xml"
    res = subprocess.run([mvn_bin, "compile", "-f", str(pom_file)], capture_output=True, text=True, shell=True)
    print("[*] Injected error compilation returncode:", res.returncode)
    assert res.returncode != 0

    # 2. Analyze error
    analyzer = ErrorAnalyzer()
    analysis = analyzer.analyze(
        stderr=res.stderr or res.stdout,
        stdout=res.stdout,
        returncode=res.returncode,
        source_code=bad_code,
        language="java",
    )
    print("[*] Diagnosis:", analysis.get("suggested_diagnosis"))

    # 3. Patch error
    engine = RecoveryEngine()
    patched = engine.generate_patch(
        source_code=bad_code,
        error_info=analysis,
        attempt=1,
    )
    src_file.write_text(patched, encoding="utf-8")
    print("[*] Fix applied.")

    # 4. Re-compile
    res2 = subprocess.run([mvn_bin, "compile", "-f", str(pom_file)], capture_output=True, text=True, shell=True)
    print("[*] Re-compilation returncode:", res2.returncode)
    assert res2.returncode == 0
    print("[+] JAVA ERROR RECOVERY AND MAVEN RE-COMPILATION SUCCEEDED 100%!")


if __name__ == "__main__":
    test_recovery()
