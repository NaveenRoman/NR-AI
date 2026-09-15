"""
NR-AI Android Runtime Diagnostics & Logcat Inspection Engine (Step 6 Phase 6).

Provides safe, bounded, and deterministic runtime perception and diagnostics for
authorized Android devices and emulators:
1. Bounded logcat capture with severity, package, and tag filtering
2. Runtime device info & process state extraction
3. Structured runtime error extraction (FATAL EXCEPTION, ANR, SecurityException,
   ResourceNotFound, ClassNotFound, ActivityNotFound)
4. Deterministic DiagnosticSnapshot generation
5. Advisory model consultation with strict JSON schema parsing
6. Authoritative ground-truth verification overriding hallucinated model claims
7. Comprehensive sensitive-data redaction
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import uuid

from app.agent.android_safety import (
    ALLOWED_ANDROID_DIAGNOSTIC_OPERATIONS,
    AUTHORIZED_DEVICE_SERIALS,
    AUTHORIZED_PACKAGE_NAME,
    AUTHORIZED_PROJECT_PATH,
    MAX_LOGCAT_BYTES,
    MAX_LOGCAT_LINES,
    MAX_SNAPSHOT_BYTES,
    AndroidErrorCode,
    AndroidSafetyError,
    AndroidSafetyGate,
    EmergencyStopActiveError,
)
from app.agent.android_tools import SafeAdbClient
from app.memory.audit_logger import AuditLogger

logger = logging.getLogger("NRAI.AndroidDiagnostics")


# -----------------------------------------------------------------------------
# Data Models: Log Levels, Entries, and Runtime Errors
# -----------------------------------------------------------------------------

class LogLevel(str, Enum):
    VERBOSE = "V"
    DEBUG = "D"
    INFO = "I"
    WARN = "W"
    ERROR = "E"
    FATAL = "F"
    UNKNOWN = "U"

    @classmethod
    def from_letter(cls, letter: str) -> "LogLevel":
        letter_up = (letter or "").strip().upper()
        for lvl in cls:
            if lvl.value == letter_up:
                return lvl
        return cls.UNKNOWN


class RuntimeErrorType(str, Enum):
    FATAL_EXCEPTION = "FATAL_EXCEPTION"
    ANDROID_RUNTIME_CRASH = "ANDROID_RUNTIME_CRASH"
    ANR = "ANR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    CLASS_NOT_FOUND = "CLASS_NOT_FOUND"
    ACTIVITY_LAUNCH_FAILURE = "ACTIVITY_LAUNCH_FAILURE"
    PACKAGE_ERROR = "PACKAGE_ERROR"
    UNCAUGHT_EXCEPTION = "UNCAUGHT_EXCEPTION"
    UNKNOWN_RUNTIME_ERROR = "UNKNOWN_RUNTIME_ERROR"


@dataclass
class LogEntry:
    """Structured representation of a single logcat entry."""
    timestamp: str
    pid: Optional[int]
    tid: Optional[int]
    level: LogLevel
    tag: str
    message: str
    raw: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "pid": self.pid,
            "tid": self.tid,
            "level": self.level.value,
            "tag": self.tag,
            "message": self.message,
            "raw": self.raw,
        }


@dataclass
class AndroidRuntimeError:
    """Structured runtime failure extracted from Android logcat or process dumps."""
    error_type: RuntimeErrorType
    exception_class: str
    message: str
    stack_trace: List[str] = field(default_factory=list)
    package_name: Optional[str] = None
    process_id: Optional[int] = None
    tag: Optional[str] = None
    raw_log_lines: List[str] = field(default_factory=list)
    source_file: Optional[str] = None
    source_line: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.error_type.value,
            "exception_class": self.exception_class,
            "message": self.message,
            "stack_trace": self.stack_trace,
            "package_name": self.package_name,
            "process_id": self.process_id,
            "tag": self.tag,
            "raw_log_lines": self.raw_log_lines,
            "source_file": self.source_file,
            "source_line": self.source_line,
        }


@dataclass
class DiagnosticSnapshot:
    """Immutable, bounded snapshot of device runtime state and logcat evidence."""
    snapshot_id: str
    timestamp: float
    device_serial: str
    device_info: Dict[str, Any]
    foreground_app: Dict[str, str]
    process_info: Dict[str, Any]
    detected_errors: List[AndroidRuntimeError]
    log_entries: List[LogEntry]
    log_summary: Dict[str, int]
    correlation_id: str
    raw_log_sample: str = ""

    def get_bounded_summary(self, max_errors: int = 5, max_logs: int = 30) -> Dict[str, Any]:
        """Returns a sanitized summary suitable for advisory LLM analysis."""
        return {
            "snapshot_id": self.snapshot_id,
            "device_serial": self.device_serial,
            "device_info": self.device_info,
            "foreground_app": self.foreground_app,
            "process_info": self.process_info,
            "detected_error_count": len(self.detected_errors),
            "detected_errors": [e.to_dict() for e in self.detected_errors[:max_errors]],
            "log_summary": self.log_summary,
            "recent_logs": [l.to_dict() for l in self.log_entries[-max_logs:]],
            "correlation_id": self.correlation_id,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp,
            "device_serial": self.device_serial,
            "device_info": self.device_info,
            "foreground_app": self.foreground_app,
            "process_info": self.process_info,
            "detected_errors": [e.to_dict() for e in self.detected_errors],
            "log_entries_count": len(self.log_entries),
            "log_summary": self.log_summary,
            "correlation_id": self.correlation_id,
            "raw_log_sample": self.raw_log_sample[:2000],
        }


# -----------------------------------------------------------------------------
# Sensitive Data Redactor
# -----------------------------------------------------------------------------

def redact_sensitive_runtime_data(text: str) -> str:
    """
    Sanitizes logcat text and diagnostic data, removing API keys, secrets,
    passwords, tokens, headers, and private keys.
    """
    if not text:
        return text

    # Redact Google / Gemini API keys
    text = re.sub(r"AIzaSy[A-Za-z0-9_-]{33}", "[REDACTED_GEMINI_KEY]", text)
    # Redact OpenAI API keys
    text = re.sub(r"sk-proj-[A-Za-z0-9_-]{20,}", "[REDACTED_OPENAI_KEY]", text)
    text = re.sub(r"sk-[A-Za-z0-9_-]{20,}", "[REDACTED_OPENAI_KEY]", text)
    # Redact GitHub tokens
    text = re.sub(r"ghp_[A-Za-z0-9_]{36,}", "[REDACTED_GITHUB_TOKEN]", text)
    text = re.sub(r"github_pat_[A-Za-z0-9_]{36,}", "[REDACTED_GITHUB_TOKEN]", text)

    # Redact Bearer / Authorization tokens
    text = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED_TOKEN]", text)
    text = re.sub(r"(?i)Authorization:\s*(?!\s*Bearer\b)[^\r\n]+", "Authorization: [REDACTED_AUTH_HEADER]", text)
    text = re.sub(r"(?i)(?:session_id|sessionId|sess_id)\s*=\s*[^\s;]+", "session_id=[REDACTED]", text)

    # Redact Private Keys
    text = re.sub(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]*?-----END [A-Z ]+ PRIVATE KEY-----", "[REDACTED_PRIVATE_KEY]", text)

    # Redact password / secret key-value assignments
    patterns = [
        (r'''(?i)(["']?(?:password|passwd|secret|api[_-]?key|access[_-]?token|authToken)["']?\s*[:=]\s*["'])([^"']+)(["'])''', r'\g<1>[REDACTED]\g<3>'),
        (r'''(?i)(\b(?:password|passwd|secret)\s*=\s*)([^\s,;&]+)''', r'\g<1>[REDACTED]'),
    ]
    for pattern, repl in patterns:
        text = re.sub(pattern, repl, text)

    return text


# -----------------------------------------------------------------------------
# Android Log Parser
# -----------------------------------------------------------------------------

class AndroidLogParser:
    """
    Parses Android logcat output into structured LogEntry and AndroidRuntimeError objects.
    Enforces size boundaries and redacts credentials.
    """

    # Threadtime regex: MM-DD HH:MM:SS.mmm  PID  TID L TAG: message
    THREADTIME_RE = re.compile(
        r"^(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)\s+(\d+)\s+(\d+)\s+([VDIWEF])\s+([^:]+?):\s*(.*)$"
    )
    # Brief regex: L/TAG(PID): message
    BRIEF_RE = re.compile(
        r"^([VDIWEF])/([^(\n]+?)\(\s*(\d+)\):\s*(.*)$"
    )

    @classmethod
    def parse_lines(cls, log_text: str, max_lines: int = MAX_LOGCAT_LINES) -> List[LogEntry]:
        """Parses multi-line logcat text into structured LogEntry instances."""
        if not log_text:
            return []

        clean_text = redact_sensitive_runtime_data(log_text)
        lines = clean_text.splitlines()[:max_lines]
        entries: List[LogEntry] = []

        for line in lines:
            raw = line.strip()
            if not raw:
                continue

            # Try threadtime format first
            m_tt = cls.THREADTIME_RE.match(raw)
            if m_tt:
                entries.append(LogEntry(
                    timestamp=m_tt.group(1),
                    pid=int(m_tt.group(2)),
                    tid=int(m_tt.group(3)),
                    level=LogLevel.from_letter(m_tt.group(4)),
                    tag=m_tt.group(5).strip(),
                    message=m_tt.group(6),
                    raw=raw,
                ))
                continue

            # Try brief format
            m_br = cls.BRIEF_RE.match(raw)
            if m_br:
                entries.append(LogEntry(
                    timestamp="",
                    pid=int(m_br.group(3)),
                    tid=None,
                    level=LogLevel.from_letter(m_br.group(1)),
                    tag=m_br.group(2).strip(),
                    message=m_br.group(4),
                    raw=raw,
                ))
                continue

            # Fallback for continuation lines or unstructured text
            entries.append(LogEntry(
                timestamp="",
                pid=None,
                tid=None,
                level=LogLevel.UNKNOWN,
                tag="",
                message=raw,
                raw=raw,
            ))

        return entries

    @classmethod
    def extract_runtime_errors(
        cls,
        log_text: str,
        target_package: Optional[str] = None,
    ) -> List[AndroidRuntimeError]:
        """
        Extracts structured runtime errors from logcat output.
        Detects FATAL EXCEPTION, ANR, SecurityException, ResourceNotFound,
        ClassNotFound, ActivityNotFound, and uncaught exceptions.
        """
        if not log_text:
            return []

        clean_text = redact_sensitive_runtime_data(log_text)
        lines = clean_text.splitlines()
        errors: List[AndroidRuntimeError] = []

        i = 0
        n = len(lines)
        while i < n:
            line = lines[i].strip()

            # 1. Detect FATAL EXCEPTION / AndroidRuntime crashes
            if "FATAL EXCEPTION:" in line or ("AndroidRuntime" in line and "fatal" in line.lower()):
                err = cls._parse_fatal_exception(lines, i, target_package)
                if err:
                    errors.append(err)
                    i += max(1, len(err.raw_log_lines))
                    continue

            # 2. Detect ANR (Application Not Responding)
            if "ANR in" in line or "am_anr" in line:
                err = cls._parse_anr(lines, i, target_package)
                if err:
                    errors.append(err)
                    i += max(1, len(err.raw_log_lines))
                    continue

            # 3. Detect SecurityException / Permission denial
            if "SecurityException" in line or "Permission Denial" in line:
                err = cls._parse_permission_denial(lines, i, target_package)
                if err:
                    errors.append(err)
                    i += max(1, len(err.raw_log_lines))
                    continue

            # 4. Detect Resources.NotFoundException
            if "Resources$NotFoundException" in line or "ResourceNotFound" in line or ("resource" in line.lower() and "not found" in line.lower() and ("android" in line.lower() or "exception" in line.lower())):
                err = cls._parse_resource_not_found(lines, i, target_package)
                if err:
                    errors.append(err)
                    i += max(1, len(err.raw_log_lines))
                    continue

            # 5. Detect ClassNotFoundException / NoClassDefFoundError
            if "ClassNotFoundException" in line or "NoClassDefFoundError" in line:
                err = cls._parse_class_not_found(lines, i, target_package)
                if err:
                    errors.append(err)
                    i += max(1, len(err.raw_log_lines))
                    continue

            # 6. Detect ActivityNotFoundException / Launch failure
            if "ActivityNotFoundException" in line or "Unable to find explicit activity class" in line:
                err = cls._parse_activity_launch_failure(lines, i, target_package)
                if err:
                    errors.append(err)
                    i += max(1, len(err.raw_log_lines))
                    continue

            i += 1

        return errors

    @classmethod
    def _parse_fatal_exception(
        cls,
        lines: List[str],
        start_idx: int,
        target_package: Optional[str],
    ) -> Optional[AndroidRuntimeError]:
        raw_lines = [lines[start_idx]]
        pkg_name = target_package
        pid = None
        ex_class = "AndroidRuntimeError"
        ex_msg = ""
        stack: List[str] = []

        j = start_idx + 1
        while j < len(lines) and j < start_idx + 60:
            l = lines[j].strip()
            raw_lines.append(lines[j])

            # Process / PID line: Process: com.nrai.test, PID: 12345
            m_proc = re.search(r"Process:\s*([a-zA-Z0-9._]+)(?:,\s*PID:\s*(\d+))?", l)
            if m_proc:
                pkg_name = m_proc.group(1)
                if m_proc.group(2):
                    pid = int(m_proc.group(2))

            # Exception class line: java.lang.NullPointerException: message
            m_ex = re.search(r"(?:java\.lang\.|kotlin\.|android\.[a-zA-Z0-9_.]+\.)([a-zA-Z0-9_]+Exception|[a-zA-Z0-9_]+Error):\s*(.*)", l)
            if m_ex:
                ex_class = m_ex.group(1)
                ex_msg = m_ex.group(2).strip()

            # Stack trace line: at com.nrai.test.MainActivity.onCreate(MainActivity.kt:42)
            if l.startswith("at ") or "\tat " in l or ": at " in l:
                clean_frame = re.sub(r"^.*?at\s+", "at ", l)
                stack.append(clean_frame)
            elif stack and not l.startswith("Caused by:") and not ("at " in l):
                break

            j += 1

        src_file = None
        src_line = None
        target_pkg = target_package or AUTHORIZED_PACKAGE_NAME
        for frame in stack:
            if target_pkg in frame:
                m_frame = re.search(r"\(([^:]+?\.(?:kt|java)):(\d+)\)", frame)
                if m_frame:
                    src_file = m_frame.group(1)
                    src_line = int(m_frame.group(2))
                    break

        return AndroidRuntimeError(
            error_type=RuntimeErrorType.FATAL_EXCEPTION,
            exception_class=ex_class,
            message=ex_msg or "Fatal unhandled exception in Android runtime.",
            stack_trace=stack,
            package_name=pkg_name or target_package,
            process_id=pid,
            tag="AndroidRuntime",
            raw_log_lines=raw_lines,
            source_file=src_file,
            source_line=src_line,
        )

    @classmethod
    def _parse_anr(
        cls,
        lines: List[str],
        start_idx: int,
        target_package: Optional[str],
    ) -> Optional[AndroidRuntimeError]:
        raw_lines = [lines[start_idx]]
        pkg_name = target_package
        reason = "Application Not Responding"

        m = re.search(r"ANR in\s+([a-zA-Z0-9._]+)", lines[start_idx])
        if m:
            pkg_name = m.group(1)

        j = start_idx + 1
        while j < len(lines) and j < start_idx + 20:
            l = lines[j].strip()
            raw_lines.append(lines[j])
            if "Reason:" in l:
                reason = l.split("Reason:", 1)[1].strip()
            elif "Load:" in l or "CPU usage" in l:
                break
            j += 1

        return AndroidRuntimeError(
            error_type=RuntimeErrorType.ANR,
            exception_class="ApplicationNotResponding",
            message=f"ANR in {pkg_name}: {reason}",
            stack_trace=[],
            package_name=pkg_name,
            tag="ActivityManager",
            raw_log_lines=raw_lines,
        )

    @classmethod
    def _parse_permission_denial(
        cls,
        lines: List[str],
        start_idx: int,
        target_package: Optional[str],
    ) -> Optional[AndroidRuntimeError]:
        l = lines[start_idx].strip()
        msg = l
        pkg = target_package
        m_pkg = re.search(r"package\s+([a-zA-Z0-9._]+)", l)
        if m_pkg:
            pkg = m_pkg.group(1)

        return AndroidRuntimeError(
            error_type=RuntimeErrorType.PERMISSION_DENIED,
            exception_class="SecurityException",
            message=msg,
            package_name=pkg,
            tag="SecurityException",
            raw_log_lines=[lines[start_idx]],
        )

    @classmethod
    def _parse_resource_not_found(
        cls,
        lines: List[str],
        start_idx: int,
        target_package: Optional[str],
    ) -> Optional[AndroidRuntimeError]:
        l = lines[start_idx].strip()
        return AndroidRuntimeError(
            error_type=RuntimeErrorType.RESOURCE_NOT_FOUND,
            exception_class="Resources$NotFoundException",
            message=l,
            package_name=target_package,
            tag="Resources",
            raw_log_lines=[lines[start_idx]],
        )

    @classmethod
    def _parse_class_not_found(
        cls,
        lines: List[str],
        start_idx: int,
        target_package: Optional[str],
    ) -> Optional[AndroidRuntimeError]:
        l = lines[start_idx].strip()
        cls_name = "UnknownClass"
        m = re.search(r"(?:ClassNotFoundException|NoClassDefFoundError):\s*(?:Didn't find class \"([^\"]+)\"|([a-zA-Z0-9._$]+))", l)
        if m:
            cls_name = m.group(1) or m.group(2)

        return AndroidRuntimeError(
            error_type=RuntimeErrorType.CLASS_NOT_FOUND,
            exception_class="ClassNotFoundException",
            message=f"Class not found: {cls_name}",
            package_name=target_package,
            tag="AndroidRuntime",
            raw_log_lines=[lines[start_idx]],
        )

    @classmethod
    def _parse_activity_launch_failure(
        cls,
        lines: List[str],
        start_idx: int,
        target_package: Optional[str],
    ) -> Optional[AndroidRuntimeError]:
        l = lines[start_idx].strip()
        return AndroidRuntimeError(
            error_type=RuntimeErrorType.ACTIVITY_LAUNCH_FAILURE,
            exception_class="ActivityNotFoundException",
            message=l,
            package_name=target_package,
            tag="ActivityTaskManager",
            raw_log_lines=[lines[start_idx]],
        )


# -----------------------------------------------------------------------------
# Android Diagnostics Controller
# -----------------------------------------------------------------------------

class AndroidDiagnosticsController:
    """
    High-level orchestrator connecting ADB client, safety gate, log parser,
    audit logger, and advisory model diagnostics.
    """

    def __init__(
        self,
        safety_gate: Optional[AndroidSafetyGate] = None,
        adb_client: Optional[SafeAdbClient] = None,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.safety = safety_gate or AndroidSafetyGate()
        self.adb = adb_client or SafeAdbClient()
        self.audit = audit_logger or AuditLogger()
        self.parser = AndroidLogParser()

    def capture_logs(
        self,
        serial: str = "emulator-5554",
        lines: int = 100,
        severity: Optional[str] = None,
        package_name: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> str:
        """Captures bounded, filtered, and sanitized logcat entries."""
        valid_serial = self.safety.validate_device_serial(serial)
        bounded_lines = self.safety.validate_logcat_bounds(lines)
        self.safety.validate_diagnostic_operation("capture_logcat")

        raw_logs = self.adb.capture_logcat_advanced(
            serial=valid_serial,
            lines=bounded_lines,
            severity=severity,
            filter_package=package_name,
            filter_tag=tag,
        )
        return raw_logs

    def create_diagnostic_snapshot(
        self,
        serial: str = "emulator-5554",
        package_name: str = AUTHORIZED_PACKAGE_NAME,
        lines: int = 200,
        severity: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> DiagnosticSnapshot:
        """
        Creates a comprehensive, immutable DiagnosticSnapshot capturing device info,
        foreground app, process info, logcat entries, and structured runtime errors.
        """
        valid_serial = self.safety.validate_device_serial(serial)
        bounded_lines = self.safety.validate_logcat_bounds(lines)
        self.safety.validate_diagnostic_operation("create_diagnostic_snapshot")

        corr_id = correlation_id or f"diag_{uuid.uuid4().hex[:8]}"

        # 1. Device Info
        dev_info = self.adb.get_device_info(valid_serial)

        # 2. Foreground App
        fg_app = self.adb.get_foreground_app(valid_serial)

        # 3. Process Info
        proc_info = self.adb.get_process_info(valid_serial, package_name)

        # 4. Capture Logcat
        raw_logs = self.adb.capture_logcat_advanced(
            serial=valid_serial,
            lines=bounded_lines,
            severity=severity,
            filter_package=package_name,
        )

        # 5. Parse entries and extract structured errors
        entries = self.parser.parse_lines(raw_logs)
        errors = self.parser.extract_runtime_errors(raw_logs, target_package=package_name)

        # Compute summary counts
        summary = {
            "total_lines": len(entries),
            "errors": sum(1 for e in entries if e.level == LogLevel.ERROR),
            "warnings": sum(1 for e in entries if e.level == LogLevel.WARN),
            "fatal": sum(1 for e in entries if e.level == LogLevel.FATAL),
            "info": sum(1 for e in entries if e.level == LogLevel.INFO),
            "debug": sum(1 for e in entries if e.level == LogLevel.DEBUG),
            "detected_runtime_failures": len(errors),
        }

        snapshot = DiagnosticSnapshot(
            snapshot_id=f"snap_{uuid.uuid4().hex[:8]}",
            timestamp=time.time(),
            device_serial=valid_serial,
            device_info=dev_info,
            foreground_app=fg_app,
            process_info=proc_info,
            detected_errors=errors,
            log_entries=entries,
            log_summary=summary,
            correlation_id=corr_id,
            raw_log_sample=raw_logs[:2000],
        )

        self._audit_diagnostic_event("DIAGNOSTIC_SNAPSHOT_CREATED", {
            "snapshot_id": snapshot.snapshot_id,
            "serial": valid_serial,
            "package": package_name,
            "detected_errors": len(errors),
            "correlation_id": corr_id,
        })

        return snapshot

    def diagnose_crash(
        self,
        serial: str = "emulator-5554",
        package_name: str = AUTHORIZED_PACKAGE_NAME,
        lines: int = 300,
    ) -> Dict[str, Any]:
        """
        Performs a deterministic crash diagnosis session.
        Ground truth is strictly authoritative.
        """
        snapshot = self.create_diagnostic_snapshot(serial, package_name, lines=lines)
        has_crash = len(snapshot.detected_errors) > 0

        primary_error = snapshot.detected_errors[0] if has_crash else None

        result = {
            "has_crash": has_crash,
            "device_serial": serial,
            "package_name": package_name,
            "detected_errors_count": len(snapshot.detected_errors),
            "primary_error": primary_error.to_dict() if primary_error else None,
            "summary": (
                f"Crash detected: {primary_error.exception_class} in {primary_error.package_name}"
                if primary_error else "No active crash detected in recent logs."
            ),
            "source_location": (
                f"{primary_error.source_file}:{primary_error.source_line}"
                if primary_error and primary_error.source_file else None
            ),
            "snapshot_id": snapshot.snapshot_id,
            "correlation_id": snapshot.correlation_id,
        }

        self._audit_diagnostic_event("DIAGNOSTIC_CRASH_ANALYZED", result)
        return result

    def verify_model_diagnosis(
        self,
        snapshot: DiagnosticSnapshot,
        model_diagnosis: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Authoritative deterministic verification of advisory model claims.
        If model claims a crash or exception occurred that is NOT supported
        by snapshot evidence, deterministic evidence overrides the hallucination.
        """
        claimed_crash = bool(model_diagnosis.get("crash_detected", False) or model_diagnosis.get("likely_cause"))
        actual_crash = len(snapshot.detected_errors) > 0

        verified = False
        override_reason = None

        if claimed_crash and not actual_crash:
            # Model hallucinated a crash when ground truth has none
            verified = False
            override_reason = (
                "Deterministic verification OVERRIDE: Advisory model claimed a crash occurred, "
                "but ground-truth logcat evidence contains 0 runtime exceptions or fatal errors."
            )
            final_assessment = "NO_CRASH_VERIFIED"
        elif actual_crash:
            verified = True
            final_assessment = "CRASH_VERIFIED"
        else:
            verified = True
            final_assessment = "CLEAN_STATE_VERIFIED"

        return {
            "verified": verified,
            "final_assessment": final_assessment,
            "actual_errors_count": len(snapshot.detected_errors),
            "model_diagnosis": model_diagnosis,
            "override_reason": override_reason,
            "deterministic_evidence": [e.to_dict() for e in snapshot.detected_errors],
        }

    def _audit_diagnostic_event(self, event_type: str, details: Dict[str, Any]) -> None:
        try:
            sanitized = json.loads(redact_sensitive_runtime_data(json.dumps(details)))
            self.audit.log_event(event_type=event_type, details=sanitized)
        except Exception as e:
            logger.warning(f"Failed to emit audit event {event_type}: {e}")


# -----------------------------------------------------------------------------
# Model Advisory Prompting & Strict Schema Parsing
# -----------------------------------------------------------------------------

def build_model_diagnosis_prompt(snapshot: DiagnosticSnapshot, user_query: str) -> Tuple[str, str]:
    """
    Constructs a strictly bounded, advisory prompt for LLM runtime diagnosis.
    Never includes passwords, secrets, or raw unstructured tokens.
    """
    sys_prompt = (
        "You are a STRICTLY ADVISORY assistant for Android runtime error diagnosis.\n"
        "You do NOT have execution authority. You cannot execute ADB, shell, or file operations.\n"
        "Analyze the sanitized diagnostic data and identify the likely cause.\n"
        "Respond ONLY with valid JSON matching this schema:\n"
        "{\n"
        '  "summary": "Brief summary of the issue or clean state",\n'
        '  "crash_detected": true | false,\n'
        '  "likely_cause": "Root cause explanation",\n'
        '  "confidence": 0.0 to 1.0,\n'
        '  "evidence_log_lines": ["line 1", "line 2"],\n'
        '  "recommendations": ["step 1", "step 2"]\n'
        "}\n"
    )

    summary = snapshot.get_bounded_summary(max_errors=5, max_logs=30)
    user_prompt = (
        f"Query: {user_query}\n"
        f"Device: {summary['device_serial']} ({summary['device_info'].get('model', 'unknown')}, "
        f"Android {summary['device_info'].get('os_version', '?')} / API {summary['device_info'].get('sdk_level', '?')})\n"
        f"Foreground App: {summary['foreground_app'].get('package')}/{summary['foreground_app'].get('activity')}\n"
        f"Detected Failures ({summary['detected_error_count']}):\n"
        f"{json.dumps(summary['detected_errors'], indent=2)}\n\n"
        f"Log Summary: {json.dumps(summary['log_summary'])}\n\n"
        f"Recent Log Sample:\n"
        f"{json.dumps(summary['recent_logs'], indent=2)}\n\n"
        "Analyze the diagnostic evidence and propose your advisory diagnosis in JSON."
    )
    return sys_prompt, user_prompt


def parse_model_diagnosis(raw_text: str) -> Dict[str, Any]:
    """
    Strictly validates and parses JSON diagnosis proposal from advisory model.
    Rejects non-JSON, missing required fields, or malformed data.
    """
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        data = json.loads(cleaned)
    except Exception as e:
        raise AndroidSafetyError(
            AndroidErrorCode.MALFORMED_DIAGNOSTIC_SCHEMA,
            f"Advisory model diagnosis is not valid JSON: {e}",
        )

    if not isinstance(data, dict):
        raise AndroidSafetyError(
            AndroidErrorCode.MALFORMED_DIAGNOSTIC_SCHEMA,
            "Model diagnosis must be a JSON dictionary.",
        )

    required_fields = ("summary", "likely_cause", "confidence", "recommendations")
    for field_name in required_fields:
        if field_name not in data:
            raise AndroidSafetyError(
                AndroidErrorCode.MALFORMED_DIAGNOSTIC_SCHEMA,
                f"Model diagnosis missing required field '{field_name}'.",
            )

    try:
        conf = float(data.get("confidence", 0.0))
        data["confidence"] = max(0.0, min(conf, 1.0))
    except (ValueError, TypeError):
        data["confidence"] = 0.5

    if not isinstance(data.get("recommendations"), list):
        data["recommendations"] = [str(data["recommendations"])]

    return data