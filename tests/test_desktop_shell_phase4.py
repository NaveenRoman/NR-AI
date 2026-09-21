"""
Unit tests for Desktop Shell Coordinator and Lifecycle in NR-AI Phase 4.
"""

import unittest

from app.desktop.events import DesktopEventBus, DesktopEventType
from app.desktop.health import DesktopHealthMonitor
from app.desktop.lifecycle import (
    DesktopLifecycleManager,
    DesktopLifecycleState,
    LifecycleTransitionError,
)
from app.desktop.permissions import DesktopIntentType
from app.desktop.shell import DesktopShellCoordinator


class TestDesktopShellPhase4(unittest.TestCase):

    def setUp(self):
        DesktopShellCoordinator.reset_instance()

    def tearDown(self):
        DesktopShellCoordinator.reset_instance()

    def test_lifecycle_normal_transitions(self):
        mgr = DesktopLifecycleManager(max_reconnect_attempts=3)
        self.assertEqual(mgr.current_state, DesktopLifecycleState.STOPPED)
        self.assertFalse(mgr.is_running)
        self.assertEqual(mgr.uptime_seconds, 0.0)

        mgr.start()
        self.assertEqual(mgr.current_state, DesktopLifecycleState.RUNNING)
        self.assertTrue(mgr.is_running)
        self.assertGreaterEqual(mgr.uptime_seconds, 0.0)

        mgr.close()
        self.assertEqual(mgr.current_state, DesktopLifecycleState.STOPPED)
        self.assertFalse(mgr.is_running)

    def test_lifecycle_illegal_transition(self):
        mgr = DesktopLifecycleManager()
        with self.assertRaises(LifecycleTransitionError):
            mgr._transition_to(DesktopLifecycleState.RUNNING)

    def test_lifecycle_reconnection_and_max_attempts(self):
        mgr = DesktopLifecycleManager(max_reconnect_attempts=2)
        mgr.start()

        # Attempt 1
        can_reconnect = mgr.handle_disconnect("network_timeout")
        self.assertTrue(can_reconnect)
        self.assertEqual(mgr.current_state, DesktopLifecycleState.RECONNECTING)

        # Successful recovery
        mgr.handle_reconnected()
        self.assertEqual(mgr.current_state, DesktopLifecycleState.RUNNING)

        # Attempt 1 again after recovery
        self.assertTrue(mgr.handle_disconnect("conn_drop_1"))
        # Attempt 2
        self.assertTrue(mgr.handle_disconnect("conn_drop_2"))
        # Attempt 3 exceeds max 2
        self.assertFalse(mgr.handle_disconnect("conn_drop_3"))
        self.assertEqual(mgr.current_state, DesktopLifecycleState.STOPPED)

    def test_desktop_shell_coordinator_intent_execution(self):
        bus = DesktopEventBus()
        shell = DesktopShellCoordinator(event_bus=bus)
        shell.start()

        # 1. NAVIGATE_VIEW
        res = shell.execute_intent(DesktopIntentType.NAVIGATE_VIEW, {"view": "tasks"})
        self.assertTrue(res["success"])
        self.assertEqual(res["current_view"], "tasks")
        self.assertEqual(shell.active_view, "tasks")

        # 2. SELECT_AGENT
        res = shell.execute_intent(DesktopIntentType.SELECT_AGENT, {"agent_id": "droid"})
        self.assertTrue(res["success"])
        self.assertEqual(res["current_agent"], "droid")
        self.assertEqual(shell.active_agent, "droid")

        # 3. TRIGGER_VOICE_ACTION
        res = shell.execute_intent(DesktopIntentType.TRIGGER_VOICE_ACTION, {"action": "toggle_mute"})
        self.assertTrue(res["success"])
        self.assertEqual(res["action"], "toggle_mute")

        # 4. SUBMIT_TASK
        res = shell.execute_intent(DesktopIntentType.SUBMIT_TASK, {"instruction": "Run integrity check"})
        self.assertTrue(res["success"])
        self.assertIn("task", res)

        # 5. QUERY_HEALTH
        res = shell.execute_intent(DesktopIntentType.QUERY_HEALTH)
        self.assertTrue(res["success"])
        self.assertIn("health", res)

        # 6. TRIGGER_EMERGENCY_STOP
        res = shell.execute_intent(DesktopIntentType.TRIGGER_EMERGENCY_STOP, {"reason": "test_stop"})
        self.assertTrue(res["success"])
        self.assertTrue(res["emergency_stop_dispatched"])

        shell.stop()


if __name__ == "__main__":
    unittest.main()
