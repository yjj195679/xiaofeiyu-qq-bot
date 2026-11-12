"""Bounded 24-hour conversation context, isolated by conversation and person."""
import json
import time

MAX_TURNS=30
MAX_CHARS=24000
TTL_SECONDS=86400
MAX_SESSIONS=256


def trim(messages):
    pairs=[]
    for i in range(0,len(messages)-1,2):
        u,a=messages[i:i+2]
        if u.get('role')=='user' and a.get('role')=='assistant' and isinstance(u.get('content'),str) and isinstance(a.get('content'),str):
            pairs.append([{'role':'user','content':u['content']},{'role':'assistant','content':a['content']}])
    pairs=pairs[-MAX_TURNS:]
    size=sum(len(m['content']) for pair in pairs for m in pair)
    while pairs and size>MAX_CHARS:
        size-=sum(len(m['content']) for m in pairs.pop(0))
    return [m for pair in pairs for m in pair]


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS context_history(conversation TEXT,user TEXT,updated REAL,messages TEXT,PRIMARY KEY(conversation,user))')


def load(bot,key):
    now=time.time()
    with bot.db() as db:
        schema(db)
        db.execute('DELETE FROM context_history WHERE updated<?',(now-TTL_SECONDS,))
        row=db.execute('SELECT messages FROM context_history WHERE conversation=? AND user=?',key).fetchone()
    if row is None:return []
    try:
        messages=json.loads(row[0])
        return trim(messages) if isinstance(messages,list) else []
    except (ValueError,TypeError,AttributeError):
        return []


def save(bot,key,messages):
    messages=trim(messages)
    with bot.db() as db:
        schema(db)
        db.execute('INSERT INTO context_history VALUES(?,?,?,?) ON CONFLICT(conversation,user) DO UPDATE SET updated=excluded.updated,messages=excluded.messages',(*key,time.time(),json.dumps(messages,ensure_ascii=False)))
        db.execute('DELETE FROM context_history WHERE updated<?',(time.time()-TTL_SECONDS,))
        db.execute('DELETE FROM context_history WHERE rowid NOT IN (SELECT rowid FROM context_history ORDER BY updated DESC,rowid DESC LIMIT ?)',(MAX_SESSIONS,))
    with bot.mutex:
        bot.sessions[key]=(time.monotonic(),messages)
        bot.sessions.move_to_end(key)
        while len(bot.sessions)>MAX_SESSIONS:bot.sessions.popitem(last=False)


def clear(bot,key):
    with bot.db() as db:
        schema(db)
        db.execute('DELETE FROM context_history WHERE conversation=? AND user=?',key)
    with bot.mutex:bot.sessions.pop(key,None)
