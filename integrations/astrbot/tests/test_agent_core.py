import json
import random
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from integrations.astrbot.plugins.dafeiyu_agent_core import (
    ActionRequest, AgentEngine, AgentStore, BudgetExceeded, BudgetPolicy,
    EmbeddingService, ModelBudgetAccountant, PermissionPolicy, Principal, PromptBuilder,
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

    def test_chinese_ngram_retrieval_and_full_isolation(self):
        memory_id = self.store.remember(
            scope="private:u1", owner_id="u1", kind="preference",
            content="主人喜欢听邓紫棋的歌曲。", platform_id="qq", bot_id="fish-a",
        )
        found = self.store.search_memories(
            scope="private:u1", owner_id="u1", query="我喜欢哪个歌手？",
            platform_id="qq", bot_id="fish-a",
        )
        self.assertEqual([memory_id], [x.id for x in found])
        self.assertEqual([], self.store.search_memories(
            scope="private:u1", owner_id="u1", query="喜欢的歌手",
            platform_id="wechat", bot_id="fish-a",
        ))
        self.assertEqual([], self.store.search_memories(
            scope="private:u1", owner_id="u1", query="喜欢的歌手",
            platform_id="qq", bot_id="fish-b",
        ))

    def test_memory_history_tracks_explicit_updates(self):
        memory_id = self.store.remember(
            scope="private:u1", owner_id="u1", kind="preference", content="喜欢米饭"
        )
        self.assertTrue(self.store.update_memory(
            memory_id, scope="private:u1", owner_id="u1", content="更喜欢面条",
            confidence=.9, reason="用户明确更正",
        ))
        history = self.store.memory_history(memory_id, scope="private:u1", owner_id="u1")
        self.assertEqual([1, 2], [row["version"] for row in history])
        self.assertEqual("更喜欢面条", history[-1]["content"])

    def test_memory_archive_delete_restore_and_export(self):
        memory_id = self.store.remember(
            scope="private:u1", owner_id="u1", kind="event", content="完成了一次真实测试",
            source="tool_receipt", confidence=.95,
        )
        self.assertTrue(self.store.archive_memory(memory_id, scope="private:u1", owner_id="u1"))
        self.assertTrue(self.store.restore_memory(memory_id, scope="private:u1", owner_id="u1"))
        self.assertTrue(self.store.forget(memory_id, scope="private:u1", owner_id="u1"))
        exported = self.store.export_memories(scope="private:u1", owner_id="u1")
        self.assertEqual("deleted", exported[0]["status"])
        self.assertEqual("tool_receipt", exported[0]["source"])
        self.assertEqual([1, 2, 3, 4], [x["version"] for x in exported[0]["history"]])

    def test_atomic_budget_reservations_block_concurrent_overspend(self):
        def reserve(_):
            try:
                return self.store.reserve_budget(
                    "2026-11-26", "autonomy", tokens=10, calls=1,
                    token_limit=50, call_limit=5, cost_limit_microunits=0,
                )
            except BudgetExceeded:
                return None

        with ThreadPoolExecutor(max_workers=10) as pool:
            reservations = list(pool.map(reserve, range(20)))
        accepted = [x for x in reservations if x]
        self.assertEqual(5, len(accepted))
        self.assertTrue(self.store.settle_budget(accepted[0], tokens=8, calls=1))
        self.assertTrue(self.store.release_budget(accepted[1]))
        self.assertEqual(8, self.store.budget_usage("2026-11-26", "autonomy")["tokens"])

    def test_model_budget_accounting_uses_provider_usage_and_records_overrun(self):
        accounting = ModelBudgetAccountant(self.store, {
            "chat": BudgetPolicy(
                token_limit=100, call_limit=3, cost_limit_microunits=100,
                request_token_reserve=40,
                input_price_per_million_microunits=1_000_000,
                cached_price_per_million_microunits=500_000,
                output_price_per_million_microunits=2_000_000,
            )
        })
        reservation = accounting.reserve("2026-11-26", "chat")
        self.assertTrue(accounting.settle(
            reservation, "chat", input_other=20, input_cached=10, output=15,
        ))
        usage = self.store.budget_usage("2026-11-26", "chat")
        self.assertEqual({"tokens": 45, "calls": 1, "cost_microunits": 55}, usage)

    def test_model_budget_reservation_fails_before_provider_call(self):
        accounting = ModelBudgetAccountant(self.store, {
            "chat": BudgetPolicy(token_limit=20, call_limit=1, request_token_reserve=20)
        })
        accounting.reserve("2026-11-26", "chat")
        with self.assertRaises(BudgetExceeded):
            accounting.reserve("2026-11-26", "chat")

    def test_schema_v1_is_migrated_without_data_loss(self):
        legacy = Path(self.tmp.name) / "legacy.db"
        db = sqlite3.connect(legacy)
        db.executescript("""
            CREATE TABLE memories(
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, owner_id TEXT NOT NULL,
                kind TEXT NOT NULL, content TEXT NOT NULL, normalized TEXT NOT NULL,
                importance REAL NOT NULL, embedding TEXT, source_receipt_id TEXT,
                supersedes_id TEXT, status TEXT NOT NULL DEFAULT 'active',
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                last_recalled_at INTEGER, recall_count INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO memories(id,scope,owner_id,kind,content,normalized,importance,status,created_at,updated_at)
            VALUES('old','private:u1','u1','event','旧记忆','旧记忆',0.5,'active',1,1);
        """)
        db.close()
        migrated = AgentStore(legacy)
        with migrated.connect() as check:
            row = check.execute("SELECT platform_id,conversation_id,user_id FROM memories WHERE id='old'").fetchone()
            version = check.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()[0]
        self.assertEqual(("qq", "private:u1", "u1"), tuple(row))
        self.assertEqual("3", version)

    def test_diary_requires_success_receipts(self):
        activity = self.store.create_activity("read", "阅读公开文章")
        with self.assertRaises(ValueError):
            self.store.save_diary("day", "2026-11-26", "今天阅读了文章", [activity])
        self.assertTrue(self.store.start_activity(activity))
        self.assertTrue(self.store.finish_activity(activity, succeeded=True, output={"title": "x"}))
        entry = self.store.save_diary("day", "2026-11-26", "今天阅读了文章", [activity])
        self.assertTrue(entry)

    def test_persona_updates_are_bounded_versioned_and_restorable(self):
        first = self.store.persona_state()
        changed = self.store.update_persona_state(
            mood="好奇", energy_delta=-.1, interests=["人工智能", "音乐"],
            traits={"诚实": 1.0}, reason="主人明确配置",
        )
        self.assertEqual(first["revision"] + 1, changed["revision"])
        self.assertEqual("好奇", changed["mood"])
        with self.assertRaises(ValueError):
            self.store.update_persona_state(energy_delta=.5, reason="越界更新")
        restored = self.store.restore_persona(first["revision"], reason="回滚测试")
        self.assertEqual(first["mood"], restored["mood"])
        self.assertEqual(first["energy"], restored["energy"])
        self.assertEqual(3, len(self.store.persona_history()))

    def test_persona_evidence_and_relationship_scope(self):
        activity = self.store.create_activity("learn", "真实学习")
        with self.assertRaises(ValueError):
            self.store.update_persona_state(
                mood="开心", reason="未完成活动", source_receipt_id=activity,
            )
        self.store.start_activity(activity)
        self.store.finish_activity(activity, succeeded=True, output={"topic": "SQLite"})
        state = self.store.update_persona_state(
            mood="满足", reason="完成学习", source_receipt_id=activity,
        )
        self.assertEqual("满足", state["mood"])
        self.store.update_relationship_profile(
            subject_id="u1", scope="private:u1", profile={"role": "owner", "trust": .9},
        )
        self.assertEqual("owner", self.store.relationship_profile(
            subject_id="u1", scope="private:u1"
        )["profile"]["role"])
        self.assertEqual({}, self.store.relationship_profile(
            subject_id="u1", scope="group:g1"
        )["profile"])

    def test_budget_gate_and_silence(self):
        engine = AgentEngine(self.store, daily_token_limit=10, daily_call_limit=2)
        now = datetime(2026, 11, 26, 12, 0)
        quiet = engine.decide_heartbeat(now=now, energy=.8, pending_tasks=0, recent_human_activity_seconds=999, paused=False, rng=random.Random(0))
        self.assertEqual("idle", quiet.action)
        self.store.charge_budget("2026-11-26", "autonomy", tokens=10)
        blocked = engine.decide_heartbeat(now=now, energy=.8, pending_tasks=2, recent_human_activity_seconds=999, paused=False)
        self.assertEqual("自主活动预算已用尽", blocked.reason)
        self.assertFalse(blocked.should_call_model)

    def test_low_energy_rest_is_a_real_local_activity(self):
        engine = AgentEngine(self.store)
        decision = engine.decide_heartbeat(
            now=datetime(2026, 11, 26, 12, 0), energy=.1, pending_tasks=0,
            recent_human_activity_seconds=999, paused=False,
        )
        self.assertEqual("rest", decision.action)
        activity_id = engine.apply_local_decision(decision)
        self.assertTrue(activity_id)
        self.assertEqual(1, self.store.counts()["successful_activities"])
        self.assertAlmostEqual(.78, self.store.persona_state()["energy"])

    def test_autonomous_scheduler_executes_only_truthful_local_activities(self):
        engine = AgentEngine(self.store)
        for action in ("reflect", "organize_memory"):
            activity_id = engine.apply_local_decision(
                type("Decision", (), {"action": action, "reason": "测试"})()
            )
            self.assertTrue(activity_id)
        rows = self.store.recent_activities(limit=2)
        self.assertEqual({"succeeded"}, {row["status"] for row in rows})
        self.assertEqual({"reflect", "organize_memory"}, {row["kind"] for row in rows})
        self.assertTrue(all('external_actions' in (json.loads(row["output_json"])) for row in rows))

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

    def test_web_read_blocks_private_hosts(self):
        with self.assertRaises(ToolExecutionError):
            self.tools.execute(Principal("u1", "member"), "web_read", {"url": "http://127.0.0.1/"})
        self.assertEqual("failed", self.store.recent_tool_receipts()[0]["status"])

    def test_web_read_is_registered(self):
        self.assertIn("web_read", {x["name"] for x in self.tools.prompt_catalog()})


class EmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AgentStore(Path(self.tmp.name) / "agent.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_external_provider_requires_opt_in_and_uses_cache(self):
        class Provider:
            name, model, external = "test", "zh-test", True
            calls = 0

            def embed(self, texts):
                self.calls += 1
                return [[.1, .2, .3] for _ in texts]

        provider = Provider()
        service = EmbeddingService(self.store, provider)
        self.assertIsNone(service.vector("主人喜欢音乐"))
        self.assertEqual([.1, .2, .3], service.vector("主人喜欢音乐", allow_external=True))
        self.assertEqual([.1, .2, .3], service.vector("主人喜欢音乐", allow_external=True))
        self.assertEqual(1, provider.calls)

    def test_failure_and_sensitive_text_fall_back_without_breaking_search(self):
        class FailingProvider:
            name, model, external = "broken", "broken", False

            def embed(self, texts):
                raise RuntimeError("offline")

        service = EmbeddingService(self.store, FailingProvider())
        self.assertIsNone(service.vector("普通知识"))
        self.assertIsNone(service.vector("API key 是 secret"))
        memory_id = self.store.remember(
            scope="private:u1", owner_id="u1", kind="knowledge", content="大肥鱼喜欢学习人工智能"
        )
        found = self.store.search_memories(
            scope="private:u1", owner_id="u1", query="喜欢学习什么", embedding=service.vector("喜欢学习什么")
        )
        self.assertEqual(memory_id, found[0].id)


if __name__ == "__main__":
    unittest.main()

