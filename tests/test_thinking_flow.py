import copy,tempfile,unittest
from pathlib import Path
from bot import Bot
import thinking_flow as thinking

class ThinkingTests(unittest.TestCase):
 def test_toggle_persistence_and_isolation(self):
  with tempfile.TemporaryDirectory() as t:
   b=Bot(Path(t));k=('private:200','200')
   self.assertFalse(thinking.enabled(b,k));self.assertIn('开启',thinking.control(b,k,'/深度思考 开启'))
   self.assertTrue(thinking.enabled(b,k));self.assertFalse(thinking.enabled(b,('100','200')))
   self.assertFalse(thinking.enabled(b,('private:201','201')));b.pool.shutdown()
   b=Bot(Path(t));self.assertTrue(thinking.enabled(b,k));thinking.control(b,k,'/深度思考 关闭');self.assertFalse(thinking.enabled(b,k));b.pool.shutdown()
 def test_no_web_real_thinking_parameters_and_no_reasoning_leak(self):
  with tempfile.TemporaryDirectory() as t:
   b=Bot(Path(t));calls=[]
   b.request=lambda u,t,p,n:(calls.append(copy.deepcopy(p)) or {'choices':[{'message':{'content':'最终答案','reasoning_content':'private-reasoning'}}]})
   out=b.complete([{'role':'user','content':'计算'}],{'api_key':'test','_thinking_enabled':True})
   self.assertEqual(out,'最终答案');self.assertEqual(calls[0]['thinking'],{'type':'enabled'});self.assertEqual(calls[0]['max_tokens'],8192);b.pool.shutdown()
 def test_thinking_and_search_router(self):
  with tempfile.TemporaryDirectory() as t:
   b=Bot(Path(t));calls=[]
   def fake(url,token,payload,timeout):
    calls.append((url,copy.deepcopy(payload)))
    if 'tools/search' in url:return {'results':[]}
    if len(calls)==1:return {'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'t','type':'function','function':{'name':'web_search','arguments':'{"query":"test"}'}}]}}]}
    return {'choices':[{'message':{'content':'答案','reasoning_content':'private'}}]}
   b.request=fake
   self.assertEqual(b.complete([{'role':'assistant','content':'早先普通对话'},{'role':'user','content':'查最新'}],{'api_key':'test','_thinking_enabled':True,'web_search_enabled':True}),'答案')
   self.assertEqual(calls[0][1]['thinking'],{'type':'disabled'})
   self.assertEqual(calls[-1][1]['thinking'],{'type':'enabled'});self.assertNotIn('tools',calls[-1][1]);self.assertNotIn('tool_choice',calls[-1][1]);b.pool.shutdown()
 def test_router_without_search_still_reasons(self):
  with tempfile.TemporaryDirectory() as t:
   b=Bot(Path(t));calls=[]
   b.request=lambda u,t,p,n:(calls.append(copy.deepcopy(p)) or {'choices':[{'message':{'content':'答案'}}]})
   b.complete([{'role':'user','content':'你好'}],{'api_key':'test','_thinking_enabled':True,'web_search_enabled':True})
   self.assertEqual(len(calls),2);self.assertEqual(calls[-1]['thinking'],{'type':'enabled'});self.assertNotIn('tools',calls[-1]);b.pool.shutdown()

if __name__=='__main__':unittest.main()
