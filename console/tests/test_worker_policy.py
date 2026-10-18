# SPDX-License-Identifier: MIT
import ctypes as c,sys,unittest
from pathlib import Path
lib=c.CDLL(str(Path(__file__).resolve().parent/'bin'/('worker-fileops-test'+('.dylib' if sys.platform=='darwin' else '.so'))))
lib.sidecar_path.argtypes=[c.c_char_p,c.c_bool];lib.sidecar_path.restype=c.c_bool
class Policy(unittest.TestCase):
 def test_worker_cannot_update_or_disable_either_module(self):
  for tid in ['420000000000D06B','420000000000D06C','420000000000d06c']:
   for name in ['exefs.nsp','flags/boot2.flag']:
    self.assertFalse(lib.sidecar_path(f'sdmc:/atmosphere/contents/{tid}/{name}'.encode(),True))
  self.assertFalse(lib.sidecar_path(b'sdmc:/config/usb-rescue/known-good.nsp',True))
 def test_existing_diagnostic_paths_remain(self):
  for p in ['sdmc:/config/4IFIR/config.ini','sdmc:/atmosphere/config/override_config.ini']:
   self.assertTrue(lib.sidecar_path(p.encode(),True))
 def test_v7_bootloader_writes_are_exact(self):
  for p in ['sdmc:/bootloader/hekate_ipl.ini','sdmc:/bootloader/payloads/Remote-Hekate.bin',
   'sdmc:/bootloader/remote/remote.ini','sdmc:/bootloader/remote/Remote-Hekate.bin']:
   self.assertTrue(lib.sidecar_path(p.encode(),True),p)
  for p in ['sdmc:/bootloader','sdmc:/bootloader/','sdmc:/bootloader/update.bin','sdmc:/bootloader/payloads/other.bin',
   'sdmc:/bootloader/payloads/Remote-Hekate.bin.bak','sdmc:/bootloader/hekate_ipl.ini.bak','sdmc:/bootloader/nyx.ini',
   'sdmc:/bootloader/sys/nyx.bin','sdmc:/bootloader/remote/','sdmc:/bootloader/remote/.','sdmc:/bootloader/remote/sub/x.ini',
   'sdmc:/bootloader/remote/../hekate_ipl.ini','sdmc:/bootloader/remote//x','sdmc:/bootloader/remote/backup.ini',
   'sdmc:/bootloader/remote/prod.keys','sdmc:/bootloader/remote/a:b','sdmc:/payload.bin','sdmc:/boot.dat',
   'sdmc:/BOOTLOADER/hekate_ipl.ini','sdmc:/emuMMC/emummc.ini','sdmc:/Nintendo/x','sdmc:/switch/prod.keys',
   'sdmc:/atmosphere/package3','sdmc:/config/usb-rescue/boot-guard.bin','sdmc:/config/usb-sidecar/pending.bin']:
   self.assertFalse(lib.sidecar_path(p.encode(),True),p)
  for p in ['sdmc:/bootloader/hekate_ipl.ini','sdmc:/bootloader/update.bin','sdmc:/bootloader/remote/remote.ini']:
   self.assertTrue(lib.sidecar_path(p.encode(),False),p)
if __name__=='__main__':unittest.main(verbosity=2)
