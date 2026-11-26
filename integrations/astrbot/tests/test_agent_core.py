import random
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from integrations.astrbot.plugins.dafeiyu_agent_core import (
    ActionRequest, AgentEngine, AgentStore, PermissionPolicy, Principal, PromptBuilder,
    ToolExecutionError, ToolExecutor,
)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AgentStore(Path(self.tmp.name) / "agent.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_memory_scope_dedup_conflict_and_hybrid_search(self):
        first = self.store.remember(scope="private:u1", owner_id="u1", kind="preference", content="喜欢白米饭", importance=.6, embedding=[1, 0])
        duplicate = self.store.remember(scope="private:u1", owner_id="u1", kind="preference", content="  喜欢白米饭  ", importance=.8, embedding=[1, 0])
        self.assertEqual(first, duplicate)
        self.store.remember(scope="private:u2", owner_id="u2", kind="preference", content="喜欢白米饭", embedding=[1, 0])
        replacement = self.store.remember(scope="private:u1", owner_id="u1", kind="preference", content="现在更喜欢面条", supersedes_id=first, embedding=[.8, .2])
        found = self.store.search_memories(scope="private:u1", owner_id="u1", query="面条", embedding=[.8, .2])
        self.assertEqual([replacement], [x.id for x in found])
        self.assertEqual([], self.store.search_memories(scope="private:u2", owner_id="u1", query="白米饭"))

    def test_diary_requires_success_receipts(self):
        activity = self.store.create_activity("read", "阅读公开文章")
        with self.assertRaises(ValueError):
            self.store.save_diary("day", "2026-11-26", "今天阅读了文章", [activity])
        self.assertTrue(self.store.start_activity(activity))
        self.assertTrue(self.store.finish_activity(activity, succeeded=True, output={"title": "x"}))
        entry = self.store.save_diary("day", "2026-11-26", "今天阅读了文章", [activity])
        self.assertTrue(entry)

    def test_budget_gate_and_silence(self):
        engine = AgentEngine(self.store, daily_token_limit=10, daily_call_limit=2)
        now = datetime(2026, 11, 26, 12, 0)
        quiet = engine.decide_heartbeat(now=now, energy=.8, pending_tasks=0, recent_human_activity_seconds=999, paused=False, rng=random.Random(0))
        self.assertEqual("idle", quiet.action)
        self.store.charge_budget("2026-11-26", "autonomy", tokens=10)
        blocked = engine.decide_heartbeat(now=now, energy=.8, pending_tasks=2, recent_human_activity_seconds=999, paused=False)
        self.assertEqual("自主活动预算已用尽", blocked.reason)
        self.assertFalse(blocked.should_call_model)

    def test_dashboard_queries_do_not_expose_secret_fields(self):
        self.store.remember(scope="private:u1", owner_id="u1", kind="preference", content="喜欢米饭")
        counts = self.store.counts()
        self.assertEqual(1, counts["active_memories"])
        self.assertNotIn("content", counts)
        self.assertEqual([], self.store.recent_activities())


class PolicyAndPromptTests(unittest.TestCase):
    def test_high_risk_needs_private_explicit_owner(self):
        policy = PermissionPolicy()
        req = ActionRequest("service_restart", "high", explicit=True)
        self.assertFalse(policy.authorize(Principal("x", "admin", True), req)[0])
        self.assertFalse(policy.authorize(Principal("x", "owner", False), req)[0])
        self.assertTrue(policy.authorize(Principal("x", "owner", True), req)[0])

    def test_server_status_requires_private_privileged_identity(self):
        policy = PermissionPolicy()
        req = ActionRequest("server_status", "read")
        self.assertFalse(policy.authorize(Principal("x", "admin", False), req)[0])
        self.assertFalse(policy.authorize(Principal("x", "friend", True), req)[0])
        self.assertTrue(policy.authorize(Principal("x", "admin", True), req)[0])

    def test_all_prompt_modules_and_dynamic_state(self):
        root = Path(__file__).parents[1] / "prompts"
        text = PromptBuilder(root).build(state={"mood": "平静"}, memories=[], tools=[], budget={"calls_left": 3})
        for name in ("core.md", "behavior.md", "memory.md", "heartbeat.md", "growth.md", "social.md", "explore.md", "diary.md", "planner.md", "tools.md", "security.md"):
            self.assertIn("## " + name, text)
        self.assertIn('"calls_left":3', text)


class ToolExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AgentStore(Path(self.tmp.name) / "agent.db")
        self.tools = ToolExecutor(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_calculator_is_real_and_audited(self):
        result = self.tools.execute(
            Principal("u1", "member"), "calculator", {"expression": "(2+3)*4**2"}
        )
        self.assertEqual(80, result["result"]["result"])
        receipt = self.store.recent_tool_receipts()[0]
        self.assertEqual("succeeded", receipt["status"])
        self.assertEqual("calculator", receipt["tool_name"])
        self.assertTrue(receipt["activity_id"])

    def test_calculator_rejects_code_and_records_failure(self):
        with self.assertRaises(ToolExecutionError):
            self.tools.execute(
                Principal("u1", "member"), "calculator",
                {"expression": "__import__('os').getcwd()"},
            )
        self.assertEqual("failed", self.store.recent_tool_receipts()[0]["status"])

    def test_server_status_permission_and_metrics(self):
        with self.assertRaises(PermissionError):
            self.tools.execute(Principal("u1", "member", False), "server_status", {})
        self.assertEqual("denied", self.store.recent_tool_receipts()[0]["status"])
        result = self.tools.execute(Principal("admin", "admin", True), "server_status", {})
        self.assertGreater(result["result"]["memory_total_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
