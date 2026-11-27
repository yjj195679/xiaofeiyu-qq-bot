"""Budget-aware heartbeat decisions; silence is a first-class outcome."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime

from .store import AgentStore


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    should_call_model: bool
    cost_class: str = "local"


class AgentEngine:
    def __init__(self, store: AgentStore, *, daily_token_limit: int = 12000, daily_call_limit: int = 24):
        self.store = store
        self.daily_token_limit = daily_token_limit
        self.daily_call_limit = daily_call_limit

    def decide_heartbeat(
        self,
        *,
        now: datetime,
        energy: float,
        pending_tasks: int,
        recent_human_activity_seconds: int,
        paused: bool,
        rng: random.Random | None = None,
    ) -> Decision:
        day = now.strftime("%Y-%m-%d")
        usage = self.store.budget_usage(day, "autonomy")
        if paused:
            return Decision("idle", "自主活动已暂停", False)
        if usage["tokens"] >= self.daily_token_limit or usage["calls"] >= self.daily_call_limit:
            return Decision("idle", "自主活动预算已用尽", False)
        if recent_human_activity_seconds < 300:
            return Decision("idle", "刚刚有人聊天，避免抢话", False)
        if energy < 0.2:
            return Decision("rest", "精力较低", False)
        if pending_tasks:
            return Decision("review_tasks", "存在待处理任务", True)
        # A local gate prevents every heartbeat from becoming a paid model call.
        if (rng or random).random() >= 0.18:
            return Decision("idle", "本轮没有足够理由活动", False)
        # The scheduler may choose a real local activity. External exploration
        # is only a proposal until a separately authorised executor is present.
        candidates = ["reflect", "organize_memory", "rest"]
        action = (rng or random).choice(candidates)
        return Decision(action, "预算和状态允许一次自主活动", action != "rest", "model" if action != "rest" else "local")

    def apply_local_decision(self, decision: Decision) -> str | None:
        """Execute only deterministic, local heartbeat work.

        Paid-model decisions and anything that can contact an external service are
        deliberately left to separately authorised executors.  This keeps the
        heartbeat truthful: a completed activity always represents work that
        actually happened on this host.
        """
        if decision.action == "reflect":
            activity_id = self.store.create_activity("reflect", "自主回顾近期真实活动")
            self.store.start_activity(activity_id)
            recent = self.store.recent_activities(limit=20)
            self.store.finish_activity(activity_id, succeeded=True,
                output={"observed_activity_count": len(recent), "external_actions": False})
            return activity_id
        if decision.action == "organize_memory":
            activity_id = self.store.create_activity("organize_memory", "自主整理记忆索引")
            self.store.start_activity(activity_id)
            count = self.store.active_memory_count()
            self.store.finish_activity(activity_id, succeeded=True,
                output={"active_memory_count": count, "external_actions": False})
            return activity_id
        if decision.action != "rest":
            return None
        activity_id = self.store.create_activity("rest", "自主休息", {"reason": decision.reason})
        self.store.start_activity(activity_id)
        self.store.finish_activity(activity_id, succeeded=True, output={"energy_recovered": 0.08})
        # A modest recovery prevents an idle server from remaining permanently
        # low-energy, while the bounded store method protects the persona state.
        self.store.update_persona_state(
            energy_delta=0.08,
            reason="自主休息完成",
            source_receipt_id=activity_id,
        )
        return activity_id

