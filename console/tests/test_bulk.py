# SPDX-License-Identifier: MIT
import ctypes as c, errno, hashlib, os, sys, tempfile, unittest
from pathlib import Path

ext = '.dylib' if sys.platform == 'darwin' else '.so'
bin_dir = Path(__file__).resolve().parent / 'bin'
rescue = c.CDLL(str(bin_dir / f'rescue-test{ext}'))
worker = c.CDLL(str(bin_dir / f'worker-fileops-test{ext}'))
rescue.bulk_path.argtypes = worker.sidecar_path.argtypes = [c.c_char_p, c.c_bool]
rescue.bulk_path.restype = worker.sidecar_path.restype = c.c_bool
rescue.bulk_begin.argtypes = [c.c_char_p, c.c_uint64, c.c_char_p, c.c_char_p, c.c_bool]
rescue.bulk_write.argtypes = [c.c_char_p, c.c_size_t]
rescue.bulk_open_read.argtypes = [c.c_char_p, c.POINTER(c.c_uint64), c.c_char_p]
rescue.bulk_read.argtypes = [c.c_char_p, c.c_size_t]
rescue.bulk_read.restype = c.c_long

PATHS = ['sdmc:/', 'sdmc:/switch/app.nro', 'sdmc:/switch/', 'sdmc:/atmosphere/kips/loader.kip', 'sdmc:/payload.bin',
         'sdmc:/a/../b', 'sdmc:/a//b', 'sdmc:/a\\b', 'sdmc:/a:b', '/switch/x', 'sdmc:/Nintendo/x', 'sdmc:/emuMMC/x',
         'sdmc:/switch/prod.keys', 'sdmc:/switch/JKSV/saves/x', 'sdmc:/x/backup/y', 'sdmc:/config/usb-rescue/x',
         'sdmc:/CONFIG/USB-RESCUE/x', 'sdmc:/config/usb-sidecar/pending.bin', 'sdmc:/config/usb-sidecar',
         'sdmc:/atmosphere/contents/420000000000D06B/exefs.nsp', 'sdmc:/atmosphere/contents/420000000000d06c/x',
         'sdmc:/atmosphere/contents/00FF0000636C6BFF/flags/boot2.flag', 'sdmc:/config/4IFIR/config.ini']
ZERO = bytes(32)


def sha(data):
    return hashlib.sha256(data).digest()


class BulkPolicy(unittest.TestCase):
    def test_same_rules_as_worker(self):
        for path in PATHS:
            for write in (False, True):
                with self.subTest(path=path, write=write):
                    self.assertEqual(rescue.bulk_path(path.encode(), write), worker.sidecar_path(path.encode(), write))


class BulkTransactions(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.root = Path(tempfile.mkdtemp())
        os.chdir(self.root)
        (self.root / 'sdmc:' / 'config' / 'usb-rescue').mkdir(parents=True)
        (self.root / 'sdmc:' / 'switch').mkdir()
        self.addCleanup(os.chdir, self.cwd)

    def sd(self, rel):
        return self.root / 'sdmc:' / rel

    def upload(self, path, data, old=None, chunk=7):
        rc = rescue.bulk_begin(path.encode(), len(data), sha(old) if old is not None else ZERO, sha(data),
                               old is not None)
        if rc:
            return rc
        for i in range(0, len(data), chunk):
            self.assertEqual(rescue.bulk_write(data[i:i + chunk], len(data[i:i + chunk])), 0)
        return rescue.bulk_commit()

    def leftovers(self):
        return sorted(p.name for p in self.sd('config/usb-rescue').iterdir())

    def test_create_and_overwrite(self):
        self.assertEqual(self.upload('sdmc:/switch/app.nro', b'first version'), 0)
        self.assertEqual(self.sd('switch/app.nro').read_bytes(), b'first version')
        self.assertEqual(self.upload('sdmc:/switch/app.nro', b'second', old=b'first version'), 0)
        self.assertEqual(self.sd('switch/app.nro').read_bytes(), b'second')
        self.assertEqual(self.leftovers(), ['bulk-previous.bin'])

    def test_wrong_hash_keeps_original(self):
        self.sd('switch/app.nro').write_bytes(b'original')
        rescue.bulk_begin(b'sdmc:/switch/app.nro', 3, sha(b'original'), sha(b'xyz'), True)
        rescue.bulk_write(b'abc', 3)
        self.assertEqual(rescue.bulk_commit(), errno.EBADMSG)
        self.assertEqual(self.sd('switch/app.nro').read_bytes(), b'original')
        self.assertEqual(self.leftovers(), [])

    def test_existing_file_must_match_old_hash(self):
        self.sd('switch/app.nro').write_bytes(b'changed meanwhile')
        self.assertNotEqual(self.upload('sdmc:/switch/app.nro', b'new', old=b'original'), 0)
        self.assertEqual(self.sd('switch/app.nro').read_bytes(), b'changed meanwhile')

    def test_refused_paths_and_busy(self):
        self.assertEqual(rescue.bulk_begin(b'sdmc:/switch/prod.keys', 1, ZERO, sha(b'x'), False), errno.EACCES)
        self.assertEqual(rescue.bulk_begin(b'sdmc:/switch/a.nro', 1, ZERO, sha(b'x'), False), 0)
        self.assertEqual(rescue.bulk_begin(b'sdmc:/switch/b.nro', 1, ZERO, sha(b'y'), False), errno.EBUSY)
        rescue.bulk_write(b'x', 1)
        self.assertEqual(rescue.bulk_commit(), 0)

    def test_recover_after_crash_between_renames(self):
        self.sd('switch/app.nro').write_bytes(b'old content')
        rescue.bulk_begin(b'sdmc:/switch/app.nro', 3, sha(b'old content'), sha(b'new'), True)
        rescue.bulk_write(b'new', 3)
        os.rename(self.sd('switch/app.nro'), self.sd('config/usb-rescue/bulk-previous.bin'))
        self.assertEqual(rescue.bulk_recover(), 0)
        self.assertEqual(self.sd('switch/app.nro').read_bytes(), b'old content')
        self.assertEqual(self.leftovers(), [])

    def test_begin_does_not_preallocate(self):
        self.assertEqual(rescue.bulk_begin(b'sdmc:/switch/big.nro', 64 << 20, ZERO, sha(b'x'), False), 0)
        self.assertEqual(self.sd('config/usb-rescue/bulk.tmp').stat().st_size, 0)
        rescue.bulk_recover()

    def test_rollback_mid_stream_keeps_original(self):
        self.sd('switch/app.nro').write_bytes(b'original')
        self.assertEqual(rescue.bulk_begin(b'sdmc:/switch/app.nro', 6, sha(b'original'), sha(b'abcdef'), True), 0)
        self.assertEqual(rescue.bulk_write(b'abc', 3), 0)
        self.assertEqual(rescue.bulk_recover(), 0)
        self.assertEqual(self.sd('switch/app.nro').read_bytes(), b'original')
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(rescue.bulk_write(b'def', 3), errno.EINVAL)
        self.assertEqual(rescue.bulk_commit(), errno.EINVAL)

    def test_read_streams_whole_file_with_hash(self):
        data = os.urandom(300000)
        self.sd('switch/big.bin').write_bytes(data)
        size, digest = c.c_uint64(), c.create_string_buffer(32)
        self.assertEqual(rescue.bulk_open_read(b'sdmc:/switch/big.bin', c.byref(size), digest), 0)
        self.assertEqual((size.value, digest.raw), (len(data), sha(data)))
        got, buf = bytearray(), c.create_string_buffer(65536)
        while (n := rescue.bulk_read(buf, 65536)) > 0:
            got += buf.raw[:n]
        rescue.bulk_close_read()
        self.assertEqual(bytes(got), data)


if __name__ == '__main__':
    unittest.main()
