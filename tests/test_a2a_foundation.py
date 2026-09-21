"""
Dedicated Unit & Integration Tests: Internal Google A2A Foundation (JSON-RPC 2.0).
Tests message formats, agent cards, broker dispatch, scope enforcement, timeout, and audit events.
"""

import time
import unittest

from app.a2a.protocol import (
    A2ARequest,
    A2AResponse,
    A2ATask,
    AgentCard,
    JsonRpcError,
    TaskState,
)
from app.a2a.router import (
    A2APermissionScope,
    LocalA2ABroker,
    RegisteredAgent,
)


class TestA2AFoundation(unittest.TestCase):

    def setUp(self):
        self.broker = LocalA2ABroker()

        # Register Agent A (Knowledge Agent)
        self.card_a = AgentCard(
            name="KnowledgeAgent",
            description="Knowledge Graph Querier",
            capabilities=["knowledge_retrieval"],
            permission_scopes=[A2APermissionScope.KNOWLEDGE_READ.value, A2APermissionScope.TASK_CREATE.value],
        )
        self.broker.register_agent(
            agent_id="KnowledgeAgent",
            card=self.card_a,
            handler=self._handler_a,
            granted_scopes={A2APermissionScope.KNOWLEDGE_READ.value, A2APermissionScope.TASK_CREATE.value},
        )

        # Register Agent B (Droid Agent)
        self.card_b = AgentCard(
            name="DroidAgent",
            description="Android Engineering Specialist",
            capabilities=["android_compile", "adb_control"],
            permission_scopes=[A2APermissionScope.TASK_CREATE.value, A2APermissionScope.TASK_QUERY.value],
        )
        self.broker.register_agent(
            agent_id="DroidAgent",
            card=self.card_b,
            handler=self._handler_b,
            granted_scopes={A2APermissionScope.TASK_CREATE.value, A2APermissionScope.TASK_QUERY.value},
        )

    def _handler_a(self, req: A2ARequest) -> A2AResponse:
        if req.method == "knowledge.query":
            return A2AResponse(
                request_id=req.request_id,
                result={"facts": ["Android AGP 8.6.0 is verified"]},
            )
        return A2AResponse(request_id=req.request_id, result={"status": "ok"})

    def _handler_b(self, req: A2ARequest) -> A2AResponse:
        if req.method == "tasks.create":
            return A2AResponse(
                request_id=req.request_id,
                result={"task_id": "DROID-101", "state": "submitted"},
            )
        elif req.method == "slow_op":
            time.sleep(1.0)
            return A2AResponse(request_id=req.request_id, result={"status": "done"})
        return A2AResponse(request_id=req.request_id, result={"status": "ok"})

    def test_agent_card_and_discovery(self):
        """Discovery cards must be retrievable."""
        card = self.broker.get_agent_card("DroidAgent")
        self.assertIsNotNone(card)
        self.assertEqual(card.name, "DroidAgent")
        self.assertIn("android_compile", card.capabilities)

        cards = self.broker.list_agent_cards()
        self.assertEqual(len(cards), 2)

    def test_authorized_dispatch(self):
        """KnowledgeAgent sends tasks.create to DroidAgent with proper scope."""
        req = A2ARequest(
            method="tasks.create",
            params={"task": "compile_apk"},
            source_agent="KnowledgeAgent",
            target_agent="DroidAgent",
        )
        resp = self.broker.dispatch(req)
        self.assertIsNone(resp.error)
        self.assertIsNotNone(resp.result)
        self.assertEqual(resp.result["task_id"], "DROID-101")

    def test_unregistered_sender_rejected(self):
        """Requests from unknown senders must be rejected."""
        req = A2ARequest(
            method="tasks.create",
            params={},
            source_agent="HackerAgent",
            target_agent="DroidAgent",
        )
        resp = self.broker.dispatch(req)
        self.assertIsNotNone(resp.error)
        self.assertEqual(resp.error["code"], JsonRpcError.UNAUTHORIZED_AGENT)

    def test_unregistered_target_rejected(self):
        """Requests to non-existent targets must be rejected."""
        req = A2ARequest(
            method="tasks.create",
            params={},
            source_agent="KnowledgeAgent",
            target_agent="GhostAgent",
        )
        resp = self.broker.dispatch(req)
        self.assertIsNotNone(resp.error)
        self.assertEqual(resp.error["code"], JsonRpcError.METHOD_NOT_FOUND)

    def test_scope_denial(self):
        """Sender without required scope must be rejected."""
        # DroidAgent lacks KNOWLEDGE_READ scope, tries to call knowledge.query
        req = A2ARequest(
            method="knowledge.query",
            params={"q": "android"},
            source_agent="DroidAgent",
            target_agent="KnowledgeAgent",
        )
        resp = self.broker.dispatch(req)
        self.assertIsNotNone(resp.error)
        self.assertEqual(resp.error["code"], JsonRpcError.SCOPE_DENIED)

    def test_timeout_protection(self):
        """Requests exceeding timeout must abort safely without hanging."""
        req = A2ARequest(
            method="slow_op",
            params={},
            source_agent="KnowledgeAgent",
            target_agent="DroidAgent",
        )
        resp = self.broker.dispatch(req, timeout=0.1)
        self.assertIsNotNone(resp.error)
        self.assertEqual(resp.error["code"], JsonRpcError.TIMEOUT)

    def test_audit_logging(self):
        """Every A2A dispatch must record audit events."""
        req = A2ARequest(
            method="tasks.create",
            params={},
            source_agent="KnowledgeAgent",
            target_agent="DroidAgent",
        )
        self.broker.dispatch(req)
        trail = self.broker.get_audit_trail()
        events = [e["event_type"] for e in trail]
        self.assertIn("MESSAGE_DISPATCH", events)
        self.assertIn("RESPONSE_DELIVERED", events)


if __name__ == "__main__":
    unittest.main()
