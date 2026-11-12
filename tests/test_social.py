import json,tempfile,unittest
from pathlib import Path
from bot import Bot
import social

class SocialTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
  (self.root/'config.json').write_text(json.dumps({'owner_qq':'1000000002','qq':'1000000001','allowed_private_users':['1000000002'],'onebot_token':'test','api_key':'test'}))
  self.b=Bot(self.root); self.calls=[]
  self.b.request=lambda u,t,p,n:(self.calls.append((u,p)) or {'retcode':0,'status':'ok','data':{'tid':'post1','result':0}})
 def tearDown(self): self.b.pool.shutdown(); self.temp.cleanup()
 def runcommand(self,text): return social.handle(self.b,'private:1000000002','1000000002',text)
 def test_owner_private_only(self):
  for g,u in [('1000000003','1000000002'),('private:200','200'),('private:1000000001','1000000001')]:
   self.assertIn('仅限',social.handle(self.b,g,u,'/签名 内容'))
  self.assertFalse(self.calls)
 def test_signature_and_duplicate(self):
  self.assertIn('已提交',self.runcommand('/签名 今天也要加油'))
  self.assertEqual(self.calls[0][1],{'longNick':'今天也要加油'})
  self.assertIn('相同内容',self.runcommand('/签名 今天也要加油')); self.assertEqual(len(self.calls),1)
 def test_post_visibility_cooldown(self):
  self.assertIn('已发布',self.runcommand('/说说 小肥鱼上线'))
  self.assertEqual(self.calls[0][1],{'content':'小肥鱼上线','ugc_right':1})
  self.assertIn('60秒',self.runcommand('/空间 换一条'))
 def test_image_catalog_and_no_arbitrary_path(self):
  self.assertIn('用法',self.runcommand('/图文 ../../etc/passwd 文案')); self.assertFalse(self.calls)
  d=self.root/'memes';d.mkdir();(d/'cheer.png').write_bytes(b'png');(d/'catalog.json').write_text('{"cheer":{"file":"cheer.png"}}')
  self.assertIn('已发布',self.runcommand('/图文 cheer 加油呀'))
  self.assertEqual(self.calls[0][1]['images'],['file://'+str(d/'cheer.png')])
 def test_draft_never_publishes_or_uses_history(self):
  seen=[];self.b.complete=lambda m,c:(seen.append((m,c)) or '草稿文字')
  self.assertIn('尚未发布',self.runcommand('/说说草稿 复习'))
  self.assertFalse(self.calls);self.assertEqual(len(seen[0][0]),1);self.assertFalse(seen[0][1]['web_search_enabled'])
 def test_missing_tid_and_failure_not_success(self):
  self.b.request=lambda *a:{'retcode':0,'data':None}
  self.assertIn('无法确认',self.runcommand('/说说 测试'))
 def test_own_list_uses_supported_pagination(self):
  self.b.request=lambda u,t,p,n:(self.calls.append((u,p)) or {'retcode':0,'data':{'msglist':[]}})
  self.assertIn('暂时没有',self.runcommand('/近期说说'))
  self.assertEqual(self.calls[0][1],{'format':'native','target_uin':1000000001})
 def test_menu_and_plain_chat(self):
  self.assertIn('/图文',self.runcommand('/帮助'))
  self.assertIsNone(self.runcommand('你好'))
  self.assertIsNone(social.command('/说说草稿伪造'))
 def test_timeout_does_not_retry(self):
  def fail(*a): self.calls.append(a); raise TimeoutError()
  self.b.request=fail
  self.assertIn('未能确认',self.runcommand('/说说 超时测试'))
  self.assertIn('相同内容',self.runcommand('/说说 超时测试')); self.assertEqual(len(self.calls),1)

if __name__=='__main__': unittest.main()
