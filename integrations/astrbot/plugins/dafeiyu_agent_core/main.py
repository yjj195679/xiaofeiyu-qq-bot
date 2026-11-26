"""AstrBot adapter for the durable Agent core.

This module provides prompt injection, explicit memory commands and a small
audited tool executor. Autonomous external actions remain disabled.
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

from .admin import DashboardServer
from .engine import AgentEngine
from .policy import Principal
from .prompts import PromptBuilder
from .store import AgentStore
from .tools import ToolExecutionError, ToolExecutor


ROOT = Path(os.environ.get("DAFEIYU_AGENT_DIR", "/opt/astrbot/data/dafeiyu_agent"))
PROMPT_DIR = Path(os.environ.get("DAFEIYU_PROMPT_DIR", str(Path(__file__).parents[2] / "prompts")))
SENSITIVE = re.compile(r"密码|验证码|密钥|api\s*key|token|secret|cookie|session", re.I)
EXPLICIT_MEMORY = re.compile(r"记住|帮我记|长期记忆|以后记得|保存这条")
OWNER_IDS = {x.strip() for x in os.environ.get("DAFEIYU_OWNER_IDS", "").split(",") if x.strip()}


def _scope(event: AstrMessageEvent) -> tuple[str, str]:
    sender = str(event.get_sender_id())
    group = str(event.get_group_id() or "")
    return (("group:" + group) if group else ("private:" + sender), sender)


def _principal(event: AstrMessageEvent) -> Principal:
    sender = str(event.get_sender_id())
    private = bool(event.is_private_chat())
    if sender in OWNER_IDS:
        role = "owner"
    elif event.is_admin():
        role = "admin"
    else:
        role = "friend" if private else "member"
    return Principal(sender, role, private)


@register("dafeiyu_agent_core", "yjj195679", "大肥鱼可审计 Agent 核心", "0.2.0")
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
        self.tools = ToolExecutor(self.store)
        self.heartbeat_task = None
        self.dashboard = DashboardServer(
            self.store, ROOT,
            host=os.environ.get("DAFEIYU_ADMIN_HOST", "127.0.0.1"),
            port=int(os.environ.get("DAFEIYU_ADMIN_PORT", "8770")),
        )
        self.last_human_activity = int(datetime.now().timestamp())

    async def initialize(self):
        ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
        await self.dashboard.start()
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
            tools=self.tools.prompt_catalog(),
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

    @filter.llm_tool(name="dafeiyu_calculate")
    async def calculate_tool(self, event: AstrMessageEvent, expression: str) -> str:
        """真实执行受限数学表达式，不执行 Python 代码。

        Args:
            expression(string): 仅包含数字、括号、加减乘除、整除、取模和乘方的表达式。
        """
        try:
            executed = await asyncio.to_thread(
                self.tools.execute, _principal(event), "calculator", {"expression": expression}
            )
        except (PermissionError, ToolExecutionError) as exc:
            return "计算工具执行失败：" + str(exc)
        return f"计算结果：{executed['result']['result']}（回执 {executed['receipt_id'][:12]}）"

    @filter.llm_tool(name="dafeiyu_server_status")
    async def server_status_tool(self, event: AstrMessageEvent) -> str:
        """真实读取服务器负载、内存、磁盘和运行时间；仅限主人或管理员私聊。"""
        try:
            executed = await asyncio.to_thread(
                self.tools.execute, _principal(event), "server_status", {}
            )
        except PermissionError:
            return "无权读取服务器状态；该工具仅限主人或管理员私聊。"
        except ToolExecutionError as exc:
            return "服务器状态读取失败：" + str(exc)
        value = executed["result"]
        gib = 1024 ** 3
        return (
            f"服务器已运行 {value['uptime_seconds'] // 3600} 小时；"
            f"1 分钟负载 {value['load_1m']}；"
            f"可用内存 {value['memory_available_bytes'] / gib:.2f} GiB；"
            f"可用磁盘 {value['disk_available_bytes'] / gib:.2f} GiB。"
            f"（回执 {executed['receipt_id'][:12]}）"
        )

    @filter.regex(r"^/?计算\s+(.+)$")
    async def calculate_command(self, event: AstrMessageEvent):
        event.stop_event()
        matched = re.match(r"^/?计算\s+(.+)$", event.message_str.strip(), re.S)
        expression = matched.group(1) if matched else ""
        try:
            executed = await asyncio.to_thread(
                self.tools.execute, _principal(event), "calculator", {"expression": expression}
            )
            yield event.plain_result(
                f"计算结果：{executed['result']['result']}\n回执：{executed['receipt_id'][:12]}"
            )
        except (PermissionError, ToolExecutionError) as exc:
            yield event.plain_result("计算失败：" + str(exc))

    @filter.regex(r"^/?服务器状态$")
    async def server_status_command(self, event: AstrMessageEvent):
        event.stop_event()
        try:
            executed = await asyncio.to_thread(
                self.tools.execute, _principal(event), "server_status", {}
            )
        except PermissionError:
            yield event.plain_result("无权读取服务器状态；仅限主人或管理员私聊。")
            return
        except ToolExecutionError as exc:
            yield event.plain_result("服务器状态读取失败：" + str(exc))
            return
        value = executed["result"]
        gib = 1024 ** 3
        yield event.plain_result(
            f"运行时间：{value['uptime_seconds'] // 3600} 小时\n"
            f"负载：{value['load_1m']} / {value['load_5m']}\n"
            f"可用内存：{value['memory_available_bytes'] / gib:.2f} GiB\n"
            f"可用磁盘：{value['disk_available_bytes'] / gib:.2f} GiB\n"
            f"回执：{executed['receipt_id'][:12]}"
        )

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
        await self.dashboard.stop()
