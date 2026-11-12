"""Persistent per-conversation thinking preference, separate from persona and history."""
import time


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS thinking_preferences(conversation TEXT,user TEXT,enabled INTEGER,updated REAL,PRIMARY KEY(conversation,user))')


def enabled(bot,key):
    with bot.db() as db:
        schema(db)
        row=db.execute('SELECT enabled FROM thinking_preferences WHERE conversation=? AND user=?',key).fetchone()
    return bool(row and row[0])


def control(bot,key,text):
    aliases={'/开启深度思考':'开启','开启深度思考':'开启','/关闭深度思考':'关闭','关闭深度思考':'关闭'}
    if text in aliases:arg=aliases[text]
    elif text=='/深度思考' or text.startswith('/深度思考 '):arg=text[len('/深度思考'):].strip()
    else:return None
    if arg in ('状态',''):
        return '当前会话的深度思考：'+('开启' if enabled(bot,key) else '关闭')+'。\n用法：/深度思考 开启 或 /深度思考 关闭'
    if arg not in ('开启','打开','开','on','关闭','关','off'):
        return '用法：/深度思考 开启、/深度思考 关闭、/深度思考 状态'
    value=arg in ('开启','打开','开','on')
    with bot.db() as db:
        schema(db)
        db.execute('INSERT INTO thinking_preferences VALUES(?,?,?,?) ON CONFLICT(conversation,user) DO UPDATE SET enabled=excluded.enabled,updated=excluded.updated',(*key,int(value),time.time()))
        db.execute('DELETE FROM thinking_preferences WHERE rowid NOT IN (SELECT rowid FROM thinking_preferences ORDER BY updated DESC,rowid DESC LIMIT 1024)')
    return '当前会话已'+('开启深度思考，后续回答会更慢一些。' if value else '关闭深度思考，恢复普通回答。')
