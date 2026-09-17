"""
NR-AI Voice Command Safety Gate.
Step 10 Phase 3 — Phone Voice Command & Secure Audio Pipeline.
"""

from enum import Enum
from typing import Set, Tuple

from app.remote.permissions import PhonePermissionScope
from app.remote.voice_intent import VoiceIntent, VoiceIntentType


class VoiceSafetyDecision(str, Enum):
    SAFE = "SAFE"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    DENIED = "DENIED"
    UNKNOWN = "UNKNOWN"


class VoiceCommandSafetyGate:
    """
    Deterministic safety gate positioned strictly between Voice Intent Parsing
    and Command Routing execution.
    """

    @classmethod
    def evaluate(
        cls,
        intent: VoiceIntent,
        is_emergency_active: bool,
        granted_scopes: Set[PhonePermissionScope],
    ) -> Tuple[VoiceSafetyDecision, str]:
        # 1. Emergency stop active check
        if is_emergency_active:
            if intent.intent_type == VoiceIntentType.EMERGENCY_STOP:
                return VoiceSafetyDecision.SAFE, "EMERGENCY_STOP_ACCEPTED"
            return VoiceSafetyDecision.DENIED, "EMERGENCY_STOP_ACTIVE: Voice execution frozen."

        # 2. Intent-level emergency stop trigger
        if intent.intent_type == VoiceIntentType.EMERGENCY_STOP:
            return VoiceSafetyDecision.SAFE, "EMERGENCY_STOP_TRIGGERED"

        # 3. Prohibited actions
        if intent.intent_type == VoiceIntentType.PROHIBITED:
            return VoiceSafetyDecision.DENIED, "PROHIBITED_VOICE_COMMAND: Command contains prohibited operations."

        # 4. Unknown intent rejection (never execute unknown speech)
        if intent.intent_type == VoiceIntentType.UNKNOWN:
            return VoiceSafetyDecision.UNKNOWN, "UNKNOWN_VOICE_INTENT: Command unrecognized."

        # 5. Permission scope verification
        if PhonePermissionScope.VOICE_COMMAND not in granted_scopes:
            return VoiceSafetyDecision.DENIED, "VOICE_PERMISSION_DENIED: Scope VOICE_COMMAND required."

        # 6. Sensitive actions requiring confirmation
        if intent.requires_confirmation:
            return VoiceSafetyDecision.CONFIRMATION_REQUIRED, "CONFIRMATION_REQUIRED: Sensitive action requires confirmation."

        # 7. Approved safe command
        return VoiceSafetyDecision.SAFE, "VOICE_COMMAND_SAFE: Approved for routing."
