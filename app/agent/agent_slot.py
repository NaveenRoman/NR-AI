"""
NR AI 10-Agent Slot Architecture.

Defines exactly 10 logical agent slots with dedicated operational roles,
preferred models, fallback chains, live availability status, and execution tracking.

Slots:
- AGENT 1: Primary Architect / Main Task Executor (Preferred: gpt-6-astra)
- AGENT 2: Senior Implementation Specialist (Preferred: gpt-5.6-sol)
- AGENT 3: Independent Implementation Specialist (Preferred: gpt-5.6-terra)
- AGENT 4: Fast Task Specialist (Preferred: gpt-5.6-luna)
- AGENT 5: Independent Verifier (Preferred: gemini-3.6-flash)
- AGENT 6: Code Reviewer (Preferred: gpt-5.6-sol)
- AGENT 7: Test and Evidence Verifier (Preferred: gemini-3.6-flash)
- AGENT 8: Error Diagnosis Specialist (Preferred: gpt-5.6-terra)
- AGENT 9: Recovery / Repair Specialist (Preferred: gpt-6-astra)
- AGENT 10: Final Quality Gate (Independent verification model)
"""

from dataclasses import dataclass, field
import logging
import threading
from typing import Any, Dict, List, Optional

from app.agent.model_provider import UnifiedModelProvider
from app.config.model_config import (
    GEMINI_FLASH_LATEST,
    GEMINI_3_7_FLASH,
    GEMINI_3_6_FLASH,
    GEMINI_3_5_FLASH,
    GPT_5_6_LUNA,
    GPT_5_6_SOL,
    GPT_5_6_TERRA,
    GPT_6_ASTRA,
    PROVIDER_GOOGLE,
    PROVIDER_OPENAI,
    ModelConfig,
)

logger = logging.getLogger("NRAI.AgentSlot")


@dataclass
class AgentSlot:
    """Represents a distinct logical agent slot with defined responsibilities."""

    slot_id: int
    name: str
    role: str
    preferred_model: str
    provider: str
    fallback_model: str
    active_model: str = ""
    fallback_reason: Optional[str] = None
    is_live_api_verified: bool = False
    status: str = "IDLE"  # IDLE, BUSY, PAUSED, ERROR
    current_task_id: Optional[str] = None
    tasks_completed: int = 0
    tasks_failed: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "name": self.name,
            "role": self.role,
            "preferred_model": self.preferred_model,
            "provider": self.provider,
            "active_model": self.active_model,
            "fallback_model": self.fallback_model,
            "fallback_reason": self.fallback_reason,
            "is_live_api_verified": self.is_live_api_verified,
            "status": self.status,
            "current_task_id": self.current_task_id,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
        }


class SlotManager:
    """
    Manages the lifecycle, model assignment, and status of exactly 10 agent slots.
    """

    TOTAL_SLOTS = 10

    def __init__(
        self,
        provider: Optional[UnifiedModelProvider] = None,
        config: Optional[ModelConfig] = None,
    ):
        self.config = config or ModelConfig.from_env()
        self.provider = provider or UnifiedModelProvider(config=self.config)
        self._lock = threading.Lock()
        self._slots: Dict[int, AgentSlot] = {}
        self._initialize_slots()
        self.refresh_model_assignments()

    def _initialize_slots(self) -> None:
        """Create exactly 10 agent slots with predefined roles and models."""
        definitions = [
            (
                1,
                "Agent-1-Architect",
                "Primary Architect / Main Task Executor",
                GPT_6_ASTRA,
                PROVIDER_OPENAI,
                GPT_5_6_SOL,
            ),
            (
                2,
                "Agent-2-SeniorDev",
                "Senior Implementation Specialist",
                GPT_5_6_SOL,
                PROVIDER_OPENAI,
                GPT_5_6_TERRA,
            ),
            (
                3,
                "Agent-3-IndepDev",
                "Independent Implementation Specialist",
                GPT_5_6_TERRA,
                PROVIDER_OPENAI,
                GPT_5_6_LUNA,
            ),
            (
                4,
                "Agent-4-FastDev",
                "Fast Task Specialist",
                GPT_5_6_LUNA,
                PROVIDER_OPENAI,
                GPT_5_6_LUNA,
            ),
            (
                5,
                "Agent-5-Verifier",
                "Independent Verifier",
                GEMINI_3_6_FLASH,
                PROVIDER_GOOGLE,
                GEMINI_FLASH_LATEST,
            ),
            (
                6,
                "Agent-6-CodeReviewer",
                "Code Reviewer",
                GPT_5_6_SOL,
                PROVIDER_OPENAI,
                GPT_5_6_TERRA,
            ),
            (
                7,
                "Agent-7-EvidenceVerifier",
                "Test and Evidence Verifier",
                GEMINI_3_6_FLASH,
                PROVIDER_GOOGLE,
                GEMINI_3_7_FLASH,
            ),
            (
                8,
                "Agent-8-ErrorDiagnosis",
                "Error Diagnosis Specialist",
                GPT_5_6_TERRA,
                PROVIDER_OPENAI,
                GPT_5_6_LUNA,
            ),
            (
                9,
                "Agent-9-RecoveryRepair",
                "Recovery / Repair Specialist",
                GPT_6_ASTRA,
                PROVIDER_OPENAI,
                GPT_5_6_SOL,
            ),
            (
                10,
                "Agent-10-QualityGate",
                "Final Quality Gate",
                GEMINI_FLASH_LATEST,
                PROVIDER_GOOGLE,
                GEMINI_3_7_FLASH,
            ),
        ]

        for slot_id, name, role, pref, prov, fall in definitions:
            self._slots[slot_id] = AgentSlot(
                slot_id=slot_id,
                name=name,
                role=role,
                preferred_model=pref,
                provider=prov,
                fallback_model=fall,
                active_model=pref,
            )

    def refresh_model_assignments(self) -> None:
        """
        Probe provider availability and resolve active models without fake claims.
        Records exact fallback reasons if models cannot be called live.
        """
        with self._lock:
            for slot in self._slots.values():
                if slot.provider == PROVIDER_OPENAI:
                    probe = self.provider.openai.verify_live_api(slot.preferred_model)
                    if probe["live_api_verified"]:
                        slot.active_model = slot.preferred_model
                        slot.is_live_api_verified = True
                        slot.fallback_reason = None
                    else:
                        slot.is_live_api_verified = False
                        slot.active_model = slot.fallback_model
                        reason = probe.get("details") or probe.get("status")
                        slot.fallback_reason = (
                            f"Preferred {slot.preferred_model} live probe unverified ({reason}). "
                            f"Active assigned fallback: {slot.fallback_model}."
                        )
                elif slot.provider == PROVIDER_GOOGLE:
                    probe = self.provider.gemini.verify_live_api(slot.preferred_model)
                    if probe["live_api_verified"]:
                        slot.active_model = slot.preferred_model
                        slot.is_live_api_verified = True
                        slot.fallback_reason = None
                    else:
                        slot.is_live_api_verified = False
                        slot.active_model = slot.fallback_model
                        reason = probe.get("details") or probe.get("status")
                        slot.fallback_reason = (
                            f"Preferred {slot.preferred_model} live probe unverified ({reason}). "
                            f"Active assigned fallback: {slot.fallback_model}."
                        )

    def get_slot(self, slot_id: int) -> Optional[AgentSlot]:
        with self._lock:
            return self._slots.get(slot_id)

    def get_all_slots(self) -> List[AgentSlot]:
        with self._lock:
            return list(self._slots.values())

    def get_idle_slot(self, preferred_role: Optional[str] = None) -> Optional[AgentSlot]:
        """Find an idle agent slot, optionally matching a preferred role."""
        with self._lock:
            if preferred_role:
                for slot in self._slots.values():
                    if slot.status == "IDLE" and preferred_role.lower() in slot.role.lower():
                        return slot
            for slot in self._slots.values():
                if slot.status == "IDLE":
                    return slot
            return None

    def assign_task_to_slot(self, slot_id: int, task_id: str) -> bool:
        """Assign a task ID to an agent slot and mark it BUSY."""
        with self._lock:
            slot = self._slots.get(slot_id)
            if not slot or slot.status != "IDLE":
                return False
            slot.status = "BUSY"
            slot.current_task_id = task_id
            return True

    def release_slot(self, slot_id: int, success: bool = True) -> None:
        """Release an agent slot back to IDLE, updating performance stats."""
        with self._lock:
            slot = self._slots.get(slot_id)
            if slot:
                slot.status = "IDLE"
                slot.current_task_id = None
                if success:
                    slot.tasks_completed += 1
                else:
                    slot.tasks_failed += 1

    def count_busy_slots(self) -> int:
        with self._lock:
            return sum(1 for s in self._slots.values() if s.status == "BUSY")

    def count_idle_slots(self) -> int:
        with self._lock:
            return sum(1 for s in self._slots.values() if s.status == "IDLE")

    def get_summary(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [s.to_dict() for s in self._slots.values()]
