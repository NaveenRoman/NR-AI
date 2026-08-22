import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.agent.code_runner import CodeRunner
from app.agent.code_writer import CodeWriter
from app.agent.error_analyzer import ErrorAnalyzer
from app.agent.recovery_engine import RecoveryEngine

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


class CodeAgent:
    """
    NR AI Unified Code Agent & Self-Healing Pipeline.

    Flow:
        COMMAND / PLAN
              ↓
            WRITE / MODIFY (CodeWriter + Checkpointing)
              ↓
            RUN (CodeRunner - Python/Java/C/JS)
              ↓
          SUCCESS?
          ├── YES ──> VERIFY ──> COMPLETE
          └── NO
                ↓
            ANALYZE (ErrorAnalyzer)
                ↓
            REASON & RECOVER (RecoveryEngine)
                ↓
            APPLY PATCH (CodeWriter)
                ↓
            RUN AGAIN (Bounded Retry Loop)
    """

    def __init__(
        self,
        workspace: Optional[str] = None,
        max_retries: int = 3,
    ):
        self.workspace = Path(workspace or os.getcwd()).resolve()
        self.max_retries = max_retries

        self.writer = CodeWriter(
            workspace=str(self.workspace),
            checkpoint_dir=str(self.workspace / "data" / "checkpoints"),
        )
        self.runner = CodeRunner(workspace=str(self.workspace))
        self.analyzer = ErrorAnalyzer()
        self.recovery = RecoveryEngine(max_retries=self.max_retries)

    # -------------------------------------------------
    # BACKWARD COMPATIBLE CONVENIENCE METHODS
    # -------------------------------------------------

    def write_code(self, filename: str, code: str) -> Dict[str, Any]:
        """Write source code to file."""
        return self.writer.write_file(filename, code, overwrite=True)

    def read_code(self, filename: str) -> Dict[str, Any]:
        """Read source code from file."""
        return self.writer.read_file(filename)

    def run_code(self, filename: str, timeout: float = 15.0) -> Dict[str, Any]:
        """Run source file through the multi-language CodeRunner."""
        return self.runner.run_file(filename, timeout=timeout)

    def analyze_error(
        self, result: Dict[str, Any], source_code: str = ""
    ) -> Dict[str, Any]:
        """Analyze execution failure."""
        return self.analyzer.analyze(
            stderr=result.get("stderr", ""),
            stdout=result.get("stdout", ""),
            returncode=result.get("returncode", -1),
            source_code=source_code,
            language=result.get("language"),
        )

    def attempt_recovery(
        self, filename: str, error: Dict[str, Any]
    ) -> bool:
        """Attempt single-step recovery on file."""
        read_res = self.read_code(filename)
        if not read_res["success"]:
            return False

        fix_res = self.recovery.generate_fix(
            error_info=error,
            source_code=read_res["code"],
            filename=filename,
        )

        if fix_res["success"]:
            write_res = self.writer.write_file(
                filename, fix_res["fixed_code"], overwrite=True, make_backup=True
            )
            if write_res["success"]:
                safe_print(f"🔧 Applied fix: {fix_res['description']}")
                return True
        return False

    # -------------------------------------------------
    # COMPLETE VERTICAL PIPELINE EXECUTION
    # -------------------------------------------------

    def execute(
        self,
        filename: str,
        code: str,
        expected_output: Optional[str] = None,
        timeout: float = 15.0,
    ) -> Dict[str, Any]:
        """
        Full vertical workflow:
        WRITE -> RUN -> (ERROR? -> ANALYZE -> FIX -> RE-RUN) -> VERIFY
        """
        safe_print("========================================")
        safe_print("        NR AI CODE AGENT")
        safe_print("========================================")
        safe_print(f"📄 Target File: {filename}")

        self.recovery.reset_history()
        history: List[Dict[str, Any]] = []

        # 1. WRITE INITIAL CODE
        write_res = self.writer.write_file(
            filename, code, overwrite=True, make_backup=True
        )
        if not write_res["success"]:
            safe_print(f"🔴 Write failed: {write_res.get('message')}")
            return {
                "success": False,
                "stage": "write",
                "message": write_res.get("message"),
                "history": history,
            }

        initial_backup = write_res.get("backup_path")

        # 2. RUN & BOUNDED RECOVERY LOOP
        for attempt in range(1, self.max_retries + 1):
            safe_print("\n----------------------------------------")
            safe_print(f"🔄 ATTEMPT {attempt}/{self.max_retries}")
            safe_print("----------------------------------------")

            run_res = self.runner.run_file(filename, timeout=timeout)
            history.append({
                "attempt": attempt,
                "run_result": run_res,
            })

            # Check for runtime success
            if run_res["success"]:
                # Optional Output Verification
                if expected_output:
                    actual_out = run_res["stdout"].strip()
                    if expected_output.strip() not in actual_out:
                        safe_print("⚠️ Execution succeeded but output verification failed.")
                        safe_print(f"Expected to contain: {expected_output}")
                        safe_print(f"Actual: {actual_out}")
                        return {
                            "success": False,
                            "stage": "output_verification",
                            "attempts": attempt,
                            "output": actual_out,
                            "expected": expected_output,
                            "history": history,
                        }

                safe_print("\n========================================")
                safe_print("🟢 CODE EXECUTION & VERIFICATION SUCCESS")
                safe_print("========================================")
                return {
                    "success": True,
                    "stage": "complete",
                    "attempts": attempt,
                    "output": run_res["stdout"],
                    "filename": filename,
                    "history": history,
                }

            # 3. ERROR ANALYSIS
            current_code = self.read_code(filename).get("code", "")
            error_info = self.analyzer.analyze(
                stderr=run_res.get("stderr", ""),
                stdout=run_res.get("stdout", ""),
                returncode=run_res.get("returncode", -1),
                source_code=current_code,
                language=run_res.get("language"),
            )

            # 4. RECOVERY REASONING & FIX GENERATION
            safe_print(f"\n🧠 Generating recovery patch for attempt {attempt}...")
            fix_res = self.recovery.generate_fix(
                error_info=error_info,
                source_code=current_code,
                filename=filename,
            )

            if not fix_res["success"]:
                safe_print(f"🔴 Recovery failed: {fix_res['description']}")
                return {
                    "success": False,
                    "stage": "recovery",
                    "attempts": attempt,
                    "error": error_info,
                    "message": fix_res["description"],
                    "history": history,
                }

            # 5. APPLY PATCH
            patch_write = self.writer.write_file(
                filename,
                fix_res["fixed_code"],
                overwrite=True,
                make_backup=True,
            )
            if not patch_write["success"]:
                safe_print(f"🔴 Failed to write patch: {patch_write['message']}")
                return {
                    "success": False,
                    "stage": "apply_patch",
                    "attempts": attempt,
                    "message": patch_write["message"],
                    "history": history,
                }

            safe_print(f"🔧 Fix applied: {fix_res['description']}")

        # Exhausted maximum retries
        safe_print("\n========================================")
        safe_print("🔴 MAXIMUM RECOVERY ATTEMPTS REACHED")
        safe_print("========================================")

        return {
            "success": False,
            "stage": "max_retries_exceeded",
            "attempts": self.max_retries,
            "message": f"Execution could not be resolved within {self.max_retries} attempts.",
            "history": history,
        }

    def run_existing(
        self, filename: str, timeout: float = 15.0
    ) -> Dict[str, Any]:
        """Run an existing file directly without overwriting."""
        safe_print(f"▶️ Running existing file: {filename}")
        return self.runner.run_file(filename, timeout=timeout)

    def fix_existing_file(
        self, filename: str, timeout: float = 15.0
    ) -> Dict[str, Any]:
        """Inspect and fix an existing file if it has errors."""
        read_res = self.read_code(filename)
        if not read_res["success"]:
            return read_res
        return self.execute(filename, read_res["code"], timeout=timeout)

    def modify_and_run(
        self,
        filename: str,
        search_text: str,
        replace_text: str,
        timeout: float = 15.0,
    ) -> Dict[str, Any]:
        """Modify an existing file and run it through the execution loop."""
        mod_res = self.writer.modify_file(filename, search_text, replace_text)
        if not mod_res["success"]:
            return mod_res
        read_res = self.read_code(filename)
        return self.execute(filename, read_res["code"], timeout=timeout)


if __name__ == "__main__":
    safe_print("========================================")
    safe_print("        NR AI CODE AGENT TEST")
    safe_print("========================================")

    agent = CodeAgent(max_retries=3)

    # 1. Test clean run
    clean_code = "print('Hello from NR AI Code Agent!')\n"
    res1 = agent.execute("data/test_clean.py", clean_code)
    safe_print(f"\n1. Clean execution result: {res1['success']}")

    # 2. Test self-healing recovery run (Python syntax error missing colon)
    buggy_code = (
        "def compute_total(a, b)\n"
        "    return a + b\n\n"
        "res = compute_total(10, 20)\n"
        "print(f'Result is: {res}')\n"
    )
    safe_print("\n2. Testing self-healing recovery on buggy code...")
    res2 = agent.execute("data/test_buggy.py", buggy_code)
    safe_print(f"Self-healing execution result: {res2['success']} in {res2.get('attempts')} attempt(s)")

    safe_print("\n========================================")
    if res1["success"] and res2["success"]:
        safe_print("🟢 CODE AGENT TESTS PASSED")
    else:
        safe_print("🔴 CODE AGENT TESTS FAILED")
    safe_print("========================================")