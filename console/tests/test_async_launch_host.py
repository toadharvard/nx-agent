# SPDX-License-Identifier: MIT
import sys,struct,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'host/src'))
from nxagent.proto.rescue import Rescue
class Fake(Rescue):
 def __init__(self,done=True,legacy=False):self.capabilities=[6,0 if legacy else 1];self.ops=[];self.done=done;self.polls=0
 def call(self,op,data=b''):
  self.ops.append(op)
  if op in (62,64):return struct.pack('<Q',321 if op==62 else 7)
  if op==65:
   self.polls+=1;done=self.done and self.polls>=3
   return struct.pack('<IIQQ',3 if done else 2,0,321 if done else 0,7)
  if op==66:assert data==struct.pack('<Q',7);return b''
  raise AssertionError(op)
class Tests(unittest.TestCase):
 def test_waits_on_same_job_and_acknowledges_result(self):
  f=Fake()
  with patch('nxagent.proto.rescue.time.sleep'):self.assertEqual(f.start_worker(),321)
  self.assertEqual(f.ops,[64,65,65,65,66])
 def test_timeout_never_replays_or_acknowledges_pending_start(self):
  f=Fake(done=False)
  with patch('nxagent.proto.rescue.time.monotonic',side_effect=[0,1,16]),patch('nxagent.proto.rescue.time.sleep'):
   with self.assertRaises(TimeoutError):f.start_worker()
  self.assertEqual(f.ops,[64,65])
 def test_legacy_device_still_uses_supported_protocol(self):
  f=Fake(legacy=True);self.assertEqual(f.start_worker(),321);self.assertEqual(f.ops,[62])
if __name__=='__main__':unittest.main(verbosity=2)
