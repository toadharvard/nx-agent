# SPDX-License-Identifier: MIT
import ctypes as c,sys,errno,os,struct,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
lib=c.CDLL(str(ROOT/'tests/bin'/('rescue-test'+('.dylib' if sys.platform=='darwin' else '.so'))))
lib.rescue_dispatch.argtypes=[c.c_void_p,c.c_void_p]
MAGIC=0x31425255
BASE=Path('sdmc:/config/usb-rescue')
JOURNAL=BASE/'boot-guard.bin'
SUFFIX='.sidecar-disabled'
def flag(tid='0100000000000352'):return f'sdmc:/atmosphere/contents/{tid}/flags/boot2.flag'
def frame(op,data=b''):return struct.pack('<4I',MAGIC,op,len(data),0)+data+bytes(4080-len(data))
def call(op,data=b''):
 out=c.create_string_buffer(4096);lib.rescue_dispatch(frame(op,data),out)
 magic,e,n,_=struct.unpack_from('<4I',out.raw);assert magic==MAGIC
 return e,out.raw[16:16+n]
def entry(src,dst):return src.encode().ljust(200,b'\0')+dst.encode().ljust(200,b'\0')
def arm(*pairs):return call(70,struct.pack('<I',len(pairs))+b''.join(entry(a,b) for a,b in pairs))[0]
def status():
 e,d=call(72);assert e==0 and len(d)==24;return struct.unpack('<6I',d)
class BootGuard(unittest.TestCase):
 def setUp(self):
  self.old=Path.cwd();self.tmp=tempfile.TemporaryDirectory();os.chdir(self.tmp.name);BASE.mkdir(parents=True)
 def tearDown(self):os.chdir(self.old);self.tmp.cleanup()
 def make(self,path,data=b''):p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
 def test_versions_and_feature_bit(self):
  e,d=call(6);self.assertEqual((e,struct.unpack('<4I',d)),(0,(7,0x52534355,1,0)))
  e,d=call(9);v=struct.unpack('<6I',d);self.assertEqual((e,v[0],v[5]),(0,7,1))
 def test_strict_path_validation(self):
  f=flag();bad=[(f,f),(f+SUFFIX,f+SUFFIX),(f,flag('0100000000000353')+SUFFIX),(f,f+'.disabled'),
   (f,f+SUFFIX+'x'),(f.replace('boot2.flag','boot2.flg'),f.replace('boot2.flag','boot2.flg')+SUFFIX),
   (f.replace('/flags/','/'),f.replace('/flags/','/')+SUFFIX),(flag('010000000000035G'),flag('010000000000035G')+SUFFIX),
   (flag('01000000000003520'),flag('01000000000003520')+SUFFIX),(flag('420000000000D06B'),flag('420000000000D06B')+SUFFIX),
   (flag('420000000000d06c')+SUFFIX,flag('420000000000d06c')),('sdmc:/atmosphere/contents/../x/flags/boot2.flag','x'),
   ('','')]
  for a,b in bad:self.assertEqual(arm((a,b)),errno.EINVAL,(a,b))
  self.assertEqual(call(70,struct.pack('<I',0))[0],errno.EINVAL)
  self.assertEqual(call(70,struct.pack('<I',9)+entry(f,f+SUFFIX)*9)[0],errno.EINVAL)
  self.assertEqual(call(70,struct.pack('<I',2)+entry(f,f+SUFFIX))[0],errno.EINVAL)
  garbage=bytearray(entry(f,f+SUFFIX));garbage[199]=0x41
  self.assertEqual(call(70,struct.pack('<I',1)+bytes(garbage))[0],errno.EINVAL)
  self.assertFalse(JOURNAL.exists());self.assertEqual(status()[:4],(0,0,2,0))
 def test_arm_confirm_and_status(self):
  f=flag();self.assertEqual(call(71)[0],errno.ENOENT)
  self.assertEqual(arm((f+SUFFIX,f),(flag('0100000000000353'),flag('0100000000000353')+SUFFIX)),0)
  self.assertEqual(status(),(1,0,2,2,0,0))
  self.assertEqual(arm((f+SUFFIX,f)),errno.EEXIST)
  self.assertEqual(call(71,b'x')[0],errno.EINVAL)
  self.assertEqual(call(71)[0],0);self.assertFalse(JOURNAL.exists());self.assertEqual(status()[:4],(0,0,2,0))
 def test_two_unconfirmed_boots_revert_once(self):
  a,b,d=flag(),flag('0100000000000353'),flag('0100000000000354')
  self.make(a+SUFFIX);self.make(b);self.make(d+SUFFIX);self.make(d)
  # Revert a (disabled -> enabled), b (enabled -> disabled); d skipped because destination exists; missing source skipped.
  self.assertEqual(arm((a+SUFFIX,a),(b,b+SUFFIX),(d+SUFFIX,d),(flag('0100000000000355'),flag('0100000000000355')+SUFFIX)),0)
  self.assertEqual(lib.guard_boot(),0);self.assertEqual(status(),(1,1,2,4,0,0))
  self.assertTrue(Path(a+SUFFIX).exists());self.assertFalse(Path(a).exists())
  self.assertEqual(lib.guard_boot(),0)
  self.assertTrue(Path(a).exists());self.assertFalse(Path(a+SUFFIX).exists())
  self.assertTrue(Path(b+SUFFIX).exists());self.assertFalse(Path(b).exists())
  self.assertTrue(Path(d).exists() and Path(d+SUFFIX).exists())
  self.assertFalse(JOURNAL.exists());self.assertEqual(status(),(0,0,2,0,0,2))
  self.assertEqual(lib.guard_boot(),0);self.assertTrue(Path(a).exists());self.assertEqual(status(),(0,0,2,0,0,2))
 def test_confirm_after_one_boot_prevents_revert(self):
  a=flag();self.make(a+SUFFIX)
  self.assertEqual(arm((a+SUFFIX,a)),0);self.assertEqual(lib.guard_boot(),0);self.assertEqual(call(71)[0],0)
  self.assertEqual(lib.guard_boot(),0);self.assertEqual(lib.guard_boot(),0)
  self.assertTrue(Path(a+SUFFIX).exists());self.assertFalse(Path(a).exists())
 def test_corrupt_journal_is_never_acted_on(self):
  a=flag();self.make(a+SUFFIX);self.assertEqual(arm((a+SUFFIX,a)),0)
  raw=bytearray(JOURNAL.read_bytes());raw[16+200:16+400]=flag('0100000000000353').encode().ljust(200,b'\0');JOURNAL.write_bytes(bytes(raw))
  for _ in range(3):self.assertEqual(lib.guard_boot(),errno.EINVAL)
  self.assertTrue(Path(a+SUFFIX).exists());self.assertEqual(status(),(1,0,2,0,errno.EINVAL,0))
  self.assertEqual(call(71)[0],0);self.assertEqual(lib.guard_boot(),0)
 def test_no_journal_boot_is_noop(self):self.assertEqual(lib.guard_boot(),0);self.assertFalse(JOURNAL.exists())
if __name__=='__main__':unittest.main(verbosity=2)
