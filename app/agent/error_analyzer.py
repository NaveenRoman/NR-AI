import re
import sys
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


class ErrorAnalyzer:
    """
    NR AI Multi-Language Error Analysis Engine.

    Input:
        Source code + execution result (stderr, stdout, returncode, language)

    Output:
        Structured error diagnostics that the recovery/AI reasoning layer
        can directly act on.
    """

    KNOWN_PYTHON_ERRORS = [
        "SyntaxError",
        "IndentationError",
        "TabError",
        "NameError",
        "TypeError",
        "ValueError",
        "IndexError",
        "KeyError",
        "AttributeError",
        "ZeroDivisionError",
        "FileNotFoundError",
        "PermissionError",
        "ModuleNotFoundError",
        "ImportError",
        "UnboundLocalError",
        "RecursionError",
        "RuntimeError",
    ]

    KNOWN_JAVA_ERRORS = [
        "NullPointerException",
        "ArrayIndexOutOfBoundsException",
        "StringIndexOutOfBoundsException",
        "ClassNotFoundException",
        "NoClassDefFoundError",
        "NoSuchMethodError",
        "NumberFormatException",
        "IllegalArgumentException",
        "IllegalStateException",
        "ArithmeticException",
        "ClassCastException",
    ]

    KNOWN_JS_ERRORS = [
        "SyntaxError",
        "ReferenceError",
        "TypeError",
        "RangeError",
        "URIError",
        "EvalError",
    ]

    def _infer_language(
        self, text: str, source_code: str, language: Optional[str]
    ) -> str:
        if language and language != "unknown":
            return language.lower()

        # Check stderr markers
        if "Traceback (most recent call last):" in text or "File \"" in text:
            return "python"
        if ".java:" in text or "Exception in thread" in text or "javac" in text:
            return "java"
        if "ReferenceError:" in text or "node:" in text or "at Object.<anonymous>" in text:
            return "javascript"
        if "error: expected" in text or "fatal error:" in text or ": error C" in text:
            return "c"

        # Check source code heuristics
        if "def " in source_code or "import " in source_code:
            return "python"
        if "public class " in source_code or "System.out.println" in source_code:
            return "java"
        if "#include <" in source_code or "int main(" in source_code:
            return "c"
        if "console.log" in source_code or "const " in source_code or "let " in source_code:
            return "javascript"

        return "unknown"

    def analyze(
        self,
        stderr: str = "",
        stdout: str = "",
        returncode: int = 0,
        source_code: str = "",
        language: Optional[str] = None,
    ) -> Dict[str, Any]:
        stderr = stderr or ""
        stdout = stdout or ""
        combined = stderr.strip()

        if returncode == 0 and not combined:
            return {
                "success": True,
                "has_error": False,
                "language": language or "unknown",
                "type": None,
                "category": None,
                "message": None,
                "line": None,
                "column": None,
                "source_line": None,
                "context_lines": {},
                "traceback": "",
                "stdout": stdout,
                "returncode": 0,
                "suggested_diagnosis": "Code executed successfully.",
            }

        lang = self._infer_language(combined, source_code, language)

        # Parse based on language
        error_type, error_category, line_number, column, diagnosis = (
            self._parse_error_details(combined, lang, source_code)
        )

        source_line = self._get_source_line(source_code, line_number)
        context_lines = self._get_context_lines(source_code, line_number)

        safe_print("\n========================================")
        safe_print("        NR AI ERROR ANALYZER")
        safe_print("========================================")
        safe_print(f"🌐 Language: {lang.upper()}")
        safe_print(f"❌ Error type: {error_type} ({error_category})")
        safe_print(f"📍 Error line: {line_number or 'Unknown'}" + (f":{column}" if column else ""))

        if source_line:
            safe_print(f"📝 Failing source: {source_line}")
        if diagnosis:
            safe_print(f"💡 Diagnosis: {diagnosis}")
        if combined:
            safe_print(f"\n📋 Error Log:\n{combined}")

        return {
            "success": False,
            "has_error": True,
            "language": lang,
            "type": error_type,
            "category": error_category,
            "message": combined,
            "line": line_number,
            "column": column,
            "source_line": source_line,
            "context_lines": context_lines,
            "traceback": combined,
            "stdout": stdout,
            "returncode": returncode,
            "suggested_diagnosis": diagnosis,
        }

    def _parse_error_details(
        self, text: str, language: str, source_code: str
    ) -> Tuple[str, str, Optional[int], Optional[int], str]:
        """Extracts (error_type, error_category, line_number, column, diagnosis)."""
        error_type = "UnknownError"
        error_category = "runtime"
        line_number: Optional[int] = None
        column: Optional[int] = None
        diagnosis = ""

        if language == "python":
            # Detect Python error type
            for err in self.KNOWN_PYTHON_ERRORS:
                if err in text:
                    error_type = err
                    break
            if error_type == "UnknownError":
                match = re.search(r"([A-Za-z_][A-Za-z0-9_]*Error)", text)
                if match:
                    error_type = match.group(1)

            # Category
            if error_type in ["SyntaxError", "IndentationError", "TabError"]:
                error_category = "syntax"
            elif error_type in ["ModuleNotFoundError", "ImportError"]:
                error_category = "import"
            elif error_type in ["TypeError", "ValueError"]:
                error_category = "type"
            elif error_type in ["NameError", "UnboundLocalError"]:
                error_category = "name"
            else:
                error_category = "runtime"

            # Line detection
            matches = re.findall(r'File "[^"]+", line (\d+)', text)
            if not matches:
                matches = re.findall(r"line\s+(\d+)", text, flags=re.IGNORECASE)
            if matches:
                line_number = int(matches[-1])

            # Diagnosis suggestions
            if error_type == "SyntaxError":
                if "expected ':'" in text or "invalid syntax" in text:
                    diagnosis = "Missing colon ':' or mismatched bracket/parenthesis."
                elif "unterminated string literal" in text or "EOL while scanning" in text:
                    diagnosis = "Unclosed string quote."
            elif error_type == "IndentationError":
                diagnosis = "Inconsistent indentation or missing indented block."
            elif error_type == "NameError":
                match_var = re.search(r"name '([^']+)' is not defined", text)
                var_name = match_var.group(1) if match_var else "variable"
                diagnosis = f"Variable or function '{var_name}' is used before definition or misspelled."
            elif error_type == "ZeroDivisionError":
                diagnosis = "Division by zero encountered."
            elif error_type == "TypeError":
                diagnosis = "Incompatible type passed or called."

        elif language == "java":
            # Java compilation error: File.java:12: error: ...
            comp_match = re.search(r"\.java:(\d+):\s*error:\s*(.*)", text)
            if comp_match:
                line_number = int(comp_match.group(1))
                error_type = "JavaCompilationError"
                error_category = "compilation"
                msg = comp_match.group(2).strip()
                diagnosis = f"Compilation error: {msg}"
            else:
                for err in self.KNOWN_JAVA_ERRORS:
                    if err in text:
                        error_type = err
                        break
                error_category = "runtime"
                line_match = re.search(r"\.java:(\d+)\)", text)
                if line_match:
                    line_number = int(line_match.group(1))
                diagnosis = f"Java runtime exception: {error_type}"

        elif language == "javascript":
            for err in self.KNOWN_JS_ERRORS:
                if err in text:
                    error_type = err
                    break
            error_category = "syntax" if error_type == "SyntaxError" else "runtime"
            line_match = re.search(r":(\d+):(\d+)", text)
            if line_match:
                line_number = int(line_match.group(1))
                column = int(line_match.group(2))
            diagnosis = f"JavaScript error: {error_type}"

        elif language == "c":
            error_category = "compilation"
            gcc_match = re.search(r":(\d+):(\d+):\s*(?:fatal\s+)?error:\s*(.*)", text)
            if gcc_match:
                line_number = int(gcc_match.group(1))
                column = int(gcc_match.group(2))
                error_type = "CCompilationError"
                diagnosis = gcc_match.group(3).strip()
            else:
                msvc_match = re.search(r"\((\d+)\):\s*error\s*C\d+:\s*(.*)", text)
                if msvc_match:
                    line_number = int(msvc_match.group(1))
                    error_type = "MSVCCompilationError"
                    diagnosis = msvc_match.group(2).strip()
                else:
                    error_type = "CError"
                    diagnosis = "C compilation or runtime failure."

        return (error_type, error_category, line_number, column, diagnosis)

    def _get_source_line(
        self, source_code: str, line_number: Optional[int]
    ) -> Optional[str]:
        if not source_code or line_number is None:
            return None
        lines = source_code.splitlines()
        index = line_number - 1
        if 0 <= index < len(lines):
            return lines[index].strip()
        return None

    def _get_context_lines(
        self, source_code: str, line_number: Optional[int], radius: int = 3
    ) -> Dict[int, str]:
        if not source_code or line_number is None:
            return {}
        lines = source_code.splitlines()
        index = line_number - 1
        start = max(0, index - radius)
        end = min(len(lines), index + radius + 1)

        return {i + 1: lines[i] for i in range(start, end)}


if __name__ == "__main__":
    analyzer = ErrorAnalyzer()

    safe_print("========================================")
    safe_print("     NR AI ERROR ANALYZER TEST")
    safe_print("========================================")

    # 1. Python Syntax Error
    py_code = 'def calc(a, b)\n    return a + b\n'
    res_py = analyzer.analyze(
        stderr='  File "test.py", line 1\n    def calc(a, b)\n                  ^\nSyntaxError: expected \':\'',
        source_code=py_code,
        language="python",
        returncode=1,
    )
    safe_print(f"Python Error Parsed: {res_py['type']} at line {res_py['line']}")

    # 2. Java Compilation Error
    java_code = 'public class Test {\n    public static void main(String[] args) {\n        int x = 10\n    }\n}'
    res_java = analyzer.analyze(
        stderr="Test.java:3: error: ';' expected\n        int x = 10\n                  ^",
        source_code=java_code,
        language="java",
        returncode=1,
    )
    safe_print(f"Java Error Parsed: {res_java['type']} at line {res_java['line']}")

    # 3. JS Reference Error
    js_code = 'console.log(myVar);'
    res_js = analyzer.analyze(
        stderr="ReferenceError: myVar is not defined\n    at Object.<anonymous> (test.js:1:13)",
        source_code=js_code,
        language="javascript",
        returncode=1,
    )
    safe_print(f"JS Error Parsed: {res_js['type']} at line {res_js['line']}")

    safe_print("\n========================================")
    safe_print("🟢 ERROR ANALYZER TEST COMPLETE")
    safe_print("========================================")