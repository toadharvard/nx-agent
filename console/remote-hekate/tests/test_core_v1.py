# SPDX-License-Identifier: GPL-2.0-only
import ctypes as c,os,struct,sys,unittest,random
from pathlib import Path
lib=c.CDLL(os.environ.get('REMOTE_CORE_LIB') or str(Path(__file__).with_name('core.dylib' if sys.platform=='darwin' else 'core.so')))
lib.remote_core_request.argtypes=[c.c_void_p,c.c_uint32,c.c_void_p,c.c_uint32,c.c_void_p]
lib.remote_core_touch.argtypes=[c.c_uint32,c.POINTER(c.c_uint32),c.POINTER(c.c_uint32),c.POINTER(c.c_int)]
SIZE=1280*720*4;MAGIC=0x5258594e
frame=c.create_string_buffer(b'abcd'*(SIZE//4))
def request(op,*args,now=100,seq=1,length=64):
 q=struct.pack('<16I',MAGIC,seq,op,*args,*([0]*(13-len(args))))
 out=c.create_string_buffer(4096);lib.remote_core_request(q,length,out,now,frame)
 h=struct.unpack_from('<8I',out.raw);return h,out.raw[32:32+h[3]]
def touch(now):
 x=c.c_uint32();y=c.c_uint32();p=c.c_int();changed=lib.remote_core_touch(now,c.byref(x),c.byref(y),c.byref(p));return changed,x.value,y.value,p.value
class Core(unittest.TestCase):
 def setUp(self):lib.remote_core_reset();touch(0)
 def test_touch_expires_even_without_host_release(self):
  self.assertEqual(request(4,1279,719,100)[0][2],0)
  self.assertEqual(touch(199),(1,1279,719,1));self.assertEqual(touch(200),(1,1279,719,0));self.assertEqual(touch(201)[0],0)
 def test_reset_and_explicit_release(self):
  request(4,10,20,500);request(5);self.assertEqual(touch(101),(1,10,20,0))
  request(4,10,20,500);lib.remote_core_reset();self.assertEqual(touch(102)[3],0)
 def test_clock_wrap(self):
  request(4,1,2,100,now=0xfffffff0);self.assertEqual(touch(20)[3],1);self.assertEqual(touch(84)[3],0)
 def test_bad_coordinates_or_hold_never_press(self):
  for args in [(1280,0,10),(0,720,10),(0,0,0),(0,0,501),(0xffffffff,0,100)]:
   self.assertEqual(request(4,*args)[0][2],2);self.assertEqual(touch(101)[0],0)
 def test_snapshot_is_coherent_and_generation_checked(self):
  h,b=request(2);g=struct.unpack('<I',b)[0]
  self.assertEqual(request(3,g,0,4064)[1],b'abcd'*1016)
  self.assertEqual(request(3,g+1,0,4)[0][2],4)
  lib.remote_core_reset();self.assertEqual(request(3,g,0,4)[0][2],4)
 def test_snapshot_bounds(self):
  g=struct.unpack('<I',request(2)[1])[0]
  for off,n in [(SIZE,1),(SIZE-1,2),(0,4065),(0,0),(0xffffffff,100)]:self.assertEqual(request(3,g,off,n)[0][2],2)
 def test_random_invalid_requests_do_not_write_outside_reply(self):
  rng=random.Random(20261015)
  for _ in range(10000):
   b=rng.randbytes(64);out=c.create_string_buffer(b'X'*4112)
   lib.remote_core_request(b,64,out,100,frame)
   self.assertEqual(out.raw[4096:4112],b'X'*16)
 def test_short_request_and_unknown_op(self):
  self.assertEqual(request(1,length=63)[0][2],2);self.assertEqual(request(0xffffffff)[0][2],1)
if __name__=='__main__':unittest.main(verbosity=2)
