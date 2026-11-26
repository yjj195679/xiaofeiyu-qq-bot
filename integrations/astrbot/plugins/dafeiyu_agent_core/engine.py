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
        return Decision("reflect_or_explore", "预算和状态允许一次自主决策", True)
