"""
Unit tests for Desktop Event Bus, Secret Scrubbing, and Schema Validation in NR-AI Phase 4.
"""

import unittest

from app.desktop.events import DesktopEvent, DesktopEventBus, DesktopEventType


class TestDesktopEventsPhase4(unittest.TestCase):

    def test_desktop_event_types_count_and_names(self):
        expected_types = [
            "SYSTEM_INITIALIZING",
            "SYSTEM_READY",
            "SYSTEM_SHUTDOWN",
            "HEALTH_METRICS_UPDATED",
            "EMERGENCY_STOP_TRIGGERED",
            "AGENT_SELECTED",
            "AGENT_STATE_CHANGED",
            "TASK_SUBMITTED",
            "TASK_PROGRESS_UPDATED",
            "TASK_COMPLETED",
            "TASK_FAILED",
            "VOICE_STATE_CHANGED",
            "VOICE_TRANSCRIPTION_RECEIVED",
            "VOICE_SYNTHESIS_COMPLETED",
        ]
        actual_types = [e.value for e in DesktopEventType]
        self.assertEqual(len(actual_types), 14)
        for et in expected_types:
            self.assertIn(et, actual_types)

    def test_event_bus_subscribe_and_publish(self):
        bus = DesktopEventBus(capacity=100)
        received = []

        def on_ready(ev: DesktopEvent):
            received.append(ev)

        bus.subscribe(DesktopEventType.SYSTEM_READY, on_ready)
        ev = bus.publish(DesktopEventType.SYSTEM_READY, {"status": "ok"})

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].event_type, DesktopEventType.SYSTEM_READY)
        self.assertEqual(received[0].payload["status"], "ok")
        self.assertIsNotNone(received[0].event_id)

    def test_event_bus_wildcard_subscription(self):
        bus = DesktopEventBus(capacity=100)
        all_events = []

        bus.subscribe("*", lambda ev: all_events.append(ev))

        bus.publish(DesktopEventType.AGENT_SELECTED, {"agent": "aegis"})
        bus.publish(DesktopEventType.TASK_SUBMITTED, {"task": "inspect"})

        self.assertEqual(len(all_events), 2)

    def test_event_bus_secret_scrubbing(self):
        bus = DesktopEventBus()
        secret_payload = {
            "user_input": "connect with api_key=sk-proj-99998888777766665555444433332222",
            "normal_field": "system_ready",
        }
        ev = bus.publish(DesktopEventType.TASK_SUBMITTED, secret_payload)
        self.assertNotIn("sk-proj-99998888777766665555444433332222", ev.payload["user_input"])
        has_redaction = (
            "[REDACTED" in ev.payload["user_input"]
            or "[API_KEY" in ev.payload["user_input"]
            or "sk-" not in ev.payload["user_input"]
        )
        self.assertTrue(has_redaction)

    def test_event_bus_payload_size_bounding(self):
        bus = DesktopEventBus()
        oversized = {"data": "X" * 70000}
        ev = bus.publish(DesktopEventType.TASK_SUBMITTED, oversized)
        self.assertEqual(ev.payload.get("error"), "PAYLOAD_SIZE_EXCEEDED")
        self.assertTrue(ev.payload.get("truncated"))

    def test_event_bus_audit_log_capacity(self):
        bus = DesktopEventBus(capacity=5)
        for i in range(10):
            bus.publish(DesktopEventType.AGENT_STATE_CHANGED, {"seq": i})

        audit = bus.get_audit_log(limit=100)
        self.assertEqual(len(audit), 5)
        self.assertEqual(audit[-1]["payload"]["seq"], 9)


if __name__ == "__main__":
    unittest.main()
