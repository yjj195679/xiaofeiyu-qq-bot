"""AstrBot adapter for the durable Agent core.

This module intentionally starts with read-only prompt injection and explicit
memory commands. Autonomous external actions remain disabled until an executor
can produce verifiable tool receipts.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register

from .engine import AgentEngine
from .prompts import PromptBuilder
from .store import AgentStore


ROOT = Path(os.environ.get("DAFEIYU_AGENT_DIR", "/opt/astrbot/data/dafeiyu_agent"))
PROMPT_DIR = Path(os.environ.get("DAFEIYU_PROMPT_DIR", str(Path(__file__).parents[2] / "prompts")))
SENSITIVE = re.compile(r"密码|验证码|密钥|api\s*key|token|secret|cookie|session", re.I)
EXPLICIT_MEMORY = re.compile(r"记住|帮我记|长期记忆|以后记得|保存这条")


def _scope(event: AstrMessageEvent) -> tuple[str, str]:
    sender = str(event.get_sender_id())
    group = str(event.get_group_id() or "")
    return (("group:" + group) if group else ("private:" + sender), sender)


@register("dafeiyu_agent_core", "yjj195679", "大肥鱼可审计 Agent 核心", "0.1.0")
class DafeiyuAgentCore(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.store = AgentStore(ROOT / "agent.sqlite")
        self.engine = AgentEngine(
            self.store,
            daily_token_limit=int(os.environ.get("DAFEIYU_AUTONOMY_TOKEN_LIMIT", "12000")),
            daily_call_limit=int(os.environ.get("DAFEIYU_AUTONOMY_CALL_LIMIT", "24")),
        )
        self.prompts = PromptBuilder(PROMPT_DIR)
        self.heartbeat_task = None
        self.last_human_activity = int(datetime.now().timestamp())

    async def initialize(self):
        ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="dafeiyu-agent-heartbeat")

    async def _heartbeat_loop(self):
        while True:
            try:
                await asyncio.sleep(900)
                state = self.store.persona_state()
                decision = self.engine.decide_heartbeat(
                    now=datetime.now(), energy=state["energy"], pending_tasks=0,
                    recent_human_activity_seconds=max(0, int(datetime.now().timestamp()) - self.last_human_activity),
                    paused=bool(self.store.get_setting("autonomy_paused", True)),
                )
                # Until the executor is wired, record only local no-op decisions.
                if not decision.should_call_model:
                    activity = self.store.create_activity("heartbeat", decision.reason, {"decision": decision.action})
                    self.store.start_activity(activity)
                    self.store.finish_activity(activity, succeeded=True, output={"decision": decision.action, "external_action": False})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Dafeiyu heartbeat failed (%s)", type(exc).__name__)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def observe_activity(self, event: AstrMessageEvent):
        if str(event.get_sender_id()) != str(event.get_self_id()):
            self.last_human_activity = int(datetime.now().timestamp())

    @filter.on_llm_request(priority=-200)
    async def inject_verified_context(self, event: AstrMessageEvent, req: ProviderRequest):
        scope, owner = _scope(event)
        memories = await asyncio.to_thread(
            self.store.search_memories, scope=scope, owner_id=owner,
            query=event.message_str[:1000], limit=6,
        )
        state = self.store.persona_state()
        day = datetime.now().strftime("%Y-%m-%d")
        usage = self.store.budget_usage(day, "autonomy")
        block = self.prompts.build(
            state=state,
            memories=[{"id": m.id, "kind": m.kind, "content": m.content, "created_at": m.created_at} for m in memories],
            tools=[],
            budget={"bucket": "autonomy", "used": usage, "paused": bool(self.store.get_setting("autonomy_paused", True))},
        )
        req.system_prompt = ((req.system_prompt or "") + "\n\n" + block).strip()

    @filter.llm_tool(name="dafeiyu_remember")
    async def remember_tool(self, event: AstrMessageEvent, content: str, kind: str = "preference") -> str:
        """仅在当前用户明确要求记住一项非敏感事实时保存长期记忆。

        Args:
            content(string): 用户明确要求保存的事实，不包含密码、密钥、验证码或推测。
            kind(string): preference、event、relationship、knowledge 之一。
        """
        if not EXPLICIT_MEMORY.search(event.message_str):
            return "用户没有明确要求保存长期记忆，本次未写入。"
        if SENSITIVE.search(content):
            return "敏感凭据不得写入长期记忆。"
        if kind not in {"preference", "event", "relationship", "knowledge"}:
            return "记忆类型无效，本次未写入。"
        scope, owner = _scope(event)
        try:
            memory_id = await asyncio.to_thread(
                self.store.remember, scope=scope, owner_id=owner, kind=kind,
                content=content, importance=.6,
            )
            return "长期记忆已保存，记录号：" + memory_id[:12]
        except Exception as exc:
            logger.warning("Memory write failed (%s)", type(exc).__name__)
            return "长期记忆保存失败。"

    @filter.llm_tool(name="dafeiyu_recall")
    async def recall_tool(self, event: AstrMessageEvent, query: str) -> str:
        """检索当前用户在当前私聊或群聊范围内明确保存的长期记忆。

        Args:
            query(string): 简短检索主题，不包含凭据。
        """
        scope, owner = _scope(event)
        rows = await asyncio.to_thread(
            self.store.search_memories, scope=scope, owner_id=owner, query=query[:300], limit=8,
        )
        if not rows:
            return "当前范围内没有找到相关长期记忆。"
        return "\n".join(f"{x.id[:12]} | {x.kind} | {x.content}" for x in rows)

    @filter.regex(r"^/?Agent状态$")
    async def status(self, event: AstrMessageEvent):
        event.stop_event()
        state = self.store.persona_state()
        counts = self.store.counts()
        paused = bool(self.store.get_setting("autonomy_paused", True))
        yield event.plain_result(
            "大肥鱼 Agent 核心已加载\n"
            f"模拟心情：{state['mood']}｜精力：{state['energy']:.0%}\n"
            f"自主活动：{'暂停' if paused else '启用'}（外部执行器尚未启用）\n"
            f"长期记忆：{counts['active_memories']}｜已验证活动：{counts['successful_activities']}｜日记：{counts['diary_entries']}"
        )

    @filter.regex(r"^/?自主活动\s+(暂停|恢复)$")
    async def autonomy_toggle(self, event: AstrMessageEvent):
        event.stop_event()
        if not event.is_private_chat() or not event.is_admin():
            yield event.plain_result("只有管理员私聊可以修改自主活动开关。")
            return
        resume = event.message_str.strip().endswith("恢复")
        self.store.set_setting("autonomy_paused", not resume)
        yield event.plain_result("自主活动已恢复。" if resume else "自主活动已暂停。")

    async def terminate(self):
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            try:
                await self.heartbeat_task
            except asyncio.CancelledError:
                pass
