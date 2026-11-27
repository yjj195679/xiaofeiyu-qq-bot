"""Small, auditable tool executor. No model output can bypass this module."""

from __future__ import annotations

import ast
import json
import ipaddress
import math
import os
import re
import urllib.parse
import urllib.request
from html import unescape
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .policy import ActionRequest, PermissionPolicy, Principal
from .store import AgentStore


class ToolExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risk: str
    handler: Callable[[dict], dict]


def _calculate(args: dict) -> dict:
    expression = str(args.get("expression", "")).strip()
    if not expression or len(expression) > 200:
        raise ToolExecutionError("expression length is invalid")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolExecutionError("invalid expression") from exc

    binary = {
        ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
        ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b,
    }

    def walk(node, depth=0):
        if depth > 24:
            raise ToolExecutionError("expression is too complex")
        if isinstance(node, ast.Expression):
            return walk(node.body, depth + 1)
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            value = node.value
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = walk(node.operand, depth + 1)
            value = value if isinstance(node.op, ast.UAdd) else -value
        elif isinstance(node, ast.BinOp) and type(node.op) in binary:
            value = binary[type(node.op)](walk(node.left, depth + 1), walk(node.right, depth + 1))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            left, right = walk(node.left, depth + 1), walk(node.right, depth + 1)
            if abs(right) > 100 or abs(left) > 1_000_000:
                raise ToolExecutionError("power operation exceeds limit")
            value = left ** right
        else:
            raise ToolExecutionError("unsupported expression")
        if isinstance(value, complex) or not math.isfinite(float(value)) or abs(value) > 1e100:
            raise ToolExecutionError("result exceeds limit")
        return value

    try:
        value = walk(tree)
    except (ArithmeticError, OverflowError) as exc:
        raise ToolExecutionError(type(exc).__name__) from exc
    return {"expression": expression, "result": value}


def _server_status(args: dict) -> dict:
    del args
    try:
        uptime_seconds = int(float(Path("/proc/uptime").read_text().split()[0]))
        mem = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            mem[key] = int(value.strip().split()[0]) * 1024
        disk = os.statvfs("/")
        loads = os.getloadavg()
    except (OSError, ValueError, IndexError) as exc:
        raise ToolExecutionError("server metrics unavailable") from exc
    return {
        "uptime_seconds": uptime_seconds,
        "load_1m": round(loads[0], 3),
        "load_5m": round(loads[1], 3),
        "memory_total_bytes": mem.get("MemTotal", 0),
        "memory_available_bytes": mem.get("MemAvailable", 0),
        "disk_total_bytes": disk.f_blocks * disk.f_frsize,
        "disk_available_bytes": disk.f_bavail * disk.f_frsize,
    }


def _web_search(args: dict) -> dict:
    """Read-only web search using DuckDuckGo's HTML endpoint.

    The response is deliberately normalized and capped before it enters a
    prompt or activity receipt. It is a real network request, not a model
    fabricated result.
    """
    query = str(args.get("query", "")).strip()
    if not query or len(query) > 200:
        raise ToolExecutionError("query length is invalid")
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(url, headers={"User-Agent": "dafeiyu-agent/0.2"})
    try:
        html = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "replace")
    except Exception as exc:
        raise ToolExecutionError("web search unavailable") from exc
    results = []
    blocks = re.findall(r'<div class="result results_links[^>]*>(.*?)</div>\s*</div>', html, re.S)
    for block in blocks[:5]:
        link = re.search(r'class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        snippet = re.search(r'class="result__snippet"[^>]*>(.*?)</a?>', block, re.S)
        if not link:
            continue
        clean = lambda s: re.sub(r"<[^>]+>", "", s).replace("&amp;", "&").strip()
        results.append({"title": clean(link.group(2)), "url": link.group(1), "snippet": clean(snippet.group(1)) if snippet else ""})
    return {"query": query, "results": results, "source": "duckduckgo_html"}


def _web_read(args: dict) -> dict:
    """Fetch a bounded public page and return readable text with its source URL."""
    raw_url = str(args.get("url", "")).strip()
    parsed = urllib.parse.urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ToolExecutionError("only public http(s) URLs are allowed")
    host = parsed.hostname.lower().rstrip(".")
    try:
        address = ipaddress.ip_address(host)
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            raise ToolExecutionError("private or reserved hosts are blocked")
    except ValueError:
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            raise ToolExecutionError("local hosts are blocked")
    safe_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", parsed.query, ""))
    req = urllib.request.Request(safe_url, headers={"User-Agent": "dafeiyu-agent/0.4"})
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                raise ToolExecutionError("unsupported page content type")
            body = response.read(256 * 1024).decode("utf-8", "replace")
    except ToolExecutionError:
        raise
    except Exception as exc:
        raise ToolExecutionError("web page unavailable") from exc
    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", body)
    description_match = re.search(
        r'''(?is)<meta[^>]+(?:name|property)=["'](?:description|og:description)["'][^>]+content=["'](.*?)["']''',
        body,
    )
    title = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", title_match.group(1)))).strip() if title_match else ""
    description = unescape(description_match.group(1)).strip() if description_match else ""
    text = re.sub(r"(?is)<(script|style|noscript|svg).*?>.*?</\1>", " ", body)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", unescape(text)).strip()
    return {"url": safe_url, "content_type": content_type, "title": title[:300],
            "description": description[:600], "text": text[:12000], "truncated": len(text) > 12000}


def _bilibili_search(args: dict) -> dict:
    """Search public Bilibili video metadata; no playback or download is performed."""
    keyword = str(args.get("keyword", "")).strip()
    if not keyword or len(keyword) > 100:
        raise ToolExecutionError("keyword length is invalid")
    url = "https://api.bilibili.com/x/web-interface/search/type?" + urllib.parse.urlencode(
        {"search_type": "video", "keyword": keyword, "page_size": 5}
    )
    req = urllib.request.Request(url, headers={"User-Agent": "dafeiyu-agent/0.4", "Referer": "https://www.bilibili.com/"})
    try:
        raw = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "replace")
        payload = json.loads(raw)
    except Exception as exc:
        raise ToolExecutionError("bilibili search unavailable") from exc
    if payload.get("code") != 0:
        raise ToolExecutionError("bilibili search rejected")
    rows = []
    for item in (payload.get("data", {}).get("result") or [])[:5]:
        bvid = str(item.get("bvid", ""))
        if not bvid:
            continue
        clean = lambda value: re.sub(r"<[^>]+>", "", unescape(str(value or ""))).strip()
        rows.append({"bvid": bvid, "title": clean(item.get("title")),
                     "author": clean(item.get("author")), "description": clean(item.get("description")),
                     "url": "https://www.bilibili.com/video/" + bvid})
    return {"keyword": keyword, "results": rows, "source": "bilibili_public_api"}


class ToolExecutor:
    def __init__(self, store: AgentStore, policy: PermissionPolicy | None = None):
        self.store = store
        self.policy = policy or PermissionPolicy()
        self.specs = {
            "calculator": ToolSpec("calculator", "受限四则运算与乘方", "read", _calculate),
            "server_status": ToolSpec("server_status", "只读服务器负载、内存、磁盘和运行时间", "read", _server_status),
            "web_search": ToolSpec("web_search", "联网搜索公开网页并返回标题、链接和摘要", "read", _web_search),
            "web_read": ToolSpec("web_read", "读取公开网页正文并返回有界文本与来源 URL", "read", _web_read),
            "bilibili_search": ToolSpec("bilibili_search", "搜索 B 站公开视频元数据，不播放不下载", "read", _bilibili_search),
        }

    def prompt_catalog(self) -> list[dict]:
        return [{"name": x.name, "description": x.description, "risk": x.risk}
                for x in self.specs.values()]

    def execute(
        self, principal: Principal, tool_name: str, args: dict, *, explicit: bool = False,
    ) -> dict:
        spec = self.specs.get(tool_name)
        if not spec:
            raise ToolExecutionError("unknown tool")
        request = ActionRequest(tool_name, spec.risk, explicit=explicit)
        receipt_id = self.store.create_tool_receipt(
            tool_name=tool_name, principal_id=principal.id, risk=spec.risk, request=args,
        )
        allowed, reason = self.policy.authorize(principal, request)
        if not allowed:
            self.store.set_tool_receipt_status(receipt_id, "denied", result={"reason": reason})
            raise PermissionError(reason)
        self.store.set_tool_receipt_status(receipt_id, "approved", result={"reason": reason})
        activity_id = self.store.create_activity("tool", f"执行工具：{tool_name}", {"receipt_id": receipt_id})
        with self.store.connect() as db:
            db.execute("UPDATE tool_receipts SET activity_id=? WHERE id=?", (activity_id, receipt_id))
        self.store.start_activity(activity_id)
        self.store.set_tool_receipt_status(receipt_id, "running")
        try:
            output = spec.handler(dict(args))
        except Exception as exc:
            error_type = type(exc).__name__
            self.store.set_tool_receipt_status(receipt_id, "failed", result={"error_type": error_type})
            self.store.finish_activity(activity_id, succeeded=False, error_type=error_type)
            if isinstance(exc, ToolExecutionError):
                raise
            raise ToolExecutionError(error_type) from exc
        self.store.set_tool_receipt_status(receipt_id, "succeeded", result=output)
        self.store.finish_activity(activity_id, succeeded=True, output={"receipt_id": receipt_id, "result": output})
        return {"receipt_id": receipt_id, "result": output}
