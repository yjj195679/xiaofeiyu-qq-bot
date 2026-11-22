"""Small compatibility bridge; QQ writes only follow explicit user commands."""
import asyncio
import base64
import aiohttp
import importlib.util
import json
import sys
import time
import re
from pathlib import Path
from aiohttp import web

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Poke, Record
from astrbot.api.provider import ProviderType
from astrbot.api.star import Context, Star, register
from .calculator import calculate

LEGACY = Path('/opt/qq-deepseek')
MENU = '''小肥鱼功能菜单 🐳
聊天：私聊直接说，群里@或叫“小肥鱼”
/天气 洛阳：实况、三天预报与温度图
/记忆 帮助：添加、查看、搜索、删除自己的长期记忆
/计算 sqrt(2)^2：计算明确的算式
/表情：原有小肥鱼表情；/表情 happy 可直接发
/深度思考 开启｜关闭｜状态：仅当前会话
/语音 你好呀：将文字发送为中文女声语音
/点歌 歌名：先发候选，选中后同时发原文件与语音\n/音质 标准｜高｜无损：设置当前会话的音乐音质
/酷狗登录、/酷狗登录状态：仅原管理员私聊使用
新增：群聊上下文、群聊状态、情感互动、引用和撤回；群管理仅原管理员可用
/清空：清空当前对话上下文
/余额：仅原授权账号私聊查询
/状态：框架和功能状态
原来的签名、说说、图文、草稿命令继续支持，权限沿用原配置。
长期记忆按用户和会话隔离，仅在你明确要求时保存。'''


@register('xiaofeiyu_compat', 'xiaofeiyu', '小肥鱼迁移兼容：原有功能、思考开关和菜单', '1.0.0')
class XiaofeiyuCompat(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.legacy = None
        self.pokes = {}
        self.health_runner = None

    async def initialize(self):
        async def health(request):
            adapter = self.context.get_platform_inst('xiaofeiyu')
            bot = getattr(adapter, 'bot', None)
            clients = getattr(bot, '_wsr_api_clients', {}) or {}
            return web.json_response({
                'status': 'ready', 'persona': '小肥鱼 v3',
                'plugins': [{'name': s.name, 'version': s.version,
                             'active': bool(s.activated and s.star_cls)} for s in self.context.get_all_stars()],
                'providers': [p.meta().id for p in self.context.get_all_providers()],
                'qq_connected': [str(x) for x in clients],
                'voice_ready': self.context.get_provider_by_id('xiaofeiyu-voice') is not None,
            })
        app = web.Application()
        app.router.add_get('/health', health)
        self.health_runner = web.AppRunner(app, access_log=None)
        await self.health_runner.setup()
        await web.TCPSite(self.health_runner, '127.0.0.1', 8766).start()

    def old(self):
        if self.legacy is None:
            if str(LEGACY) not in sys.path:
                sys.path.insert(0, str(LEGACY))
            spec = importlib.util.spec_from_file_location('xiaofeiyu_legacy_runtime', LEGACY / 'bot.py')
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            persona = json.loads(Path('/opt/astrbot/migration/persona.json').read_text())
            module.PERSONA_TEXT = persona['system_prompt']
            module.PERSONA_EXAMPLES = ()
            self.legacy = module.Bot(LEGACY)
        return self.legacy

    async def make_voice(self, text: str) -> str:
        text = text.strip()
        if not 1 <= len(text) <= 300:
            raise ValueError('text length')
        provider = self.context.get_provider_by_id('xiaofeiyu-voice')
        if provider is None:
            raise RuntimeError('voice unavailable')
        return await asyncio.wait_for(provider.get_audio(text), timeout=45)

    @filter.llm_tool(name='xiaofeiyu_speak')
    async def tool_speak(self, event: AstrMessageEvent, text: str) -> str:
        '''仅在用户明确要求语音回复、念出来或发送语音时使用中文女声，在当前会话发送语音。

        Args:
            text(string): 需要朗读的文本，1至300字；不要朗读密码、密钥或验证码。
        '''
        if not re.search(r'语音|念[一出给]|读[一出给]|朗读|说给我听|voice|speak', event.message_str, re.I):
            return '用户没有明确要求语音，请使用文字回复。'
        if re.search(r'密码|验证码|密钥|api.?key|token|secret', text, re.I):
            return '请不要将密码、密钥或验证码转为语音。'
        try:
            path = await self.make_voice(text)
            await event.send(event.chain_result([Record.fromFileSystem(path)]))
            return '语音已经发送到当前会话。无需重复发送同样内容。'
        except Exception:
            return '语音生成或发送失败，请如实说明并使用文字回复。'

    @filter.regex(r'^/?酷狗(?:登录|登录状态)(?:\s*)$')
    async def kugou_login(self, event: AstrMessageEvent):
        if event.get_platform_id() != 'xiaofeiyu':
            return
        event.stop_event()
        if not event.is_private_chat() or not event.is_admin():
            yield event.plain_result('酷狗登录仅限原管理员私聊使用。')
            return
        status_only = '状态' in event.message_str
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=24)) as session:
                method = session.get if status_only else session.post
                path = '/auth/status' if status_only else '/auth/start'
                async with method('http://127.0.0.1:3072' + path) as response:
                    data = await response.json()
            if data.get('qr_image'):
                png = base64.b64decode(data['qr_image'].partition(',')[2], validate=True)
                yield event.chain_result([Image.fromBytes(png)])
                yield event.plain_result('请用酷狗概念版扫码，并在手机上确认登录。二维码约几分钟有效。登录会话将保存在你的阿里云服务器，仅供小肥鱼点歌；不要发送密码或验证码。完成后用 /酷狗登录状态 查看。')
            else:
                labels = {'authenticated':'酷狗已登录，可以尝试 /点歌 歌名。完整播放仍取决于歌曲权限和接口实际返回。', 'waiting_for_scan':'等待扫码。', 'confirm_on_phone':'已扫码，请在手机上确认。', 'expired':'二维码已过期，请重新发送 /酷狗登录。', 'not_authenticated':'尚未登录，请发送 /酷狗登录。', 'failed':'酷狗登录接口未成功返回，请稍后手动重试。'}
                yield event.plain_result(labels.get(data.get('status'), '酷狗登录服务暂时不可用。'))
        except Exception:
            yield event.plain_result('酷狗登录服务暂时不可用，请稍后重试。')

    @filter.llm_tool(name='xiaofeiyu_calculate')
    async def tool_calculate(self, event: AstrMessageEvent, expression: str) -> str:
        '''计算明确的数值算式，三角函数使用弧度。

        Args:
            expression(string): 使用数字、括号及数学函数的算式，例如 sqrt(2)^2。
        '''
        try:
            return calculate(expression)
        except (ValueError, ZeroDivisionError, OverflowError):
            return '算式无效或超出计算范围。不要编造计算结果。'

    @filter.llm_tool(name='xiaofeiyu_weather')
    async def tool_weather(self, event: AstrMessageEvent, city: str) -> str:
        '''查询用户指定城市的当前天气和三天预报。必须先明确城市。

        Args:
            city(string): 用户明确指定的城市或区县名称，例如洛阳。不要传入地址、个人信息或整段聊天。
        '''
        if not isinstance(city, str) or not 2 <= len(city.strip()) <= 40 or any(x in city for x in ('http', '/', '\\', '\n', '@')):
            return '请先明确城市名称。'
        def query():
            module = next((m for n, m in sys.modules.items() if n.endswith('.astrbot_plugin_tq.main')), None)
            if module is None:
                return '天气插件尚未就绪。'
            geo = module._geo(city.strip())
            if not geo:
                return '找不到这个城市，请核对名称。'
            current, daily, hourly = module._fetch_all(geo['latitude'], geo['longitude'])
            if not current and not daily:
                return '天气服务没有返回可用数据，请稍后再试。'
            return module._build_report(module._geo_fmt(geo), current, daily, hourly) + '\n来源：Open-Meteo。数值为气象模型预报，不是官方灾害预警。'
        try:
            return await asyncio.to_thread(query)
        except Exception as exc:
            logger.warning('Weather query failed (%s)', type(exc).__name__)
            return '天气服务暂时不可用，请勿编造天气。'

    @filter.regex(r'^/?(?:语音|帮助|功能|help|清空|状态|余额|balance|查询余额|查看余额|看看余额|余额多少|还有多少钱|表情|计算|深度思考|签名草稿|说说草稿|近期说说|签名|说说|空间|图文)(?:\s[\s\S]*)?$')
    async def commands(self, event: AstrMessageEvent):
        if event.get_platform_id() != 'xiaofeiyu':
            return
        prompt = event.message_str.strip()
        # AstrBot's waking stage has already removed the / wake prefix.
        prompt = '/' + prompt.lstrip('/')
        parts = prompt.split(None, 1)
        name, arg = parts[0], parts[1].strip() if len(parts) > 1 else ''
        user = str(event.get_sender_id())
        group = str(event.get_group_id() or '') or 'private:' + user
        answer = None
        if name in ('/查询余额', '/查看余额', '/看看余额', '/余额多少', '/还有多少钱'):
            name = '/余额'
        if name == '/语音':
            event.stop_event()
            try:
                path = await self.make_voice(arg)
                yield event.chain_result([Record.fromFileSystem(path)])
            except Exception:
                yield event.plain_result('语音生成失败，请稍后重试。一次请发送1至300字。')
            return
        elif name in ('/帮助', '/功能', '/help'):
            answer = MENU
        elif name == '/计算':
            try:
                answer = '算得：' + calculate(arg)
            except (ValueError, ZeroDivisionError, OverflowError):
                answer = '这个算式算不了。支持 + - * / ^、括号，以及 sqrt、sin、cos、log、ln、factorial；三角函数用弧度。例如 /计算 sqrt(2)^2'
        elif name == '/状态':
            provider = await self.context.get_using_provider_async(event.unified_msg_origin)
            answer = '小肥鱼在线 🐳\n框架：AstrBot 4.29.4 + NapCat\n人设：小肥鱼 v3\n模型：' + (provider.meta().id if provider else '未就绪') + '\n功能：联网检索、天气、长期记忆、表情、计算、戳一戳、语音、点歌、群聊上下文、情感互动'
        elif name == '/清空':
            cm = self.context.conversation_manager
            cid = await cm.get_curr_conversation_id(event.unified_msg_origin)
            if cid:
                await cm.update_conversation(event.unified_msg_origin, cid, history=[])
            answer = '这段对话的上下文清空了。明确保存的长期记忆仍可用 /记忆 管理。'
        elif name == '/深度思考':
            if arg in ('开启', '开', 'on', '关闭', '关', 'off'):
                enabled = arg in ('开启', '开', 'on')
                await self.context.provider_manager.set_provider('deepseek-thinking' if enabled else 'deepseek-chat', ProviderType.CHAT_COMPLETION, event.unified_msg_origin)
                answer = '当前会话的深度思考已' + ('开启。' if enabled else '关闭。')
            else:
                p = await self.context.get_using_provider_async(event.unified_msg_origin)
                answer = '当前会话深度思考：' + ('开启' if p and p.meta().id == 'deepseek-thinking' else '关闭')
        elif name == '/表情':
            catalog = self.old().meme_catalog()
            if arg and arg in catalog:
                event.stop_event()
                yield event.chain_result([Image.fromFileSystem(catalog[arg]['path'])])
                return
            answer = '小肥鱼的表情：\n' + '\n'.join(k + '：' + v['label'] for k, v in catalog.items()) + '\n例如 /表情 cheer；也可以直接让我发个加油表情。'
        elif name in ('/余额', '/balance'):
            answer = await asyncio.to_thread(self.old().balance_reply, group, user, self.old().config())
        else:
            self.old()
            import social
            answer = await asyncio.to_thread(social.handle, self.old(), group, user, prompt)
        if answer is not None:
            event.stop_event()
            yield event.plain_result(answer)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def poke(self, event: AstrMessageEvent):
        if event.get_platform_id() != 'xiaofeiyu':
            return
        if not any(isinstance(x, Poke) and str(x.id) == str(event.get_self_id()) for x in event.get_messages()):
            return
        if str(event.get_sender_id()) == str(event.get_self_id()):
            return
        key = (str(event.unified_msg_origin), str(event.get_sender_id()))
        now = time.monotonic()
        if now - self.pokes.get(key, -100) < 30:
            return
        self.pokes[key] = now
        self.pokes = {k: v for k, v in self.pokes.items() if now - v < 300}
        event.stop_event()
        yield event.plain_result('探头。找小肥鱼什么事呀？')

    async def terminate(self):
        if self.health_runner is not None:
            await self.health_runner.cleanup()
        if self.legacy is not None:
            self.legacy.pool.shutdown(wait=False)
