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
 def test_full_sd_writes(self):
  for p in ['sdmc:/switch/app.nro','sdmc:/atmosphere/kips/loader.kip','sdmc:/atmosphere/package3',
   'sdmc:/bootloader/update.bin','sdmc:/bootloader/hekate_ipl.ini','sdmc:/BOOTLOADER/hekate_ipl.ini',
   'sdmc:/payload.bin','sdmc:/atmosphere/contents/00FF0000636C6BFF/flags/boot2.flag','sdmc:/config/usb-worker-mailbox/x']:
   self.assertTrue(lib.sidecar_path(p.encode(),True),p)
 def test_secrets_data_and_modules_stay_closed(self):
  for p in ['sdmc:/','sdmc:/switch/','sdmc:/bootloader/remote/../hekate_ipl.ini','sdmc:/bootloader/remote//x',
   'sdmc:/bootloader/remote/a:b','sdmc:/emuMMC/emummc.ini','sdmc:/Nintendo/x','sdmc:/switch/prod.keys',
   'sdmc:/switch/JKSV/saves/x','sdmc:/bootloader/remote/backup.ini','sdmc:/config/usb-rescue/boot-guard.bin',
   'sdmc:/config/usb-sidecar/pending.bin','sdmc:/CONFIG/USB-SIDECAR/pending.bin','sdmc:/Config/Usb-Rescue/x',
   'sdmc:/atmosphere/contents/420000000000D06B/exefs.nsp','/switch/x','sdmc:/a\\b','sdmc:/x\ny']:
   self.assertFalse(lib.sidecar_path(p.encode(),True),p)
 def test_reads(self):
  for p in ['sdmc:/','sdmc:/atmosphere/kips/loader.kip','sdmc:/config/usb-sidecar/pending.bin','sdmc:/payload.bin']:
   self.assertTrue(lib.sidecar_path(p.encode(),False),p)
  for p in ['sdmc:/switch/prod.keys','sdmc:/Nintendo/Contents','sdmc:/emuMMC/RAW1','sdmc:/a/../b']:
   self.assertFalse(lib.sidecar_path(p.encode(),False),p)
 def test_tree_ops_never_move_protected_parents(self):
  lib.sidecar_tree_path.argtypes=[c.c_char_p];lib.sidecar_tree_path.restype=c.c_bool
  for p in ['sdmc:/config','sdmc:/Config','sdmc:/atmosphere','sdmc:/atmosphere/contents','sdmc:/ATMOSPHERE/Contents',
   'sdmc:/atmosphere/contents/420000000000d06c','sdmc:/config/usb-rescue','sdmc:/']:
   self.assertFalse(lib.sidecar_tree_path(p.encode()),p)
  for p in ['sdmc:/atmosphere/kips','sdmc:/atmosphere/kips/loader.kip','sdmc:/switch','sdmc:/config/4IFIR',
   'sdmc:/atmosphere/contents/00FF0000636C6BFF']:
   self.assertTrue(lib.sidecar_tree_path(p.encode()),p)
if __name__=='__main__':unittest.main(verbosity=2)
