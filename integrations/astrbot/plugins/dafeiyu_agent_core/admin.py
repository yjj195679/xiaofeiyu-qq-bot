"""Loopback-only, token-authenticated management surface."""

from __future__ import annotations

import hmac
import json
import os
import secrets
from datetime import datetime
from pathlib import Path

from aiohttp import web


HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>大肥鱼 Agent</title><style>
:root{color-scheme:light dark;--bg:#f5f7fa;--card:#fff;--ink:#18212f;--muted:#667085;--blue:#1769e0;--line:#e4e7ec}
@media(prefers-color-scheme:dark){:root{--bg:#111827;--card:#1f2937;--ink:#f3f4f6;--muted:#a8b0bf;--line:#374151}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,sans-serif}
main{max-width:1040px;margin:auto;padding:24px}.top{display:flex;gap:14px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px;margin:18px 0}.card{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:18px;box-shadow:0 6px 22px #0000000b}
.big{font-size:28px;font-weight:700}.muted{color:var(--muted)}input,button{font:inherit;border-radius:9px;border:1px solid var(--line);padding:9px 12px}button{background:var(--blue);color:#fff;border:0;cursor:pointer}button.secondary{background:#667085}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:9px;border-bottom:1px solid var(--line);vertical-align:top}.scroll{overflow:auto}.pill{display:inline-block;padding:2px 8px;border-radius:99px;background:#1769e020;color:var(--blue)}
</style></head><body><main><div class="top"><div><h1>大肥鱼 Agent</h1><div class="muted">真实状态与活动回执面板</div></div><div><input id="token" type="password" placeholder="管理令牌"><button onclick="load()">连接</button></div></div>
<div id="error" class="muted"></div><section id="app" hidden><div class="grid">
<div class="card"><div class="muted">模拟心情</div><div id="mood" class="big"></div></div>
<div class="card"><div class="muted">精力</div><div id="energy" class="big"></div></div>
<div class="card"><div class="muted">长期记忆</div><div id="memories" class="big"></div></div>
<div class="card"><div class="muted">自主活动</div><div id="autonomy" class="big"></div><button id="toggle" class="secondary"></button></div>
</div><div class="card"><h2>今日自主预算</h2><div id="budget"></div></div>
<div class="card"><h2>最近活动</h2><div class="scroll"><table><thead><tr><th>时间</th><th>类型</th><th>状态</th><th>原因</th></tr></thead><tbody id="activities"></tbody></table></div></div></section>
<script>
const q=id=>document.getElementById(id);let paused=true;
async function api(path,opts={}){const token=q('token').value.trim();const r=await fetch(path,{...opts,headers:{...(opts.headers||{}),'Authorization':'Bearer '+token,'Content-Type':'application/json'}});if(!r.ok)throw Error('请求失败：'+r.status);return r.json()}
function td(v){const e=document.createElement('td');e.textContent=v??'';return e}
async function load(){try{const d=await api('/api/summary');q('error').textContent='';q('app').hidden=false;q('mood').textContent=d.persona.mood;q('energy').textContent=Math.round(d.persona.energy*100)+'%';q('memories').textContent=d.counts.active_memories;paused=d.autonomy_paused;q('autonomy').textContent=paused?'暂停':'运行';q('toggle').textContent=paused?'恢复':'暂停';q('budget').textContent=`调用 ${d.budget.calls} 次 · Token ${d.budget.tokens} · 成本微单位 ${d.budget.cost_microunits}`;const body=q('activities');body.replaceChildren();for(const x of d.activities){const tr=document.createElement('tr');tr.append(td(new Date(x.created_at*1000).toLocaleString()),td(x.kind),td(x.status),td(x.reason));body.append(tr)}}catch(e){q('app').hidden=true;q('error').textContent=e.message}}
q('toggle').onclick=async()=>{if(!confirm((paused?'恢复':'暂停')+'自主活动？'))return;await api('/api/autonomy',{method:'POST',body:JSON.stringify({paused:!paused})});await load()}
</script></main></body></html>"""


class DashboardServer:
    def __init__(self, store, root: Path, host: str = "127.0.0.1", port: int = 8770):
        if host not in {"127.0.0.1", "::1"}:
            raise ValueError("dashboard must remain loopback-only")
        self.store = store
        self.root = root
        self.host = host
        self.port = port
        self.runner = None
        self.token = self._token()

    def _token(self) -> str:
        path = self.root / "admin.token"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.exists():
            token = path.read_text(encoding="utf-8").strip()
            if len(token) < 32:
                raise ValueError("invalid dashboard token")
            return token
        token = secrets.token_urlsafe(36)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(token + "\n")
        return token

    def _allowed(self, request: web.Request) -> bool:
        supplied = request.headers.get("Authorization", "")
        expected = "Bearer " + self.token
        return hmac.compare_digest(supplied, expected)

    async def index(self, request):
        return web.Response(text=HTML, content_type="text/html")

    async def health(self, request):
        return web.json_response({"status": "ready", "auth": "required"})

    async def summary(self, request):
        if not self._allowed(request):
            raise web.HTTPUnauthorized()
        day = datetime.now().strftime("%Y-%m-%d")
        return web.json_response({
            "persona": self.store.persona_state(),
            "counts": self.store.counts(),
            "activities": self.store.recent_activities(40),
            "budget": self.store.budget_usage(day, "autonomy"),
            "autonomy_paused": bool(self.store.get_setting("autonomy_paused", True)),
        })

    async def autonomy(self, request):
        if not self._allowed(request):
            raise web.HTTPUnauthorized()
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            raise web.HTTPBadRequest() from None
        if type(body.get("paused")) is not bool:
            raise web.HTTPBadRequest()
        self.store.set_setting("autonomy_paused", body["paused"])
        return web.json_response({"ok": True, "paused": body["paused"]})

    async def start(self):
        app = web.Application(client_max_size=64 * 1024)
        app.router.add_get("/", self.index)
        app.router.add_get("/health", self.health)
        app.router.add_get("/api/summary", self.summary)
        app.router.add_post("/api/autonomy", self.autonomy)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, self.host, self.port).start()

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
