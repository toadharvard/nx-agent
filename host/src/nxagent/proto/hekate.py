"""Nyx screen/touch client. A mutation with a lost reply is never replayed."""
import fcntl,struct
import usb.core,usb.util
from .. import paths
MAGIC=0x5258594e
class Nyx:
 def __init__(self):
  self.lock=paths.usb_lock('nyx-usb.lock').open('a')
  fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  self.dev=None;self.claimed=False;self.faulted=False;self.seq=0
  try:
   self.dev=usb.core.find(idVendor=0x11ec,idProduct=0xa7e1)
   if self.dev is None:raise RuntimeError('Remote Nyx is not connected')
   iface=self.dev.get_active_configuration()[(0,0)]
   if (iface.bInterfaceClass,iface.bInterfaceSubClass,iface.bInterfaceProtocol)!=(255,82,1):raise RuntimeError('Unexpected USB interface')
   usb.util.claim_interface(self.dev,0);self.claimed=True
   self.input=usb.util.find_descriptor(iface,bEndpointAddress=0x81)
   self.output=usb.util.find_descriptor(iface,bEndpointAddress=0x01)
  except BaseException:self.close();raise
 def call(self,op,*args):
  if self.faulted:raise RuntimeError('Previous reply uncertain; no operation replay allowed')
  self.seq=(self.seq+1)&0xffffffff
  q=struct.pack('<16I',MAGIC,self.seq,op,*args,*([0]*(13-len(args))))
  try:
   if self.output.write(q,timeout=2000)!=64:raise IOError('Short write')
   b=bytes(self.input.read(4096,timeout=2000))
   if len(b)!=4096:raise IOError('Short reply')
   magic,seq,error,size,echo,*_=struct.unpack_from('<8I',b)
   if magic!=MAGIC or seq!=self.seq or echo!=op or size>4064:raise IOError('Reply mismatch')
  except BaseException:self.faulted=True;raise
  if error:raise RuntimeError(f'Nyx error {error}')
  return b[32:32+size]
 def screenshot(self,path):
  from PIL import Image
  version,w,h,bpp,total,hold=struct.unpack('<6I',self.call(1))
  if (w,h,bpp,total)!=(1280,720,4,1280*720*4):raise RuntimeError('Unknown framebuffer format')
  generation=struct.unpack('<I',self.call(2))[0];data=bytearray()
  for offset in range(0,total,4064):data.extend(self.call(3,generation,offset,min(4064,total-offset)))
  if len(data)!=total:raise IOError('Incomplete framebuffer')
  Image.frombytes('RGB',(w,h),bytes(data),'raw','BGRX').save(path)
 def tap(self,x,y,ms=150):self.call(4,x,y,ms)
 def close(self):
  if self.dev is not None:
   if self.claimed:usb.util.release_interface(self.dev,0)
   usb.util.dispose_resources(self.dev)
  self.lock.close()
