"""Loopback-only KuGou Concept -> Meting bridge. No privileged music access."""
import asyncio,html,json,pathlib,re,time,unicodedata
from aiohttp import web,ClientSession,ClientTimeout,CookieJar
from yarl import URL
BASE='http://127.0.0.1:3071'
def clean(x):return html.unescape(re.sub('<[^>]*>','',str(x or '')))[:200]
def normalize(song):
 h=song.get('FileHash') or song.get('hash') or song.get('Hash') or ''
 album=song.get('album') or {};album=album if isinstance(album,dict) else {}
 albumid=song.get('AlbumID') or song.get('album_id') or album.get('album_id') or 0
 aid=song.get('AlbumAudioID') or song.get('album_audio_id') or album.get('album_audio_id') or song.get('MixSongID') or song.get('mixsongid') or 0
 if not re.fullmatch('[a-fA-F0-9]{32}',str(h)):return None
 pic=song.get('Image') or song.get('img') or song.get('image') or song.get('trans_param',{}).get('union_cover') or ''
 return {'display_name':clean(song.get('FileName') or song.get('SongName') or song.get('songname')), 'duration':song.get('Duration') or song.get('duration') or 0, 'id':str(h),'name':clean(song.get('SongName') or song.get('songname') or song.get('song_name') or song.get('FileName') or song.get('filename')),'artist':clean(song.get('SingerName') or song.get('singername') or song.get('singer_name')),'album':clean(song.get('AlbumName') or album.get('album_name')),'pic':str(pic).replace('{size}','400'),'url':'http://127.0.0.1:3072/resolve?hash='+str(h),'source':'kugou','_xiaofeiyu_kugou':True,'album_id':str(albumid),'album_audio_id':str(aid)}
async def upstream(app,path,params):
 async with app['session'].get(BASE+path,params=params) as response:
  response.raise_for_status();return await response.json(content_type=None)
async def search(req):
 if req.query.get('type')!='search':return web.json_response({'error':'unsupported operation'},status=400)
 keyword=req.query.get('id','').strip()
 if not 1<=len(keyword)<=100:return web.json_response([],status=400)
 try:
  d=await upstream(req.app,'/search',{'keywords':keyword,'pagesize':5,'type':'song'})
  data=d.get('data',{});items=data.get('lists',data.get('info',[]))
  songs=[v for x in items if (v:=normalize(x))]
  return web.json_response(songs,headers={'Cache-Control':'no-store'})
 except Exception:return web.json_response({'error':'酷狗搜索暂时不可用'},status=502)
def title_key(value):
 return re.sub(r'[\W_]+','',unicodedata.normalize('NFKC',clean(value)).casefold())
def verify_identity(data, expected_name, expected_display, expected_duration):
 actual=data.get('fileName') or data.get('filename') or data.get('songName') or data.get('song_name') or ''
 valid_titles={title_key(expected_name),title_key(expected_display)}-{''}
 if not actual or not valid_titles or title_key(actual) not in valid_titles:
  return False,'音源歌名与所选候选不一致，已停止发送。'
 try:
  wanted=float(expected_duration or 0);actual_duration=float(data.get('timeLength') or data.get('duration') or 0)
 except (TypeError,ValueError):return False,'音源时长信息异常，已停止发送。'
 if wanted>0 and (actual_duration<=0 or abs(wanted-actual_duration)>3):
  return False,'音源时长与所选候选不一致，已停止发送。'
 return True,''
async def resolve(req):
 h=req.query.get('hash','')
 if not re.fullmatch('[a-fA-F0-9]{32}',h):return web.json_response({'error':'invalid hash'},status=400)
 params={'hash':h}
 for key in ('album_id','album_audio_id'):
  val=req.query.get(key,'0');params[key]=val if val.isdigit() else '0'
 try:
  authenticated=logged_in(req.app)
  if authenticated:await upstream(req.app,'/user/verify',{'timestamp':int(time.time()*1000)})
  endpoint='/song/url/auth/merge' if authenticated else '/song/url'
  for quality in (req.query.get('quality','320'),):
   if quality not in ('128','320','flac'):return web.json_response({'url':'','error':'请选择128、320或无损音质'},status=400)
   try:d=await upstream(req.app,endpoint,dict(params,quality=quality))
   except Exception:continue
   data=d.get('data',d)
   urls=data.get('url') or d.get('url') or []
   url=urls[0] if isinstance(urls,list) and urls else urls if isinstance(urls,str) else ''
   if not re.match(r'^https?://',url):continue
   verified,error=verify_identity(data,req.query.get('expected_name',''),req.query.get('expected_display',''),req.query.get('expected_duration','0'))
   if not verified:return web.json_response({'url':'','identity_verified':False,'error':error},status=409)
   return web.json_response({'url':url,'identity_verified':True,'actual_name':data.get('fileName') or data.get('filename'),'actual_duration':data.get('timeLength') or data.get('duration'),'quality_requested':quality,'source_bit_rate':data.get('bitRate'),'official_url':'https://www.kugou.com/song/#'+h},headers={'Cache-Control':'no-store'})
  return web.json_response({'url':'','error':'当前所选音质暂不可用，请换一种音质；账号和歌曲权限以平台返回为准。'},status=502)
 except Exception:return web.json_response({'url':'','error':'播放地址暂时不可用'},status=502)
SESSION_FILE=pathlib.Path('/var/lib/kugou-api/session.json')
def logged_in(app):
 jar=app['session'].cookie_jar.filter_cookies(URL(BASE))
 return bool(jar.get('token') and jar.get('userid'))
def save_session(app):
 jar=app['session'].cookie_jar.filter_cookies(URL(BASE))
 data={k:v.value for k,v in jar.items()}
 tmp=SESSION_FILE.with_suffix('.tmp');tmp.write_text(json.dumps(data));tmp.chmod(0o600);tmp.replace(SESSION_FILE)
async def poll_login(app,key):
 try:
  for _ in range(75):
   await asyncio.sleep(3)
   d=await upstream(app,'/login/qr/check',{'key':key,'timestamp':int(time.time()*1000)})
   state=int((d.get('data') or {}).get('status',0))
   if state==4:
    data=d.get('data') or {}
    if data.get('token') and data.get('userid'):
     app['session'].cookie_jar.update_cookies({k:str(data[k]) for k in ('token','userid')},URL(BASE))
    if logged_in(app):save_session(app);app['auth_state']='authenticated'
    else:app['auth_state']='failed'
    return
   if state==0:app['auth_state']='expired';return
   app['auth_state']='confirm_on_phone' if state==2 else 'waiting_for_scan'
  app['auth_state']='expired'
 except asyncio.CancelledError:raise
 except Exception:app['auth_state']='failed'
async def auth_start(req):
 app=req.app
 async with app['auth_lock']:
  if logged_in(app):return web.json_response({'status':'authenticated'},headers={'Cache-Control':'no-store'})
  if app.get('auth_task') and not app['auth_task'].done() and app.get('qr_image'):
   return web.json_response({'status':app['auth_state'],'qr_image':app['qr_image']},headers={'Cache-Control':'no-store'})
  try:
   d=await upstream(app,'/login/qr/key',{'timestamp':int(time.time()*1000)})
   data=d.get('data') or {};key=data.get('qrcode') or data.get('key')
   if not key:raise ValueError('QR key unavailable')
   d=await upstream(app,'/login/qr/create',{'key':key,'qrimg':'1','timestamp':int(time.time()*1000)})
   image=(d.get('data') or {}).get('base64','')
   if not image.startswith('data:image/png;base64,'):raise ValueError('QR image unavailable')
   app['qr_image']=image;app['auth_state']='waiting_for_scan';app['auth_task']=asyncio.create_task(poll_login(app,key))
   return web.json_response({'status':app['auth_state'],'qr_image':image},headers={'Cache-Control':'no-store'})
  except Exception:
   app['auth_state']='failed'
   return web.json_response({'status':'failed','error':'酷狗暂未提供可用的登录二维码'},status=502)
async def auth_status(req):
 return web.json_response({'status':'authenticated' if logged_in(req.app) else req.app.get('auth_state','not_authenticated')},headers={'Cache-Control':'no-store'})
async def health(req):
 return web.json_response({'status':'ready','upstream':'kugou-concept','authentication':'authenticated' if logged_in(req.app) else 'anonymous'})
async def context(app):
 app['auth_lock']=asyncio.Lock();app['auth_state']='not_authenticated';app['auth_task']=None
 app['session']=ClientSession(timeout=ClientTimeout(total=18),cookie_jar=CookieJar(unsafe=True))
 if SESSION_FILE.exists():
  try:app['session'].cookie_jar.update_cookies(json.loads(SESSION_FILE.read_text()),URL(BASE))
  except Exception:pass
 try:await upstream(app,'/register/dev',{})
 except Exception:pass
 yield
 if app.get('auth_task'):
  app['auth_task'].cancel()
  await asyncio.gather(app['auth_task'],return_exceptions=True)
 await app['session'].close()
app=web.Application();app.cleanup_ctx.append(context)
app.router.add_get('/api',search);app.router.add_get('/resolve',resolve);app.router.add_get('/health',health)
app.router.add_post('/auth/start',auth_start);app.router.add_get('/auth/status',auth_status)
if __name__=='__main__':web.run_app(app,host='127.0.0.1',port=3072,access_log=None,print=None)
