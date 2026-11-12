#!/usr/bin/env python3
"""Low-frequency greetings to the explicitly authorized private recipient."""
import json
import os
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from bot import Bot, ROOT


def allowed(state, now):
    local=datetime.fromtimestamp(now,timezone(timedelta(hours=8)))
    if not 9 <= local.hour < 22:
        return False
    if now-float(state.get('last_sent',0)) < 4*3600:
        return False
    return state.get('day') != local.date().isoformat() or int(state.get('count',0)) < 2

def run():
    p=ROOT/'private-greeting-state.json'
    state=json.loads(p.read_text()) if p.exists() else {}
    now=time.time()
    if not allowed(state,now):
        print('Greeting skipped by quiet hours or frequency limit')
        return
    b=Bot(ROOT)
    try:
        c=b.config()
        recipient=int(c['owner_qq'])
        if str(recipient) not in set(map(str,c.get('allowed_private_users',[]))):
            raise ValueError('recipient is not allowlisted')
        local=datetime.fromtimestamp(now,timezone(timedelta(hours=8)))
        c['_ambient']=True
        prompt=('现在是北京时间'+local.strftime('%H:%M')+'。请以小肥鱼固定人设主动向熟悉的用户私聊打个招呼。'
                '只写一到两句短句，随意自然，不要催用户回复，不要反复叫主人，不假装知道用户正在干什么，'
                '不编造双方经历，不问隐私，不提余额，不引用示例里的具体事件，不加来源或网址，不执行搜索。')
        text=b.complete([{'role':'user','content':prompt}],c)
        if not isinstance(text,str) or not text.strip():
            raise ValueError('empty greeting')
        result=b.request('http://127.0.0.1:3000/send_private_msg',c['onebot_token'],
               {'user_id':recipient,'message':[{'type':'text','data':{'text':text[:280]}}]},20)
        if result.get('retcode') != 0:
            raise RuntimeError('private greeting not delivered')
        day=local.date().isoformat()
        count=int(state.get('count',0))+1 if state.get('day')==day else 1
        fd,tmp=tempfile.mkstemp(dir=ROOT,prefix='.greeting-')
        with os.fdopen(fd,'w') as f:
            json.dump({'day':day,'count':count,'last_sent':now},f)
        os.replace(tmp,p)
        print('Private greeting delivered')
    finally:
        b.pool.shutdown()

if __name__=='__main__':
    try:
        run()
    except Exception as exc:
        print('Private greeting failed: '+type(exc).__name__)
        raise SystemExit(1)
