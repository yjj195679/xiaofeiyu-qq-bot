import unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
from bot import Bot,eligible
from reply_flow import split_reply,send_reply

class ReplyFlowTests(unittest.TestCase):
 def test_lossless_bounded_and_code(self):
  for text in ['短句','一句话。\n\n'*250,'a'*3500,'解释如下：\n```python\n'+('print(1)\n'*50)+'```\n结束。']:
   pieces=split_reply(text)
   self.assertEqual(''.join(pieces),text)
   self.assertLessEqual(len(pieces),6)
  math='$$'+'x+y=1 '*100+'$$'
  self.assertTrue(any(math in p for p in split_reply('开头。'+math+'结尾。')))
  code='```python\n'+'x=1\n'*100+'```'
  self.assertTrue(any(code in p for p in split_reply('开头。\n'+code+'\n结尾。')))
 def test_delivery_order_delay_final_image_and_loop_guard(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);c={'allowed_private_users':['200'],'onebot_token':'t'}
   (root/'config.json').write_text(json.dumps(c));b=Bot(root);calls=[]
   b.request=lambda u,t,p,n:(calls.append(p) or {'retcode':0})
   text='这是一段比较长的回复。\n'*35
   with patch('reply_flow.time.sleep') as sleep:
    self.assertTrue(send_reply(b,'private:200','200',text,[{'type':'face','data':{'id':'14'}}]))
    self.assertEqual(sleep.call_count,len(calls))
    self.assertTrue(all(1.5<=a.args[0]<=4 for a in sleep.call_args_list))
   self.assertEqual(''.join(p['message'][0]['data']['text'] for p in calls),text)
   self.assertTrue(all(len(p['message'])==1 for p in calls[:-1]))
   self.assertEqual(calls[-1]['message'][-1]['type'],'face')
   self.assertTrue(all(('private:200',p['message'][0]['data']['text'].strip()) in b.sent_texts for p in calls))
   b.pool.shutdown()
 def test_group_mentions_once_and_revocation_stops(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'config.json').write_text(json.dumps({'allowed_groups':['100'],'onebot_token':'t'}));b=Bot(root);calls=[]
   b.request=lambda u,t,p,n:(calls.append(p) or {'retcode':0})
   with patch('reply_flow.time.sleep'):
    send_reply(b,'100','200','段落。'*200,[])
   self.assertEqual(sum(s['type']=='at' for p in calls for s in p['message']),1)
   calls.clear()
   def revoke(_): (root/'config.json').write_text(json.dumps({'allowed_groups':[],'onebot_token':'t'}))
   with patch('reply_flow.time.sleep',side_effect=revoke):
    self.assertFalse(send_reply(b,'100','200','回复',[]))
   self.assertFalse(calls);b.pool.shutdown()
 def test_failed_send_stops_remainder(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'config.json').write_text('{"allowed_private_users":["200"],"onebot_token":"t"}');b=Bot(root);calls=[]
   b.request=lambda *a:(calls.append(a) or {'retcode':1})
   with patch('reply_flow.time.sleep'):
    self.assertFalse(send_reply(b,'private:200','200','长回复。'*100,[]))
   self.assertEqual(len(calls),1);b.pool.shutdown()

if __name__=='__main__':unittest.main()
