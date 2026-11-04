#!/usr/bin/env python3
"""V1 (D14) against J-0127 (8b24dc6, the S3 loader rounds an unaligned SizeOfImage up).

    python3 tests/v1/test_loader_soi.py            (from ports/fnv-nx; output under $FNVNX_V1_OUT or build/v1)
    env: FNVNX_GAME_EXE=<the player's FalloutNV.exe> also fuzzes and loads the real executable (agent-only data:
         nothing of it is printed or written outside the output directory); FNVNX_V1_ITERS (default 30000)

1. tests/v1/fuzz_soi.c on the synthetic PEs (and the real exe): structured header mutations around the change,
   the whole pipeline under ASan/UBSan, invariants I1-I5 on every accepted header.
2. Differential: the first 3000 mutated headers (header bytes only, in one record file deleted after the replay) of each seed through loader/tools/fnvnx_pe.py; the C loader and the
   Python tool must accept the same headers with the same rounded size.
3. The real exe: fnvnx_pe.PE and loadcheck --headers accept it, SizeOfImage 0x010089e4 -> 0x01009000.
"""
import os
import shutil
import struct
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
LOADER = os.path.join(ROOT, 'loader')
OUT = os.path.abspath(os.environ.get('FNVNX_V1_OUT', os.path.join(ROOT, 'build', 'v1')))
BIN = os.path.join(OUT, 'loader')
SYN = os.path.join(OUT, 'synth')
GAME = os.environ.get('FNVNX_GAME_EXE', '')
ITERS = os.environ.get('FNVNX_V1_ITERS', '30000')
KEEP = 3000
HDR = 0x2000
sys.path.insert(0, os.path.join(LOADER, 'tools'))
from fnvnx_pe import PE, PEError  # noqa: E402

C_ERR = {0: None, -2: 'FORMAT', -3: 'UNSUPPORTED'}


def run(a, **kw):
    return subprocess.run(a, capture_output=True, text=True, **kw)


def setUpModule():
    os.makedirs(OUT, exist_ok=True)
    r = run(['make', '-s', '-C', LOADER, 'OUT=' + BIN])
    assert r.returncode == 0, r.stdout + r.stderr
    if not os.path.isdir(SYN):
        r = run([sys.executable, os.path.join(ROOT, 'tests', 's3', 'gen_synth_pe.py'), SYN])
        assert r.returncode == 0, r.stdout + r.stderr
    r = run([os.environ.get('CC', 'cc'), '-std=c99', '-O1', '-g', '-fsanitize=address,undefined',
             '-fno-sanitize-recover=all', '-I' + os.path.join(LOADER, 'include'), os.path.join(HERE, 'fuzz_soi.c'),
             os.path.join(BIN, 'libfnvnx_loader.a'), '-o', os.path.join(BIN, 'fuzz_soi')])
    assert r.returncode == 0, r.stderr


def seeds():
    s = [os.path.join(SYN, 'plain.exe'), os.path.join(SYN, 'reloc.exe')]
    return s + ([GAME] if GAME else [])


def py_verdict(data):
    try:
        return None, PE(data).size_of_image
    except PEError as e:
        return e.args[0], 0
    except Exception as e:   # a crash of the tool is a finding
        return 'CRASH %s: %s' % (type(e).__name__, e), 0


class T(unittest.TestCase):
    def test_fuzz_and_differential(self):
        bad = []
        for i, seed in enumerate(seeds()):
            tag = 'game' if seed == GAME else os.path.basename(seed)
            rec = os.path.join(OUT, 'soi-%s.rec' % tag)       # K records of 16 + 0x2000 bytes, deleted below
            r = run([os.path.join(BIN, 'fuzz_soi'), seed, ITERS, str(11 + i), rec, str(KEEP)], timeout=540)
            print('\n  %s: %s' % (tag, r.stdout.strip().replace('\n', '; ')), file=sys.stderr)
            if r.returncode:
                bad.append('%s: fuzz_soi exit %d: %s' % (tag, r.returncode, r.stderr[-2000:]))
            diffs = []
            with open(rec, 'rb') as f:
                blob = f.read()
            os.remove(rec)
            for o in range(0, len(blob), 16 + HDR):
                n, rc, soi, fsize = struct.unpack_from('<IiII', blob, o)
                hdr = blob[o + 16:o + 16 + HDR]
                data = hdr[:fsize] + bytes(max(0, fsize - HDR))  # fnvnx_pe.PE reads only headers and len(data)
                perr, psoi = py_verdict(data)
                c_ok, p_ok = rc == 0, perr is None
                if c_ok != p_ok or (c_ok and soi != psoi) or (perr or '').startswith('CRASH'):
                    decl = struct.unpack_from('<I', hdr, struct.unpack_from('<I', hdr, 0x3c)[0] + 24 + 56)[0]
                    diffs.append((n, rc, hex(soi), perr, hex(psoi), hex(decl)))
                    if len(diffs) <= 20 and seed != GAME:   # reproducers: synthetic headers only, never game bytes
                        with open(os.path.join(OUT, 'soi-diff-%s-%d.hdr' % (tag, n)), 'wb') as f:
                            f.write(struct.pack('<I', fsize) + hdr)
            for d in diffs[:10]:
                print('  differs: #%d C rc=%d soi=%s | py %s soi=%s | declared %s' % d, file=sys.stderr)
            if diffs:
                bad.append('%s: %d of %d headers differ between pe.c and fnvnx_pe.py' % (tag, len(diffs), KEEP))
        self.assertEqual(bad, [])

    def test_python_accepts_what_c_rejects_near_4g(self):
        """Hand-made: ImageBase 0 and SizeOfImage 0xfffff001 (the C loader: > 0xfffff000 is FORMAT)."""
        with open(os.path.join(SYN, 'plain.exe'), 'rb') as f:
            d = bytearray(f.read())
        opt = struct.unpack_from('<I', d, 0x3c)[0] + 24
        struct.pack_into('<I', d, opt + 28, 0)
        struct.pack_into('<I', d, opt + 56, 0xfffff001)
        p = os.path.join(OUT, 'soi_4g.exe')
        with open(p, 'wb') as f:
            f.write(d)
        c = run([os.path.join(BIN, 'loadcheck'), '--exe', p, '--headers'])
        perr, psoi = py_verdict(bytes(d))
        self.assertEqual((c.returncode != 0), (perr is not None),
                         'C loadcheck rc=%d, fnvnx_pe.py %s size_of_image=%#x' % (c.returncode, perr, psoi))

    @unittest.skipUnless(GAME, 'FNVNX_GAME_EXE not set')
    def test_real_exe_headers(self):
        """pe_open (the boot path, toolchain/game/boot.cpp) and fnvnx_pe.PE take the real exe, rounded."""
        with open(GAME, 'rb') as f:
            data = f.read()
        opt = struct.unpack_from('<I', data, 0x3c)[0] + 24
        self.assertEqual(struct.unpack_from('<I', data, opt + 56)[0], 0x010089e4)
        self.assertEqual(PE(data).size_of_image, 0x01009000)
        r = run([os.path.join(BIN, 'loadcheck'), '--exe', GAME, '--headers'])
        self.assertIn('pe_open=0 ', r.stdout)
        self.assertIn('size_of_image=0x1009000', r.stdout)

    @unittest.skipUnless(GAME, 'FNVNX_GAME_EXE not set')
    def test_real_exe_loadcheck(self):
        """loadcheck, the host acceptance tool (docs/DATAIMAGE.md), takes the real exe as plain."""
        r = run([os.path.join(BIN, 'loadcheck'), '--exe', GAME, '--headers'])
        notplain = [l.split()[1] for l in r.stdout.splitlines() if l.startswith('section ') and l.endswith('plain=0')]
        print('\n  loadcheck --headers (real exe): rc=%d, not plain: %s' % (r.returncode, notplain), file=sys.stderr)
        self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-500:])


if __name__ == '__main__':
    unittest.main()
