"""
Dedicated Unit & Integration Tests: Memory Boundaries & Scoping Engine.
Tests isolation between the 5 memory domains (Knowledge, Conversation, Task, Project, Agent),
workspace leakage prevention, and agent scratchpad isolation.
"""

import unittest

from app.memory.boundaries import (
    MemoryBoundaryManager,
    MemoryCategory,
    MemoryRecord,
)


class TestMemoryBoundaries(unittest.TestCase):

    def setUp(self):
        self.mgr = MemoryBoundaryManager()

    def test_knowledge_memory_globally_readable(self):
        """Knowledge memory must be universally readable by any agent."""
        rec = MemoryRecord(
            category=MemoryCategory.KNOWLEDGE,
            owner_id="KnowledgeAgent",
            workspace_scope="global",
            data={"fact": "Android Studio Ladybug 2024.2.1 is verified"},
        )
        ok, reason = self.mgr.validate_access(
            caller_agent_id="AnyAgent",
            caller_workspace="C:\\NR-AI\\dev_projects\\NR-AI",
            record=rec,
            operation="read",
        )
        self.assertTrue(ok)
        self.assertIn("universally readable", reason)

    def test_knowledge_memory_write_restricted(self):
        """Knowledge memory write must be restricted to verified authorities."""
        rec = MemoryRecord(
            category=MemoryCategory.KNOWLEDGE,
            owner_id="KnowledgeAgent",
            workspace_scope="global",
        )
        ok, reason = self.mgr.validate_access(
            caller_agent_id="UntrustedWorker",
            caller_workspace="C:\\NR-AI\\dev_projects\\NR-AI",
            record=rec,
            operation="write",
        )
        self.assertFalse(ok)
        self.assertIn("verified knowledge authority", reason)

    def test_cross_workspace_task_memory_leakage_blocked(self):
        """Agent in Workspace A cannot access Task memory belonging to Workspace B."""
        rec = MemoryRecord(
            category=MemoryCategory.TASK,
            owner_id="DroidAgent",
            workspace_scope="C:\\NR-AI\\dev_projects\\ProjectAlpha",
            data={"task_id": "TASK-ALPHA"},
        )
        ok, reason = self.mgr.validate_access(
            caller_agent_id="DroidAgent",
            caller_workspace="C:\\NR-AI\\dev_projects\\ProjectBeta",
            record=rec,
            operation="read",
        )
        self.assertFalse(ok)
        self.assertIn("WORKSPACE_LEAKAGE_DENIED", reason)

    def test_matching_workspace_allowed(self):
        """Agent in matching workspace is authorized."""
        rec = MemoryRecord(
            category=MemoryCategory.PROJECT,
            owner_id="ArchitectAgent",
            workspace_scope="C:\\NR-AI\\dev_projects\\NR-AI",
            data={"build_target": "release"},
        )
        ok, _ = self.mgr.validate_access(
            caller_agent_id="ArchitectAgent",
            caller_workspace="C:\\NR-AI\\dev_projects\\NR-AI",
            record=rec,
            operation="read",
        )
        self.assertTrue(ok)

    def test_agent_scratchpad_isolation(self):
        """Agent A cannot access Agent B's private scratchpad."""
        rec = MemoryRecord(
            category=MemoryCategory.AGENT,
            owner_id="AgentA",
            workspace_scope="global",
            data={"private_thought": "secret"},
        )
        ok, reason = self.mgr.validate_access(
            caller_agent_id="AgentB",
            caller_workspace="C:\\NR-AI",
            record=rec,
        )
        self.assertFalse(ok)
        self.assertIn("AGENT_ISOLATION_DENIED", reason)

    def test_scratchpad_ephemeral_storage(self):
        """Private scratchpads update and clear properly."""
        self.mgr.update_agent_scratchpad("AgentX", {"note": "step 1 complete"})
        pad = self.mgr.get_agent_scratchpad("AgentX")
        self.assertEqual(pad.get("note"), "step 1 complete")

        self.mgr.clear_agent_scratchpad("AgentX")
        pad_empty = self.mgr.get_agent_scratchpad("AgentX")
        self.assertEqual(len(pad_empty), 0)


if __name__ == "__main__":
    unittest.main()
