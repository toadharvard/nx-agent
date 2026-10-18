# SPDX-License-Identifier: MIT
import sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'host/src'))
from nxagent.proto.transport import UsbSocket
class Interface(list):
 def __init__(self,n,protocol):
  super().__init__([SimpleNamespace(bmAttributes=2,bEndpointAddress=0x81+n),SimpleNamespace(bmAttributes=2,bEndpointAddress=0x01+n)])
  self.bInterfaceNumber=n;self.bInterfaceClass=0xff;self.bInterfaceSubClass=0x52;self.bInterfaceProtocol=protocol
class Interfaces(unittest.TestCase):
 def connect(self,interfaces,index):
  s=UsbSocket.__new__(UsbSocket);s.interface_number=index;s.claimed=False
  with patch('nxagent.proto.transport.usb.core.find',return_value=SimpleNamespace(get_active_configuration=lambda:interfaces)),patch('nxagent.proto.transport.usb.util.claim_interface') as claim:
   s._open(False);claim.assert_called_once_with(s.dev,index)
  return s
 def test_channels_are_explicit_and_separate(self):
  interfaces=[Interface(0,0),Interface(1,1)]
  self.assertIs(self.connect(interfaces,0).interface,interfaces[0])
  self.assertIs(self.connect(interfaces,1).interface,interfaces[1])
 def test_legacy_single_interface_still_works(self):self.connect([Interface(0,0)],0)
 def test_recovery_never_falls_back_to_worker(self):
  with self.assertRaises(RuntimeError):self.connect([Interface(0,0)],1)
  with self.assertRaises(RuntimeError):self.connect([Interface(0,0),Interface(1,0)],1)
if __name__=='__main__':unittest.main(verbosity=2)
