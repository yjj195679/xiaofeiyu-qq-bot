import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from bot import Bot
import fish_flow,memory_flow,reply_flow

class FishFlowTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
  self.cfg={'qq':'300','allowed_groups':['100'],'onebot_token':'test','api_key':'test','daily_group_limit':100}
  (self.root/'config.json').write_text(json.dumps(self.cfg));self.b=Bot(self.root)
  self.event={'post_type':'message','message_type':'group','group_id':100,'user_id':200,'self_id':300,'message_id':20,'message':[{'type':'text','data':{'text':'那为什么呢？'}}]}
 def tearDown(self):self.b.pool.shutdown();self.temp.cleanup()
 def test_followup_same_user_window_and_other_recipient(self):
  with patch('fish_flow.time.monotonic',return_value=100):
   fish_flow.delivered(self.b,'100','200',{'data':{'message_id':'bot1'}},False,True)
   self.assertTrue(fish_flow.continuation(self.b,self.event))
   self.assertFalse(fish_flow.continuation(self.b,{**self.event,'user_id':201}))
   e={**self.event,'message':[{'type':'at','data':{'qq':'201'}},{'type':'text','data':{'text':'那为什么？'}}]}
   self.assertFalse(fish_flow.continuation(self.b,e))
   self.assertFalse(fish_flow.continuation(self.b,{**self.event,'message':[{'type':'text','data':{'text':'大家晚上吃什么'}}]}))
  with patch('fish_flow.time.monotonic',return_value=161):self.assertFalse(fish_flow.continuation(self.b,self.event))
 def test_quoting_bot_and_group_isolation(self):
  fish_flow.delivered(self.b,'100','200',{'data':{'message_id':'bot1'}},False,True,'本鱼的公开回复')
  e={**self.event,'user_id':201,'message':[{'type':'reply','data':{'id':'bot1'}},{'type':'text','data':{'text':'解释一下'}}]}
  self.assertTrue(fish_flow.continuation(self.b,e))
  self.assertEqual(fish_flow.quoted(self.b,e),'本鱼的公开回复')
  self.assertEqual(fish_flow.quoted(self.b,{**e,'group_id':999}),'')
  self.assertFalse(fish_flow.continuation(self.b,{**e,'group_id':999}))
  self.assertFalse(fish_flow.continuation(self.b,{**e,'message':[{'type':'reply','data':{'id':'other'}},{'type':'text','data':{'text':'那为什么？'}}]}))
 def test_followup_routes_without_ambient_and_allowlist_remains(self):
  fish_flow.delivered(self.b,'100','200',{'data':{'message_id':'bot1'}},False,True)
  self.b.answer=lambda *a:None
  self.assertTrue(self.b.enqueue(self.event))
  self.assertFalse(self.b.enqueue({**self.event,'group_id':999,'message_id':21}))
 def test_ambient_can_stay_silent_without_polluting_memory(self):
  self.b.complete=lambda *a:'[[silent]]'
  self.b.request=lambda *a:self.fail('must not send')
  self.b.slots.acquire();self.b.answer(('100','200'),'普通群聊',{**self.cfg,'_ambient':True,'_group_context':'一段已结束的闲聊'})
  self.assertEqual(memory_flow.load(self.b,('100','200')),[])
 def test_chat_bubbles_preserve_text_and_technical_blocks(self):
  text='在呢\n今天也辛苦啦'
  self.assertEqual(reply_flow.split_reply(text),['在呢\n','今天也辛苦啦'])
  self.assertEqual(''.join(reply_flow.split_reply('1. 先申请资源\n2. 再释放资源')),'1. 先申请资源\n2. 再释放资源')
  math='$$a+b=c$$';self.assertTrue(any(math in x for x in reply_flow.split_reply('结果：\n'+math)))

if __name__=='__main__':unittest.main()
