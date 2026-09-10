import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def safe_print(msg: str) -> None:
    try:
        print(msg)
    except Exception:
        try:
            print(msg.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


class ExecutionResult:
    def __init__(
        self,
        success: bool,
        stage: str,
        returncode: int,
        stdout: str,
        stderr: str,
        duration_ms: float = 0.0,
        language: str = "unknown",
        command: Optional[List[str]] = None,
        timed_out: bool = False,
        message: str = "",
    ):
        self.success = success
        self.stage = stage
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.duration_ms = duration_ms
        self.language = language
        self.command = command or []
        self.timed_out = timed_out
        self.message = message

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "stage": self.stage,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "language": self.language,
            "command": " ".join(self.command) if self.command else "",
            "timed_out": self.timed_out,
            "message": self.message,
        }


class BaseLanguageRunner:
    """Base interface for language execution handlers."""

    language_name: str = "generic"
    supported_extensions: Tuple[str, ...] = ()

    def __init__(self, workspace: Path):
        self.workspace = workspace

    def run(
        self, file_path: Path, timeout: float = 15.0
    ) -> ExecutionResult:
        raise NotImplementedError


class PythonRunner(BaseLanguageRunner):
    language_name = "python"
    supported_extensions = (".py", ".pyw")

    def run(
        self, file_path: Path, timeout: float = 15.0
    ) -> ExecutionResult:
        python_exe = sys.executable or "python"
        command = [python_exe, "-u", str(file_path)]

        start_time = time.perf_counter()
        try:
            run_env = dict(os.environ)
            run_env["PYTHONIOENCODING"] = "utf-8"
            process = subprocess.run(
                command,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=run_env,
                timeout=timeout,
            )
            duration_ms = (time.perf_counter() - start_time) * 1000
            stdout = process.stdout.strip()
            stderr = process.stderr.strip()
            return ExecutionResult(
                success=(process.returncode == 0),
                stage="run",
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                language=self.language_name,
                command=command,
                message="Execution succeeded"
                if process.returncode == 0
                else "Python execution error",
            )
        except subprocess.TimeoutExpired:
            duration_ms = (time.perf_counter() - start_time) * 1000
            return ExecutionResult(
                success=False,
                stage="timeout",
                returncode=-1,
                stdout="",
                stderr=f"Execution timed out after {timeout} seconds.",
                duration_ms=duration_ms,
                language=self.language_name,
                command=command,
                timed_out=True,
                message=f"Timeout expired ({timeout}s)",
            )
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            return ExecutionResult(
                success=False,
                stage="run",
                returncode=-1,
                stdout="",
                stderr=str(e),
                duration_ms=duration_ms,
                language=self.language_name,
                command=command,
                message=f"Failed to start Python process: {e}",
            )


class JavaScriptRunner(BaseLanguageRunner):
    language_name = "javascript"
    supported_extensions = (".js", ".mjs", ".cjs")

    def run(
        self, file_path: Path, timeout: float = 15.0
    ) -> ExecutionResult:
        node_exe = shutil.which("node") or "node"
        command = [node_exe, str(file_path)]

        start_time = time.perf_counter()
        try:
            process = subprocess.run(
                command,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            duration_ms = (time.perf_counter() - start_time) * 1000
            stdout = process.stdout.strip()
            stderr = process.stderr.strip()
            return ExecutionResult(
                success=(process.returncode == 0),
                stage="run",
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                language=self.language_name,
                command=command,
                message="Node execution succeeded"
                if process.returncode == 0
                else "Node execution error",
            )
        except subprocess.TimeoutExpired:
            duration_ms = (time.perf_counter() - start_time) * 1000
            return ExecutionResult(
                success=False,
                stage="timeout",
                returncode=-1,
                stdout="",
                stderr=f"Node execution timed out after {timeout} seconds.",
                duration_ms=duration_ms,
                language=self.language_name,
                command=command,
                timed_out=True,
                message=f"Timeout expired ({timeout}s)",
            )
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            return ExecutionResult(
                success=False,
                stage="run",
                returncode=-1,
                stdout="",
                stderr=str(e),
                duration_ms=duration_ms,
                language=self.language_name,
                command=command,
                message=f"Failed to run Node: {e}",
            )


class JavaRunner(BaseLanguageRunner):
    language_name = "java"
    supported_extensions = (".java",)

    def _extract_class_name(self, file_path: Path) -> str:
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            # Look for public class ClassName or class ClassName
            match = re.search(r"public\s+class\s+([A-Za-z_][A-Za-z0-9_]*)", content)
            if match:
                return match.group(1)
            match = re.search(r"class\s+([A-Za-z_][A-Za-z0-9_]*)", content)
            if match:
                return match.group(1)
        except Exception:
            pass
        return file_path.stem

    def run(
        self, file_path: Path, timeout: float = 15.0
    ) -> ExecutionResult:
        javac_exe = shutil.which("javac")
        java_exe = shutil.which("java") or "java"

        start_time = time.perf_counter()

        # Step 1: Compile with javac if available
        if javac_exe:
            compile_cmd = [javac_exe, "-encoding", "UTF-8", str(file_path)]
            try:
                comp_proc = subprocess.run(
                    compile_cmd,
                    cwd=file_path.parent,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                )
                if comp_proc.returncode != 0:
                    duration_ms = (time.perf_counter() - start_time) * 1000
                    return ExecutionResult(
                        success=False,
                        stage="compile",
                        returncode=comp_proc.returncode,
                        stdout=comp_proc.stdout.strip(),
                        stderr=comp_proc.stderr.strip(),
                        duration_ms=duration_ms,
                        language=self.language_name,
                        command=compile_cmd,
                        message="Java compilation failed.",
                    )
            except subprocess.TimeoutExpired:
                duration_ms = (time.perf_counter() - start_time) * 1000
                return ExecutionResult(
                    success=False,
                    stage="compile_timeout",
                    returncode=-1,
                    stdout="",
                    stderr="Java compilation timed out.",
                    duration_ms=duration_ms,
                    language=self.language_name,
                    command=compile_cmd,
                    timed_out=True,
                )

            # Step 2: Execute compiled class
            class_name = self._extract_class_name(file_path)
            run_cmd = [java_exe, "-cp", str(file_path.parent), class_name]
        else:
            # Fallback to direct Java 11+ source file execution
            run_cmd = [java_exe, str(file_path)]

        try:
            run_proc = subprocess.run(
                run_cmd,
                cwd=file_path.parent,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            duration_ms = (time.perf_counter() - start_time) * 1000
            return ExecutionResult(
                success=(run_proc.returncode == 0),
                stage="run",
                returncode=run_proc.returncode,
                stdout=run_proc.stdout.strip(),
                stderr=run_proc.stderr.strip(),
                duration_ms=duration_ms,
                language=self.language_name,
                command=run_cmd,
                message="Java execution succeeded"
                if run_proc.returncode == 0
                else "Java runtime error",
            )
        except subprocess.TimeoutExpired:
            duration_ms = (time.perf_counter() - start_time) * 1000
            return ExecutionResult(
                success=False,
                stage="timeout",
                returncode=-1,
                stdout="",
                stderr=f"Java execution timed out after {timeout} seconds.",
                duration_ms=duration_ms,
                language=self.language_name,
                command=run_cmd,
                timed_out=True,
            )
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            return ExecutionResult(
                success=False,
                stage="run",
                returncode=-1,
                stdout="",
                stderr=str(e),
                duration_ms=duration_ms,
                language=self.language_name,
                command=run_cmd,
                message=f"Failed to execute Java: {e}",
            )


class CRunner(BaseLanguageRunner):
    language_name = "c"
    supported_extensions = (".c", ".cpp", ".cc")

    def _find_c_compiler(self) -> Optional[Tuple[str, str]]:
        """Returns (compiler_type, executable_path) e.g. ('gcc', 'gcc.exe')"""
        for comp in ["gcc", "clang", "tcc", "cl", "g++", "clang++"]:
            exe = shutil.which(comp)
            if exe:
                if "cl" in comp.lower() and not ("gcc" in comp.lower() or "clang" in comp.lower()):
                    return ("msvc", exe)
                return ("gcc_compatible", exe)
        return None

    def run(
        self, file_path: Path, timeout: float = 15.0
    ) -> ExecutionResult:
        compiler_info = self._find_c_compiler()
        start_time = time.perf_counter()

        if not compiler_info:
            duration_ms = (time.perf_counter() - start_time) * 1000
            return ExecutionResult(
                success=False,
                stage="compile",
                returncode=-1,
                stdout="",
                stderr=(
                    "No C/C++ compiler found on system PATH (checked gcc, clang, cl, tcc).\n"
                    "Please install MinGW GCC or Clang to compile and run C programs."
                ),
                duration_ms=duration_ms,
                language=self.language_name,
                message="Compiler not found",
            )

        compiler_type, comp_exe = compiler_info
        out_binary = file_path.with_suffix(".exe" if os.name == "nt" else ".out")

        if compiler_type == "msvc":
            compile_cmd = [comp_exe, "/nologo", str(file_path), f"/Fe:{out_binary}"]
        else:
            compile_cmd = [comp_exe, str(file_path), "-o", str(out_binary)]

        # Compile
        try:
            comp_proc = subprocess.run(
                compile_cmd,
                cwd=file_path.parent,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            if comp_proc.returncode != 0:
                duration_ms = (time.perf_counter() - start_time) * 1000
                return ExecutionResult(
                    success=False,
                    stage="compile",
                    returncode=comp_proc.returncode,
                    stdout=comp_proc.stdout.strip(),
                    stderr=comp_proc.stderr.strip(),
                    duration_ms=duration_ms,
                    language=self.language_name,
                    command=compile_cmd,
                    message="C compilation error",
                )
        except subprocess.TimeoutExpired:
            duration_ms = (time.perf_counter() - start_time) * 1000
            return ExecutionResult(
                success=False,
                stage="compile_timeout",
                returncode=-1,
                stdout="",
                stderr="C compilation timed out.",
                duration_ms=duration_ms,
                language=self.language_name,
                command=compile_cmd,
                timed_out=True,
            )

        # Run compiled binary
        run_cmd = [str(out_binary)]
        try:
            run_proc = subprocess.run(
                run_cmd,
                cwd=file_path.parent,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            duration_ms = (time.perf_counter() - start_time) * 1000
            return ExecutionResult(
                success=(run_proc.returncode == 0),
                stage="run",
                returncode=run_proc.returncode,
                stdout=run_proc.stdout.strip(),
                stderr=run_proc.stderr.strip(),
                duration_ms=duration_ms,
                language=self.language_name,
                command=run_cmd,
                message="C execution succeeded"
                if run_proc.returncode == 0
                else "C program runtime error",
            )
        except subprocess.TimeoutExpired:
            duration_ms = (time.perf_counter() - start_time) * 1000
            return ExecutionResult(
                success=False,
                stage="timeout",
                returncode=-1,
                stdout="",
                stderr=f"C program timed out after {timeout} seconds.",
                duration_ms=duration_ms,
                language=self.language_name,
                command=run_cmd,
                timed_out=True,
            )
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            return ExecutionResult(
                success=False,
                stage="run",
                returncode=-1,
                stdout="",
                stderr=str(e),
                duration_ms=duration_ms,
                language=self.language_name,
                command=run_cmd,
                message=f"Failed to run C binary: {e}",
            )


class CodeRunner:
    """
    NR AI Extensible Multi-Language Code Execution Engine.

    Manages execution across Python, JavaScript, Java, C, and custom languages
    with safety timeouts, process isolation, and output capture.
    """

    def __init__(self, workspace: Optional[str] = None):
        self.workspace = Path(workspace or os.getcwd()).resolve()
        self._runners: Dict[str, BaseLanguageRunner] = {}
        self._ext_map: Dict[str, BaseLanguageRunner] = {}

        # Register default language handlers
        self.register_runner(PythonRunner(self.workspace))
        self.register_runner(JavaScriptRunner(self.workspace))
        self.register_runner(JavaRunner(self.workspace))
        self.register_runner(CRunner(self.workspace))

    def register_runner(self, runner: BaseLanguageRunner) -> None:
        """Register a language runner handler."""
        self._runners[runner.language_name.lower()] = runner
        for ext in runner.supported_extensions:
            self._ext_map[ext.lower()] = runner

    def get_supported_languages(self) -> List[str]:
        return list(self._runners.keys())

    def run_file(
        self,
        filename: str,
        timeout: float = 15.0,
        language: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a code file in its respective language runtime."""
        path = Path(filename)
        if not path.is_absolute():
            path = (self.workspace / path).resolve()

        if not path.exists():
            return {
                "success": False,
                "stage": "pre_check",
                "returncode": -1,
                "stdout": "",
                "stderr": f"File does not exist: {path}",
                "duration_ms": 0.0,
                "language": language or "unknown",
                "message": "File not found",
            }

        runner = None
        if language and language.lower() in self._runners:
            runner = self._runners[language.lower()]
        else:
            ext = path.suffix.lower()
            runner = self._ext_map.get(ext)

        if not runner:
            return {
                "success": False,
                "stage": "pre_check",
                "returncode": -1,
                "stdout": "",
                "stderr": f"Unsupported language extension: {path.suffix}",
                "duration_ms": 0.0,
                "language": "unknown",
                "message": f"Unsupported file type: {path.suffix}",
            }

        safe_print(f"\n▶️ Running {runner.language_name.upper()}: {path.name}")
        res = runner.run(path, timeout=timeout)
        result_dict = res.to_dict()

        safe_print(f"📤 Exit code: {result_dict['returncode']} ({result_dict['duration_ms']:.1f}ms)")
        if result_dict["stdout"]:
            safe_print(f"\n📤 OUTPUT:\n{result_dict['stdout']}")
        if result_dict["stderr"]:
            safe_print(f"\n❌ ERROR/STDERR:\n{result_dict['stderr']}")

        return result_dict


if __name__ == "__main__":
    safe_print("========================================")
    safe_print("        NR AI CODE RUNNER TEST")
    safe_print("========================================")

    runner = CodeRunner()
    safe_print(f"Supported languages: {runner.get_supported_languages()}")

    # Test Python execution
    py_test = Path("data/runner_test.py")
    py_test.parent.mkdir(parents=True, exist_ok=True)
    py_test.write_text("print('Python Runner OK')", encoding="utf-8")

    res = runner.run_file("data/runner_test.py")
    safe_print(f"Python run success: {res['success']}")

    # Test JS execution
    js_test = Path("data/runner_test.js")
    js_test.write_text("console.log('JS Runner OK');", encoding="utf-8")
    res_js = runner.run_file("data/runner_test.js")
    safe_print(f"JS run success: {res_js['success']}")

    # Test Java execution
    java_test = Path("data/RunnerTest.java")
    java_test.write_text(
        "public class RunnerTest {\n"
        "    public static void main(String[] args) {\n"
        "        System.out.println(\"Java Runner OK\");\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    res_java = runner.run_file("data/RunnerTest.java")
    safe_print(f"Java run success: {res_java['success']}")

    safe_print("\n========================================")
    safe_print("🟢 CODE RUNNER TEST COMPLETE")
    safe_print("========================================")
