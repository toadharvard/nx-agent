#!/usr/bin/env python3
"""tests of S16-compare's capture side (tools/compare): the NXD9 reader/writer, the capture verdict and its metric,
the synthetic scene list, and (when the tools are built) the whole loop on the synthetic scenes. Synthetic data only.

    python3 -I tests/s16/test_capture.py
    S16_E2E=<dir with nxd9play.exe, nx_render, s6b_asm, sdp2dksh> python3 -I tests/s16/test_capture.py   (+ Wine run)
"""
import json, os, shutil, struct, subprocess, sys, tempfile, unittest
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, '..', '..', 'tools', 'compare')
sys.path.insert(0, TOOLS)
import nxd9io, capcompare


def save(a, p):
    from PIL import Image
    os.makedirs(os.path.dirname(p), exist_ok=True)
    Image.fromarray(a, 'RGBA').save(p)


def frame(seed, h=48, w=64):
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)
    a[..., 3] = 255
    return a


class Nxd9(unittest.TestCase):
    def test_round_trip_and_padding(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, 'a.nxd9')
            w = nxd9io.Writer(p)
            w.u32('DEV ', 320, 240, 21, 75)
            w.rec('VBU ', b'\1\2\3')          # 3 bytes: one byte of padding
            w.rec('PRES')
            w.close()
            r = [(t_, bytes(p_)) for t_, p_ in nxd9io.records(p)]
            self.assertEqual(r, [('DEV ', struct.pack('<4I', 320, 240, 21, 75)), ('VBU ', b'\1\2\3'), ('PRES', b'')])
            self.assertEqual(os.path.getsize(p), 8 + 8 + 16 + 8 + 4 + 8)
            s = nxd9io.stat(p)
            self.assertEqual((s['frames'], s['draws']), (1, 0))

    def test_bad_magic_and_truncation(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, 'b.nxd9')
            open(p, 'wb').write(b'NXCAP\0\0\1')
            with self.assertRaises(ValueError): list(nxd9io.records(p))
            open(p, 'wb').write(nxd9io.MAGIC + struct.pack('<II', nxd9io.tag_u32('VBU '), 100) + b'x' * 10)
            with self.assertRaises(ValueError): list(nxd9io.records(p))
            open(p, 'wb').write(b'')
            with self.assertRaises(ValueError): list(nxd9io.records(p))

    def test_fnv1a64(self):
        self.assertEqual(nxd9io.fnv1a64(b''), 0xcbf29ce484222325)
        self.assertEqual(nxd9io.fnv1a64(b'a'), 0xaf63dc4c8601ec8c)


class CapGate(unittest.TestCase):
    def runs(self, t, cand_delta=0, missing=False, floor_delta=0):
        for side in ('ref', 'ref2', 'cand'):
            for c in ('x', 'y'):
                for f in range(2):
                    a = frame(10 * ord(c) + f)
                    if side == 'cand' and c == 'y':
                        a[..., :3] = np.clip(a[..., :3].astype(int) + cand_delta, 0, 255).astype(np.uint8)
                    if side == 'ref2' and c == 'y':
                        a[..., :3] = np.clip(a[..., :3].astype(int) + floor_delta, 0, 255).astype(np.uint8)
                    if side == 'cand' and missing and c == 'y' and f == 1:
                        continue
                    save(a, f'{t}/{side}/{c}/f{f:05d}.png')

    def gate(self, t):
        p = subprocess.run([sys.executable, '-I', os.path.join(TOOLS, 'capcompare.py'), '--ref', f'{t}/ref', '--ref2',
                            f'{t}/ref2', '--cand', f'{t}/cand', '--out', f'{t}/out'], capture_output=True, text=True)
        return p, json.load(open(f'{t}/out/capgate.json'))

    def test_pass(self):
        with tempfile.TemporaryDirectory() as t:
            self.runs(t)
            p, g = self.gate(t)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            m = g['summary']['metric']
            self.assertEqual((m['frames'], m['frames_over'], m['max_abs'], m['floor_max_abs']), (4, 0, 0, 0))
            self.assertIn('metric: 0/4 over', p.stdout)

    def test_fail_counts_frames_over(self):
        with tempfile.TemporaryDirectory() as t:
            self.runs(t, cand_delta=40)
            p, g = self.gate(t)
            self.assertEqual(p.returncode, 1)
            m = g['summary']['metric']
            self.assertEqual(m['frames_over'], 2)
            self.assertEqual(m['max_abs'], 40)
            self.assertGreater(m['mean_abs'], 0)

    def test_missing_frame_is_over(self):
        with tempfile.TemporaryDirectory() as t:
            self.runs(t, missing=True)
            p, g = self.gate(t)
            self.assertEqual(p.returncode, 1)
            self.assertEqual(g['summary']['metric']['frames_over'], 1)
        with tempfile.TemporaryDirectory() as t:
            self.runs(t)
            shutil.rmtree(f'{t}/cand/y')
            p, g = self.gate(t)
            self.assertEqual(g['summary']['metric']['frames_over'], 2)
            self.assertEqual(g['summary']['metric']['captures_incomplete'], 1)

    def test_nonzero_floor_widens_bounds(self):
        with tempfile.TemporaryDirectory() as t:
            self.runs(t, floor_delta=3)
            p, g = self.gate(t)
            m = g['summary']['metric']
            self.assertEqual(m['floor_max_abs'], 3)
            y = [c for c in g['captures'] if c['capture'] == 'y'][0]
            self.assertEqual(y['status'], 'unstable')


class Synth(unittest.TestCase):
    def test_list(self):
        p = subprocess.run([sys.executable, '-I', os.path.join(TOOLS, 'synth.py'), 'list'], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        names = [l.split('\t')[0] for l in p.stdout.splitlines()]
        for n in ('vcolor', 'texture', 'blend', 'stencil', 'rtt', 'lit', 'sm2fog', 'srgb'):
            self.assertIn(n, names)


@unittest.skipUnless(os.environ.get('S16_E2E'), 'S16_E2E=<bin dir> runs the Wine loop')
class EndToEnd(unittest.TestCase):
    def test_synthetic_loop(self):
        b = os.environ['S16_E2E']
        with tempfile.TemporaryDirectory() as t:
            py = lambda *a: subprocess.run([sys.executable, '-I', *a], capture_output=True, text=True, timeout=1200)
            r = py(os.path.join(TOOLS, 'synth.py'), 'build', '--asm', f'{b}/s6b_asm', '--sdp2dksh', f'{b}/sdp2dksh',
                   '--out', f'{t}/syn')
            self.assertEqual(r.returncode, 0, r.stderr)
            caps = sorted(os.path.join(t, 'syn', f) for f in os.listdir(f'{t}/syn'))
            for side, extra in (('ref1', ['--lp-threads', '4']), ('ref2', ['--lp-threads', '1'])):
                r = py(os.path.join(TOOLS, 'replay.py'), 'ref', '--exe', f'{b}/nxd9play.exe', '--prefix', f'{b}/pfx',
                       '--out', f'{t}/{side}', *extra, *caps)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = py(os.path.join(TOOLS, 'replay.py'), 'cand', '--bin', f'{b}/nx_render', '--out', f'{t}/cand', *caps)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = py(os.path.join(TOOLS, 'capcompare.py'), '--ref', f'{t}/ref1', '--ref2', f'{t}/ref2', '--cand',
                   f'{t}/cand', '--out', f'{t}/gate')
            g = json.load(open(f'{t}/gate/capgate.json'))
            self.assertEqual(g['summary']['metric']['floor_max_abs'], 0, 'the Wine reference must repeat itself')


if __name__ == '__main__':
    unittest.main(verbosity=1)
