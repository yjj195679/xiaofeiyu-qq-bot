"""Small conversational cues for a QQ companion, without cross-user memory."""
import re
import time

FOLLOWUP_SECONDS=60


def continuation(bot,event):
    if event.get('message_type')!='group' or str(event.get('user_id'))==str(event.get('self_id')):
        return False
    segments=event.get('message')
    if not isinstance(segments,list):return False
    own=str(event.get('self_id',''));group=str(event.get('group_id',''));user=str(event.get('user_id',''))
    ats=[str(s.get('data',{}).get('qq')) for s in segments if isinstance(s,dict) and s.get('type')=='at']
    if any(q!=own for q in ats):return False
    replies=[str(s.get('data',{}).get('id')) for s in segments if isinstance(s,dict) and s.get('type')=='reply']
    now=time.monotonic()
    with bot.mutex:
        if replies:return any(now-bot.reply_ids.get((group,r),-1e12)<600 for r in replies)
        until=bot.followups.get((group,user),0)
    if now>=until:return False
    text=''.join(str(s.get('data',{}).get('text','')) for s in segments if isinstance(s,dict) and s.get('type')=='text').strip()
    return bool(text and (text.endswith(('?','？')) or re.match(r'^(那|所以|然后|这个|那个|它|你|能|可以|怎么|为什么|还有|对了|等下|不对|不是|嗯|是的|好|行|继续)',text)))


def delivered(bot,group,user,result,ambient,first,text=''):
    if group.startswith('private:'):return
    now=time.monotonic();data=result.get('data')
    mid=data.get('message_id') if isinstance(data,dict) else None
    with bot.mutex:
        bot.reply_ids={k:v for k,v in bot.reply_ids.items() if now-v<600}
        if mid is not None:
            bot.reply_ids[(group,str(mid))]=now
            bot.reply_texts[(group,str(mid))]=text[:1500]
        if first and not ambient:bot.followups[(group,user)]=now+FOLLOWUP_SECONDS
        bot.followups={k:v for k,v in bot.followups.items() if v>now}
        while len(bot.reply_ids)>4096:bot.reply_ids.pop(next(iter(bot.reply_ids)))
        while len(bot.followups)>4096:bot.followups.pop(next(iter(bot.followups)))
        bot.reply_texts={k:v for k,v in bot.reply_texts.items() if k in bot.reply_ids}


def quoted(bot,event):
    group=str(event.get('group_id',''))
    if event.get('message_type')!='group':return ''
    segments=event.get('message')
    if not isinstance(segments,list):return ''
    ids=[str(s.get('data',{}).get('id')) for s in segments if isinstance(s,dict) and s.get('type')=='reply']
    now=time.monotonic()
    with bot.mutex:
        return '\n'.join(bot.reply_texts.get((group,i),'') for i in ids if now-bot.reply_ids.get((group,i),-1e12)<600)[:2000]
