#!/usr/bin/env python3
"""Interactive local configuration; secrets stay on the server."""
import argparse,getpass,json,os,re,secrets,tempfile
from pathlib import Path


def number(text):
    if not re.fullmatch(r'[1-9][0-9]{4,15}',text): raise ValueError('账号格式错误')
    return text

def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.config-')
    with os.fdopen(fd,'w') as f:json.dump(data,f,ensure_ascii=False,indent=2)
    os.chmod(name,0o600);os.replace(name,path)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path('/opt/qq-deepseek'))
    parser.add_argument('--napcat-config-dir',type=Path,help='可选：已确认的 NapCat config 目录')
    args=parser.parse_args();p=args.root/'config.json'
    c=json.loads(p.read_text()) if p.exists() else {}
    c['qq']=number(input('机器人QQ：').strip())
    c['owner_qq']=number(input('主人QQ（接收提醒、管理签名和空间）：').strip())
    raw=input('允许回复的群号，逗号分隔；留空只用私聊：').strip()
    c['allowed_groups']=[number(x.strip()) for x in raw.split(',') if x.strip()]
    c['allowed_private_users']=[c['owner_qq']]
    c['allow_self_commands']=input('启用机器人自身私聊 /ai 命令？[y/N]：').strip().lower()=='y'
    if c['allow_self_commands']:c['allowed_private_users'].append(c['qq'])
    key=getpass.getpass('DeepSeek API Key（隐藏输入，留空保留已有值）：').strip()
    if key:c['api_key']=key
    if not c.get('api_key'):raise ValueError('API Key 为空，未保存')
    c.setdefault('onebot_token',secrets.token_hex(32))
    if not c['onebot_token']:c['onebot_token']=secrets.token_hex(32)
    c.setdefault('model','deepseek-flash');c.setdefault('web_search_enabled',True)
    c.setdefault('daily_group_limit',100)
    # Preserve optional ambient settings for existing allowed groups only.
    c['ambient_groups']={k:v for k,v in c.get('ambient_groups',{}).items() if k in c['allowed_groups']}
    atomic(p,c)
    network={'httpServers':[{'name':'xiaofeiyu-api','enable':True,'host':'127.0.0.1','port':3000,'enableCors':False,'enableWebsocket':False,'messagePostFormat':'array','token':c['onebot_token'],'debug':False}],
             'httpClients':[{'name':'xiaofeiyu-events','enable':True,'url':'http://127.0.0.1:8765/event','messagePostFormat':'array','reportSelfMessage':c['allow_self_commands'],'token':c['onebot_token'],'debug':False}],
             'httpSseServers':[],'websocketServers':[],'websocketClients':[]}
    gateway={'network':network,'musicSignUrl':'','enableLocalFile2Url':False,'parseMultMsg':False}
    # Contains a generated token: keep private, never commit this file.
    atomic(args.root/('onebot11_'+c['qq']+'.json'),gateway)
    if args.napcat_config_dir:
        out=args.napcat_config_dir/('onebot11_'+c['qq']+'.json')
        if out.exists():print('现有NapCat配置未覆盖，请按文档合并；私密模板已在机器人目录生成。')
        else:atomic(out,gateway)
    print('已保存私密配置及OneBot模板；没有输出密钥。按部署文档配置NapCat并重启服务。')

if __name__=='__main__':main()
