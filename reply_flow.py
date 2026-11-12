"""Paced QQ replies with paragraph-aware splitting and intact code fences."""
import fish_flow
import math
import random
import re
import time


def split_reply(text, target=180, max_parts=6):
    if not text:
        return []
    lines=text.splitlines(keepends=True)
    if 2<=len(lines)<=3 and all(0<len(x.strip())<=60 for x in lines) and len(text)<=180 and not any(x in text for x in ('```','$$',r'\[')) and not any(re.match(r'^\s*(?:[-*#]|\d+[.、)])',x) for x in lines):
        return lines
    target=max(target,math.ceil(len(text)/max_parts))
    # Treat fenced code blocks as indivisible units, including an unclosed fence.
    units=re.split(r'(```[^\n]*\n[\s\S]*?(?:```|\Z)|\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\])',text)
    parts=[]; current=''
    for unit in units:
        if not unit: continue
        if unit.startswith(('```','$$',r'\[')):
            if current: parts.append(current);current=''
            parts.append(unit)
            continue
        while unit:
            room=target-len(current)
            if room<=0:
                parts.append(current);current='';continue
            if len(unit)<=room:
                current+=unit;break
            region=unit[:room]
            breaks=[m.end() for m in re.finditer(r'\n+|[。！？!?；;](?:[”’」』])?|(?<=[.!?])\s+',region)]
            good=[x for x in breaks if len(current)+x>=target//2]
            cut=good[-1] if good else room
            current+=unit[:cut];unit=unit[cut:]
            parts.append(current);current=''
    if current:parts.append(current)
    # Bound the number of QQ notifications without dropping any text.
    if len(parts)>max_parts:
        parts=parts[:max_parts-1]+[''.join(parts[max_parts-1:])]
    return parts


def send_reply(bot,group,user,content,extra_segments,ambient=False):
    parts=split_reply(content) or ([''] if extra_segments else [])
    for index,part in enumerate(parts):
        time.sleep(random.uniform(2,4) if index==0 else random.uniform(1.5,3))
        # Permissions and credentials may change while a reply is waiting.
        config=bot.config()
        private=group.startswith('private:')
        if private:
            if user not in set(map(str,config.get('allowed_private_users',[]))):return False
            endpoint='send_private_msg';payload={'user_id':int(user),'message':[]}
        else:
            if group not in set(map(str,config.get('allowed_groups',[]))):return False
            endpoint='send_group_msg';payload={'group_id':int(group),'message':[]}
            if index==0 and not ambient:
                payload['message'].append({'type':'at','data':{'qq':user}})
        if part:
            payload['message'].append({'type':'text','data':{'text':(' ' if not private and index==0 and not ambient else '')+part}})
        if index==len(parts)-1:
            payload['message'].extend(extra_segments)
        with bot.mutex:
            bot.sent_texts[(group,part.strip())]=time.monotonic()
        result=bot.request('http://127.0.0.1:3000/'+endpoint,config['onebot_token'],payload,15)
        if result.get('retcode')!=0:
            return False  # Never resend ambiguous failures or continue a broken sequence.
        fish_flow.delivered(bot,group,user,result,ambient,index==0,part)
    return True
