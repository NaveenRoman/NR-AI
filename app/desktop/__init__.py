"""
NR-AI Desktop Package.
Provides safe desktop shell coordination, eventing, lifecycle, permissions, and health monitoring.
OpenJarvis-inspired desktop shell bridge adapting local Galaxy UI.
"""

from app.desktop.events import DesktopEvent, DesktopEventBus, DesktopEventType
from app.desktop.health import DesktopHealthMonitor
from app.desktop.lifecycle import (
    DesktopLifecycleManager,
    DesktopLifecycleState,
    LifecycleTransitionError,
)
from app.desktop.permissions import (
    ALLOWED_AGENTS,
    ALLOWED_VIEWS,
    ALLOWED_VOICE_ACTIONS,
    DesktopIntentType,
    DesktopPermissionValidator,
    IntentValidationResult,
)
from app.desktop.shell import DesktopShellCoordinator

__all__ = [
    "DesktopShellCoordinator",
    "DesktopPermissionValidator",
    "DesktopIntentType",
    "IntentValidationResult",
    "DesktopEventBus",
    "DesktopEvent",
    "DesktopEventType",
    "DesktopLifecycleManager",
    "DesktopLifecycleState",
    "LifecycleTransitionError",
    "DesktopHealthMonitor",
    "ALLOWED_VIEWS",
    "ALLOWED_AGENTS",
    "ALLOWED_VOICE_ACTIONS",
]
