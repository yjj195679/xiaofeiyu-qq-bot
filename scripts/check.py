#!/usr/bin/env python3
"""Readiness check: never sends a chat message or prints credentials."""
import argparse,json,re,urllib.request
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('/opt/qq-deepseek'));a=p.parse_args()
c=json.loads((a.root/'config.json').read_text());errors=[]
for k in ('qq','owner_qq'):
    if not re.fullmatch(r'[1-9][0-9]{4,15}',str(c.get(k,''))):errors.append(k+'未正确配置')
for k in ('api_key','onebot_token'):
    if not c.get(k):errors.append(k+'为空')
if str(c.get('owner_qq')) not in set(map(str,c.get('allowed_private_users',[]))):errors.append('主人未加入私聊白名单')
print('配置检查：'+('通过' if not errors else '；'.join(errors)))
try:
    r=urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=5)
    print('消息服务health：',r.status)
except Exception as e:
    errors.append('health未通过');print('消息服务health未通过：',type(e).__name__)
try:
    req=urllib.request.Request('http://127.0.0.1:3000/get_login_info',data=b'{}',headers={'Content-Type':'application/json','Authorization':'Bearer '+c.get('onebot_token','')})
    data=json.load(urllib.request.urlopen(req,timeout=10));ok=data.get('retcode')==0 and str((data.get('data') or {}).get('user_id'))==str(c.get('qq'))
    print('QQ登录身份匹配：',ok)
    if not ok:errors.append('QQ身份不匹配')
except Exception as e:
    errors.append('QQ网关检查未通过');print('QQ网关检查未通过：',type(e).__name__)
raise SystemExit(1 if errors else 0)
