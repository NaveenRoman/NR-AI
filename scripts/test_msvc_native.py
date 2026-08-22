import os
import shutil
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path("C:/NR-AI").resolve()

def test_msvc():
    print("=== NATIVE MSVC C++ COMPILER VERIFICATION ===")
    cl_exe = r"C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools\VC\Tools\MSVC\14.29.30133\bin\Hostx64\x64\cl.exe"
    assert os.path.exists(cl_exe), f"cl.exe not found at {cl_exe}"

    demo_dir = WORKSPACE / "data" / "real_benchmarks" / "msvc_demo"
    if demo_dir.exists():
        shutil.rmtree(demo_dir)
    demo_dir.mkdir(parents=True, exist_ok=True)

    cpp_src = demo_dir / "main.cpp"
    cpp_src.write_text(
        "#include <iostream>\n"
        "int main() {\n"
        "    std::cout << \"MSVC C++ Native Execution 100% Verified!\" << std::endl;\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8"
    )

    # Compile with cl.exe
    # Need INCLUDE and LIB env vars or use vcvarsall.bat
    vcvars_bat = r"C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
    cmd = f'"{vcvars_bat}" && cl /nologo /EHsc /Fe:"{demo_dir}\\main.exe" "{cpp_src}"'
    res = subprocess.run(cmd, cwd=str(demo_dir), capture_output=True, text=True, shell=True)
    print(f"[*] Compilation stdout:\n{res.stdout}")
    assert res.returncode == 0, f"Compilation failed: {res.stderr}"

    # Run compiled native .exe
    exe_path = demo_dir / "main.exe"
    assert exe_path.exists(), "main.exe was not created"
    run_res = subprocess.run([str(exe_path)], capture_output=True, text=True)
    print(f"[+] Native Execution Output: {run_res.stdout.strip()}")
    assert "MSVC C++ Native Execution 100% Verified!" in run_res.stdout
    print("=== MSVC C++ COMPILER VALIDATION: PASS ===")

if __name__ == "__main__":
    test_msvc()
