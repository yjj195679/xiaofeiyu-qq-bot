import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from bot import Bot, poke_target

class PokeTests(unittest.TestCase):
    def setUp(self):
        self.cfg={'qq':'300','allowed_groups':['100'],'allowed_private_users':['200'],'onebot_token':'test'}
        self.event={'post_type':'notice','notice_type':'notify','sub_type':'poke','self_id':300,'user_id':200,'target_id':300,'time':10}
    def test_target_and_allowlists(self):
        self.assertEqual(poke_target(self.event,self.cfg),('private:200','200'))
        self.assertEqual(poke_target({**self.event,'group_id':100},self.cfg),('100','200'))
        for change in ({'target_id':201},{'user_id':300},{'user_id':201},{'group_id':999},{'self_id':301},{'sub_type':'other'},{'user_id':0}):
            self.assertIsNone(poke_target({**self.event,**change},self.cfg))
        self.assertIsNone(poke_target({**self.event,'user_id':300,'sender_id':200},self.cfg))
    def test_dispatch_cooldown_duplicate_and_no_model(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); (root/'config.json').write_text(json.dumps(self.cfg))
            b=Bot(root); calls=[]
            b.request=lambda url,token,payload,timeout:(calls.append((url,payload)) or {'retcode':0})
            with patch('bot.time.monotonic',return_value=100):
                self.assertTrue(b.enqueue(self.event))
                self.assertFalse(b.enqueue({**self.event,'time':11}))
            b.pool.shutdown()
            self.assertEqual(len(calls),1)
            self.assertTrue(calls[0][0].endswith('/send_private_msg'))
            self.assertEqual(calls[0][1]['user_id'],200)
            self.assertEqual(b.sessions,{})
            with patch('bot.time.monotonic',return_value=131):
                self.assertFalse(b.enqueue(self.event))
    def test_group_throttle_and_slot_release(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); (root/'config.json').write_text(json.dumps(self.cfg))
            b=Bot(root); calls=[]
            b.request=lambda url,token,payload,timeout:(calls.append((url,payload)) or {'retcode':0})
            with patch('bot.time.monotonic',return_value=100):
                self.assertTrue(b.enqueue({**self.event,'group_id':100}))
                self.assertFalse(b.enqueue({**self.event,'group_id':100,'user_id':201,'time':11}))
            b.pool.shutdown()
            self.assertTrue(calls[0][0].endswith('/send_group_msg'))
            self.assertEqual(calls[0][1]['message'][0],{'type':'at','data':{'qq':'200'}})
            self.assertEqual(calls[0][1]['group_id'],100)
            self.assertTrue(b.slots.acquire(blocking=False))
    def test_revoked_permission_before_send(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); (root/'config.json').write_text(json.dumps({**self.cfg,'allowed_private_users':[]}))
            b=Bot(root); b.request=lambda *a:self.fail('must not send')
            b.slots.acquire(); b.poke_reply('private:200','200'); b.pool.shutdown()

if __name__=='__main__': unittest.main()
