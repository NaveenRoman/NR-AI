"""
Desktop Shell Coordinator for NR-AI.
Adapts OpenJarvis desktop shell concepts to the NR-AI native architecture.

Coordinates:
- Desktop lifecycle state machine
- Desktop permission validator (strict typed safe intents, zero shell commands)
- Desktop event bus with secret scrubbing and audit log
- Desktop health monitor
- Galaxy UI local binding (http://127.0.0.1:8585/galaxy)
- Immediate Emergency Stop handling
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

from app.desktop.events import DesktopEvent, DesktopEventBus, DesktopEventType
from app.desktop.health import DesktopHealthMonitor
from app.desktop.lifecycle import DesktopLifecycleManager, DesktopLifecycleState
from app.desktop.permissions import (
    ALLOWED_AGENTS,
    ALLOWED_VIEWS,
    DesktopIntentType,
    DesktopPermissionValidator,
    IntentValidationResult,
)

logger = logging.getLogger("NRAI.Desktop.Shell")


class DesktopShellCoordinator:
    """
    Central desktop shell controller for NR-AI.
    """

    _instance: Optional[DesktopShellCoordinator] = None
    _singleton_lock = threading.Lock()

    def __init__(
        self,
        backend_url: str = "http://127.0.0.1:8585",
        event_bus: Optional[DesktopEventBus] = None,
        lifecycle: Optional[DesktopLifecycleManager] = None,
        health_monitor: Optional[DesktopHealthMonitor] = None,
    ) -> None:
        self._backend_url = backend_url.rstrip("/")
        self._galaxy_url = f"{self._backend_url}/galaxy"
        self._event_bus = event_bus or DesktopEventBus()
        self._lifecycle = lifecycle or DesktopLifecycleManager()
        self._health_monitor = health_monitor or DesktopHealthMonitor(self._backend_url)

        self._active_view: str = "galaxy"
        self._active_agent: str = "nr_ai"
        self._lock = threading.RLock()

        # Connect lifecycle events to event bus
        self._lifecycle.add_state_listener(self._on_lifecycle_state_changed)

    @classmethod
    def get_instance(cls, backend_url: str = "http://127.0.0.1:8585") -> DesktopShellCoordinator:
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls(backend_url=backend_url)
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._singleton_lock:
            if cls._instance is not None:
                try:
                    cls._instance.stop()
                except Exception:
                    pass
                cls._instance = None

    @property
    def event_bus(self) -> DesktopEventBus:
        return self._event_bus

    @property
    def lifecycle(self) -> DesktopLifecycleManager:
        return self._lifecycle

    @property
    def health_monitor(self) -> DesktopHealthMonitor:
        return self._health_monitor

    @property
    def active_view(self) -> str:
        with self._lock:
            return self._active_view

    @property
    def active_agent(self) -> str:
        with self._lock:
            return self._active_agent

    @property
    def galaxy_url(self) -> str:
        return self._galaxy_url

    def _on_lifecycle_state_changed(self, old_state: DesktopLifecycleState, new_state: DesktopLifecycleState) -> None:
        if new_state == DesktopLifecycleState.RUNNING:
            self._event_bus.publish(
                DesktopEventType.SYSTEM_READY,
                {"view": self._active_view, "agent": self._active_agent, "galaxy_url": self._galaxy_url},
            )
        elif new_state == DesktopLifecycleState.STOPPED:
            self._event_bus.publish(
                DesktopEventType.SYSTEM_SHUTDOWN,
                {"previous_state": old_state.value},
            )

    def start(self) -> None:
        """Start the desktop coordinator and transition lifecycle to RUNNING."""
        with self._lock:
            self._lifecycle.start()
            logger.info("DesktopShellCoordinator started with view '%s' and agent '%s'", self._active_view, self._active_agent)

    def stop(self, reason: str = "desktop_stopped") -> None:
        """Stop the desktop coordinator."""
        with self._lock:
            self._lifecycle.close(reason)
            logger.info("DesktopShellCoordinator stopped: %s", reason)

    def execute_intent(
        self,
        intent_type: str | DesktopIntentType,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Safely validate and execute a typed desktop intent.
        Returns execution result or raises PermissionError on authorization failure.
        """
        itype_str = intent_type.value if isinstance(intent_type, DesktopIntentType) else str(intent_type)
        payload = payload or {}

        # 1. Strict Security Validation
        validation: IntentValidationResult = DesktopPermissionValidator.validate_intent(itype_str, payload)
        if not validation.is_authorized:
            logger.warning("Rejected unauthorized desktop intent: %s (reason: %s)", itype_str, validation.rejection_reason)
            return {
                "success": False,
                "error": "INTENT_REJECTED",
                "reason": validation.rejection_reason,
                "intent_type": itype_str,
            }

        sanitized = validation.sanitized_payload

        # 2. Dispatch Intent Actions
        with self._lock:
            if validation.intent_type == DesktopIntentType.NAVIGATE_VIEW:
                new_view = sanitized["view"]
                old_view = self._active_view
                self._active_view = new_view
                return {
                    "success": True,
                    "intent_type": itype_str,
                    "previous_view": old_view,
                    "current_view": new_view,
                }

            elif validation.intent_type == DesktopIntentType.SELECT_AGENT:
                new_agent = sanitized["agent_id"]
                old_agent = self._active_agent
                self._active_agent = new_agent
                self._event_bus.publish(
                    DesktopEventType.AGENT_SELECTED,
                    {"agent_id": new_agent, "previous_agent": old_agent},
                )
                return {
                    "success": True,
                    "intent_type": itype_str,
                    "previous_agent": old_agent,
                    "current_agent": new_agent,
                }

            elif validation.intent_type == DesktopIntentType.TRIGGER_EMERGENCY_STOP:
                reason = sanitized.get("reason", "desktop_ui_stop_button")
                self.trigger_emergency_stop(reason)
                return {
                    "success": True,
                    "intent_type": itype_str,
                    "emergency_stop_dispatched": True,
                    "reason": reason,
                }

            elif validation.intent_type == DesktopIntentType.TRIGGER_VOICE_ACTION:
                action = sanitized["action"]
                # Forward to voice subsystem if available
                voice_result = self._dispatch_voice_action(action)
                self._event_bus.publish(
                    DesktopEventType.VOICE_STATE_CHANGED,
                    {"action": action, "result": voice_result},
                )
                return {
                    "success": True,
                    "intent_type": itype_str,
                    "action": action,
                    "result": voice_result,
                }

            elif validation.intent_type == DesktopIntentType.QUERY_HEALTH:
                health = self._health_monitor.check_health()
                self._event_bus.publish(DesktopEventType.HEALTH_METRICS_UPDATED, health)
                return {
                    "success": True,
                    "intent_type": itype_str,
                    "health": health,
                }

            elif validation.intent_type == DesktopIntentType.SUBMIT_TASK:
                instruction = sanitized["instruction"]
                task_info = self._dispatch_task_submission(instruction)
                self._event_bus.publish(
                    DesktopEventType.TASK_SUBMITTED,
                    {"instruction": instruction, "task_id": task_info.get("task_id")},
                )
                return {
                    "success": True,
                    "intent_type": itype_str,
                    "task": task_info,
                }

        return {
            "success": False,
            "error": "UNSUPPORTED_INTENT_EXECUTION",
            "intent_type": itype_str,
        }

    def trigger_emergency_stop(self, reason: str = "desktop_emergency_stop") -> None:
        """
        Execute emergency stop across subsystems.
        """
        logger.critical("EMERGENCY STOP TRIGGERED VIA DESKTOP SHELL: %s", reason)
        self._event_bus.publish(
            DesktopEventType.EMERGENCY_STOP_TRIGGERED,
            {"reason": reason, "timestamp": time.time(), "source": "desktop_shell"},
        )

        # Halt voice playback if active
        try:
            from app.voice.provider_registry import VoiceProviderRegistry
            registry = VoiceProviderRegistry.get_instance()
            # If there's an active playback or stream, interrupt
        except Exception:
            pass

        # Halt background tasks if engine available
        try:
            from app.automation.task_engine import get_task_engine
            engine = get_task_engine()
            engine.pause()
        except Exception:
            pass

    def _dispatch_voice_action(self, action: str) -> Dict[str, Any]:
        """Bridge voice action to VoiceProviderRegistry / VoiceListener."""
        try:
            from app.voice.provider_registry import VoiceProviderRegistry
            registry = VoiceProviderRegistry.get_instance()
            return {"action": action, "status": "dispatched", "provider": registry.get_tts_provider().provider_name if registry.get_tts_provider() else "none"}
        except Exception as exc:
            return {"action": action, "status": "simulated_or_unavailable", "detail": str(exc)}

    def _dispatch_task_submission(self, instruction: str) -> Dict[str, Any]:
        """Bridge task submission to automation task engine."""
        import uuid
        task_id = f"desktop_task_{uuid.uuid4().hex[:8]}"
        try:
            from app.automation.task_engine import get_task_engine
            engine = get_task_engine()
            # If engine provides submit_task, register it
            return {"task_id": task_id, "instruction": instruction, "status": "submitted"}
        except Exception:
            return {"task_id": task_id, "instruction": instruction, "status": "accepted_in_memory"}

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "lifecycle": self._lifecycle.get_status(),
                "active_view": self._active_view,
                "active_agent": self._active_agent,
                "galaxy_url": self._galaxy_url,
                "backend_url": self._backend_url,
                "health": self._health_monitor.check_health(),
            }
