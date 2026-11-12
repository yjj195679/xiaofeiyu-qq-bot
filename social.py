"""Owner-controlled profile and Qzone commands; no model-selected write actions."""
import thinking_flow
import hashlib
import json
import time
from datetime import datetime, timezone, timedelta

HELP='''小肥鱼功能菜单 🐳
聊天：私聊直接问，群内@小肥鱼
联网：按需搜索，回复不附长来源
/余额：查看DeepSeek余额（仅授权私聊）
/清空：清空当前聊天记忆
上下文：最多30轮、24小时有效，支持重启续聊
回复：发送前等2至4秒，长回复分条发送
/深度思考 开启｜关闭｜状态：当前会话独立开关
/表情：查看可用表情包
戳一戳：双击头像，30秒冷却
以下功能仅配置的主人账号私聊可用：
/签名 内容：修改个性签名
/签名草稿：生成签名建议
/说说 内容：发布文字说说，所有人可见
/空间 内容：同上
/图文 表情键 内容：带现有表情包发布
/说说草稿 主题：生成文案，不发布
/近期说说：查看自己的最近5条
/状态：查看机器人功能状态
已有低余额提醒及低频私聊问候。'''
COMMANDS=('/签名草稿','/说说草稿','/近期说说','/签名','/说说','/空间','/图文')

def command(prompt):
    if prompt in ('/帮助','/功能','/help','/表情','/状态'): return prompt,''
    for name in COMMANDS:
        if prompt==name or prompt.startswith(name+' ') or prompt.startswith(name+'\n'):
            return name,prompt[len(name):].strip()
    return None

def checked(result):
    if result.get('retcode')!=0 or result.get('status','ok')!='ok':
        raise RuntimeError('QQ接口拒绝了请求')
    return result.get('data')

def handle(bot,group,user,prompt):
    mode_reply=thinking_flow.control(bot,(group,user),prompt)
    if mode_reply is not None:return mode_reply
    parsed=command(prompt)
    if parsed is None: return None
    name,text=parsed
    if name in ('/帮助','/功能','/help'): return HELP
    if name=='/表情':
        return '可用表情包：\n'+'\n'.join(k+'：'+v['label'] for k,v in bot.meme_catalog().items())+'\n聊天时可以直接说“发个加油表情”。'
    if name=='/状态':
        c=bot.config()
        return '小肥鱼在线 🐳\n模型：'+str(c.get('model','deepseek-flash'))+'\n联网：'+('开启' if c.get('web_search_enabled') else '关闭')+'\n深度思考：'+('开启' if thinking_flow.enabled(bot,(group,user)) else '关闭')+'\n表情包：'+str(len(bot.meme_catalog()))+'张\n戳一戳：开启\n签名和空间：已通过实际写入验证，动态默认所有人可见'
    config=bot.config()
    owner=str(config.get('owner_qq') or '')
    if not owner or group!='private:'+owner or user!=owner:
        return '签名和空间操作仅限配置的主人账号私聊使用。'
    if user not in set(map(str,config.get('allowed_private_users',[]))): return '私聊权限已关闭。'
    if name in ('/签名草稿','/说说草稿'):
        topic=text[:200] or '小肥鱼的轻松日常'
        requirement='写3条个性签名，每条不超过30字。' if name=='/签名草稿' else '写一条适合发QQ空间的原创短文案，30到80字。'
        out=bot.complete([{'role':'user','content':requirement+'主题是：'+topic+'。沿用小肥鱼人设，不使用私人聊天内容，不虚构真实经历，不加来源链接。只输出草稿。'}],{**config,'web_search_enabled':False})
        return '草稿（尚未发布）：\n'+out[:800]
    def call(action,payload):
        return checked(bot.request('http://127.0.0.1:3000/'+action,config['onebot_token'],payload,30))
    if name=='/近期说说':
        try:
            data=call('get_qzone_msg_list',{'format':'native','target_uin':int(config['qq'])})
            rows=data if isinstance(data,list) else (data or {}).get('msglist',[])
            if not rows: return '空间里暂时没有说说，或接口未返回条目。'
            lines=[]
            for i,row in enumerate(rows[:5],1):
                body=str(row.get('content') or row.get('text') or '')[:180]
                tid=str(row.get('tid') or '')[:80]
                lines.append(str(i)+'. '+body+'\nID：'+tid)
            return '自己的近期说说：\n'+'\n'.join(lines)
        except Exception: return '暂时读不到自己的空间，请稍后再试。'
    payload={}; action='send_qzone_msg'; images=[]
    if name=='/签名':
        if not text or len(text)>80: return '用法：/签名 内容（1到80字）'
        payload={'longNick':text}; action='set_self_longnick'
    else:
        if name=='/图文':
            key,sep,text=text.partition(' ')
            item=bot.meme_catalog().get(key)
            if not sep or not item: return '用法：/图文 表情键 内容；表情键见 /表情。'
            images=['file://'+item['path']]
        if not text or len(text)>1500: return '用法：/说说 内容，或 /图文 表情键 内容（正文1到1500字）。'
        payload={'content':text,'ugc_right':1}
        if images: payload['images']=images
    # Persist before sending: timeout is ambiguous, so never retry automatically.
    digest=hashlib.sha256(json.dumps([action,payload],sort_keys=True).encode()).hexdigest()
    now=time.time()
    day=datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
    with bot.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS social_attempts(ts REAL,day TEXT,kind TEXT,digest TEXT)')
        db.execute('BEGIN IMMEDIATE')
        last=db.execute('SELECT MAX(ts) FROM social_attempts WHERE kind=?',(action,)).fetchone()[0]
        duplicate=db.execute('SELECT 1 FROM social_attempts WHERE digest=? AND ts>?',(digest,now-86400)).fetchone()
        count=db.execute('SELECT COUNT(*) FROM social_attempts WHERE day=? AND kind=?',(day,action)).fetchone()[0]
        if duplicate: return '相同内容24小时内已尝试提交，请先检查空间或签名，避免重复。'
        if last is not None and now-last<60: return '操作间隔至少60秒，稍等一下再试。'
        if count>=10: return '今天这类操作已达到10次，请明天再试。'
        db.execute('INSERT INTO social_attempts VALUES(?,?,?,?)',(now,day,action,digest))
        db.execute('DELETE FROM social_attempts WHERE ts<?',(now-172800,))
    try:
        data=call(action,payload)
        if action=='set_self_longnick':
            if isinstance(data,dict) and data.get('result',0)!=0: return 'QQ未确认签名修改成功，请检查资料页。'
            return '个性签名已提交：'+text
        if not isinstance(data,dict) or not data.get('tid'):
            return 'QQ已接收请求，但未返回说说ID，暂时无法确认发布结果，请检查空间；不会自动重发。'
        return '说说已发布到空间（所有人可见）。\nID：'+str(data['tid'])[:100]
    except Exception:
        return '本次操作未能确认成功，请先检查QQ签名或空间，再决定是否重试；不会自动重发。'
