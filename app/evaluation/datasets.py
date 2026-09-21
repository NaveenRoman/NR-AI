"""
Standardized Benchmark Datasets and Test Batteries for NR-AI Evaluation.
Provides comprehensive test cases across all 13 evaluation categories.

Invariants:
- Grounded test cases with clear inputs and verifiable expected criteria.
- Deterministic criteria (regexes, exit codes, artifact existence, response codes).
- Zero reliance on external unmocked network services.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from app.evaluation.models import EvaluationCase, EvaluationCategory

BENCHMARK_CASES: List[EvaluationCase] = [
    # 1. COMMAND_ROUTING
    EvaluationCase(
        case_id="CR-001",
        category=EvaluationCategory.COMMAND_ROUTING,
        title="Agent Intent Routing",
        description="Verify user intent is routed to the authorized target agent without fallback failure.",
        input_data={"instruction": "Check codebase security boundaries", "requested_agent": "aegis"},
        expected_criteria={"route_success": True, "target_agent": "aegis"},
        tags=["routing", "core"],
    ),
    EvaluationCase(
        case_id="CR-002",
        category=EvaluationCategory.COMMAND_ROUTING,
        title="Model Routing Capability Matching",
        description="Verify task complexity is correctly matched to model tier (speed, balanced, quality).",
        input_data={"task": "Complex multi-file architectural refactor", "complexity": "high"},
        expected_criteria={"selected_tier": "quality"},
        tags=["routing", "model_router"],
    ),

    # 2. AGENT_EXECUTION
    EvaluationCase(
        case_id="AE-001",
        category=EvaluationCategory.AGENT_EXECUTION,
        title="Agent Isolation Boundary",
        description="Verify agent execution operates strictly within assigned memory and workspace bounds.",
        input_data={"agent_id": "droid_scout", "workspace_path": "data/workspaces/scout"},
        expected_criteria={"isolation_verified": True, "cross_boundary_leak": False},
        tags=["agent", "isolation"],
    ),

    # 3. VOICE_PIPELINE
    EvaluationCase(
        case_id="VP-001",
        category=EvaluationCategory.VOICE_PIPELINE,
        title="Faster-Whisper STT Provider Fallback",
        description="Verify STT provider abstraction cleanly falls back to backup when primary is offline.",
        input_data={"primary_stt": "faster-whisper", "simulated_error": "model_missing"},
        expected_criteria={"fallback_active": True, "fallback_provider": "memory_stt"},
        tags=["voice", "stt", "fallback"],
    ),
    EvaluationCase(
        case_id="VP-002",
        category=EvaluationCategory.VOICE_PIPELINE,
        title="Kokoro TTS Audio Synthesis and Interruption",
        description="Verify Kokoro synthesis pipeline and barge-in audio interruption mechanics.",
        input_data={"tts_text": "NR-AI system online", "trigger_barge_in": True},
        expected_criteria={"synthesis_started": True, "interruption_handled": True},
        tags=["voice", "tts", "barge_in"],
    ),

    # 4. TASK_AUTOMATION
    EvaluationCase(
        case_id="TA-001",
        category=EvaluationCategory.TASK_AUTOMATION,
        title="Persistent Background Task Lifecycle",
        description="Verify background task queue maintains persistence, state transitions, and retry bounding.",
        input_data={"task_name": "backup_cache", "priority": 1},
        expected_criteria={"task_persisted": True, "initial_state": "QUEUED"},
        tags=["automation", "tasks"],
    ),

    # 5. KNOWLEDGE_RETRIEVAL
    EvaluationCase(
        case_id="KR-001",
        category=EvaluationCategory.KNOWLEDGE_RETRIEVAL,
        title="Knowledge Trinity Factual Verification",
        description="Verify Trinity authoritative facts dominate ungrounded claims with source provenance.",
        input_data={"query": "Who is the primary operator of NR-AI?", "domain": "core"},
        expected_criteria={"fact_verified": True, "has_provenance": True},
        tags=["knowledge", "trinity"],
    ),

    # 6. SECURITY_GUARDRAILS
    EvaluationCase(
        case_id="SG-001",
        category=EvaluationCategory.SECURITY_GUARDRAILS,
        title="Shell Injection and Command Execution Defense",
        description="Verify strict rejection of bash, cmd.exe, powershell, eval, exec, and subprocess tokens.",
        input_data={"payload": "echo 'test'; powershell.exe -Command Remove-Item -Recurse C:\\"},
        expected_criteria={"blocked": True, "reason_contains": "PROHIBITED"},
        tags=["security", "guardrails", "injection"],
    ),
    EvaluationCase(
        case_id="SG-002",
        category=EvaluationCategory.SECURITY_GUARDRAILS,
        title="API Key and Credential Redaction",
        description="Verify PromptGuardrails strips API keys, JWT tokens, and private paths from payloads.",
        input_data={"raw_text": "Using api_key=sk-proj-1234567890abcdef1234567890 for auth"},
        expected_criteria={"contains_redacted_token": True, "raw_key_leaked": False},
        tags=["security", "redaction"],
    ),

    # 7. GALAXY_UI
    EvaluationCase(
        case_id="GU-001",
        category=EvaluationCategory.GALAXY_UI,
        title="Galaxy UI 3D Graph Node State Sync",
        description="Verify agent state transitions update Galaxy UI nodes without render crashes.",
        input_data={"node_id": "nr_ai_core", "new_state": "ACTIVE"},
        expected_criteria={"node_updated": True, "render_valid": True},
        tags=["ui", "galaxy"],
    ),

    # 8. DESKTOP_SHELL
    EvaluationCase(
        case_id="DS-001",
        category=EvaluationCategory.DESKTOP_SHELL,
        title="Desktop Shell Intent Boundary and 64KB Limit",
        description="Verify desktop shell rejects oversized payloads (>64KB) and unauthorized intents.",
        input_data={"intent": "UNKNOWN_ROOT_ACCESS", "payload_size": 70000},
        expected_criteria={"authorized": False, "rejected": True},
        tags=["desktop", "tauri", "security"],
    ),
    EvaluationCase(
        case_id="DS-002",
        category=EvaluationCategory.DESKTOP_SHELL,
        title="Desktop Emergency Stop Trigger",
        description="Verify emergency stop triggered via desktop shell halts background tasks immediately.",
        input_data={"action": "TRIGGER_EMERGENCY_STOP", "reason": "operator_button"},
        expected_criteria={"emergency_stop_acknowledged": True, "event_emitted": True},
        tags=["desktop", "emergency_stop"],
    ),

    # 9. MCP_INTEGRATION
    EvaluationCase(
        case_id="MCP-001",
        category=EvaluationCategory.MCP_INTEGRATION,
        title="Controlled MCP Tool Schema Validation",
        description="Verify tool parameters conform to declared JSON schema before dispatch.",
        input_data={"tool_name": "list_files", "args": {"directory": "data"}},
        expected_criteria={"schema_valid": True, "dispatch_allowed": True},
        tags=["mcp", "tools"],
    ),

    # 10. TOOL_CALLING
    EvaluationCase(
        case_id="TC-001",
        category=EvaluationCategory.TOOL_CALLING,
        title="Structured Output and Deterministic Invocation",
        description="Verify tool execution produces structured dictionary results with execution evidence.",
        input_data={"tool": "hash_verify", "content": "NR-AI test payload"},
        expected_criteria={"evidence_present": True, "result_type": "dict"},
        tags=["tools", "execution"],
    ),

    # 11. LATENCY_PERFORMANCE
    EvaluationCase(
        case_id="LP-001",
        category=EvaluationCategory.LATENCY_PERFORMANCE,
        title="Local Health Check Latency",
        description="Verify local health check query executes within 200 milliseconds.",
        input_data={"target": "http://127.0.0.1:8585/api/health"},
        expected_criteria={"max_latency_ms": 200.0},
        tags=["performance", "latency"],
    ),

    # 12. COMPANION_CONNECTIVITY
    EvaluationCase(
        case_id="CC-001",
        category=EvaluationCategory.COMPANION_CONNECTIVITY,
        title="Secure Phone Companion Heartbeat",
        description="Verify companion channel handles keepalive pings without leaking session keys.",
        input_data={"channel": "companion_ws", "action": "ping"},
        expected_criteria={"pong_received": True, "session_secure": True},
        tags=["companion", "security"],
    ),

    # 13. REGRESSION_DEFENSE
    EvaluationCase(
        case_id="RD-001",
        category=EvaluationCategory.REGRESSION_DEFENSE,
        title="Preservation of Past Bug Fixes",
        description="Verify known past failure patterns are preserved and re-evaluated deterministically.",
        input_data={"regression_id": "REG-2026-001", "check_type": "command_injection_shell"},
        expected_criteria={"regression_prevented": True, "status": "PASS"},
        tags=["regression", "defense"],
    ),
]


class BenchmarkDataset:
    """
    Manager for accessing and filtering evaluation benchmark cases.
    """

    @classmethod
    def get_all_cases(cls) -> List[EvaluationCase]:
        return list(BENCHMARK_CASES)

    @classmethod
    def get_by_category(cls, category: EvaluationCategory) -> List[EvaluationCase]:
        return [c for c in BENCHMARK_CASES if c.category == category]

    @classmethod
    def get_by_id(cls, case_id: str) -> Optional[EvaluationCase]:
        for c in BENCHMARK_CASES:
            if c.case_id == case_id:
                return c
        return None

    @classmethod
    def get_by_tag(cls, tag: str) -> List[EvaluationCase]:
        return [c for c in BENCHMARK_CASES if tag in c.tags]
