"""AstrBot adapter for the durable Agent core.

This module provides prompt injection, explicit memory commands and a small
audited tool executor with bounded, receipt-backed autonomous exploration.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, register
try:
    from astrbot.api.agents import TextPart
except ImportError:  # AstrBot 4.29 compatibility
    from astrbot.core.agent.message import TextPart

from .admin import DashboardServer
from .accounting import BudgetPolicy, ModelBudgetAccountant
from .engine import AgentEngine, Decision
from .policy import Principal
from .prompts import PromptBuilder
from .store import AgentStore, BudgetExceeded
from .tools import ToolExecutionError, ToolExecutor


ROOT = Path(os.environ.get("DAFEIYU_AGENT_DIR", "/opt/astrbot/data/dafeiyu_agent"))
PROMPT_DIR = Path(os.environ.get("DAFEIYU_PROMPT_DIR", str(Path(__file__).parent / "prompts")))
SENSITIVE = re.compile(r"密码|验证码|密钥|api\s*key|token|secret|cookie|session", re.I)
EXPLICIT_MEMORY = re.compile(r"记住|帮我记|长期记忆|以后记得|保存这条")
OWNER_IDS = {x.strip() for x in os.environ.get("DAFEIYU_OWNER_IDS", "").split(",") if x.strip()}
BUDGET_EVENT_KEY = "dafeiyu_budget_reservations"


def _scope(event: AstrMessageEvent) -> tuple[str, str]:
    sender = str(event.get_sender_id())
    group = str(event.get_group_id() or "")
    return (("group:" + group) if group else ("private:" + sender), sender)


def _memory_identity(event: AstrMessageEvent) -> dict[str, str]:
    scope, owner = _scope(event)
    return {
        "scope": scope,
        "owner_id": owner,
        "platform_id": str(event.get_platform_id() or event.get_platform_name() or "unknown"),
        "bot_id": str(event.get_self_id() or "unknown"),
    }


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


@register("dafeiyu_agent_core", "yjj195679", "大肥鱼可审计 Agent 核心", "0.2.2")
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
        input_rate = int(os.environ.get("DAFEIYU_INPUT_PRICE_PER_M_MICRO", "0"))
        cached_rate = int(os.environ.get("DAFEIYU_CACHED_PRICE_PER_M_MICRO", "0"))
        output_rate = int(os.environ.get("DAFEIYU_OUTPUT_PRICE_PER_M_MICRO", "0"))
        self.budgets = ModelBudgetAccountant(self.store, {
            "chat": BudgetPolicy(
                token_limit=int(os.environ.get("DAFEIYU_CHAT_TOKEN_LIMIT", "200000")),
                call_limit=int(os.environ.get("DAFEIYU_CHAT_CALL_LIMIT", "200")),
                cost_limit_microunits=int(os.environ.get("DAFEIYU_CHAT_COST_LIMIT_MICRO", "0")),
                request_token_reserve=int(os.environ.get("DAFEIYU_CHAT_REQUEST_TOKEN_RESERVE", "8192")),
                input_price_per_million_microunits=input_rate,
                cached_price_per_million_microunits=cached_rate,
                output_price_per_million_microunits=output_rate,
            ),
            "autonomy": BudgetPolicy(
                token_limit=int(os.environ.get("DAFEIYU_AUTONOMY_TOKEN_LIMIT", "12000")),
                call_limit=int(os.environ.get("DAFEIYU_AUTONOMY_CALL_LIMIT", "24")),
                cost_limit_microunits=int(os.environ.get("DAFEIYU_AUTONOMY_COST_LIMIT_MICRO", "0")),
                request_token_reserve=int(os.environ.get("DAFEIYU_AUTONOMY_REQUEST_TOKEN_RESERVE", "4096")),
                input_price_per_million_microunits=input_rate,
                cached_price_per_million_microunits=cached_rate,
                output_price_per_million_microunits=output_rate,
            ),
        })
        self.heartbeat_task = None
        self.dashboard = DashboardServer(
            self.store, ROOT,
            host=os.environ.get("DAFEIYU_ADMIN_HOST", "127.0.0.1"),
            port=int(os.environ.get("DAFEIYU_ADMIN_PORT", "8770")),
        )
        self.last_human_activity = int(datetime.now().timestamp())
        self.warmup_remaining = max(0, int(os.environ.get("DAFEIYU_AUTONOMY_WARMUP_ACTIVITIES", "3")))
        self.random_interval_min = max(60, int(os.environ.get("DAFEIYU_AUTONOMY_INTERVAL_MIN", "1200")))
        self.random_interval_max = max(self.random_interval_min, int(os.environ.get("DAFEIYU_AUTONOMY_INTERVAL_MAX", "3600")))

    async def initialize(self):
        ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
        await self.dashboard.start()
        self.heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="dafeiyu-agent-heartbeat")

    async def _heartbeat_loop(self):
        while True:
            try:
                if self.warmup_remaining:
                    # A few bounded startup activities make the new life loop
                    # observable immediately after deployment, then normal
                    # randomized intervals take over.
                    await asyncio.sleep(random.uniform(10, 45))
                    if bool(self.store.get_setting("autonomy_paused", True)):
                        continue
                    self.warmup_remaining -= 1
                    state = self.store.persona_state()
                    await self._run_autonomous_life_cycle(
                        state, Decision("explore", "启动后的随机自主活动", True, "model")
                    )
                    await asyncio.to_thread(self._generate_daily_diary)
                    continue
                await asyncio.sleep(random.uniform(self.random_interval_min, self.random_interval_max))
                state = self.store.persona_state()
                decision = self.engine.decide_heartbeat(
                    now=datetime.now(), energy=state["energy"], pending_tasks=0,
                    recent_human_activity_seconds=max(0, int(datetime.now().timestamp()) - self.last_human_activity),
                    paused=bool(self.store.get_setting("autonomy_paused", True)),
                )
                local_id = await asyncio.to_thread(self.engine.apply_local_decision, decision)
                # Silence/rest is a decision, not a completed activity. Do not inflate
                # the activity timeline or diary evidence with heartbeat no-ops.
                if not decision.should_call_model:
                    continue
                await self._run_autonomous_life_cycle(state, decision)
                await asyncio.to_thread(self._generate_daily_diary)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Dafeiyu heartbeat failed (%s)", type(exc).__name__)

    async def _run_autonomous_life_cycle(self, state: dict, decision) -> None:
        """Spend a bounded autonomy call on a decision grounded in real receipts.

        The model may choose a plan, but only the host records what actually ran.
        No external action is claimed here; tool-backed activities must produce
        their own receipts before they can become memories or diary evidence.
        """
        day = datetime.now().strftime("%Y-%m-%d")
        try:
            reservation = await asyncio.to_thread(self.budgets.reserve, day, "autonomy")
        except BudgetExceeded:
            return
        activity_id = await asyncio.to_thread(
            self.store.create_activity, "autonomous_reflection", "模型自主生活决策",
            {"decision": decision.action, "reason": decision.reason},
        )
        await asyncio.to_thread(self.store.start_activity, activity_id)
        try:
            providers = self.context.get_all_providers()
            wanted = os.environ.get("DAFEIYU_AUTONOMY_PROVIDER_ID", "")
            provider = next((p for p in providers if p.meta().id == wanted), None) if wanted else None
            provider = provider or (providers[0] if providers else None)
            if provider is None:
                raise RuntimeError("no text provider available")
            recent = await asyncio.to_thread(self.store.recent_activities, 12)
            prompt = (
                "你是大肥鱼的自主生活规划器。只根据给定真实状态和活动日志决策。"
                "请输出 JSON：{action,reason,next_step,needs_external_tool,query}。"
                "next_step 必须是本机可验证的下一步；禁止声称已经搜索、阅读、观看或联系任何人。\n"
                f"当前状态：{state}\n候选决策：{decision.action}/{decision.reason}\n"
                f"最近活动：{recent}"
            )
            response = await provider.text_chat(
                prompt=prompt,
                system_prompt="你必须区分计划与已完成事实；没有工具回执就不能声称外部活动完成。",
            )
            text = str(getattr(response, "completion_text", None) or getattr(response, "text", None) or response)
            usage = getattr(response, "usage", None)
            if usage is None:
                await asyncio.to_thread(self.store.release_budget, reservation)
            else:
                await asyncio.to_thread(self.budgets.settle, reservation, "autonomy",
                    input_other=int(getattr(usage, "input_other", 0) or 0),
                    input_cached=int(getattr(usage, "input_cached", 0) or 0),
                    output=int(getattr(usage, "output", 0) or 0))
            external = None
            # Only a narrow, read-only search action can be autonomous. The
            # host executes it and stores the receipt; the model cannot claim
            # success merely by mentioning it in JSON.
            if '"needs_external_tool"' in text and '"web_search"' in text:
                import json
                try:
                    plan = json.loads(text[text.find("{"):text.rfind("}") + 1])
                    query = str(plan.get("query", "")).strip()
                    if query:
                        external = await asyncio.to_thread(
                            self.tools.execute, Principal("system", "system", True),
                            "web_search", {"query": query}, explicit=True,
                        )
                except Exception as exc:
                    external = {"error_type": type(exc).__name__}
            await asyncio.to_thread(self.store.finish_activity, activity_id, succeeded=True,
                output={"model_decision": text[:8000], "external_actions": bool(external),
                        "tool_result": external})
        except Exception as exc:
            await asyncio.to_thread(self.store.release_budget, reservation)
            await asyncio.to_thread(self.store.finish_activity, activity_id, succeeded=False,
                error_type=type(exc).__name__)
            logger.warning("Autonomous life cycle failed (%s)", type(exc).__name__)

    def _generate_daily_diary(self) -> None:
        """Create a factual daily diary from succeeded activity receipts only."""
        now = datetime.now()
        key = now.strftime("%Y-%m-%d")
        start = int(datetime(now.year, now.month, now.day).timestamp())
        ids = self.store.successful_activity_ids(start, int(now.timestamp()))
        if not ids:
            return
        existing = self.store.recent_diaries(period="daily", limit=100)
        if any(x.get("period_key") == key for x in existing):
            return
        rows = {x["id"]: x for x in self.store.recent_activities(200)}
        lines = [f"{key} 大肥鱼日记", "", "今天我确实做过的事："]
        labels = {"web_search": "查了一些资料", "learn": "学习了一会儿",
                  "read": "阅读了一些内容", "rest": "休息了一会儿",
                  "organize_memory": "整理了记忆", "reflect": "做了一次自我反思"}
        kinds = []
        for activity_id in ids:
            row = rows.get(activity_id)
            if row:
                kind = row.get('kind', 'activity')
                kinds.append(kind)
                lines.append(f"- {labels.get(kind, kind)}：{row.get('reason', '')[:160]}")
        interests = self._update_interests_from_activities(rows, ids)
        if interests:
            lines.extend(["", "今天的兴趣线索：" + "、".join(interests[:3]) + "。"])
        lines.extend(["", "小结：这些内容都来自已经成功完成的活动回执；没有把计划当成经历。"])
        try:
            self.store.save_diary("daily", key, "\n".join(lines), ids)
        except Exception as exc:
            logger.warning("Daily diary generation failed (%s)", type(exc).__name__)

    def _weekly_diary(self) -> dict | None:
        now = datetime.now()
        end = int(now.timestamp())
        start = int((now - timedelta(days=7)).timestamp())
        ids = self.store.successful_activity_ids(start, end)
        if not ids:
            return None
        rows = {x["id"]: x for x in self.store.recent_activities(200)}
        lines = [f"近七日成长记录（截至 {now:%Y-%m-%d}）", "", "这一周实际完成的活动："]
        for activity_id in ids:
            row = rows.get(activity_id)
            if row:
                lines.append(f"- {row.get('kind', 'activity')}：{row.get('reason', '')[:160]}")
        interests = self._interest_summary(rows, ids)
        if interests:
            lines.extend(["", "兴趣变化：" + "、".join(interests) + "出现得比较频繁。"])
        lines.extend(["", "成长总结：我根据这七天真正做过的事，发现自己更愿意持续探索这些方向；下一步仍以小步、真实完成为准。",
                      "说明：仅根据成功活动回执整理，未将计划或失败操作视为完成。"])
        return {"body": "\n".join(lines), "receipt_ids": ids}

    @staticmethod
    def _interest_summary(rows: dict, ids: list[str]) -> list[str]:
        names = {"web_search": "联网探索", "learn": "学习", "read": "阅读",
                 "organize_memory": "整理记忆", "reflect": "自我反思", "rest": "休息"}
        counts = {}
        for aid in ids:
            kind = rows.get(aid, {}).get("kind")
            if kind in names:
                counts[names[kind]] = counts.get(names[kind], 0) + 1
        return [name for name, _ in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]

    def _update_interests_from_activities(self, rows: dict, ids: list[str]) -> list[str]:
        interests = self._interest_summary(rows, ids)
        if not interests:
            return []
        try:
            state = self.store.persona_state()
            merged = list(dict.fromkeys([*state.get("interests", []), *interests]))[-12:]
            source = ids[-1] if ids else None
            self.store.update_persona_state(interests=merged,
                reason="根据今日已成功活动提取兴趣线索", source_receipt_id=source)
        except Exception as exc:
            logger.warning("Interest growth update failed (%s)", type(exc).__name__)
        return interests

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def observe_activity(self, event: AstrMessageEvent):
        if str(event.get_sender_id()) != str(event.get_self_id()):
            self.last_human_activity = int(datetime.now().timestamp())

    @filter.on_llm_request(priority=-200)
    async def inject_verified_context(self, event: AstrMessageEvent, req: ProviderRequest):
        identity = _memory_identity(event)
        bucket = str(event.get_extra("dafeiyu_budget_bucket", "chat"))
        if bucket not in self.budgets.policies:
            bucket = "chat"
        day = datetime.now().strftime("%Y-%m-%d")
        try:
            reservation_id = await asyncio.to_thread(self.budgets.reserve, day, bucket)
        except BudgetExceeded:
            event.should_call_llm(True)
            event.set_result("今日模型预算已用尽，本次没有调用模型。")
            event.stop_event()
            return
        reservations = list(event.get_extra(BUDGET_EVENT_KEY, []))
        reservations.append({"id": reservation_id, "bucket": bucket})
        event.set_extra(BUDGET_EVENT_KEY, reservations)
        memories = await asyncio.to_thread(
            self.store.search_memories, **identity,
            query=event.message_str[:1000], limit=6,
        )
        state = self.store.persona_state()
        usage = self.store.budget_usage(day, bucket)
        runtime = self.prompts.runtime_context(
            state=state,
            memories=[{"id": m.id, "kind": m.kind, "content": m.content, "created_at": m.created_at} for m in memories],
            tools=self.tools.prompt_catalog(),
            budget={"bucket": bucket, "used": usage, "paused": bool(self.store.get_setting("autonomy_paused", True))},
        )
        req.system_prompt = (
            (req.system_prompt or "") + "\n\n" + self.prompts.static_prompt()
        ).strip()
        part = TextPart(text=runtime)
        if hasattr(part, "mark_as_temp"):
            part = part.mark_as_temp()
        req.extra_user_content_parts.append(part)

    @filter.on_llm_response(priority=-200)
    async def settle_model_usage(self, event: AstrMessageEvent, resp: LLMResponse):
        reservations = list(event.get_extra(BUDGET_EVENT_KEY, []))
        if not reservations:
            return
        item = reservations.pop(0)
        event.set_extra(BUDGET_EVENT_KEY, reservations)
        usage = getattr(resp, "usage", None)
        if usage is None:
            await asyncio.to_thread(self.store.release_budget, item["id"])
            logger.warning("Dafeiyu model response had no usage; reservation released")
            return
        await asyncio.to_thread(
            self.budgets.settle, item["id"], item["bucket"],
            input_other=max(0, int(getattr(usage, "input_other", 0) or 0)),
            input_cached=max(0, int(getattr(usage, "input_cached", 0) or 0)),
            output=max(0, int(getattr(usage, "output", 0) or 0)),
        )

    @filter.on_agent_done(priority=-200)
    async def release_unsettled_model_budget(self, event: AstrMessageEvent, run_context, resp):
        reservations = list(event.get_extra(BUDGET_EVENT_KEY, []))
        event.set_extra(BUDGET_EVENT_KEY, [])
        for item in reservations:
            await asyncio.to_thread(self.store.release_budget, item["id"])

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
        identity = _memory_identity(event)
        try:
            memory_id = await asyncio.to_thread(
                self.store.remember, **identity, kind=kind,
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
        identity = _memory_identity(event)
        rows = await asyncio.to_thread(
            self.store.search_memories, **identity, query=query[:300], limit=8,
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

    @filter.llm_tool(name="dafeiyu_web_search")
    async def web_search_tool(self, event: AstrMessageEvent, query: str) -> str:
        """真实联网搜索公开网页；结果带有工具回执，不能由模型伪造。"""
        try:
            executed = await asyncio.to_thread(
                self.tools.execute, _principal(event), "web_search", {"query": query[:200]},
            )
        except (PermissionError, ToolExecutionError) as exc:
            return "联网搜索失败：" + str(exc)
        rows = executed["result"].get("results", [])
        if not rows:
            return f"没有找到结果（回执 {executed['receipt_id'][:12]}）。"
        return "\n".join(
            f"{i}. {r['title']}\n{r['url']}\n{r['snippet']}"
            for i, r in enumerate(rows, 1)
        ) + f"\n回执：{executed['receipt_id'][:12]}"

    @filter.regex(r"^/?搜索\s+(.+)$")
    async def web_search_command(self, event: AstrMessageEvent):
        event.stop_event()
        query = event.message_str.strip()[2:].strip()
        try:
            executed = await asyncio.to_thread(
                self.tools.execute, _principal(event), "web_search", {"query": query[:200]},
            )
        except (PermissionError, ToolExecutionError) as exc:
            yield event.plain_result("搜索失败：" + str(exc)); return
        rows = executed["result"].get("results", [])
        lines = [f"搜索：{query}"] + [f"{i}. {r['title']}\n{r['url']}\n{r['snippet']}" for i, r in enumerate(rows, 1)]
        lines.append("回执：" + executed["receipt_id"][:12])
        yield event.plain_result("\n".join(lines))

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

    @filter.regex(r"^/?Agent\s*状态$")
    async def status(self, event: AstrMessageEvent):
        event.stop_event()
        state = self.store.persona_state()
        counts = self.store.counts()
        paused = bool(self.store.get_setting("autonomy_paused", True))
        yield event.plain_result(
            "大肥鱼 Agent 核心已加载\n"
            f"模拟心情：{state['mood']}｜精力：{state['energy']:.0%}\n"
            f"自主活动：{'暂停' if paused else '启用'}（模型决策与只读搜索已启用）\n"
            f"长期记忆：{counts['active_memories']}｜已验证活动：{counts['successful_activities']}｜日记：{counts['diary_entries']}"
        )

    @filter.regex(r"^/?Agent\s*活动(?:\s+(\d+))?$")
    async def activity_log(self, event: AstrMessageEvent):
        """Show only persisted activity receipts; no planned work is reported."""
        event.stop_event()
        matched = re.match(r"^/?Agent\s*活动(?:\s+(\d+))?$", event.message_str.strip())
        limit = min(10, max(1, int(matched.group(1))) if matched and matched.group(1) else 5)
        rows = self.store.recent_activities(limit)
        if not rows:
            yield event.plain_result("暂无已记录的真实活动。")
            return
        lines = ["最近真实活动："]
        for row in rows:
            status = row.get("status", "unknown")
            lines.append(f"- {row.get('kind', 'unknown')}｜{status}｜{row.get('reason', '')[:80]}")
        yield event.plain_result("\n".join(lines))

    @filter.regex(r"^/?Agent\s*日记$")
    async def diary_command(self, event: AstrMessageEvent):
        event.stop_event()
        rows = self.store.recent_diaries(period="daily", limit=1)
        if not rows:
            yield event.plain_result("还没有生成日记；需要先有成功的真实活动记录。")
            return
        yield event.plain_result(rows[0]["body"])

    @filter.regex(r"^/?Agent\s*周报$")
    async def weekly_diary_command(self, event: AstrMessageEvent):
        event.stop_event()
        report = self._weekly_diary()
        if not report:
            yield event.plain_result("近七天还没有足够的成功活动记录。")
            return
        yield event.plain_result(report["body"])

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
