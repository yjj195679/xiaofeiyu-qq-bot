import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from bot import Bot
import memory_flow as memory

class MemoryTests(unittest.TestCase):
 def test_turn_and_character_budget_preserves_pairs(self):
  messages=[{'role':role,'content':str(i)+'x'*100} for i in range(45) for role in ('user','assistant')]
  out=memory.trim(messages);self.assertEqual(len(out),60);self.assertTrue(out[0]['content'].startswith('15'))
  out=memory.trim([{'role':role,'content':'x'*4000} for _ in range(10) for role in ('user','assistant')])
  self.assertLessEqual(sum(len(m['content']) for m in out),24000);self.assertEqual(len(out)%2,0)
 def test_restart_isolation_expiry_and_clear(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);b=Bot(root);key=('private:200','200');history=[{'role':'user','content':'记住蓝色'},{'role':'assistant','content':'记住啦'}]
   with patch('memory_flow.time.time',return_value=100000):memory.save(b,key,history)
   b.pool.shutdown();b=Bot(root)
   with patch('memory_flow.time.time',return_value=100010):
    self.assertEqual(memory.load(b,key),history)
    self.assertEqual(memory.load(b,('100','200')),[])
    self.assertEqual(memory.load(b,('private:201','201')),[])
    memory.clear(b,key);self.assertEqual(memory.load(b,key),[])
    memory.save(b,key,history)
   with patch('memory_flow.time.time',return_value=100010+86401):self.assertEqual(memory.load(b,key),[])
   b.pool.shutdown()
 def test_after_six_turns_earlier_context_is_retained(self):
  with tempfile.TemporaryDirectory() as t:
   b=Bot(Path(t));history=[]
   for i in range(10):
    history=memory.load(b,('private:200','200'))+[{'role':'user','content':'问题'+str(i)},{'role':'assistant','content':'回答'+str(i)}]
    memory.save(b,('private:200','200'),history)
   self.assertEqual(len(memory.load(b,('private:200','200'))),20)
   self.assertEqual(memory.load(b,('private:200','200'))[0]['content'],'问题0');b.pool.shutdown()

if __name__=='__main__':unittest.main()
