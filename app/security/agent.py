"""
NR-AI SkyShield: Security Agent & Model Isolation Gate.
Step 10 Phase 1 — Security Agent Foundation.

Provides:
- SecurityAgent implementation complying with NR-AI Agent interface
- DeterministicSecurityGate enforcing model isolation
- Rejection of model-generated arbitrary OS commands, shell invocations, and covert operations
"""

import logging
from typing import Any, Dict, Optional

from app.security.coordinator import SecurityCoordinator
from app.security.models import (
    SecurityPolicy,
    SecurityState,
)

logger = logging.getLogger("NRAI.SkyShield.Agent")


class DeterministicSecurityGate:
    """
    Zero-trust validation layer that intercepts and filters any model suggestions
    before execution. Ensures no AI model can execute arbitrary operating system
    shell commands or violate safety invariants.
    """

    def validate_and_sanitize_command(self, command: str) -> Dict[str, Any]:
        """Validates raw command strings directly against zero-trust invariants."""
        return self.evaluate_model_proposal({"command": command})

    def validate_action(self, action: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Validates action and parameters directly against zero-trust invariants."""
        prop = {"action": action}
        if details:
            prop.update(details)
        return self.evaluate_model_proposal(prop)

    @classmethod
    def evaluate_model_proposal(cls, proposal: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validates model output.
        Rejects proposals containing shell code, eval, exec, credential extraction,
        covert activation, or message interception.
        """
        action = str(proposal.get("action", ""))
        command = str(proposal.get("command", "") or proposal.get("cmd", ""))
        parts = []
        for k, v in proposal.items():
            if isinstance(v, (str, int, float, bool)):
                parts.append(str(v).lower())
            elif isinstance(v, (dict, list)):
                parts.append(json.dumps(v).lower())
        combined = " ".join(parts)

        # 1. Enforce SecurityPolicy
        if not SecurityPolicy.is_action_permitted(combined):
            raise PermissionError(
                f"DeterministicSecurityGate REJECTED proposal: prohibited action pattern detected in '{combined}'."
            )

        # 2. Check for shell or script injection indicators
        prohibited_tokens = [
            "powershell", "cmd.exe", "bash", "sh -c", "exec(", "eval(",
            "subprocess", "shell=true", "os.system", "rmdir", "del /",
            "execute_shell", "shell", "whoami",
            "select * from messages", "whatsapp.db", "snapchat.db",
            "messages from", "sms database", "direct chats", "messages from phone",
            "chat history database", "secret camera", "covertly", "secretly",
            "hidden eavesdropping", "eavesdropping", "secret", "covert",
            "screen_capture", "camera_capture", "microphone_recording", "keylogging",
            "private_message_content", "credential_access",
        ]
        for token in prohibited_tokens:
            if token in combined:
                raise PermissionError(
                    f"DeterministicSecurityGate REJECTED proposal: prohibited execution token '{token}' detected."
                )

        # 3. Model Isolation & Authorization Invariants (Section 20)
        # AI models are strictly advisory; they CANNOT approve pairing, authorize devices,
        # or grant capability scopes. Authorization requires explicit deterministic owner actions.
        unauthorized_model_decisions = [
            "approve pairing", "authorize device", "grant camera", "grant microphone",
            "grant screen capture", "grant private message", "bypass pairing", "auto approve",
            "elevate capability", "grant root", "skip owner approval", "authorize phone number",
            "authenticate by phone only", "force enroll", "override authorization",
            "skyshield.pair_approve", "pair_approve", "authorize",
        ]
        for token in unauthorized_model_decisions:
            if token in combined:
                raise PermissionError(
                    f"DeterministicSecurityGate REJECTED proposal: AI models cannot make authorization decisions or approve pairing ('{token}')."
                )

        return {
            "approved": True,
            "action": action or command,
            "classification": "DETERMINISTICALLY_APPROVED",
        }


class SecurityAgent:
    """
    Dedicated SkyShield Security Agent.
    Specialist agent for device posture, application permissions, and threat monitoring.
    """

    def __init__(self, coordinator: Optional[SecurityCoordinator] = None):
        self.coordinator = coordinator or SecurityCoordinator()
        self.agent_id = "security_agent"
        self.friendly_name = "SkyShield"
        self.name = "SkyShield"
        self.role = "Security Agent"
        self.category = "Security & Defense"
        self.workspace_name = "SkyShield Command Center"
        self.security_gate = DeterministicSecurityGate()

    def get_greeting(self) -> str:
        """Returns exact greeting string."""
        return (
            "I am SkyShield, your dedicated Security Agent. "
            "I audit device security posture, inspect application permissions, and monitor threat vectors."
        )

    def execute_command(self, command: str) -> Dict[str, Any]:
        """
        Executes a security command with deterministic safety gate and structured output.
        """
        if self.coordinator.emergency_stop.is_active() or self.coordinator.current_state == SecurityState.STOPPED:
            if any(w in command.lower() for w in ("reset", "resume", "clear")):
                self.coordinator.reset_emergency_stop()
                return {
                    "success": True,
                    "reply": "SkyShield: Emergency Stop reset. State returned to IDLE.",
                    "data": self.coordinator.get_dashboard_state(),
                }
            return {
                "success": False,
                "reply": "SkyShield: EMERGENCY STOP is currently ACTIVE. All operations blocked.",
                "data": self.coordinator.get_dashboard_state(),
            }

        # Gate check
        self.security_gate.validate_and_sanitize_command(command)

        t_clean = command.strip().lower()
        if any(w in t_clean for w in ("emergency stop", "halt", "abort", "freeze", "kill")):
            res = self.coordinator.trigger_emergency_stop(reason=f"Directive: {command}")
            return {
                "success": True,
                "reply": "🛑 SkyShield: EMERGENCY STOP triggered. All operations halted.",
                "data": self.coordinator.get_dashboard_state(),
            }
        elif any(w in t_clean for w in ("scan", "inspect")):
            res = self.coordinator.run_full_scan()
            return {
                "success": res.get("success", False),
                "reply": f"SkyShield: Scan Complete. Found {len(res.get('findings', []))} issues. System status: {self.coordinator.current_state.value}.",
                "data": res,
            }
        elif any(w in t_clean for w in ("permission", "privilege", "audit")):
            perms = self.coordinator.scanner.scan_permissions()
            findings = self.coordinator.auditor.audit_device_permissions(perms)
            return {
                "success": True,
                "reply": f"SkyShield: Audited {len(perms)} permissions. {len(findings)} elevated findings.",
                "data": {"permissions": {k: v.value for k, v in perms.items()}, "findings": [f.to_dict() for f in findings]},
            }
        elif any(w in t_clean for w in ("status", "state", "report")):
            dash = self.coordinator.get_dashboard_state()
            return {
                "success": True,
                "reply": f"SkyShield: State: {self.coordinator.current_state.value}. Security Status: {dash['overall_security_status']}.",
                "data": dash,
            }
        else:
            dash = self.coordinator.get_dashboard_state()
            return {
                "success": True,
                "reply": f"SkyShield: Standing by in Command Center. State is {self.coordinator.current_state.value}.",
                "data": dash,
            }

    def handle_action(self, action_id: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Deterministic handler for capability actions.
        Routes to coordinator methods.
        """
        params = params or {}
        # Pre-execution gate validation
        self.security_gate.evaluate_model_proposal({"action": action_id, **params})

        if action_id in ("skyshield.scan", "security.audit_status"):
            return self.coordinator.run_full_scan()

        elif action_id in ("skyshield.audit_permissions", "security.verify_invariants"):
            perms = self.coordinator.scanner.scan_permissions()
            findings = self.coordinator.auditor.audit_device_permissions(perms)
            return {
                "success": True,
                "permissions": {k: v.value for k, v in perms.items()},
                "findings": [f.to_dict() for f in findings],
            }

        elif action_id in ("skyshield.emergency_stop", "security.emergency_stop"):
            reason = params.get("reason", "Halt requested via SkyShield Agent")
            return self.coordinator.trigger_emergency_stop(reason=reason)

        elif action_id in ("skyshield.status", "security.status"):
            return self.coordinator.get_dashboard_state()

        elif action_id == "skyshield.pair_request":
            dev_name = params.get("device_name", "Unknown Android Device")
            platform = params.get("platform", "android")
            phone = params.get("phone_number")
            caps = params.get("requested_capabilities")
            ttl = int(params.get("ttl_seconds", 600))
            ok, msg, req = self.coordinator.create_pairing_request(
                device_name=dev_name, platform=platform, phone_number=phone,
                requested_capabilities=caps, ttl_seconds=ttl,
            )
            return {"success": ok, "message": msg, "pairing_request": req.to_dict() if req else None}

        elif action_id == "skyshield.pair_approve":
            p_id = params.get("pairing_id", "")
            code = params.get("pairing_code", "")
            fp = params.get("device_fingerprint", "fp_default")
            pub_key = params.get("public_key_hex")
            ok, msg, dev = self.coordinator.approve_pairing(
                pairing_id=p_id, pairing_code=code, device_fingerprint=fp, public_key_hex=pub_key,
            )
            return {"success": ok, "message": msg, "device": dev.to_dict() if dev else None}

        elif action_id == "skyshield.pair_reject":
            p_id = params.get("pairing_id", "")
            reason = params.get("reason", "Rejected by operator")
            ok, msg = self.coordinator.reject_pairing(pairing_id=p_id, reason=reason)
            return {"success": ok, "message": msg}

        elif action_id == "skyshield.list_devices":
            return {"success": True, "devices": self.coordinator.list_devices()}

        elif action_id == "skyshield.suspend_device":
            dev_id = params.get("device_id", "")
            reason = params.get("reason", "Suspended by operator")
            ok, msg = self.coordinator.suspend_device(dev_id, reason=reason)
            return {"success": ok, "message": msg}

        elif action_id == "skyshield.revoke_device":
            dev_id = params.get("device_id", "")
            reason = params.get("reason", "Revoked by operator")
            ok, msg = self.coordinator.revoke_device(dev_id, reason=reason)
            return {"success": ok, "message": msg}

        elif action_id == "skyshield.reauthorize_device":
            dev_id = params.get("device_id", "")
            reason = params.get("reason", "Reauthorized by operator")
            ok, msg = self.coordinator.reauthorize_device(dev_id, reason=reason)
            return {"success": ok, "message": msg}

        else:
            raise ValueError(f"Unknown SkyShield action: {action_id}")

    def handle_text_command(self, text: str) -> Dict[str, Any]:
        """
        Handles operator text directives through the security gate.
        """
        return self.execute_command(text)
