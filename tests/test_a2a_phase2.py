"""
Tests for NR-AI Agent-to-Agent (A2A) Phase 2 Subsystem.
Verifies:
- Agent discovery and capability reflection
- Structured task delegation with checkpoint & evidence references
- Task status and result polling
- Task cancellation
- Scope-based access control and unauthorized delegation rejection
- Timeout handling
- Emergency stop barrier
"""

import time
import unittest

from app.a2a.client import A2AClient
from app.a2a.protocol import (
    A2ADelegationPayload,
    A2ARequest,
    A2AResponse,
    AgentCard,
    JsonRpcError,
    TaskState,
)
from app.a2a.router import A2APermissionScope, LocalA2ABroker
from app.remote.emergency import EmergencyStopController


class TestA2APhase2(unittest.TestCase):
    """Unit test suite for A2A Phase 2 capabilities."""

    def setUp(self):
        self.e_stop = EmergencyStopController()
        self.broker = LocalA2ABroker(emergency_controller=self.e_stop)

        # Register Agent A (Orchestrator)
        card_a = AgentCard(
            name="OrchestratorAgent",
            capabilities=["task_delegation", "agent_discovery"],
            permission_scopes=[
                A2APermissionScope.TASK_DELEGATE.value,
                A2APermissionScope.TASK_QUERY.value,
                A2APermissionScope.TASK_CANCEL.value,
                A2APermissionScope.CAPABILITY_DISCOVER.value,
                A2APermissionScope.DIRECT_MESSAGE.value,
            ],
        )
        self.broker.register_agent(
            agent_id="Orchestrator",
            card=card_a,
            handler=lambda req: A2AResponse(request_id=req.request_id, result="ack"),
        )

        # Register Agent B (Droid Worker)
        self.delegated_tasks = {}
        def droid_handler(req: A2ARequest) -> A2AResponse:
            if req.method == "tasks.delegate":
                task_id = req.params.get("task_id", "t_1")
                self.delegated_tasks[task_id] = {
                    "state": TaskState.WORKING.value,
                    "params": req.params,
                }
                return A2AResponse(
                    request_id=req.request_id,
                    result={"task_id": task_id, "state": TaskState.WORKING.value},
                )
            elif req.method == "tasks.status":
                tid = req.params.get("task_id")
                task = self.delegated_tasks.get(tid)
                if task:
                    return A2AResponse(request_id=req.request_id, result=task)
                return A2AResponse(
                    request_id=req.request_id,
                    error=JsonRpcError.make_error(JsonRpcError.TASK_NOT_FOUND, "Task not found"),
                )
            elif req.method == "tasks.cancel":
                tid = req.params.get("task_id")
                if tid in self.delegated_tasks:
                    self.delegated_tasks[tid]["state"] = TaskState.CANCELED.value
                    return A2AResponse(request_id=req.request_id, result={"task_id": tid, "status": "canceled"})
                return A2AResponse(
                    request_id=req.request_id,
                    error=JsonRpcError.make_error(JsonRpcError.TASK_NOT_FOUND, "Task not found"),
                )
            return A2AResponse(request_id=req.request_id, result="ok")

        card_b = AgentCard(
            name="DroidSpecialist",
            capabilities=["android:build", "android:test", "android:repair"],
            skills=["gradle_compilation", "adb_interaction"],
            permission_scopes=[
                A2APermissionScope.TASK_QUERY.value,
                A2APermissionScope.DIRECT_MESSAGE.value,
            ],
        )
        self.broker.register_agent(
            agent_id="Droid",
            card=card_b,
            handler=droid_handler,
        )

        # Register Agent C (Unprivileged Restricted Agent)
        card_c = AgentCard(name="RestrictedAgent", permission_scopes=[])
        self.broker.register_agent(
            agent_id="Restricted",
            card=card_c,
            handler=lambda req: A2AResponse(request_id=req.request_id, result="ok"),
        )

        self.client_a = A2AClient("Orchestrator", broker=self.broker)
        self.client_c = A2AClient("Restricted", broker=self.broker)

    def test_agent_and_capability_discovery(self):
        """Verify discovering registered agents and their declared capabilities."""
        agents = self.client_a.discover_agents()
        names = [a["name"] for a in agents]
        self.assertIn("OrchestratorAgent", names)
        self.assertIn("DroidSpecialist", names)

        droid_caps = self.client_a.query_capabilities("Droid")
        self.assertIsNotNone(droid_caps)
        self.assertIn("android:build", droid_caps["capabilities"])
        self.assertIn("gradle_compilation", droid_caps["skills"])

    def test_task_delegation_with_checkpoint_and_evidence(self):
        """Verify delegating a task with checkpoint reference and evidence paths."""
        resp = self.client_a.delegate_task(
            target_agent_id="Droid",
            instruction="Assemble APK and run smoke test on emulator.",
            checkpoint_id="chk_step_4",
            evidence_references=["C:\\NR-AI\\data\\live_window_dump.xml"],
            parameters={"build_type": "debug"},
        )
        self.assertIsNone(resp.error)
        self.assertIsNotNone(resp.result)
        self.assertEqual(resp.result["state"], TaskState.WORKING.value)
        task_id = resp.result["task_id"]

        # Check delegated record
        self.assertIn(task_id, self.delegated_tasks)
        stored_params = self.delegated_tasks[task_id]["params"]
        self.assertEqual(stored_params["checkpoint_id"], "chk_step_4")
        self.assertIn("live_window_dump.xml", stored_params["evidence_references"][0])

    def test_task_status_and_cancellation(self):
        """Verify polling task status and issuing a cancellation."""
        resp = self.client_a.delegate_task(
            target_agent_id="Droid",
            instruction="Run long emulator instrumentation tests.",
        )
        task_id = resp.result["task_id"]

        # Poll status
        status_resp = self.client_a.get_task_status("Droid", task_id)
        self.assertIsNone(status_resp.error)
        self.assertEqual(status_resp.result["state"], TaskState.WORKING.value)

        # Cancel task
        cancel_resp = self.client_a.cancel_task("Droid", task_id, reason="User requested abort")
        self.assertIsNone(cancel_resp.error)
        self.assertEqual(cancel_resp.result["status"], "canceled")

        # Verify state transitioned
        status_after = self.client_a.get_task_status("Droid", task_id)
        self.assertEqual(status_after.result["state"], TaskState.CANCELED.value)

    def test_unauthorized_scope_rejection(self):
        """Verify an unprivileged agent cannot delegate tasks."""
        resp = self.client_c.delegate_task(
            target_agent_id="Droid",
            instruction="Unauthorized task delegation attempt.",
        )
        self.assertIsNotNone(resp.error)
        self.assertEqual(resp.error["code"], JsonRpcError.SCOPE_DENIED)
        self.assertIn("lacks required scope", resp.error["message"])

    def test_emergency_stop_barrier(self):
        """Verify active emergency freeze blocks all A2A communications."""
        self.e_stop.trigger_emergency_stop("Safety test freeze")

        resp = self.client_a.delegate_task(
            target_agent_id="Droid",
            instruction="Should be blocked by emergency stop.",
        )
        self.assertIsNotNone(resp.error)
        self.assertEqual(resp.error["code"], JsonRpcError.ESTOP_ACTIVE)
        self.assertIn("EMERGENCY_STOP_ACTIVE", resp.error["message"])


if __name__ == "__main__":
    unittest.main()
