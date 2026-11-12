#!/usr/bin/env python3
"""Server-local balance monitor; no secrets or amounts in logs/state."""
import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path
from bot import Bot, ROOT

THRESHOLD=Decimal('1')

def low_balance(data):
    rows=[r for r in data.get('balance_infos',[]) if r.get('currency')=='CNY']
    if not rows:
        raise ValueError('CNY balance unavailable')
    amount=sum((Decimal(r['total_balance']) for r in rows),Decimal('0'))
    if not amount.is_finite():
        raise ValueError('invalid balance')
    return amount < THRESHOLD, amount

def run():
    statepath=ROOT/'balance-watch-state.json'
    state=json.loads(statepath.read_text()) if statepath.exists() else {}
    bot=Bot(ROOT)
    try:
        c=bot.config()
        recipient=int(c['owner_qq'])
        if str(recipient) not in set(map(str,c.get('allowed_private_users',[]))):
            raise ValueError('owner is not allowlisted')
        low,amount=low_balance(bot.get_balance(c))
        if low and not state.get('notified_low',False):
            text=f'鱼粮提醒：DeepSeek 可用余额只剩 {amount} 元，低于 1 元啦，记得及时充值 🐳'
            result=bot.request('http://127.0.0.1:3000/send_private_msg',c['onebot_token'],
                    {'user_id':recipient,'message':[{'type':'text','data':{'text':text}}]},20)
            if result.get('retcode') != 0:
                raise RuntimeError('QQ notification not delivered')
            print('Low-balance notification delivered')
        fd,tmp=tempfile.mkstemp(dir=ROOT,prefix='.balance-state-')
        with os.fdopen(fd,'w') as f:
            json.dump({'notified_low':low},f)
        os.replace(tmp,statepath)
        print('Balance check completed')
    finally:
        bot.pool.shutdown()

if __name__=='__main__':
    try:
        run()
    except Exception as exc:
        print('Balance check failed: '+type(exc).__name__)
        raise SystemExit(1)
