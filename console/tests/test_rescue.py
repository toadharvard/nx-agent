# SPDX-License-Identifier: MIT
import ctypes as c,sys,errno,hashlib,json,os,socket,struct,tempfile,threading,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
lib=c.CDLL(str(ROOT/'tests/bin'/('rescue-test'+('.dylib' if sys.platform=='darwin' else '.so'))))
lib.rescue_dispatch.argtypes=[c.c_void_p,c.c_void_p]
lib.bridge_exchange.argtypes=[c.c_void_p,c.c_void_p]
MAGIC=0x31425255
TARGET='sdmc:/atmosphere/contents/420000000000D06B/exefs.nsp'
BASE=Path('sdmc:/config/usb-rescue')
def frame(op,data=b''):
 return struct.pack('<4I',MAGIC,op,len(data),0)+data+bytes(4080-len(data))
def call(op,data=b''):
 out=c.create_string_buffer(4096);lib.rescue_dispatch(frame(op,data),out)
 magic,e,n,_=struct.unpack_from('<4I',out.raw);assert magic==MAGIC
 return e,out.raw[16:16+n]
class RescueTests(unittest.TestCase):
 def setUp(self):
  self.old=Path.cwd();self.tmp=tempfile.TemporaryDirectory();os.chdir(self.tmp.name)
  Path(TARGET).parent.mkdir(parents=True);BASE.mkdir(parents=True);Path(TARGET).write_bytes(b'original')
  lib.mock_set_running(0)
 def tearDown(self):
  lib.mock_set_running(0);lib.sidecar_recover();os.chdir(self.old);self.tmp.cleanup()
 def begin(self,data=b'new',path=TARGET):
  return call(32,struct.pack('<BQ',1,len(data))+hashlib.sha256(b'original').digest()+hashlib.sha256(data).digest()+path.encode())[0]
 def test_worker_stopped_required_and_errors_not_treated_as_stopped(self):
  lib.mock_set_running(1);self.assertEqual(self.begin(),errno.EBUSY)
  lib.mock_set_query_result(12345);self.assertEqual(self.begin(),12345)
  self.assertEqual(Path(TARGET).read_bytes(),b'original')
 def test_commit_rechecks_stopped(self):
  self.assertEqual(self.begin(),0);self.assertEqual(call(33,struct.pack('<Q',0)+b'new')[0],0)
  lib.mock_set_running(1);self.assertEqual(call(34)[0],errno.EBUSY)
  self.assertEqual(Path(TARGET).read_bytes(),b'original')
 def test_success_backup_and_exact_readback(self):
  self.assertEqual(self.begin(),0);self.assertEqual(call(33,struct.pack('<Q',0)+b'new')[0],0)
  self.assertEqual(call(34)[0],0);self.assertEqual(Path(TARGET).read_bytes(),b'new')
  self.assertEqual((BASE/'previous.bin').read_bytes(),b'original')
  e,b=call(18,TARGET.encode());self.assertEqual(e,0);self.assertEqual(b[8:],hashlib.sha256(b'new').digest())
 def test_partial_and_corrupt_upload_never_replace_original(self):
  self.assertEqual(self.begin(),0);call(33,struct.pack('<Q',0)+b'x');self.assertNotEqual(call(34)[0],0)
  self.assertEqual(Path(TARGET).read_bytes(),b'original');call(35)
  self.assertEqual(self.begin(),0);call(33,struct.pack('<Q',0)+b'bad');self.assertEqual(call(34)[0],errno.EBADMSG)
  self.assertEqual(Path(TARGET).read_bytes(),b'original')
 def test_no_start_with_pending_upload(self):
  self.assertEqual(self.begin(),0);self.assertEqual(call(62,b'START')[0],errno.EBUSY)
 def test_fixed_process_commands_and_forbidden_paths(self):
  for path in ['sdmc:/atmosphere/contents/420000000000D06C/exefs.nsp',
   'sdmc:/atmosphere/contents/420000000000D06C/flags/boot2.flag','sdmc:/atmosphere/package3',
   'sdmc:/config/usb-rescue/../prod.keys','sdmc:/config/usb-rescue/previous.bin']:
   self.assertEqual(self.begin(path=path),errno.EACCES,path)
  for op,data in [(61,b''),(62,b'OTHER'),(41,b''),(43,b''),(46,b'REBOOT'),(8,b'\0')]:
   self.assertEqual(call(op,data)[0],errno.ENOSYS)
 def test_rename_gap_recovery(self):
  self.assertEqual(self.begin(),0);call(33,struct.pack('<Q',0)+b'new');Path(TARGET).rename(BASE/'previous.bin')
  self.assertEqual(call(35)[0],0);self.assertEqual(Path(TARGET).read_bytes(),b'original')
 def test_bridge_hang_leaves_rescue_callable_and_does_not_replay(self):
  accepted=threading.Event();finished=threading.Event();received=[]
  server=socket.socket();server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);server.bind(('127.0.0.1',22226));server.listen()
  def stuck_worker():
   conn,_=server.accept()
   with conn:
    request=b''
    while len(request)<4096:request+=conn.recv(4096-len(request))
    received.append(request);accepted.set();finished.wait(3)
  t=threading.Thread(target=stuck_worker);t.start()
  output=c.create_string_buffer(4096)
  bridge=threading.Thread(target=lambda:lib.bridge_exchange(frame(43),output));bridge.start()
  self.assertTrue(accepted.wait(1));start=time.monotonic()
  for _ in range(100):self.assertEqual(call(6)[0],0)
  self.assertLess(time.monotonic()-start,.5)
  self.assertEqual(self.begin(),0);call(33,struct.pack('<Q',0)+b'new');self.assertEqual(call(34)[0],0)
  bridge.join(2);self.assertFalse(bridge.is_alive());self.assertEqual(struct.unpack_from('<I',output.raw,4)[0],errno.ETIMEDOUT)
  finished.set();t.join();server.close();self.assertEqual(len(received),1)
 def test_pending_launch_keeps_ping_live_and_blocks_replacement(self):
  lib.launch_set_ready()
  e,data=call(64,b'START');self.assertEqual(e,0);job=struct.unpack('<Q',data)[0]
  try:
   for _ in range(100):self.assertEqual(call(6)[0],0)
   self.assertEqual(call(64,b'START')[0],errno.EBUSY)
   self.assertEqual(self.begin(),errno.EBUSY)
   lib.launch_execute()
   e,data=call(65);self.assertEqual(e,0)
   state,result,pid,observed=struct.unpack('<IIQQ',data)
   self.assertEqual((state,result,pid,observed),(3,0,123,job))
   self.assertEqual(call(66,struct.pack('<Q',job+1))[0],errno.EBUSY)
  finally:
   lib.launch_execute();call(66,struct.pack('<Q',job))
if __name__=='__main__':unittest.main(verbosity=2)
